#!/usr/bin/env python3
"""Isolated, fail-closed Linux calibration lab. No import-time external effects.

The controller stays in the management namespace. All traffic workers run as
the invoking normal user inside owned endpoint namespaces. Pure plan is portable.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import secrets
import select
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time

SCHEMA_VERSION = 2
JOURNAL_PARENT_DIR = "/run/mfl-network-lab"
MAX_EVIDENCE_BYTES = 25 * 1024 * 1024
MAX_VERIFY_SECONDS = 900
SUMMARY_RESERVE = 64 * 1024
IDENTITY_FIELDS = ("pid", "start", "net", "pgid", "uid", "gid")
PATHS = {
    "a": {"robot": "198.18.10.1", "gateway_r": "198.18.10.2",
          "gateway_s": "198.18.11.1", "station": "198.18.11.2",
          "robot_dev": "ra", "router_r": "ar", "router_s": "as",
          "station_dev": "sa", "echo": 5301, "iperf": 5201},
    "b": {"robot": "198.18.20.1", "gateway_r": "198.18.20.2",
          "gateway_s": "198.18.21.1", "station": "198.18.21.2",
          "robot_dev": "rb", "router_r": "br", "router_s": "bs",
          "station_dev": "sb", "echo": 5302, "iperf": 5202},
}
PROFILES = ("unshaped", "symmetric", "slow-a")
PACKET = struct.Struct("!16sQd32s")  # exactly 64 bytes
EXPECTED_UNREACHABLE = {errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ENETDOWN}


class Unsafe(ValueError):
    """Unverified ownership, invalid input or collision: exit 2."""


class Missing(RuntimeError):
    """Unavailable platform, prerequisite or privilege: exit 3."""


class Failed(RuntimeError):
    """Executed validation, measurement or restoration failure: exit 1."""


def select_management_device(links, routes):
    """Select the unique best usable IPv4 default-route device, without mutation."""
    available = {link["ifname"] for link in links if "UP" in link.get("flags", [])}
    candidates = [(int(route.get("metric", 0)), route["dev"]) for route in routes
                  if route.get("dst") in ("default", "0.0.0.0/0")
                  and route.get("dev") in available and route.get("dev") != "lo"]
    if not candidates:
        raise Failed("Management device unavailable: no usable IPv4 default route")
    best_metric = min(metric for metric, device in candidates)
    devices = {device for metric, device in candidates if metric == best_metric}
    if len(devices) != 1:
        raise Failed("Ambiguous management devices at the same default-route metric")
    return devices.pop()


def validate_run_id(run):
    if not isinstance(run, str) or len(run) != 8 or any(c not in "0123456789abcdef" for c in run):
        raise Unsafe("run-id must be exactly eight lowercase hexadecimal characters")
    return run


def namespaces(run):
    validate_run_id(run)
    return {r: "mfl-" + letter + "-" + run
            for r, letter in (("robot", "r"), ("station", "s"), ("a", "a"), ("b", "b"))}


def invoking_user():
    if sys.platform != "linux" or os.geteuid() != 0:
        raise Missing("Linux and sudo from a normal user are required")
    import pwd
    import grp
    try:
        uid, gid = int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"])
        pw = pwd.getpwuid(uid)
        grp.getgrgid(gid)
        groups = os.getgrouplist(pw.pw_name, pw.pw_gid)
    except (KeyError, ValueError, OverflowError) as exc:
        raise Unsafe("Unverified sudo invoker") from exc
    if uid <= 0 or gid != pw.pw_gid or gid not in groups:
        raise Unsafe("Invoker must be a normal account with its primary group")
    return {"uid": uid, "gid": gid, "name": pw.pw_name}


def tools():
    result = {}
    for name in ("ip", "tc", "iperf3", "tcpdump", "setpriv", "sysctl"):
        found = shutil.which(name)
        if not found:
            raise Missing("Missing executable: " + name)
        result[name] = str(Path(found).resolve(strict=True))
    result["python"] = str(Path("/usr/bin/python3").resolve(strict=True))
    return result


def worker_argv(run, kind, path, **options):
    validate_run_id(run)
    if kind not in ("_server", "_probe") or path not in PATHS:
        raise Unsafe("Invalid worker")
    argv = ["--run-id", run, kind, "--path", path]
    for key, value in options.items():
        if key not in ("count", "duration"):
            raise Unsafe("Invalid worker option")
        argv += ["--" + key, str(value)]
    return argv


def pure_plan(run):
    ns = namespaces(run)
    result = {"operation_status": "PASS", "verification_status": "NOT_RUN",
              "phase_complete": False, "run_id": run, "namespaces": ns,
              "addresses": PATHS, "commands": {"setup": [], "profiles": {}, "links": {}, "cleanup": []}}
    commands = result["commands"]
    for name in ns.values():
        commands["setup"] += [["ip", "netns", "add", name],
                              ["ip", "netns", "exec", name, "ip", "link", "set", "dev", "lo", "up"]]
    for path, spec in PATHS.items():
        router = ns[path]
        commands["setup"].append(["ip", "netns", "exec", router, "sysctl", "-w", "net.ipv4.ip_forward=1"])
        for left, right, owner_l, owner_r, ip_l, ip_r in (
            (spec["robot_dev"], spec["router_r"], "robot", path, spec["robot"], spec["gateway_r"]),
            (spec["router_s"], spec["station_dev"], path, "station", spec["gateway_s"], spec["station"])):
            tl, tr = "v_" + left + "_" + run, "v_" + right + "_" + run
            commands["setup"].append(["ip", "link", "add", tl, "type", "veth", "peer", "name", tr])
            for temp, dev, owner, address in ((tl, left, owner_l, ip_l), (tr, right, owner_r, ip_r)):
                # <TOKEN> is a placeholder; execution uses a random ownership token.
                commands["setup"] += [
                    ["ip", "link", "set", "dev", temp, "alias", "mfl:" + run + ":" + dev + ":<TOKEN>"],
                    ["ip", "link", "set", "dev", temp, "netns", ns[owner], "name", dev],
                    ["ip", "netns", "exec", ns[owner], "ip", "link", "set", "dev", dev, "mtu", "1500"],
                    ["ip", "netns", "exec", ns[owner], "ip", "addr", "add", address + "/" + str(address_prefix(spec, owner)), "dev", dev],
                    ["ip", "netns", "exec", ns[owner], "ip", "link", "set", "dev", dev, "up"]]
        for owner, destination, gateway, dev, src in (
            ("robot", spec["station"], spec["gateway_r"], spec["robot_dev"], spec["robot"]),
            ("station", spec["robot"], spec["gateway_s"], spec["station_dev"], spec["station"])):
            commands["setup"].append(["ip", "netns", "exec", ns[owner], "ip", "route", "add",
                                      destination + "/32", "via", gateway, "dev", dev, "src", src])
        commands["links"][path] = {
            state: ["ip", "netns", "exec", router, "ip", "link", "set", "dev", spec["router_s"], state]
            for state in ("up", "down")}
    for profile in PROFILES:
        commands["profiles"][profile] = []
        for path, spec in PATHS.items():
            for dev in (spec["router_r"], spec["router_s"]):
                commands["profiles"][profile] += shaping_commands(ns[path], dev, profile, path)
    commands["cleanup"] = [["ip", "netns", "del", name] for name in reversed(list(ns.values()))]
    result["notes"] = ["Every mutation has live ownership guards not represented as shell commands.",
                       "Cleanup/rollback delete proven owned resources only.",
                       "unshaped deletion is conditional on successfully inspected live state."]
    return result


def shaping_commands(ns, dev, profile, path):
    if profile not in PROFILES or path not in PATHS or dev not in (PATHS[path]["router_r"], PATHS[path]["router_s"]):
        raise Unsafe("Invalid shaping scope")
    prefix = ["ip", "netns", "exec", ns, "tc"]
    if profile == "unshaped":
        return [prefix + ["qdisc", "del", "dev", dev, "root"]]
    rate = "2mbit" if profile == "slow-a" and path == "a" else "10mbit"
    return [
        prefix + ["qdisc", "replace", "dev", dev, "root", "handle", "1:", "htb", "default", "10"],
        prefix + ["class", "replace", "dev", dev, "parent", "1:", "classid", "1:10",
                  "htb", "rate", rate, "ceil", rate, "burst", "32kb", "cburst", "32kb", "quantum", "1500"],
        prefix + ["qdisc", "replace", "dev", dev, "parent", "1:10", "handle", "10:",
                  "netem", "delay", "20ms", "limit", "1000"]]


def existing_htb_root_matches(argv, state):
    """Skip ONLY the identical static HTB root operation; never ignore errors."""
    if len(argv) != 11 or argv[:4] != ["tc", "qdisc", "replace", "dev"] or argv[5:] != [
            "root", "handle", "1:", "htb", "default", "10"]:
        return False
    roots = [q for q in state["qdisc"] if q.get("root")]
    return (len(roots) == 1 and roots[0].get("kind") == "htb"
            and roots[0].get("handle") == "1:"
            and roots[0].get("options", {}).get("default") in ("0x10", 16)
            and roots[0].get("options", {}).get("r2q") == 10)


def stable(value):
    """Strip volatile tc statistics, retain actual configuration including options."""
    volatile = {"bytes", "packets", "drops", "overlimits", "requeues", "backlog",
                "qlen", "refcnt", "stats", "stats2", "xstats", "used", "lastuse",
                "tokens", "ctokens", "lended", "borrowed", "giants", "direct_packets_stat"}
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items() if k not in volatile}
    if isinstance(value, list):
        return sorted((stable(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    return value


def process_identity(pid):
    try:
        text = Path("/proc/" + str(pid) + "/stat").read_text()
        status = Path("/proc/" + str(pid) + "/status").read_text().splitlines()
        uid = int(next(line for line in status if line.startswith("Uid:")).split()[2])
        gid = int(next(line for line in status if line.startswith("Gid:")).split()[2])
        return {"pid": pid, "start": text.rsplit(")", 1)[1].split()[19],
                "net": os.stat("/proc/" + str(pid) + "/ns/net").st_ino,
                "pgid": os.getpgid(pid), "uid": uid, "gid": gid}
    except (OSError, IndexError, StopIteration, ValueError):
        return None


def implementation_hashes():
    root = Path(__file__).resolve().parent.parent
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ("tools/network_lab.py", "tests/test_network_lab.py",
                         "docs/linux-network-lab.md", "tests/test_suite_manifest.json")}


def numeric(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise Failed("Invalid " + name)
    if value < 0 or (positive and value == 0):
        raise Failed("Invalid " + name)
    return value


def parse_iperf(text, minimum_seconds=14.0):
    try:
        data = json.loads(text)
        if data.get("error") or data["start"]["test_start"]["protocol"] != "UDP":
            raise Failed("iperf error/wrong protocol")
        metric = data["end"].get("sum_received")
        if metric is None:
            metric = data["end"].get("sum")
        if not isinstance(metric, dict) or metric.get("sender") is not False:
            raise Failed("Receiver role is not proven")
        rate = numeric(metric["bits_per_second"], "receiver bitrate")
        seconds = numeric(metric["seconds"], "duration", positive=True)
        count = numeric(metric["bytes"], "received bytes")
        if seconds < minimum_seconds or count == 0:
            raise Failed("Short/empty iperf measurement")
        return {"receiver_bps": rate, "seconds": seconds, "bytes": count,
                "loss_percent": metric.get("lost_percent")}
    except (KeyError, TypeError, ValueError) as exc:
        raise Failed("Malformed/incomplete receiver iperf JSON") from exc


def capacity_ok(rates, cap, trials=3):
    return (len(rates) == trials and
            all(isinstance(r, (int, float)) and not isinstance(r, bool) and math.isfinite(r)
                and 0.85 * cap <= r <= 1.05 * cap for r in rates))


def delay_ok(baseline, shaped):
    """Predeclared paired-trial rule: every one of three valid pairs must pass."""
    if len(baseline) != 3 or len(shaped) != 3:
        return False
    return all(probe_ok(u) and probe_ok(s) and 30 <= s["median_ms"] - u["median_ms"] <= 60
               for u, s in zip(baseline, shaped))


def probe_ok(result, required=99, count=100):
    return (result.get("count") == count and result.get("received", -1) >= required
            and result.get("mismatches", 1) == 0 and result.get("duplicates", 1) == 0)


def required_checks(stage):
    checks = {"readiness", "traversal"}
    if stage in ("calibration", "all"):
        checks.add("delay")
        checks.update(profile + "-" + path + "-" + direction
                      for profile in PROFILES for path in PATHS for direction in ("forward", "reverse"))
    if stage in ("isolation", "all"):
        checks.update({"isolation-a", "isolation-b"})
    return checks


def reply_verdict(data, peer, expected_peer, token, sent, received, accepted, deadline=2.0):
    if peer != expected_peer or len(data) != PACKET.size:
        return "mismatch", None
    tag, seq, timestamp, padding = PACKET.unpack(data)
    if tag != token or seq not in sent or data != sent[seq]["packet"]:
        return "mismatch", None
    if seq in accepted:
        return "duplicate", seq
    if received < sent[seq]["time"] or received - sent[seq]["time"] > deadline:
        return "late", seq
    return "valid", seq


def echo_server(run, path, duration):
    spec = PATHS[path]
    if not 1 <= duration <= 960:
        raise Unsafe("Invalid server lifetime")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((spec["station"], spec["echo"]))
        sock.settimeout(0.2)
        print(json.dumps({"ready": True, "path": path}), flush=True)
        until = time.monotonic() + duration
        while time.monotonic() < until:
            try:
                data, peer = sock.recvfrom(128)
                if len(data) == PACKET.size and data[:8] == run.encode() and peer[0] == spec["robot"]:
                    sock.sendto(data, peer)
            except socket.timeout:
                continue


def echo_probe(run, path, count, interval=0.1, deadline=2.0, warm=5):
    if not 1 <= count <= 100:
        raise Unsafe("Probe count must be 1..100")
    spec = PATHS[path]
    token = run.encode() + secrets.token_bytes(8)
    sent, accepted, events = {}, {}, []
    total = count + warm
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((spec["robot"], 0))
        sock.setblocking(False)
        start = time.monotonic()
        next_seq = 0
        final_deadline = start + (total - 1) * interval + deadline
        while time.monotonic() < final_deadline:
            now = time.monotonic()
            if next_seq < total and now >= start + next_seq * interval:
                packet = PACKET.pack(token, next_seq, now, bytes(32))
                sent[next_seq] = {"time": now, "packet": packet}
                try:
                    sock.sendto(packet, (spec["station"], spec["echo"]))
                except OSError as exc:
                    if exc.errno not in EXPECTED_UNREACHABLE:
                        raise
                    events.append({"seq": next_seq, "at": now, "outcome": "send_error", "error": str(exc)})
                next_seq += 1
            wait = min(0.02, max(0, final_deadline - time.monotonic()))
            if select.select([sock], [], [], wait)[0]:
                try:
                    data, peer = sock.recvfrom(128)
                except OSError as exc:
                    if exc.errno not in EXPECTED_UNREACHABLE:
                        raise
                    events.append({"seq": None, "at": time.monotonic(), "outcome": "receive_error", "error": str(exc)})
                    continue
                received = time.monotonic()
                verdict, seq = reply_verdict(data, peer, (spec["station"], spec["echo"]),
                                             token, sent, received, accepted, deadline)
                events.append({"seq": seq, "at": received, "outcome": verdict})
                if verdict == "valid":
                    accepted[seq] = received
            if next_seq == total and len(accepted) == total:
                break
        samples = [{"seq": seq, "send": sent[seq]["time"], "receive": accepted.get(seq),
                    "rtt_ms": (accepted[seq] - sent[seq]["time"]) * 1000 if seq in accepted else None,
                    "outcome": "valid" if seq in accepted else "timeout"}
                   for seq in range(warm, total)]
    rtts = sorted(s["rtt_ms"] for s in samples if s["outcome"] == "valid")
    median = ((rtts[(len(rtts)-1)//2] + rtts[len(rtts)//2]) / 2 if rtts else None)
    return {"count": count, "received": len(rtts), "timeouts": count-len(rtts),
            "median_ms": median, "p95_ms": rtts[min(len(rtts)-1, math.ceil(.95*len(rtts))-1)] if rtts else None,
            "mismatches": sum(e["outcome"] == "mismatch" for e in events),
            "duplicates": sum(e["outcome"] == "duplicate" for e in events),
            "late": sum(e["outcome"] == "late" for e in events),
            "transport_errors": sum(e["outcome"] in ("send_error", "receive_error") for e in events),
            "start": start, "end": time.monotonic(), "samples": samples, "events": events}


def secure_component(path, owner, group=None, directory=False):
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode) or (directory and not stat.S_ISDIR(st.st_mode)):
        raise Unsafe("Unsafe path type: " + str(path))
    if not directory and not stat.S_ISREG(st.st_mode):
        raise Unsafe("Not a regular file: " + str(path))
    if st.st_uid != owner or st.st_mode & 0o022 or (group is not None and st.st_gid != group):
        raise Unsafe("Unsafe ownership/mode: " + str(path))


def secure_parents(path):
    for parent in reversed(path.absolute().parents):
        secure_component(parent, 0, directory=True)


def atomic_json(path, data, gid):
    """Root-only, fixed owned directory; no symlink following or writable group."""
    secure_component(path.parent, 0, gid, directory=True)
    temporary = path.parent / (".write-" + secrets.token_hex(8))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(temporary, flags, 0o640)
    try:
        os.fchown(fd, 0, gid)
        with os.fdopen(fd, "w", encoding="utf8") as handle:
            json.dump(data, handle, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists() or path.is_symlink():
            secure_component(path, 0, gid)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def address_prefix(layout, role):
    """Default /30 stays unchanged; separate DDS diagnostic uses endpoint /23.

    More-specific peer /32 gateway routes still enforce router traversal.
    Never widen router interfaces or accept arbitrary subnet sizes.
    """
    prefix = layout.get("endpoint_prefixlen", 30) if role in ("robot", "station") else 30
    if prefix not in (23, 30):
        raise Unsafe("Unsupported lab endpoint prefix")
    return prefix


class Lab:
    """All external operations are centralized here, journaled and bounded."""
    def __init__(self, run, create=False, *, allow_profile_inspection_recovery=False,
                 allow_sensor_fault_recovery=False):
        self.run = validate_run_id(run)
        self.user = invoking_user()
        self.exe = tools()
        self.ns = namespaces(run)
        self.parent = Path(JOURNAL_PARENT_DIR)
        self.directory = self.parent / run
        self.journal_path = self.directory / "journal.json"
        self.children = {}
        self.deadline = None
        self.attempt = None
        self.lock = threading.RLock()
        secure_parents(self.parent)
        if create:
            if not self.parent.exists():
                self.parent.mkdir(mode=0o755)
            secure_component(self.parent, 0, directory=True)
            if self.directory.exists() or self.directory.is_symlink():
                raise Unsafe("Run ID already used")
            # Single active lab; do not interfere with an unrelated resource.
            for child in self.parent.iterdir():
                secure_component(child, 0, directory=True)
                saved = child / "journal.json"
                if not saved.is_file() or saved.is_symlink():
                    raise Unsafe("Unexplained run directory")
                secure_component(saved, 0)
                prior = json.loads(saved.read_text())
                if prior.get("closed") is not True:
                    raise Unsafe("Another lab is active; review/clean it first")
            existing = self.exec(["ip", "netns", "list"], record=False)["stdout"]
            if any(name in existing.split() for name in self.ns.values()):
                raise Unsafe("Existing namespace name collision")
            for name in self.temporary_names():
                if self.link("root", name, absent_ok=True, record=False) is not None:
                    raise Unsafe("Existing temporary link collision")
            self.directory.mkdir(mode=0o750)
            os.chown(self.directory, 0, self.user["gid"])
            os.chmod(self.directory, 0o750)
            self.data = {"schema_version": SCHEMA_VERSION, "run_id": run, "user": self.user,
                         "token": secrets.token_hex(16), "controller_net": os.stat("/proc/self/ns/net").st_ino,
                         "closed": False, "pending": None, "namespaces": {}, "links": {},
                         "pids": [], "profile": "unshaped", "tc": {}, "commands": [],
                         "attempts": [], "setup_status": "NOT_RUN", "cleanup_status": "NOT_RUN"}
            self.data["implementation"] = implementation_hashes()
            self.save()
        else:
            secure_component(self.parent, 0, directory=True)
            secure_component(self.directory, 0, self.user["gid"], directory=True)
            secure_component(self.journal_path, 0, self.user["gid"])
            self.data = json.loads(self.journal_path.read_text())
            if (self.data.get("schema_version") != SCHEMA_VERSION or self.data.get("run_id") != run
                    or self.data.get("user") != self.user):
                raise Unsafe("Journal schema/run/invoker mismatch")
            if self.data.get("controller_net") != os.stat("/proc/self/ns/net").st_ino:
                raise Unsafe("Controller namespace changed")
            pending = self.data.get("pending")
            sensor_recovery = False
            if pending and allow_sensor_fault_recovery:
                path = pending.get("ns")
                dev = PATHS.get(path, {}).get("router_s")
                sensor_recovery = bool(dev and pending.get("label") == "benchmark link " + dev
                    and pending.get("argv") in (["ip", "link", "set", "dev", dev, "down"],
                                                 ["ip", "link", "set", "dev", dev, "up"]))
            if pending and not (sensor_recovery or (allow_profile_inspection_recovery
                                and pending.get("label", "").startswith("profile ")
                                and pending.get("argv", [])[:3] in
                                (["tc", "class", "replace"], ["tc", "qdisc", "replace"]))):
                raise Unsafe("Interrupted mutation: ownership uncertain; manual review required")

    def save(self):
        with self.lock:
            atomic_json(self.journal_path, self.data, self.user["gid"])

    def temporary_names(self):
        return ["v_" + dev + "_" + self.run for p in PATHS.values()
                for dev in (p["robot_dev"], p["router_r"], p["router_s"], p["station_dev"])]

    def command(self, argv, ns=None, user=False):
        first = self.exe.get(argv[0], argv[0])
        if not Path(first).is_absolute():
            raise Unsafe("Executable must be resolved")
        cmd = [first] + list(argv[1:])
        if user:
            cmd = [self.exe["setpriv"], "--reuid", str(self.user["uid"]),
                   "--regid", str(self.user["gid"]), "--init-groups", "--"] + cmd
        if ns:
            if ns not in self.ns or ns not in self.data.get("namespaces", {}):
                raise Unsafe("Unowned namespace")
            self.assert_namespace(ns)
            cmd = [self.exe["ip"], "netns", "exec", self.ns[ns]] + cmd
        return cmd

    def exec(self, argv, ns=None, user=False, timeout=20, record=True):
        cmd = self.command(argv, ns, user)
        bound = timeout if self.deadline is None else min(timeout, self.deadline - time.monotonic())
        if bound <= 0:
            raise Failed("Whole verification deadline exceeded")
        start = time.monotonic()
        child = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, start_new_session=True)
        identity = process_identity(child.pid)
        timed_out = False
        safety_error = None
        try:
            stdout, stderr = child.communicate(timeout=bound)
        except subprocess.TimeoutExpired as expired:
            timed_out = True
            current = process_identity(child.pid)
            # ip netns exec/setpriv legitimately change net/UID/GID AFTER
            # Popen. PID, birth time and the private process group must not.
            expected_net = self.data.get("namespaces", {}).get(ns, {}).get("inode") if ns else None
            allowed = (identity is not None and current is not None
                       and all(identity.get(k) is not None and current.get(k) is not None
                               for k in ("pid", "start", "pgid", "net", "uid", "gid"))
                       and all(identity.get(k) is not None and identity[k] == current.get(k)
                               for k in ("pid", "start", "pgid"))
                       and current["pid"] == child.pid and current["pgid"] == child.pid
                       and current.get("net") in (identity.get("net"), expected_net)
                       and current.get("uid") in (identity.get("uid"), self.user["uid"] if user else identity.get("uid"))
                       and current.get("gid") in (identity.get("gid"), self.user["gid"] if user else identity.get("gid")))
            if allowed and ns:
                try:
                    self.assert_namespace(ns)
                except Unsafe:
                    allowed = False
            if not allowed:
                safety_error = "Cannot prove timed-out command process identity"
                stdout, stderr = expired.output or "", expired.stderr or ""
            else:
                os.killpg(child.pid, signal.SIGKILL)
                try:
                    stdout, stderr = child.communicate(timeout=3)
                except subprocess.TimeoutExpired as exc:
                    safety_error = "Pipe drain exceeded bounded deadline"
                    stdout, stderr = exc.output or "", exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
        outcome = {"argv": cmd, "namespace": ns or "management", "start": start,
                   "end": time.monotonic(), "exit": child.returncode, "timeout": timed_out,
                   "stdout": stdout, "stderr": stderr}
        if timed_out:
            outcome.update(identity_initial=identity, identity_at_timeout=current,
                           timeout_bound_seconds=bound, safety_error=safety_error)
        if record:
            self.data["commands"].append(outcome)
            self.save()
            if self.attempt:
                self.write("command-" + secrets.token_hex(6) + ".json", outcome)
        if safety_error:
            raise Unsafe(safety_error + ": " + str(outcome))
        if timed_out or child.returncode != 0:
            raise Failed("Command failed: " + str(outcome))
        return outcome

    def read_json(self, argv, ns=None, *, record=True):
        output = self.exec(argv, ns, record=record)["stdout"]
        # Some iproute2 releases print nothing for an empty class dump.
        # Only this successful, read-only query has an empty-list meaning.
        if argv[:4] == ["tc", "-j", "class", "show"] and not output.strip():
            return []
        try:
            value = json.loads(output)
        except ValueError as exc:
            # iproute2 6.1 q_htb prints classes as text even with -j. Retain
            # the ENTIRE static line for equality guards; never treat it as an
            # empty dump or accept arbitrary non-JSON from other inspections.
            if argv[:4] == ["tc", "-j", "class", "show"]:
                lines = [line.strip() for line in output.splitlines() if line.strip()]
                pattern = (r"class htb [0-9a-f]+:[0-9a-f]+ (?:root|parent [0-9a-f]+:[0-9a-f]*) "
                           r"(?:leaf [0-9a-f]+: )?prio [0-9]+ rate [0-9.]+[KMGT]?bit ceil [0-9.]+[KMGT]?bit "
                           r"burst [0-9.]+[KMGT]?b cburst [0-9.]+[KMGT]?b")
                if lines and all(re.fullmatch(pattern, line) for line in lines):
                    return [{"kind": "htb", "legacy_static_text": line} for line in lines]
            raise Failed("Invalid inspection JSON") from exc
        if not isinstance(value, list):
            raise Failed("Expected JSON list")
        return value

    def assert_namespace(self, role):
        saved = self.data["namespaces"].get(role)
        if not saved or saved["name"] != self.ns[role]:
            raise Unsafe("Namespace not owned")
        try:
            inode = os.stat("/run/netns/" + self.ns[role]).st_ino
        except OSError as exc:
            raise Unsafe("Namespace missing") from exc
        if inode != saved["inode"]:
            raise Unsafe("Namespace was replaced")

    def link(self, role, dev, absent_ok=False, *, record=True):
        result = self.read_json(["ip", "-j", "link", "show"], None if role == "root" else role,
                                record=record)
        found = [v for v in result if v["ifname"].split("@")[0] == dev]
        if len(found) != 1:
            if not found and absent_ok:
                return None
            raise Unsafe("Missing/ambiguous interface: " + dev)
        return found[0]

    def assert_link(self, key):
        saved = self.data["links"][key]
        allowed = {p["robot_dev"]: "robot" for p in PATHS.values()}
        allowed.update({p["station_dev"]: "station" for p in PATHS.values()})
        for path, p in PATHS.items():
            allowed.update({p["router_r"]: path, p["router_s"]: path})
        expected_alias = "mfl:" + self.run + ":" + key + ":" + self.data["token"]
        expected_dev = "v_" + key + "_" + self.run if saved["role"] == "root" else key
        if (key not in allowed or saved["dev"] != expected_dev or
                saved["role"] not in ("root", allowed[key]) or saved["alias"] != expected_alias):
            raise Unsafe("Invalid/unverified link ownership record")
        actual = self.link(saved["role"], saved["dev"])
        if actual.get("ifindex") != saved["ifindex"] or actual.get("ifalias") != saved["alias"]:
            raise Unsafe("Link replaced/unowned: " + key)
        return actual

    def ensure_active(self):
        if self.data.get("pending"):
            raise Unsafe("Pending mutation requires ownership/state reconciliation")
        if self.data["closed"] or self.data.get("setup_status") != "PASS":
            raise Unsafe("Run is closed or setup incomplete")
        for role in self.data["namespaces"]:
            self.assert_namespace(role)
        for key in self.data["links"]:
            self.assert_link(key)

    def tc_state(self, path, dev):
        return {kind: stable(self.read_json(["tc", "-j", kind, "show", "dev", dev], path))
                for kind in ("qdisc", "class")}

    def assert_tc(self, path, dev):
        actual = self.tc_state(path, dev)
        if actual != self.data["tc"].get(dev):
            raise Unsafe("Unknown qdisc alteration: " + dev)
        return actual

    def mutation(self, label, argv, ns=None):
        if self.data.get("pending"):
            raise Unsafe("Cannot replace an unresolved mutation")
        self.data["pending"] = {"label": label, "argv": argv, "ns": ns}
        self.save()
        result = self.exec(argv, ns)
        return result

    def completed(self):
        self.data["pending"] = None
        self.save()

    def reconcile_failed_change(self):
        """Only the same supervisor may clear a failed observable link/tc change.

        Never adopt uncertain namespace/link creation or replacement. A restarted
        process refuses pending journals in __init__ and requests manual review.
        """
        pending = self.data.get("pending")
        if not pending:
            return
        label = pending["label"]
        if label.startswith("profile "):
            dev = label.split()[1]
            self.assert_link(dev)
            self.assert_tc(pending["ns"], dev)
        elif label.startswith("link "):
            self.assert_link(label.split()[1])
        else:
            raise Unsafe("Creation/deletion outcome uncertain; manual review required")
        self.completed()

    def snapshot(self):
        links = self.read_json(["ip", "-j", "link", "show"])
        routes = self.read_json(["ip", "-j", "-4", "route", "show"])
        device = self.data.get("management_device")
        if device is None:
            device = select_management_device(links, routes)
            self.data["management_device"] = device
        management = [v for v in links if v["ifname"] == device]
        if len(management) != 1:
            raise Failed("Management device unavailable: " + str(device))
        ipv4 = self.read_json(["ip", "-j", "-4", "addr", "show", "dev", device])
        return {"net": os.stat("/proc/self/ns/net").st_ino,
                "management_device": device,
                "links": {v["ifname"]: {k: v.get(k) for k in
                          ("ifindex", "address", "flags", "mtu")} for v in links},
                "addresses": sorted([i["local"], i["prefixlen"]]
                                    for v in ipv4 for i in v["addr_info"] if i["family"] == "inet"),
                "routes": stable([{k: v.get(k) for k in ("dst", "gateway", "dev", "metric", "prefsrc", "table")}
                                  for v in routes]),
                "forward": self.exec(["sysctl", "-n", "net.ipv4.ip_forward"])["stdout"].strip(),
                "qdisc": stable(self.read_json(["tc", "-j", "qdisc", "show", "dev", device])),
                "namespaces": sorted(self.exec(["ip", "netns", "list"])["stdout"].splitlines())}

    def management_safe(self):
        before, after = self.data["management_before"], self.snapshot()
        for key in ("net", "addresses", "routes", "forward", "qdisc"):
            if before[key] != after[key]:
                raise Failed("Management changed: " + key)
        for name, value in before["links"].items():
            if after["links"].get(name) != value:
                raise Failed("Pre-existing interface changed: " + name)
        unexpected = set(after["links"]) - set(before["links"])
        if unexpected:
            raise Failed("Unexpected management interfaces: " + str(unexpected))
        for entry in before["namespaces"]:
            if entry not in after["namespaces"]:
                raise Failed("Pre-existing namespace disappeared")
        return after

    def setup(self):
        try:
            self.data["management_before"] = self.snapshot()
            self.save()
            for role, name in self.ns.items():
                self.mutation("create namespace", ["ip", "netns", "add", name])
                self.data["namespaces"][role] = {"name": name, "inode": os.stat("/run/netns/" + name).st_ino}
                self.completed()
                self.exec(["ip", "link", "set", "dev", "lo", "up"], role)
            for path, p in PATHS.items():
                self.exec(["sysctl", "-q", "-w", "net.ipv4.ip_forward=1"], path)
                for left, right, owner_l, owner_r, ip_l, ip_r in (
                    (p["robot_dev"], p["router_r"], "robot", path, p["robot"], p["gateway_r"]),
                    (p["router_s"], p["station_dev"], path, "station", p["gateway_s"], p["station"])):
                    tl, tr = "v_" + left + "_" + self.run, "v_" + right + "_" + self.run
                    if self.link("root", tl, True) or self.link("root", tr, True):
                        raise Unsafe("Temporary link collision")
                    self.mutation("create pair", ["ip", "link", "add", tl, "type", "veth", "peer", "name", tr])
                    for temp, key in ((tl, left), (tr, right)):
                        actual = self.link("root", temp)
                        self.data["links"][key] = {"role": "root", "dev": temp,
                                                  "ifindex": actual["ifindex"], "alias": None}
                    self.completed()
                    for temp, dev, role, address in ((tl, left, owner_l, ip_l), (tr, right, owner_r, ip_r)):
                        saved = self.data["links"][dev]
                        # Created end identity must still match before adding its ownership tag.
                        if self.link("root", temp)["ifindex"] != saved["ifindex"]:
                            raise Unsafe("New temporary link replaced")
                        alias = "mfl:" + self.run + ":" + dev + ":" + self.data["token"]
                        self.mutation("tag pair end", ["ip", "link", "set", "dev", temp, "alias", alias])
                        saved["alias"] = alias
                        self.completed()
                        self.assert_link(dev)
                        self.mutation("move pair end", ["ip", "link", "set", "dev", temp, "netns", self.ns[role], "name", dev])
                        actual = self.link(role, dev)
                        if actual.get("ifalias") != alias:
                            raise Unsafe("Moved link token mismatch")
                        saved.update(role=role, dev=dev, ifindex=actual["ifindex"])
                        self.completed()
                        self.assert_link(dev)
                        self.exec(["ip", "link", "set", "dev", dev, "mtu", "1500"], role)
                        self.exec(["ip", "addr", "add", address + "/" + str(address_prefix(p, role)), "dev", dev], role)
                        self.exec(["ip", "link", "set", "dev", dev, "up"], role)
                for role, dst, gateway, dev, src in (
                    ("robot", p["station"], p["gateway_r"], p["robot_dev"], p["robot"]),
                    ("station", p["robot"], p["gateway_s"], p["station_dev"], p["station"])):
                    self.assert_link(dev)
                    self.exec(["ip", "route", "add", dst + "/32", "via", gateway, "dev", dev, "src", src], role)
                for dev in (p["router_r"], p["router_s"]):
                    self.data["tc"][dev] = self.tc_state(path, dev)
            self.data["management_after_setup"] = self.management_safe()
            self.data["setup_status"] = "PASS"
            self.save()
        except BaseException as exc:
            self.data["setup_status"] = "FAILED"
            self.data["setup_error"] = str(exc)
            self.save()
            # Ambiguous pending mutations are not adopted or blindly rolled back.
            if not self.data["pending"]:
                try:
                    self.cleanup()
                except BaseException as cleanup_error:
                    self.data["cleanup_error"] = str(cleanup_error)
                    self.save()
            raise
        return {"setup_status": "PASS", "phase_complete": False, "run_id": self.run}

    def profile(self, profile):
        if profile not in PROFILES:
            raise Unsafe("Unknown profile")
        self.ensure_active()
        # Inspect EVERY target before any mutation.
        for path, p in PATHS.items():
            for dev in (p["router_r"], p["router_s"]):
                self.assert_tc(path, dev)
        for path, p in PATHS.items():
            for dev in (p["router_r"], p["router_s"]):
                for cmd in shaping_commands(self.ns[path], dev, profile, path):
                    if (cmd[4:7] == ["tc", "qdisc", "replace"]
                            and existing_htb_root_matches(cmd[4:], self.data["tc"][dev])):
                        # HTB lacks a root change operation on some kernels.
                        # Replacing the identical owned root attempts CHANGE and
                        # returns EOPNOTSUPP. Preserve it, then update its children.
                        continue
                    if profile == "unshaped":
                        state = self.assert_tc(path, dev)
                        if not any(q.get("kind") != "noqueue" and q.get("root") for q in state["qdisc"]):
                            continue
                    self.assert_link(dev)
                    self.mutation("profile " + dev, cmd[4:], path)
                    self.data["tc"][dev] = self.tc_state(path, dev)
                    self.completed()
        self.data["profile"] = profile
        self.save()

    def link_state(self, path, state):
        if path not in PATHS or state not in ("up", "down"):
            raise Unsafe("Invalid link change")
        self.ensure_active()
        dev = PATHS[path]["router_s"]
        self.assert_link(dev)
        self.mutation("link " + dev, ["ip", "link", "set", "dev", dev, state], path)
        actual = self.assert_link(dev)
        if ("UP" in actual["flags"]) != (state == "up"):
            raise Failed("Link state read-back failed")
        self.completed()

    def benchmark_link_state(self, path, state):
        """Bounded, owned-interface-only switch after full trial preflight.

        Does not inspect every unrelated qdisc/interface inside the timing window.
        Namespace inode and target interface index/alias are rechecked at each
        switch; the existing general-purpose link_state API is unchanged.
        """
        if path not in PATHS or state not in ("up", "down"):
            raise Unsafe("Invalid benchmark link change")
        if self.data.get("pending") or self.data["closed"] or self.data.get("setup_status") != "PASS":
            raise Unsafe("Run not ready for benchmark switch")
        for role in self.data["namespaces"]:
            self.assert_namespace(role)
        dev = PATHS[path]["router_s"]
        previous_deadline = self.deadline
        self.deadline = min(previous_deadline or float("inf"), time.monotonic() + 1)
        try:
            self.assert_link(dev)
            self.mutation("benchmark link " + dev, ["ip", "link", "set", "dev", dev, state], path)
            actual = self.assert_link(dev)
            if ("UP" in actual["flags"]) != (state == "up"):
                raise Failed("Benchmark link read-back failed")
            self.completed()
        finally:
            self.deadline = previous_deadline

    def sensor_packet_fault(self, path, state):
        """Drop all packets both ways, retaining the exact baseline delay/rate.

        Unlike link-down this does not close/reconfigure endpoint interfaces.
        Both candidates use this identical blackhole fault, not carrier loss.
        """
        if path not in PATHS or state not in ("up", "down"):
            raise Unsafe("Invalid sensor packet fault")
        if self.data.get("pending") or self.data["closed"] or self.data.get("setup_status") != "PASS":
            raise Unsafe("Run not ready for sensor fault")
        self.assert_namespace(path)
        faults = self.data.setdefault("sensor_faults", {})
        for dev in (PATHS[path]["router_r"], PATHS[path]["router_s"]):
            self.assert_link(dev)
            baseline = shaping_commands(self.ns[path], dev, "baseline", path)[-1][4:]
            command = baseline + (["loss", "100%"] if state == "down" else [])
            if state == "down":
                if dev in faults:
                    raise Unsafe("Sensor fault already active")
                self.assert_tc(path, dev)
                faults[dev] = {"path": path, "baseline": self.data["tc"][dev]}
                self.save()
            elif dev in faults:
                if self.tc_state(path, dev) != faults[dev].get("fault_tc"):
                    raise Unsafe("Sensor fault qdisc changed outside controller")
            else:
                self.assert_tc(path, dev)
                continue
            self.mutation("sensor packet loss " + dev, command, path)
            actual = self.tc_state(path, dev)
            if state == "down":
                netem = [q for q in actual["qdisc"] if q.get("kind") == "netem"]
                if len(netem) != 1 or netem[0].get("options", {}).get("loss-random", {}).get("loss") != 1:
                    raise Failed("100% packet loss read-back not proven")
                faults[dev]["fault_tc"] = actual
            else:
                if actual != faults[dev]["baseline"]:
                    raise Failed("Exact baseline restoration not proven")
                del faults[dev]
            self.completed()

    def status(self):
        self.ensure_active()
        actual = {}
        for role in self.ns:
            actual[role] = {"links": self.read_json(["ip", "-j", "link", "show"], role),
                            "addresses": self.read_json(["ip", "-j", "-4", "addr", "show"], role),
                            "routes": self.read_json(["ip", "-j", "-4", "route", "show"], role)}
        for path, p in PATHS.items():
            for dev in (p["router_r"], p["router_s"]):
                self.assert_tc(path, dev)
        return {"operation_status": "PASS", "verification_status": "NOT_RUN",
                "phase_complete": False, "topology": actual, "profile": self.data["profile"]}

    def topology_check(self):
        self.status()
        for path, p in PATHS.items():
            for role, dev, address in (("robot", p["robot_dev"], p["robot"]),
                                       (path, p["router_r"], p["gateway_r"]),
                                       (path, p["router_s"], p["gateway_s"]),
                                       ("station", p["station_dev"], p["station"])):
                link = self.assert_link(dev)
                if link["mtu"] != 1500 or "UP" not in link["flags"]:
                    raise Failed("Lab link not healthy")
                addr = self.read_json(["ip", "-j", "-4", "addr", "show", "dev", dev], role)
                if not any(i.get("local") == address and i.get("prefixlen") == address_prefix(p, role)
                           for entry in addr for i in entry["addr_info"]):
                    raise Failed("Unexpected lab address")
            for role, src, dst, dev, via in (("robot", p["robot"], p["station"], p["robot_dev"], p["gateway_r"]),
                                            ("station", p["station"], p["robot"], p["station_dev"], p["gateway_s"])):
                route = self.read_json(["ip", "-j", "route", "get", dst, "from", src], role)
                if len(route) != 1 or route[0].get("dev") != dev or route[0].get("gateway") != via:
                    raise Failed("Route lookup does not match path")
        for role in self.ns:
            routes = self.read_json(["ip", "-j", "-4", "route", "show"], role)
            if any(r.get("dst") == "default" for r in routes):
                raise Failed("Unexpected lab default route")
            links = self.read_json(["ip", "-j", "link", "show"], role)
            expected = {"lo"} | {v["dev"] for v in self.data["links"].values() if v["role"] == role}
            if {v["ifname"].split("@")[0] for v in links} != expected:
                raise Failed("Unexpected lab interface/uplink")
            forward = self.exec(["sysctl", "-n", "net.ipv4.ip_forward"], role)["stdout"].strip()
            if forward != ("1" if role in PATHS else "0"):
                raise Failed("Wrong forwarding scope")

    def write(self, name, content):
        with self.lock:
            self._write(name, content)

    def _write(self, name, content):
        if not self.attempt or Path(name).name != name:
            raise Unsafe("Invalid artifact name/scope")
        payload = json.dumps(content, indent=2, allow_nan=False).encode()
        size = sum(p.stat().st_size for p in self.attempt.iterdir() if p.is_file())
        if size + len(payload) > MAX_EVIDENCE_BYTES - SUMMARY_RESERVE:
            raise Failed("Evidence limit exceeded")
        destination = self.attempt / name
        if destination.exists() or destination.is_symlink():
            raise Unsafe("Artifact overwrite refused")
        atomic_json(destination, content, self.user["gid"])

    def start(self, argv, role, user=True, label="worker"):
        self.ensure_active()
        cmd = self.command(argv, role, user)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True)
        identity = None
        until = time.monotonic() + 3
        while time.monotonic() < until and proc.poll() is None:
            identity = process_identity(proc.pid)
            if (identity and identity["net"] == self.data["namespaces"][role]["inode"]
                    and identity["uid"] == (self.user["uid"] if user else 0)
                    and identity["gid"] == (self.user["gid"] if user else os.getegid())):
                break
            time.sleep(.01)
        if (proc.poll() is not None or not identity or identity["net"] != self.data["namespaces"][role]["inode"]
                or identity["uid"] != (self.user["uid"] if user else 0)
                or identity["gid"] != (self.user["gid"] if user else os.getegid())):
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate(timeout=3)
            raise Failed("Worker failed to enter owned namespace: " + str((cmd, out, err)))
        item = dict(identity, role=role, label=label, argv=cmd, exited=False)
        self.children[proc.pid] = (proc, item)
        self.data["pids"].append(item)
        self.save()
        return proc

    def stop(self, proc):
        item = self.children[proc.pid][1]
        if proc.poll() is None:
            if process_identity(proc.pid) != {k: item[k] for k in IDENTITY_FIELDS}:
                raise Unsafe("PID identity changed before termination")
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            out, err = proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            if process_identity(proc.pid) != {k: item[k] for k in IDENTITY_FIELDS}:
                raise Unsafe("PID identity changed before escalation")
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate(timeout=3)
        item["exited"] = True
        self.save()
        if self.attempt:
            self.write("child-" + str(proc.pid) + "-" + secrets.token_hex(4) + ".json",
                       {"process": item, "exit": proc.returncode, "stdout": out, "stderr": err})
        return out, err

    def finish(self, proc, timeout=45):
        item = self.children[proc.pid][1]
        remaining = timeout if self.deadline is None else min(timeout, self.deadline-time.monotonic())
        if remaining <= 0:
            raise Failed("Verification deadline exceeded")
        try:
            out, err = proc.communicate(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            self.stop(proc)
            raise Failed("Worker timeout: " + item["label"]) from exc
        item["exited"] = True
        self.save()
        self.write("child-" + str(proc.pid) + "-" + secrets.token_hex(4) + ".json",
                   {"process": item, "exit": proc.returncode, "stdout": out, "stderr": err})
        if proc.returncode:
            raise Failed("Worker failed: " + err)
        return out

    def probe_start(self, path, count=100):
        return self.start(["python", "-I", "-B", str(Path(__file__).resolve())] +
                          worker_argv(self.run, "_probe", path, count=count),
                          "robot", label="probe-" + path)

    def probe_finish(self, proc):
        try:
            result = json.loads(self.finish(proc, 20))
        except ValueError as exc:
            raise Failed("Invalid probe JSON") from exc
        return result

    def paired_probes(self):
        # Both clients are live before waiting for either one.
        clients = {p: self.probe_start(p) for p in PATHS}
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = {p: pool.submit(self.probe_finish, c) for p, c in clients.items()}
            return {p: f.result() for p, f in futures.items()}

    def counters(self):
        return {p: {d: {"link": self.read_json(["ip", "-j", "-s", "link", "show", "dev", d], p),
                        "qdisc": self.read_json(["tc", "-j", "-s", "qdisc", "show", "dev", d], p),
                        "class": self.read_json(["tc", "-j", "-s", "class", "show", "dev", d], p)}
                    for d in (v["router_r"], v["router_s"])} for p, v in PATHS.items()}

    def iperf_trial(self, path, profile, reverse):
        p = PATHS[path]
        rate = 20 if profile != "slow-a" or path == "b" else 4
        server = self.start(["iperf3", "-s", "-1", "-B", p["station"], "-p", str(p["iperf"]), "-J"],
                            "station", label="iperf-server")
        client = observer = None
        try:
            # Port/listener readiness, no probe connection consuming the one-shot server.
            ready_by = time.monotonic() + 3
            while time.monotonic() < ready_by:
                if server.poll() is not None:
                    raise Failed("iperf server exited before readiness")
                listen = self.exec(["python", "-I", "-B", "-c",
                                    "import pathlib; print(pathlib.Path('/proc/net/tcp').read_text())"],
                                   "station", user=True)["stdout"]
                if any(line.split()[1].endswith(":" + format(p["iperf"], "04X"))
                       and line.split()[3] == "0A" for line in listen.splitlines()[1:] if len(line.split()) > 3):
                    break
                time.sleep(.05)
            else:
                raise Failed("iperf listener readiness timeout")
            before = self.counters()
            args = ["iperf3", "-c", p["station"], "-B", p["robot"], "-p", str(p["iperf"]),
                    "-u", "-b", str(rate) + "M", "-l", "1200", "-t", "15", "-O", "3", "-J"]
            if reverse:
                args += ["-R"]
            client = self.start(args, "robot", label="iperf-client")
            load_started = time.monotonic()
            observer = self.probe_start("b") if path == "a" else None
            client_raw = self.finish(client)
            load_ended = time.monotonic()
            server_raw = self.finish(server, 10)
            # Use the output of the actual receiving process, not offered sender rate.
            metric = parse_iperf(client_raw if reverse else server_raw)
            observer_result = self.probe_finish(observer) if observer else None
            if observer_result is not None and not probe_ok(observer_result):
                raise Failed("B unavailable under A load")
            if observer_result is not None and not (load_started <= observer_result["start"] < observer_result["end"] <= load_ended):
                raise Failed("B probe did not fit inside the A load observation window")
            self.write("iperf-" + secrets.token_hex(6) + ".json",
                       {"path": path, "direction": "reverse" if reverse else "forward",
                        "profile": profile, "offered_mbps": rate, "metric": metric,
                        "load_window": [load_started, load_ended],
                        "client_raw": client_raw, "server_raw": server_raw,
                        "b_observer": observer_result, "counters_before": before,
                        "counters_after": self.counters()})
            return metric["receiver_bps"]
        finally:
            for handle in (client, observer, server):
                if handle is not None and not self.children[handle.pid][1]["exited"]:
                    self.stop(handle)

    def capture_pair(self, label):
        captures = {}
        try:
            for p in PATHS:
                filename = label + "-on-" + p + "-" + secrets.token_hex(4) + ".pcap"
                dest = self.attempt / filename
                fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
                os.fchown(fd, 0, self.user["gid"])
                os.close(fd)
                # Robot-side router device remains up during station-side outage.
                proc = self.start(["tcpdump", "-U", "-n", "-p", "-Z", "root", "-i", PATHS[p]["router_r"],
                                   "-s", "128", "-c", "2000", "-w", str(dest),
                                   "udp and (port 5301 or port 5302)"], p, user=False, label="capture")
                captures[p] = (proc, dest)
            time.sleep(.2)
            if any(proc.poll() is not None for proc, dest in captures.values()):
                raise Failed("Capture exited before traversal")
        except BaseException:
            for proc, dest in captures.values():
                self.stop(proc)
            raise
        return captures

    def finish_captures(self, captures):
        errors = []
        for proc, dest in captures.values():
            try:
                self.stop(proc)
                if proc.returncode not in (0, -signal.SIGTERM):
                    raise Failed("Capture process failed")
                secure_component(dest, 0, self.user["gid"])
                if dest.stat().st_size < 24:
                    raise Failed("Capture missing valid pcap header")
            except BaseException as exc:
                errors.append(str(exc))
        if errors:
            raise Failed("Capture completion errors: " + str(errors))

    def capture_counts(self, captures, path):
        counts = {}
        for p, (proc, dest) in captures.items():
            parsed = self.exec(["tcpdump", "-n", "-r", str(dest),
                                "udp and host " + PATHS[path]["robot"] + " and host " + PATHS[path]["station"]],
                               user=True)["stdout"]
            counts[p] = len(parsed.splitlines())
        return counts

    def traversal(self, path):
        captures = self.capture_pair("traverse-" + path)
        before = self.counters()
        try:
            result = self.probe_finish(self.probe_start(path))
            if not probe_ok(result):
                raise Failed("Traversal probe failed")
        finally:
            self.finish_captures(captures)
        counts = self.capture_counts(captures, path)
        if counts[path] == 0 or counts["b" if path == "a" else "a"] != 0:
            raise Failed("Missing traversal/cross-path leakage")
        self.write("traversal-" + path + ".json", {"counts": counts, "probe": result,
                    "counters_before": before, "counters_after": self.counters()})

    def isolation(self, path):
        other = "b" if path == "a" else "a"
        healthy = self.paired_probes()
        if not all(probe_ok(v) for v in healthy.values()):
            raise Failed("Pre-outage readiness failed")
        self.link_state(path, "down")
        time.sleep(2)  # exclude in-flight replies before the defined window
        captures = self.capture_pair("outage-" + path)
        try:
            healthy_client = self.probe_start(other)
            down_client = self.probe_start(path, count=10)
            down = self.probe_finish(down_client)
            unaffected = self.probe_finish(healthy_client)
        finally:
            self.finish_captures(captures)
        leak_counts = self.capture_counts(captures, path)
        other_counts = self.capture_counts(captures, other)
        if leak_counts[other] != 0 or other_counts[other] == 0:
            raise Failed("Outage path leakage/missing healthy-path packet evidence")
        if not (unaffected["start"] <= down["start"] < down["end"] <= unaffected["end"]):
            raise Failed("Healthy-path window does not cover failed-path probes")
        if not probe_ok(down, required=0, count=10) or down["received"] != 0 or not probe_ok(unaffected):
            raise Failed("Outage isolation failed")
        self.link_state(path, "up")
        time.sleep(5)
        recovery = self.paired_probes()
        if not all(probe_ok(v) for v in recovery.values()):
            raise Failed("Recovery/other-path continuity failed")
        self.write("isolation-" + path + ".json",
                   {"healthy": healthy, "down": down, "unaffected": unaffected, "recovery": recovery,
                    "failed_path_packets": leak_counts, "healthy_path_packets": other_counts})

    def verify(self, stage):
        if stage not in ("calibration", "isolation", "all"):
            raise Unsafe("Invalid verification stage")
        self.ensure_active()
        if self.data.get("implementation") != implementation_hashes():
            raise Unsafe("Implementation changed since setup; clean the lab before updating/retrying")
        initial_profile = self.data["profile"]
        initial_links = {p: "up" if "UP" in self.assert_link(v["router_s"])["flags"] else "down"
                         for p, v in PATHS.items()}
        attempt_id = str(time.time_ns()) + "-" + secrets.token_hex(4)
        self.attempt = self.directory / ("attempt-" + attempt_id)
        self.attempt.mkdir(mode=0o750)
        os.chown(self.attempt, 0, self.user["gid"])
        os.chmod(self.attempt, 0o750)
        summary = {"schema_version": SCHEMA_VERSION, "run_id": self.run, "attempt_id": attempt_id,
                   "scope": "network lab validation, no library benchmark", "stage": stage,
                   "phase_complete": False, "run_complete": False, "verification_status": "NOT_RUN",
                   "restoration_status": "NOT_RUN", "cleanup_status": "NOT_RUN",
                   "checks": {name: "NOT_RUN" for name in sorted(required_checks("all"))},
                   "criteria": {"healthy_probes": [99, 100], "outage_probes": [0, 10],
                                "delay_delta_ms": [30, 60], "delay_rule": "all three paired trials",
                                "capacity_ratio": [.85, 1.05], "unshaped_min_bps": 18e6,
                                "iperf_duration_seconds": 15, "minimum_receiver_seconds": 14},
                   "metrics": {"delay_deltas_ms": {}, "capacity_bps": {}},
                   "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "started_monotonic": time.monotonic(), "initial_profile": initial_profile,
                   "initial_links": initial_links, "controller_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        summary["implementation"] = self.data["implementation"]
        self.data["attempts"].append(self.attempt.name)
        self.save()
        self.deadline = time.monotonic() + MAX_VERIFY_SECONDS
        servers = []
        network_changed = False
        try:
            print("Checking worker readiness and actual packet traversal...", file=sys.stderr, flush=True)
            self.write("environment.json", {"platform": platform.platform(), "python": sys.version,
                       "executables": self.exe, "cpus": os.cpu_count(),
                       "preflight": preflight(self.run, self)})
            self.topology_check()
            for p in PATHS:
                servers.append(self.start(["python", "-I", "-B", str(Path(__file__).resolve())] +
                                          worker_argv(self.run, "_server", p, duration=960),
                                          "station", label="echo-server-" + p))
            # Process liveness plus a real bounded probe establish readiness.
            readiness = self.paired_probes()
            summary["checks"]["readiness"] = "FAIL"
            if not all(probe_ok(r) for r in readiness.values()):
                raise Failed("Echo readiness failed")
            self.write("readiness.json", readiness)
            summary["checks"]["readiness"] = "PASS"
            summary["checks"]["traversal"] = "FAIL"
            for path in PATHS:
                self.traversal(path)
            summary["checks"]["traversal"] = "PASS"
            if stage in ("calibration", "all"):
                print("Calibrating idle RTT (three paired trials)...", file=sys.stderr, flush=True)
                delay = {profile: {p: [] for p in PATHS} for profile in ("unshaped", "symmetric")}
                summary["checks"]["delay"] = "FAIL"
                network_changed = True
                for profile in delay:
                    self.profile(profile)
                    for trial in range(3):
                        results = self.paired_probes()
                        self.write("delay-" + profile + "-" + str(trial) + ".json", results)
                        for p in PATHS:
                            delay[profile][p].append(results[p])
                if not all(delay_ok(delay["unshaped"][p], delay["symmetric"][p]) for p in PATHS):
                    raise Failed("Delay calibration failed (all three pairs required)")
                summary["metrics"]["delay_deltas_ms"] = {
                    p: [s["median_ms"] - u["median_ms"] for u, s in
                        zip(delay["unshaped"][p], delay["symmetric"][p])] for p in PATHS}
                summary["checks"]["delay"] = "PASS"
                for profile in PROFILES:
                    self.profile(profile)
                    for path in PATHS:
                        for reverse in (False, True):
                            print("Capacity: " + profile + " / " + path + " / " + ("reverse" if reverse else "forward"),
                                  file=sys.stderr, flush=True)
                            n = 1 if profile == "unshaped" else 3
                            name = profile + "-" + path + "-" + ("reverse" if reverse else "forward")
                            summary["checks"][name] = "FAIL"
                            rates = [self.iperf_trial(path, profile, reverse) for _ in range(n)]
                            cap = 2e6 if profile == "slow-a" and path == "a" else 10e6
                            passed = rates[0] >= 18e6 if profile == "unshaped" else capacity_ok(rates, cap)
                            summary["metrics"]["capacity_bps"][name] = rates
                            summary["checks"][name] = "PASS" if passed else "FAIL"
                            if not passed:
                                raise Failed("Capacity calibration failed: " + name)
            if stage in ("isolation", "all"):
                print("Testing independent path outage/recovery...", file=sys.stderr, flush=True)
                network_changed = True
                self.profile("symmetric")
                for path in PATHS:
                    summary["checks"]["isolation-" + path] = "FAIL"
                    self.isolation(path)
                    summary["checks"]["isolation-" + path] = "PASS"
            summary["management_after_verify"] = self.management_safe()
            if any(summary["checks"][name] != "PASS" for name in required_checks(stage)):
                raise Failed("Mandatory checks incomplete")
            summary["verification_status"] = "PASS"
        except BaseException as exc:
            summary["verification_status"] = "FAILED"
            summary["error"] = type(exc).__name__ + ": " + str(exc)[:4000]
        finally:
            print("Restoring initial states and saving attempt evidence...", file=sys.stderr, flush=True)
            # Restoration gets its own bounded command budget after verification timeout.
            self.deadline = None
            errors = []
            try:
                self.reconcile_failed_change()
            except BaseException as exc:
                errors.append("pending mutation: " + str(exc))
            for proc, item in list(self.children.values()):
                if not item["exited"]:
                    try:
                        self.stop(proc)
                    except BaseException as exc:
                        errors.append("child: " + str(exc))
            for p, state in (initial_links.items() if network_changed else []):
                try:
                    self.link_state(p, state)
                except BaseException as exc:
                    errors.append("link " + p + ": " + str(exc))
            try:
                if network_changed:
                    self.profile(initial_profile)
                summary["management_after_restoration"] = self.management_safe()
                if implementation_hashes() != self.data["implementation"]:
                    raise Failed("Implementation changed during verification")
            except BaseException as exc:
                errors.append("profile/management: " + str(exc))
            summary["restoration_status"] = "FAILED" if errors else "PASS"
            summary["restoration_errors"] = errors
            summary["ended_monotonic"] = time.monotonic()
            summary["overall_status"] = ("PASS" if summary["verification_status"] == "PASS" and not errors else "FAILED")
            summary["run_complete"] = stage == "all" and summary["overall_status"] == "PASS"
            # Reserve the final small summary independently of a full evidence quota.
            atomic_json(self.attempt / "summary.json", summary, self.user["gid"])
            self.attempt = None
        return summary

    def cleanup(self):
        if self.data.get("pending"):
            raise Unsafe("Interrupted mutation needs manual ownership review")
        if "management_before" not in self.data and (
                self.data.get("setup_status") not in ("NOT_RUN", "FAILED")
                or self.data["namespaces"] or self.data["links"] or self.data["pids"]):
            raise Unsafe("Missing management baseline for a previously active run")
        if self.data["closed"]:
            existing = self.exec(["ip", "netns", "list"])["stdout"].split()
            if any(n in existing for n in self.ns.values()) or any(self.link("root", n, True) for n in self.temporary_names()):
                raise Unsafe("Resources appeared after closed run")
            return {"cleanup_status": "ALREADY_CLEANED", "phase_complete": False}
        # Validate every live target before stopping/deleting ANY resource.
        live_owned = []
        for role in self.data["namespaces"]:
            self.assert_namespace(role)
        for key in self.data["links"]:
            self.assert_link(key)
        for role in self.data["namespaces"]:
            current = self.exec(["ip", "netns", "pids", self.ns[role]])["stdout"].split()
            for pid_text in current:
                identity = process_identity(int(pid_text))
                owned = [p for p in self.data["pids"] if not p["exited"] and
                         {k: p[k] for k in IDENTITY_FIELDS} == identity]
                if not owned:
                    raise Unsafe("Foreign process in lab namespace")
                live_owned.append(owned[0])
        # No deletion or signalling until all namespace/process ownership passed.
        for item in live_owned:
            expected = {k: item[k] for k in IDENTITY_FIELDS}
            if item["pgid"] != item["pid"] or process_identity(item["pid"]) != expected:
                raise Unsafe("Owned process identity changed")
            os.killpg(item["pgid"], signal.SIGTERM)
            until = time.monotonic() + 3
            while process_identity(item["pid"]) == expected and time.monotonic() < until:
                time.sleep(.05)
            if process_identity(item["pid"]) == expected:
                os.killpg(item["pgid"], signal.SIGKILL)
                until = time.monotonic() + 3
                while process_identity(item["pid"]) == expected and time.monotonic() < until:
                    time.sleep(.05)
            if process_identity(item["pid"]) is not None:
                raise Unsafe("Process remains or PID was reused; manual review required")
            item["exited"] = True
            self.save()
        for role in self.data["namespaces"]:
            if self.exec(["ip", "netns", "pids", self.ns[role]])["stdout"].strip():
                raise Unsafe("Namespace still has processes; refuse deletion")
        for role in reversed(list(self.data["namespaces"])):
            self.assert_namespace(role)
            self.mutation("delete namespace", ["ip", "netns", "del", self.ns[role]])
            del self.data["namespaces"][role]
            # Deleting namespaces destroys both ends of their veth pairs.
            remaining = {}
            for key, saved in self.data["links"].items():
                if saved["role"] != role:
                    actual = self.link(saved["role"], saved["dev"], True) if saved["role"] in self.data["namespaces"] or saved["role"] == "root" else None
                    if actual is not None:
                        remaining[key] = saved
            self.data["links"] = remaining
            self.completed()
        for key, saved in list(self.data["links"].items()):
            if saved["role"] != "root":
                raise Unsafe("Unexplained remaining link")
            self.assert_link(key)
            self.mutation("delete owned temporary link", ["ip", "link", "del", "dev", saved["dev"]])
            del self.data["links"][key]
            self.completed()
        existing = self.exec(["ip", "netns", "list"])["stdout"].split()
        if any(n in existing for n in self.ns.values()) or any(self.link("root", n, True) for n in self.temporary_names()):
            raise Failed("Cleanup left lab resources")
        if "management_before" in self.data:
            self.data["management_after_cleanup"] = self.management_safe()
        elif self.data.get("setup_status") in ("NOT_RUN", "FAILED") and not self.data["pids"]:
            # A failed initial snapshot created a journal, but no network
            # resources. The ownership checks above still run before closure.
            self.data["management_after_cleanup"] = None
            self.data["cleanup_scope"] = "no management baseline; no lab resources created"
        else:
            raise Unsafe("Missing management baseline for a previously active run")
        self.data["closed"] = True
        self.data["cleanup_status"] = "PASS"
        self.save()
        return {"cleanup_status": "PASS", "phase_complete": False}


def preflight(run, lab=None):
    validate_run_id(run)
    if sys.platform != "linux":
        raise Missing("Preflight needs Linux; pure plan works on Windows")
    exe = tools()
    records = {}
    commands = {"ip": [exe["ip"], "-V"], "tc": [exe["tc"], "-V"],
                "iperf": [exe["iperf3"], "--version"], "iperf_help": [exe["iperf3"], "--help"],
                "tcpdump": [exe["tcpdump"], "--version"], "memory": ["/usr/bin/free", "-b"],
                "disk": ["/usr/bin/df", "-B1", "."], "git_head": ["/usr/bin/git", "rev-parse", "HEAD"],
                "git_branch": ["/usr/bin/git", "branch", "--show-current"],
                "git_dirty": ["/usr/bin/git", "status", "--porcelain"]}
    for label, cmd in commands.items():
        if lab:
            result = lab.exec(cmd, user=label.startswith("git"))
            records[label] = result
        else:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=True)
            records[label] = {"argv": cmd, "stdout": result.stdout, "stderr": result.stderr, "exit": result.returncode}
    return {"operation_status": "PASS", "verification_status": "NOT_RUN", "phase_complete": False,
            "platform": platform.platform(), "python": sys.version, "executables": exe, "records": records}


def no_symlink_path(path):
    path = Path(os.path.abspath(path))
    for component in (path,) + tuple(path.parents):
        if component.is_symlink():
            raise Unsafe("Symlink path refused: " + str(component))
    return path


def export_report(run, output):
    validate_run_id(run)
    if sys.platform != "linux" or os.geteuid() == 0:
        raise Missing("Export must run as the normal Linux user")
    source = Path(JOURNAL_PARENT_DIR) / run
    no_symlink_path(source)
    secure_component(source.parent, 0, directory=True)
    secure_component(source, 0, directory=True)
    secure_component(source / "journal.json", 0)
    journal = json.loads((source / "journal.json").read_text())
    if journal.get("schema_version") != SCHEMA_VERSION or journal.get("run_id") != run or journal["user"]["uid"] != os.geteuid():
        raise Unsafe("Unverified export owner/journal")
    destination = no_symlink_path(output)
    if destination.exists():
        raise Unsafe("Export destination must be new")
    files = []
    for item in source.rglob("*"):
        if item.is_symlink():
            raise Unsafe("Source symlink refused")
        secure_component(item, 0, journal["user"]["gid"], directory=item.is_dir())
        if item.is_file():
            files.append(item)
    summaries = []
    for attempt in journal["attempts"]:
        if Path(attempt).name != attempt:
            raise Unsafe("Invalid attempt reference")
        summary_file = source / attempt / "summary.json"
        if not summary_file.is_file():
            raise Failed("Missing attempt summary")
        summaries.append(json.loads(summary_file.read_text()))
    destination.mkdir(parents=True)
    manifest = []
    for item in files:
        relative = item.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as handle:
            raw = item.read_bytes()
            handle.write(raw)
        manifest.append({"path": str(relative), "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    valid = bool(summaries) and all(
        s.get("overall_status") == "PASS" and s.get("stage") in ("calibration", "isolation", "all")
        and all(s.get("checks", {}).get(k) == "PASS" for k in required_checks(s["stage"]))
        for s in summaries)
    cleanup = journal.get("closed") is True and journal.get("cleanup_status") == "PASS"
    status = "PASS" if valid and cleanup else ("NOT_RUN" if not summaries else "FAILED")
    report = {"schema_version": SCHEMA_VERSION, "run_id": run, "scope": "network lab validation, no library benchmark",
              "overall_status": status, "phase_complete": False, "cleanup_status": journal.get("cleanup_status"),
              "full_run_passed": cleanup and any(s.get("run_complete") for s in summaries),
              "attempts": summaries, "files": manifest,
              "note": "Two fresh full runs, regression and reviewer approval are external acceptance gates."}
    (destination / "acceptance_report.json").write_text(json.dumps(report, indent=2), encoding="utf8")
    (destination / "acceptance_report.md").write_text(
        "# Network lab report\n\nRun: " + run + "\nStatus: " + status +
        "\nPhase complete: false (external review gates remain)\n", encoding="utf8")
    return report


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--run-id", required=True)
    sub = root.add_subparsers(dest="command", required=True)
    for command in ("plan", "preflight", "setup", "status", "cleanup"):
        sub.add_parser(command)
    profile = sub.add_parser("profile")
    profile.add_argument("--name", required=True, choices=PROFILES)
    link = sub.add_parser("link")
    link.add_argument("--path", required=True, choices=PATHS)
    link.add_argument("--state", required=True, choices=("up", "down"))
    verify = sub.add_parser("verify")
    verify.add_argument("--stage", choices=("calibration", "isolation", "all"), default="all")
    export = sub.add_parser("export-report")
    export.add_argument("--output-dir", required=True)
    for kind in ("_server", "_probe"):
        worker = sub.add_parser(kind)
        worker.add_argument("--path", required=True, choices=PATHS)
        worker.add_argument("--count", type=int, default=100)
        worker.add_argument("--duration", type=float, default=960)
    return root


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        validate_run_id(args.run_id)
        if args.command == "plan":
            result = pure_plan(args.run_id)
        elif args.command == "preflight":
            result = preflight(args.run_id)
        elif args.command == "export-report":
            result = export_report(args.run_id, args.output_dir)
        elif args.command == "_server":
            if sys.platform != "linux" or os.geteuid() == 0:
                raise Missing("Worker must be a normal user in Linux")
            echo_server(args.run_id, args.path, args.duration)
            return 0
        elif args.command == "_probe":
            if sys.platform != "linux" or os.geteuid() == 0:
                raise Missing("Worker must be a normal user in Linux")
            result = echo_probe(args.run_id, args.path, args.count)
        else:
            lab = Lab(args.run_id, create=args.command == "setup")
            if args.command == "setup":
                result = lab.setup()
            elif args.command == "status":
                result = lab.status()
            elif args.command == "profile":
                lab.profile(args.name)
                result = {"operation_status": "PASS", "phase_complete": False}
            elif args.command == "link":
                lab.link_state(args.path, args.state)
                result = {"operation_status": "PASS", "phase_complete": False}
            elif args.command == "verify":
                result = lab.verify(args.stage)
            else:
                result = lab.cleanup()
        print(json.dumps(result, indent=2, allow_nan=False))
        return 1 if result.get("overall_status") in ("FAILED", "NOT_RUN", "INCOMPLETE") else 0
    except SystemExit as exc:
        return int(exc.code)
    except Unsafe as exc:
        print("UNSAFE: " + str(exc), file=sys.stderr)
        return 2
    except Missing as exc:
        print("PREREQUISITE: " + str(exc), file=sys.stderr)
        return 3
    except (Exception, KeyboardInterrupt) as exc:
        print("FAILED: " + type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
