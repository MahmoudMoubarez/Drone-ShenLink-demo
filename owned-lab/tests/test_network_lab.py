"""Portable negative tests and mocked orchestration; never mutate host networking."""
import contextlib
import ast
import copy
import importlib.util
import io
import itertools
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import types
from unittest import mock

import pytest
from tools import network_lab as n

RUN = "6f03a1c9"


def test_empty_tc_class_dump_is_an_empty_list():
    lab = n.Lab.__new__(n.Lab)
    lab.exec = mock.Mock(return_value={"stdout": "\n"})
    assert lab.read_json(["tc", "-j", "class", "show", "dev", "ar"], "a") == []
    lab.exec.return_value = {"stdout": "not JSON"}
    with pytest.raises(n.Failed, match="Invalid inspection JSON"):
        lab.read_json(["tc", "-j", "class", "show", "dev", "ar"], "a")
    lab.exec.return_value = {"stdout": ""}
    with pytest.raises(n.Failed, match="Invalid inspection JSON"):
        lab.read_json(["ip", "-j", "link", "show"])


def test_iproute2_61_htb_text_is_retained_not_discarded():
    lab = n.Lab.__new__(n.Lab)
    line = "class htb 1:10 root prio 0 rate 30Mbit ceil 30Mbit burst 32Kb cburst 32Kb"
    lab.exec = mock.Mock(return_value={"stdout": line + " \n"})
    assert lab.read_json(["tc", "-j", "class", "show", "dev", "ar"], "a") == [
        {"kind": "htb", "legacy_static_text": line}]
    with pytest.raises(n.Failed, match="Invalid inspection JSON"):
        lab.read_json(["tc", "-j", "qdisc", "show", "dev", "ar"], "a")
    for bad in (line + " unknown", "class htb garbage", "[]\n" + line, line + "\nERROR"):
        lab.exec.return_value = {"stdout": bad}
        with pytest.raises(n.Failed, match="Invalid inspection JSON"):
            lab.read_json(["tc", "-j", "class", "show", "dev", "ar"], "a")


def test_iproute2_61_htb_with_attached_netem_leaf():
    lab = n.Lab.__new__(n.Lab)
    line = "class htb 1:10 root leaf 10: prio 0 rate 30Mbit ceil 30Mbit burst 32Kb cburst 32Kb"
    lab.exec = mock.Mock(return_value={"stdout": line + " \n"})
    assert lab.read_json(["tc", "-j", "class", "show", "dev", "ar"], "a") == [
        {"kind": "htb", "legacy_static_text": line}]
    lab.exec.return_value = {"stdout": line.replace("leaf 10:", "leaf garbage")}
    with pytest.raises(n.Failed):
        lab.read_json(["tc", "-j", "class", "show", "dev", "ar"], "a")


def metric(rate=10e6, seconds=15, sender=False):
    return json.dumps({"start": {"test_start": {"protocol": "UDP"}},
                       "end": {"sum_received": {"sender": sender, "bits_per_second": rate,
                                                "seconds": seconds, "bytes": 100000}}})


def probe(median=1, received=100):
    return {"count": 100, "received": received, "median_ms": median,
            "mismatches": 0, "duplicates": 0}


def bare():
    lab = n.Lab.__new__(n.Lab)
    lab.run = RUN
    lab.ns = n.namespaces(RUN)
    lab.user = {"uid": 1000, "gid": 1000, "name": "tester"}
    lab.exe = {key: sys.executable for key in ("ip", "tc", "setpriv", "python", "iperf3", "tcpdump")}
    lab.data = {"schema_version": 2, "run_id": RUN, "closed": False,
                "setup_status": "PASS", "profile": "unshaped", "pending": None,
                "namespaces": {"a": {"name": lab.ns["a"], "inode": 88}},
                "token": "test-token",
                "links": {"as": {"role": "a", "dev": "as", "ifindex": 7, "alias": "mfl:" + RUN + ":as:test-token"}},
                "pids": [], "tc": {}, "commands": [], "attempts": [],
                "implementation": n.implementation_hashes()}
    lab.children = {}
    lab.deadline = None
    lab.attempt = None
    lab.lock = threading.RLock()
    lab.save = mock.Mock()
    return lab


@pytest.mark.parametrize("inspection", ["clear", "collision", "failure"])
def test_real_constructor_bootstrap_inspects_before_journal(tmp_path, inspection):
    """Exercise real __init__/link/read_json/exec without Linux or root effects."""
    parent = tmp_path / "lab"
    calls = []
    real_stat = os.stat

    def stat(path, *args, **kwargs):
        if str(path) == "/proc/self/ns/net":
            return types.SimpleNamespace(st_ino=88)
        return real_stat(path, *args, **kwargs)

    def popen(argv, **kwargs):
        calls.append(argv)
        assert argv[1:] in (["netns", "list"], ["-j", "link", "show"])
        links = [{"ifname": "v_ra_" + RUN}] if inspection == "collision" else []
        stdout = "" if argv[1:] == ["netns", "list"] else json.dumps(links)
        child = mock.Mock(pid=123, returncode=1 if inspection == "failure" else 0)
        child.communicate.return_value = (stdout, "inspection failed" if child.returncode else "")
        return child

    with mock.patch.object(n, "JOURNAL_PARENT_DIR", str(parent)), \
         mock.patch.object(n, "invoking_user", return_value={"uid": 1000, "gid": 1000, "name": "tester"}), \
         mock.patch.object(n, "tools", return_value={"ip": sys.executable}), \
         mock.patch.object(n, "secure_parents"), mock.patch.object(n, "secure_component"), \
         mock.patch.object(n.os, "chown", create=True), mock.patch.object(n.os, "chmod"), \
         mock.patch.object(n.os, "stat", side_effect=stat), \
         mock.patch.object(n, "atomic_json") as save, \
         mock.patch.object(n, "process_identity", return_value=None), \
         mock.patch.object(n.subprocess, "Popen", side_effect=popen):
        if inspection == "clear":
            lab = n.Lab(RUN, create=True)
            assert lab.data["commands"] == []
            assert lab.data["controller_net"] == 88
            assert len(calls) == 1 + len(lab.temporary_names())
            save.assert_called_once()
            # Subsequent inspections still retain the normal journal behavior.
            lab.link("root", "missing", absent_ok=True)
            assert len(lab.data["commands"]) == 1
            assert save.call_count == 2
        else:
            error = n.Unsafe if inspection == "collision" else n.Failed
            with pytest.raises(error):
                n.Lab(RUN, create=True)
            assert not (parent / RUN).exists()
            save.assert_not_called()


@pytest.mark.parametrize("value", ["", "123", "6F03a1c9", "../12345", "abcdefgh", "123456789",
                                   "1234;678", "1234 678", "1234/678", None])
def test_bad_run_id(value):
    with pytest.raises(n.Unsafe):
        n.validate_run_id(value)


def test_real_import_has_no_process_socket_or_directory_effect():
    spec = importlib.util.spec_from_file_location("fresh_lab_review", n.__file__)
    module = importlib.util.module_from_spec(spec)
    with mock.patch("subprocess.Popen", side_effect=AssertionError("spawn")), \
         mock.patch("socket.socket", side_effect=AssertionError("socket")), \
         mock.patch("pathlib.Path.mkdir", side_effect=AssertionError("mkdir")):
        spec.loader.exec_module(module)
    assert module.validate_run_id(RUN) == RUN


def test_real_plan_portable_no_external_actions():
    with mock.patch("subprocess.Popen", side_effect=AssertionError("spawn")), \
         mock.patch("pathlib.Path.mkdir", side_effect=AssertionError("mkdir")), \
         mock.patch("socket.socket", side_effect=AssertionError("socket")):
        plan = n.pure_plan(RUN)
    assert not plan["phase_complete"] and plan["verification_status"] == "NOT_RUN"
    assert set(plan["commands"]["profiles"]) == set(n.PROFILES)
    assert set(plan["commands"]["links"]) == {"a", "b"}
    for command in plan["commands"]["setup"]:
        assert "enp0s3" not in command
        if "sysctl" in command or ("route" in command and "add" in command):
            assert command[:3] == ["ip", "netns", "exec"]
        if "type" in command:
            assert len(command[3]) <= 15 and len(command[-1]) <= 15


def test_all_link_mutation_builders_use_explicit_dev():
    # iproute2 parses bare "br" as an abbreviation of "broadcast", not a device.
    tree = ast.parse(Path(n.__file__).read_text())
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.List):
            continue
        values = [e.value if isinstance(e, ast.Constant) else None for e in node.elts]
        for i in range(len(values) - 3):
            if values[i:i+3] in (["ip", "link", "set"], ["ip", "link", "del"]):
                assert values[i+3] == "dev", (node.lineno, values)
                checked += 1
    assert checked >= 10


def test_real_setup_emits_unambiguous_br_mtu_command():
    lab = bare()
    lab.snapshot = mock.Mock(return_value={})
    lab.management_safe = mock.Mock(return_value={})
    lab.mutation = mock.Mock()
    lab.assert_link = mock.Mock()
    lab.tc_state = mock.Mock(return_value={})
    lab.exec = mock.Mock(return_value={"stdout": "", "exit": 0})

    def link(role, dev, absent_ok=False):
        if role == "root" and absent_ok:
            return None
        return {"ifindex": 7, "ifalias": "mfl:" + RUN + ":" + dev + ":test-token"}

    lab.link = link
    with mock.patch.object(n.os, "stat", return_value=types.SimpleNamespace(st_ino=88)):
        assert lab.setup()["setup_status"] == "PASS"
    assert mock.call(["ip", "link", "set", "dev", "br", "mtu", "1500"], "b") in lab.exec.call_args_list
    assert mock.call(["ip", "link", "set", "dev", "br", "up"], "b") in lab.exec.call_args_list


@pytest.mark.parametrize("changed_field", [None, "local", "prefixlen"])
def test_management_addresses_survive_real_snapshot_json_roundtrip(changed_field):
    lab = bare()
    address = {"family": "inet", "local": "10.0.2.15", "prefixlen": 24}

    def read_json(argv, ns=None, **kwargs):
        if argv == ["ip", "-j", "link", "show"]:
            return [{"ifname": "enp0s3", "ifindex": 2, "address": "00:00:00:00:00:01",
                     "flags": ["UP"], "mtu": 1500}]
        if "addr" in argv:
            return [{"addr_info": [address.copy()]}]
        if "route" in argv:
            return [{"dst": "default", "gateway": "10.0.2.2", "dev": "enp0s3"}]
        return [{"kind": "fq_codel", "root": True}]

    lab.read_json = read_json
    lab.exec = mock.Mock(side_effect=lambda argv, **kwargs:
                         {"stdout": "0" if argv[0] == "sysctl" else ""})
    with mock.patch.object(n.os, "stat", return_value=types.SimpleNamespace(st_ino=88)):
        lab.data["management_before"] = json.loads(json.dumps(lab.snapshot()))
        if changed_field is not None:
            address[changed_field] = "10.0.2.16" if changed_field == "local" else 25
            with pytest.raises(n.Failed, match="Management changed: addresses"):
                lab.management_safe()
        else:
            assert lab.management_safe()["addresses"] == [["10.0.2.15", 24]]


@pytest.mark.parametrize("path", ["a", "b"])
@pytest.mark.parametrize("kind", ["_server", "_probe"])
def test_emitted_worker_arguments_parse(kind, path):
    args = n.parser().parse_args(n.worker_argv(RUN, kind, path, count=100))
    assert args.run_id == RUN and args.command == kind and args.path == path


def test_old_worker_argument_order_is_rejected():
    with contextlib.redirect_stderr(io.StringIO()), pytest.raises(SystemExit):
        n.parser().parse_args(["_server", "--path", "a", "--run-id", RUN])


@pytest.mark.parametrize("profile", n.PROFILES)
@pytest.mark.parametrize("path", ["a", "b"])
def test_shaping_scoped_and_fixed(profile, path):
    ns = n.namespaces(RUN)[path]
    for dev in (n.PATHS[path]["router_r"], n.PATHS[path]["router_s"]):
        commands = n.shaping_commands(ns, dev, profile, path)
        assert all(c[:4] == ["ip", "netns", "exec", ns] for c in commands)
        assert all(c[c.index("dev")+1] == dev for c in commands)


def test_invalid_shaping_device_refuses():
    with pytest.raises(n.Unsafe):
        n.shaping_commands("mfl-a-" + RUN, "enp0s3", "symmetric", "a")


def test_platform_refuses_before_mutation():
    with mock.patch.object(n.sys, "platform", "win32"), \
         mock.patch.object(n.subprocess, "Popen", side_effect=AssertionError("spawn")):
        assert n.main(["--run-id", RUN, "setup"]) == 3


def test_nonroot_refuses_before_unix_import():
    with mock.patch.object(n.sys, "platform", "linux"), \
         mock.patch.object(n.os, "geteuid", return_value=1000, create=True):
        with pytest.raises(n.Missing):
            n.invoking_user()


def test_namespace_replacement_refuses():
    lab = bare()
    with mock.patch.object(n.os, "stat", return_value=types.SimpleNamespace(st_ino=99)):
        with pytest.raises(n.Unsafe):
            lab.assert_namespace("a")


def test_unknown_namespace_refuses_before_exec():
    lab = bare()
    with pytest.raises(n.Unsafe):
        lab.command(["ip", "link", "show"], "station")


@pytest.mark.parametrize("value", [
    {"ifindex": 7, "ifalias": "foreign"}, {"ifindex": 8, "ifalias": "mfl:" + RUN + ":as:test-token"}])
def test_link_identity_replacement_refuses(value):
    lab = bare()
    lab.link = mock.Mock(return_value=value)
    with pytest.raises(n.Unsafe):
        lab.assert_link("as")


def test_closed_run_refuses_before_inspection_or_mutation():
    lab = bare()
    lab.data["closed"] = True
    lab.exec = mock.Mock(side_effect=AssertionError("execution"))
    with pytest.raises(n.Unsafe):
        lab.profile("symmetric")
    lab.exec.assert_not_called()


def test_wrong_tc_refuses_before_change():
    lab = bare()
    lab.tc_state = mock.Mock(return_value={"qdisc": [{"kind": "foreign"}], "class": []})
    with pytest.raises(n.Unsafe):
        lab.assert_tc("a", "as")


def test_failed_qdisc_delete_propagates_not_pass():
    lab = bare()
    lab.ensure_active = mock.Mock()
    lab.assert_link = mock.Mock()
    lab.assert_tc = mock.Mock(return_value={"qdisc": [{"root": True, "kind": "htb"}]})
    lab.mutation = mock.Mock(side_effect=n.Failed("denied"))
    with pytest.raises(n.Failed):
        lab.profile("unshaped")


def test_pending_cleanup_never_deletes():
    lab = bare()
    lab.data["pending"] = {"label": "unknown creation"}
    lab.exec = mock.Mock(side_effect=AssertionError("execution"))
    with pytest.raises(n.Unsafe):
        lab.cleanup()
    lab.exec.assert_not_called()


def test_cleanup_checks_replaced_link_before_any_deletion():
    lab = bare()
    lab.assert_namespace = mock.Mock()
    lab.link = mock.Mock(return_value={"ifindex": 9, "ifalias": "foreign"})
    lab.exec = mock.Mock(side_effect=AssertionError("execution"))
    with pytest.raises(n.Unsafe):
        lab.cleanup()
    lab.exec.assert_not_called()


def test_cleanup_refuses_foreign_process():
    lab = bare()
    lab.assert_namespace = mock.Mock()
    lab.assert_link = mock.Mock()
    lab.exec = mock.Mock(return_value={"stdout": "123"})
    with mock.patch.object(n, "process_identity", return_value={"pid": 123}):
        with pytest.raises(n.Unsafe):
            lab.cleanup()
    assert not any("del" in call.args[0] for call in lab.exec.call_args_list)


def test_pid_reuse_is_not_signalled():
    lab = bare()
    proc = mock.Mock(pid=123)
    proc.poll.return_value = None
    lab.children[123] = (proc, {"pid": 123, "start": "old", "net": 88, "pgid": 123, "uid": 1000, "gid": 1000})
    with mock.patch.object(n, "process_identity", return_value={"pid": 123, "start": "new", "net": 88, "pgid": 123, "uid": 1000, "gid": 1000}), \
         mock.patch.object(n.os, "killpg", create=True) as kill:
        with pytest.raises(n.Unsafe):
            lab.stop(proc)
        kill.assert_not_called()


@pytest.mark.parametrize("change,allowed", [
    ({},True),({"start":"reused"},False),({"net":999},False),
    ({"uid":2000},False),({"gid":2000},False),({"pgid":222},False),
    ({"net":None},False)])
def test_exec_timeout_accepts_only_planned_namespace_user_transition(change,allowed):
    lab=bare()
    lab.command=mock.Mock(return_value=["/usr/bin/ip","netns","exec",lab.ns["a"],"/usr/bin/python3"])
    lab.assert_namespace=mock.Mock()
    initial={"pid":123,"start":"birth","pgid":123,"net":7,"uid":0,"gid":0}
    current={**initial,"net":88,"uid":1000,"gid":1000,**change}
    proc=mock.Mock(pid=123,returncode=-9)
    proc.communicate.side_effect=[subprocess.TimeoutExpired("query",.1,output=b"partial"),("partial","")]
    with mock.patch.object(n.subprocess,"Popen",return_value=proc), \
         mock.patch.object(n,"process_identity",side_effect=[initial,current]), \
         mock.patch.object(n.os,"killpg",create=True) as kill:
        with pytest.raises(n.Failed if allowed else n.Unsafe):
            lab.exec(["python"],"a",user=True,timeout=.1)
        if allowed:
            kill.assert_called_once_with(123,signal.SIGKILL)
            lab.assert_namespace.assert_called_once_with("a")
        else:
            kill.assert_not_called()
        recorded=lab.data["commands"][-1]
        assert recorded["timeout"] is True
        assert recorded["identity_initial"]==initial
        assert recorded["identity_at_timeout"]==current
        assert recorded["argv"]==lab.command.return_value


@pytest.mark.skipif(sys.platform!="linux",reason="Linux process identity")
def test_real_exec_timeout_records_and_reaps_owned_process():
    lab=bare()
    lab.command=lambda argv,ns,user:[sys.executable,"-I","-B","-c","import time; time.sleep(3)"]
    with pytest.raises(n.Failed,match="Command failed"):
        lab.exec(["python"],timeout=.1)
    command=lab.data["commands"][-1]
    assert command["timeout"] is True
    assert command["safety_error"] is None
    assert command["exit"]==-signal.SIGKILL


def test_tc_options_change_not_ignored_but_counters_are():
    first = {"kind": "netem", "options": {"delay": 20000}, "bytes": 10}
    second = copy.deepcopy(first)
    second["bytes"] = 20
    assert n.stable(first) == n.stable(second)
    second["options"]["delay"] = 40000
    assert n.stable(first) != n.stable(second)


def test_snapshot_failure_is_not_empty_preserved():
    lab = bare()
    lab.read_json = mock.Mock(side_effect=n.Failed("inspection error"))
    with pytest.raises(n.Failed):
        lab.snapshot()


def test_absent_management_device_is_failure():
    lab = bare()
    lab.read_json = mock.Mock(return_value=[])
    with pytest.raises(n.Failed):
        lab.snapshot()


@pytest.mark.parametrize("device", ["enp0s3", "wlan0", "eth0"])
def test_management_device_is_selected_from_best_default_route(device):
    links = [{"ifname": device, "flags": ["UP"]},
             {"ifname": "tailscale0", "flags": ["UP"]}]
    routes = [{"dst": "default", "dev": device, "metric": 100},
              {"dst": "100.64.0.0/10", "dev": "tailscale0"}]
    assert n.select_management_device(links, routes) == device


def test_management_default_route_selection_rejects_ambiguity_and_down_devices():
    links = [{"ifname": "wlan0", "flags": ["UP"]},
             {"ifname": "eth0", "flags": ["UP"]}]
    routes = [{"dst": "default", "dev": "wlan0", "metric": 100},
              {"dst": "default", "dev": "eth0", "metric": 100}]
    with pytest.raises(n.Failed, match="Ambiguous"):
        n.select_management_device(links, routes)
    routes[1]["metric"] = 200
    assert n.select_management_device(links, routes) == "wlan0"
    with pytest.raises(n.Failed, match="no usable"):
        n.select_management_device([{"ifname": "wlan0", "flags": []}], routes)


def test_snapshot_pins_management_device_across_later_route_changes():
    lab = bare()
    links = [{"ifname": device, "flags": ["UP"]} for device in ("wlan0", "eth0")]
    routes = [{"dst": "default", "dev": "wlan0"}]
    address_devices = []
    def read_json(argv, *args, **kwargs):
        if "link" in argv:
            return links
        if "route" in argv:
            return routes
        if "addr" in argv:
            address_devices.append(argv[-1])
            return [{"addr_info": [{"family": "inet", "local": "192.168.1.44", "prefixlen": 24}]}]
        return []
    lab.read_json = read_json
    lab.exec = mock.Mock(return_value={"stdout": ""})
    with mock.patch.object(n.os, "stat", return_value=types.SimpleNamespace(st_ino=88)):
        lab.data["management_before"] = lab.snapshot()
        routes[0] = {"dst": "default", "dev": "eth0"}
        with pytest.raises(n.Failed, match="Management changed: routes"):
            lab.management_safe()
    assert address_devices == ["wlan0", "wlan0"]


def test_cleanup_closes_initial_snapshot_failure_without_network_mutation():
    lab = bare()
    lab.data.update(setup_status="NOT_RUN", namespaces={}, links={}, pids=[])
    lab.exec = mock.Mock(return_value={"stdout": ""})
    lab.link = mock.Mock(return_value=None)
    lab.mutation = mock.Mock()
    assert lab.cleanup()["cleanup_status"] == "PASS"
    assert lab.data["closed"] is True
    assert lab.data["management_after_cleanup"] is None
    lab.mutation.assert_not_called()


def test_cleanup_missing_baseline_with_owned_resources_is_refused_before_mutation():
    lab = bare()
    lab.mutation = mock.Mock()
    lab.assert_namespace = mock.Mock()
    with pytest.raises(n.Unsafe, match="Missing management baseline"):
        lab.cleanup()
    lab.assert_namespace.assert_not_called()
    lab.mutation.assert_not_called()


@pytest.mark.parametrize("cap", [2e6, 10e6])
@pytest.mark.parametrize("factor,expected", [(0.849999, False), (.85, True), (1, True), (1.05, True), (1.050001, False)])
def test_real_capacity_function_boundaries(cap, factor, expected):
    assert n.capacity_ok([factor*cap]*3, cap) is expected


@pytest.mark.parametrize("rates", [[], [10e6], [math.nan]*3, [math.inf]*3, [-1]*3, [True]*3])
def test_missing_or_invalid_capacity_samples(rates):
    assert not n.capacity_ok(rates, 10e6)


@pytest.mark.parametrize("delta,expected", [(29.9, False), (30, True), (40, True), (60, True), (60.1, False)])
def test_real_delay_function_boundaries(delta, expected):
    assert n.delay_ok([probe(1)]*3, [probe(1+delta)]*3) is expected


def test_delay_does_not_select_only_middle_successful_trial():
    assert not n.delay_ok([probe(1)]*3, [probe(100), probe(41), probe(100)])


def test_delay_missing_or_loss_trial_refuses():
    assert not n.delay_ok([], [])
    assert not n.delay_ok([probe(1)]*3, [probe(41, 98)]*3)


@pytest.mark.parametrize("rate", [-1, math.nan, math.inf, True])
def test_iperf_invalid_rate_rejected(rate):
    with pytest.raises(n.Failed):
        n.parse_iperf(metric(rate))


@pytest.mark.parametrize("seconds", [0, 1, 13.9, math.nan, -1])
def test_iperf_short_invalid_duration(seconds):
    with pytest.raises(n.Failed):
        n.parse_iperf(metric(seconds=seconds))


def test_iperf_receiver_fixture_and_sender_refusal():
    assert n.parse_iperf(metric())["receiver_bps"] == 10e6
    with pytest.raises(n.Failed):
        n.parse_iperf(metric(sender=True))
    with pytest.raises(n.Failed):
        n.parse_iperf(json.dumps({"start": {"test_start": {"protocol": "UDP"}},
                                 "end": {"sum": {"sender": True, "bits_per_second": 20e6}}}))


@pytest.mark.parametrize("raw", ["", "{", "{}", '{"error":"failed"}', '{"end":{}}'])
def test_bad_iperf_json(raw):
    with pytest.raises(n.Failed):
        n.parse_iperf(raw)


def sample_reply():
    token = RUN.encode() + bytes(8)
    packet = n.PACKET.pack(token, 7, 10, bytes(32))
    return token, packet, {7: {"packet": packet, "time": 10}}


@pytest.mark.parametrize("peer", [("other", 5301), ("ip", 9999)])
def test_probe_rejects_wrong_peer(peer):
    token, packet, sent = sample_reply()
    assert n.reply_verdict(packet, peer, ("ip", 5301), token, sent, 10.04, {})[0] == "mismatch"


def test_probe_deadline_duplicates_timestamp_and_sequence():
    token, packet, sent = sample_reply()
    assert n.reply_verdict(packet, ("ip", 5301), ("ip", 5301), token, sent, 10.04, {}) == ("valid", 7)
    assert n.reply_verdict(packet, ("ip", 5301), ("ip", 5301), token, sent, 12.01, {}) == ("late", 7)
    assert n.reply_verdict(packet, ("ip", 5301), ("ip", 5301), token, sent, 11, {7: 10.1}) == ("duplicate", 7)
    tampered = n.PACKET.pack(token, 7, 9, bytes(32))
    assert n.reply_verdict(tampered, ("ip", 5301), ("ip", 5301), token, sent, 10.04, {})[0] == "mismatch"
    assert n.reply_verdict(packet, ("ip", 5301), ("ip", 5301), token, {}, 10.04, {})[0] == "mismatch"


def test_worker_is_nonroot_and_namespace_bound():
    lab = bare()
    lab.data["namespaces"]["robot"] = {"name": lab.ns["robot"], "inode": 90}
    lab.assert_namespace = mock.Mock()
    argv = lab.command(["python", "-I", "-B", "script.py"] + n.worker_argv(RUN, "_probe", "a"),
                       "robot", user=True)
    assert argv[1:4] == ["netns", "exec", lab.ns["robot"]]
    assert "--reuid" in argv and argv[argv.index("--reuid")+1] == "1000"
    assert argv.index("--run-id") < argv.index("_probe")


def test_export_failure_cli_exit_and_collision_classification():
    with mock.patch.object(n, "export_report", return_value={"overall_status": "FAILED"}):
        assert n.main(["--run-id", RUN, "export-report", "--output-dir", "unused"]) == 1
    with mock.patch.object(n, "export_report", side_effect=n.Unsafe("collision")):
        assert n.main(["--run-id", RUN, "export-report", "--output-dir", "unused"]) == 2


class Simulation(n.Lab):
    """Fake transport/kernel, real verification/evaluation/restoration control flow."""
    def __init__(self, tmp_path):
        base = bare()
        self.__dict__.update(base.__dict__)
        self.directory = tmp_path
        self.profile_history = []
        self.links = {"a": "up", "b": "up"}
        self.calls = []
        self.artifacts = {}
        self.bad_capacity = False
        self.bad_echo = False
        self.data["namespaces"] = {p: {"inode": 88, "name": name} for p, name in self.ns.items()}

    def ensure_active(self):
        pass

    def topology_check(self):
        pass

    def assert_link(self, dev):
        p = "a" if dev == "as" else "b"
        return {"flags": ["UP"] if self.links[p] == "up" else []}

    def profile(self, profile):
        self.profile_history.append(profile)
        self.data["profile"] = profile

    def link_state(self, path, state):
        self.calls.append(("link", path, state))
        self.links[path] = state

    def management_safe(self):
        return {"preserved": True}

    def write(self, name, content):
        if name in self.artifacts:
            raise AssertionError("overwritten")
        self.artifacts[name] = copy.deepcopy(content)

    def start(self, argv, role, user=True, label="worker"):
        self.calls.append(("start", role, argv))
        proc = mock.Mock(pid=len(self.calls))
        proc.poll.return_value = None
        item = {"exited": False}
        self.children[proc.pid] = (proc, item)
        return proc

    def stop(self, proc):
        self.children[proc.pid][1]["exited"] = True
        self.calls.append(("stop", proc.pid))

    def paired_probes(self):
        if self.bad_echo:
            raise n.Failed("echo failure")
        return {p: probe(1 if self.data["profile"] == "unshaped" else 41) for p in n.PATHS}

    def traversal(self, path):
        self.calls.append(("traversal", path))

    def iperf_trial(self, path, profile, reverse):
        self.calls.append(("capacity", path, profile, reverse))
        if self.bad_capacity:
            raise n.Failed("measurement error")
        return 20e6 if profile == "unshaped" else (2e6 if profile == "slow-a" and path == "a" else 10e6)

    def isolation(self, path):
        self.calls.append(("isolation", path))
        self.links[path] = "up"


def simulate_verify(lab, stage="all"):
    with mock.patch.object(n, "preflight", return_value={"mocked": True}), \
         mock.patch.object(n.os, "chown", create=True), \
         mock.patch.object(n, "atomic_json") as save:
        result = lab.verify(stage)
    return result, save


def test_full_orchestration_covers_both_directions_paths_profiles(tmp_path):
    lab = Simulation(tmp_path)
    result, save = simulate_verify(lab)
    assert result["overall_status"] == "PASS" and result["run_complete"]
    assert result["phase_complete"] is False
    directions = {(c[1], c[2], c[3]) for c in lab.calls if c[0] == "capacity"}
    assert directions == {(p, profile, reverse) for p in n.PATHS for profile in n.PROFILES for reverse in (False, True)}
    assert len([c for c in lab.calls if c[0] == "capacity"]) == 28
    assert {c[1] for c in lab.calls if c[0] == "isolation"} == {"a", "b"}
    assert all(item["exited"] for proc, item in lab.children.values())


def test_failed_verification_keeps_report_and_restores_initial_down_link(tmp_path):
    lab = Simulation(tmp_path)
    lab.links["a"] = "down"
    lab.bad_capacity = True
    result, save = simulate_verify(lab)
    assert result["overall_status"] == "FAILED" and not result["run_complete"]
    assert result["restoration_status"] == "PASS"
    assert lab.links["a"] == "down"
    assert lab.data["profile"] == "unshaped"
    assert save.called


def test_early_echo_failure_reaps_servers_and_records_failure(tmp_path):
    lab = Simulation(tmp_path)
    lab.bad_echo = True
    result, save = simulate_verify(lab)
    assert result["verification_status"] == "FAILED"
    assert all(item["exited"] for proc, item in lab.children.values())
    assert save.called


def test_repeated_attempts_have_new_directories(tmp_path):
    lab = Simulation(tmp_path)
    result, _ = simulate_verify(lab, "isolation")
    second, _ = simulate_verify(lab, "isolation")
    assert result["attempt_id"] != second["attempt_id"]
    assert len(lab.data["attempts"]) == 2
    assert not second["run_complete"] and not second["phase_complete"]


def test_restoration_failure_blocks_pass(tmp_path):
    lab = Simulation(tmp_path)
    original = lab.link_state
    def change(path, state):
        if state == "up":
            raise n.Failed("restore denied")
        return original(path, state)
    lab.link_state = change
    result, _ = simulate_verify(lab, "isolation")
    assert result["overall_status"] == "FAILED" and result["restoration_status"] == "FAILED"


def test_evidence_quota_and_overwrite(tmp_path):
    lab = bare()
    lab.attempt = tmp_path
    with mock.patch.object(n, "atomic_json") as write, mock.patch.object(n, "MAX_EVIDENCE_BYTES", 1):
        with pytest.raises(n.Failed):
            lab.write("sample.json", {"data": "test"})
        write.assert_not_called()
    existing = tmp_path / "existing.json"
    existing.write_text("{}")
    with pytest.raises(n.Unsafe):
        lab.write("existing.json", {})


def test_stable_management_differences_not_repaired():
    lab = bare()
    baseline = {"net": 88, "addresses": [], "routes": [], "forward": "0",
                "qdisc": [{"kind": "netem", "options": {"delay": 20000}}],
                "links": {"enp0s3": {"flags": ["UP"]}}, "namespaces": []}
    lab.data["management_before"] = baseline
    changed = copy.deepcopy(baseline)
    changed["qdisc"][0]["options"]["delay"] = 40000
    lab.snapshot = mock.Mock(return_value=changed)
    with pytest.raises(n.Failed):
        lab.management_safe()


def test_all_mandatory_checks_are_explicit():
    assert len(n.required_checks("all")) == 17
    assert n.required_checks("isolation") == {"readiness", "traversal", "isolation-a", "isolation-b"}


def test_pending_state_cannot_be_overwritten():
    lab = bare()
    lab.data["pending"] = {"label": "old change"}
    lab.exec = mock.Mock(side_effect=AssertionError("execute"))
    with pytest.raises(n.Unsafe):
        lab.mutation("new change", ["ip", "link", "set", "as", "up"], "a")
    lab.exec.assert_not_called()


def test_forged_management_link_record_never_mutates():
    lab = bare()
    lab.data["links"]["as"]["dev"] = "enp0s3"
    lab.link = mock.Mock(side_effect=AssertionError("inspect target"))
    with pytest.raises(n.Unsafe):
        lab.assert_link("as")
    lab.link.assert_not_called()


def test_changed_source_refuses_verification_before_network_work(tmp_path):
    lab = Simulation(tmp_path)
    lab.data["implementation"] = {"wrong": "hash"}
    with pytest.raises(n.Unsafe):
        lab.verify("all")
    assert lab.calls == []


def test_real_isolation_rejects_other_path_failure():
    lab = bare()
    lab.paired_probes = mock.Mock(return_value={"a": probe(), "b": probe()})
    lab.link_state = mock.Mock()
    lab.capture_pair = mock.Mock(return_value={})
    lab.finish_captures = mock.Mock()
    lab.probe_start = mock.Mock(side_effect=lambda p, count=100: p)
    down = {"count": 10, "received": 0, "mismatches": 0, "duplicates": 0, "start": 2, "end": 4}
    bad = dict(probe(received=98), start=1, end=10)
    lab.probe_finish = mock.Mock(side_effect=lambda p: down if p == "a" else bad)
    lab.capture_counts = mock.Mock(side_effect=lambda captures, p: {"a": 1, "b": 0} if p == "a" else {"a": 0, "b": 100})
    with mock.patch.object(n.time, "sleep"), pytest.raises(n.Failed, match="Outage isolation"):
        lab.isolation("a")


def test_cleanup_closed_run_with_recreated_resource_refuses():
    lab = bare()
    lab.data["closed"] = True
    lab.exec = mock.Mock(return_value={"stdout": lab.ns["a"]})
    with pytest.raises(n.Unsafe):
        lab.cleanup()
    assert not any("del" in call.args[0] for call in lab.exec.call_args_list)


def test_udp_unreachable_is_recorded_as_zero_replies_not_worker_crash():
    sock = mock.MagicMock()
    sock.__enter__.return_value = sock
    sock.sendto.side_effect = OSError(n.errno.EHOSTUNREACH, "unreachable")
    sock.recvfrom.side_effect = OSError(n.errno.EHOSTUNREACH, "unreachable")
    ticks = itertools.count(0, .01)
    with mock.patch.object(n.socket, "socket", return_value=sock), \
         mock.patch.object(n.select, "select", return_value=([sock], [], [])), \
         mock.patch.object(n.time, "monotonic", side_effect=lambda: next(ticks)):
        result = n.echo_probe(RUN, "a", 10)
    assert result["received"] == 0 and result["timeouts"] == 10
    assert result["transport_errors"] > 0
    assert sock.bind.call_args.args[0][0] == n.PATHS["a"]["robot"]


def test_unexpected_socket_permission_error_is_fatal():
    sock = mock.MagicMock()
    sock.__enter__.return_value = sock
    sock.sendto.side_effect = OSError(n.errno.EPERM, "denied")
    with mock.patch.object(n.socket, "socket", return_value=sock), pytest.raises(OSError):
        n.echo_probe(RUN, "a", 10)
