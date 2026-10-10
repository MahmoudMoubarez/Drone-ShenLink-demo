# Linux network lab — corrected Phase 1 runbook

This tool creates four **network namespaces, not four VMs**, with two isolated
IPv4 paths. Only calibration UDP echo and iperf3 traffic is used. It does not
benchmark the communications library or prove application failover.

Status: portable/mock tests can validate control logic; real Linux HTB/netem,
packet capture and installed iperf receiver JSON still require the VM trials.
Do not call Phase 1 accepted merely because a command or one run passes.

## What is protected

- The controller stays in the management namespace. No shaping, routes,
  forwarding, firewall, DNS, or SSH configuration is changed on the management
  interface. Setup selects the UP interface of the lowest-metric IPv4 default
  route (for example enp0s3, wlan0, or eth0), then pins it in the journal.
  Missing usable default routes or equal-metric routes on different devices
  are refused. Later route changes cannot silently select another interface.
- Robot/station workers run through setpriv as the invoking normal Linux user.
  Captures run only on the owned router devices, with synthetic-flow filters.
- Namespace inode, interface ifindex and random ownership alias are checked.
  Deletion uses recorded resources, not a broad wildcard.
- An unresolved interrupted mutation is refused for manual ownership review.
  Do not respond by deleting every namespace, removing the journal, or granting
  passwordless sudo.
- Commands/workers are bounded; verification has a 900-second measurement budget.
  Bounded restoration and cleanup happen afterward.
- Code/test/doc/manifest hashes are frozen at setup. Do not pull or edit code
  while a lab is active. Clean it first.
- No credentials, sudoers changes, root agent, or global package installs.

If initial management discovery fails before any lab resources are created,
cleanup can close that empty journal without a management baseline. A journal
with recorded resources but no baseline is refused for manual review.

## Topology and profiles

| Path | Robot to router | Router to station |
| --- | --- | --- |
| A | ra 198.18.10.1/30 ↔ ar 198.18.10.2/30 | as 198.18.11.1/30 ↔ sa 198.18.11.2/30 |
| B | rb 198.18.20.1/30 ↔ br 198.18.20.2/30 | bs 198.18.21.1/30 ↔ sb 198.18.21.2/30 |

Namespaces: mfl-r-RUN, mfl-s-RUN, mfl-a-RUN, mfl-b-RUN.
There are no default routes, external uplinks, bridges, or NAT in the lab.
Both egress devices of each router use HTB + netem, so each direction traverses
one shaper. MTU 1500; rate=ceil; burst/cburst 32kb; quantum 1500; netem limit 1000.

| Profile | A, each direction | B, each direction |
| --- | --- | --- |
| unshaped | no artificial cap/delay | no artificial cap/delay |
| symmetric | 10 Mbit/s, 20 ms | 10 Mbit/s, 20 ms |
| slow-a | 2 Mbit/s, 20 ms | 10 Mbit/s, 20 ms |

These are calibration settings, not measured Wi-Fi/5G/satellite models.

## 1. Update and check without sudo

Work only on linux-compat-tests. If status shows unexpected local changes, stop
and report them; do not reset, delete, auto-stash, or overwrite them.

```bash
cd /home/mahmoud/work/Nework-Edge-cop/Native_Dual_Link_WebRTC_Test
git branch --show-current
git status --short
git pull --ff-only origin linux-compat-tests
source .venv-linux/bin/activate
git log -1 --oneline
python -m pytest tests/test_network_lab.py tests/test_test_runner.py -q
python tools/network_lab.py --run-id c061a701 preflight
python tools/network_lab.py --run-id c061a701 plan
```

Requirements already expected on this VM: ip, tc, iperf3, tcpdump, setpriv,
sysctl, /usr/bin/python3, git and free/df. Missing tools or unknown versions
are reported, not installed automatically. Pure plan/import/unit logic works
on Windows; actual network operations refuse it.

## 2. First VM pilot: setup and short isolation test

Use a NEW eight-character lowercase hexadecimal run ID. Never reuse a closed ID.
The normal user executes each reviewed sudo command in the VM terminal.
Stop on any error and retain the output. Do not start the next verification after
a failed one just to hide the failed result.

```bash
sudo /usr/bin/python3 -I -B tools/network_lab.py --run-id c061a701 setup
sudo /usr/bin/python3 -I -B tools/network_lab.py --run-id c061a701 status
sudo /usr/bin/python3 -I -B tools/network_lab.py --run-id c061a701 verify --stage isolation
sudo /usr/bin/python3 -I -B tools/network_lab.py --run-id c061a701 cleanup
python tools/network_lab.py --run-id c061a701 export-report --output-dir logs/network-lab/c061a701-review
```

First run this pilot and share the results. It tests worker startup, real packet
traversal/captures, HTB/netem application, both path outages, simultaneous
other-path reachability and recovery. It does NOT measure full bandwidth/delay
calibration. Expect a few minutes and progress messages on stderr.

If verify fails, cleanup is still required. If cleanup refuses uncertain ownership
or a pending mutation, stop and send the output/journal for review. Do not invent
manual deletion commands. Evidence under /run is volatile: export before reboot.

## 3. Full calibration after a successful reviewed pilot

Create a NEW run, for example c061a702. Use the same setup/status/cleanup/export
sequence, replacing the pilot verification with:

```bash
sudo /usr/bin/python3 -I -B tools/network_lab.py --run-id c061a702 verify --stage all
```

Use output directory logs/network-lab/c061a702-review. Repeat a complete fresh
setup/full verify/cleanup/export for c061a703. Do not run two labs concurrently.
The full test typically needs about 10–15 minutes depending on VM scheduling;
a deadline failure is recorded, not converted to success.

Full checks:

- Both explicitly bound endpoints and actual expected routes/MTUs/forwarding.
- A-only and B-only packet traversal with no matching flow on the other path.
- Three paired idle RTT trials, unshaped then symmetric. EVERY valid pair must
  have a shaped-minus-baseline median delta in 30–60 ms (−10/+20 around 40).
  Healthy probes need >=99/100; raw per-packet times/timeouts are retained.
- Unshaped UDP 20 Mbit/s offer on A/B, forward/reverse: received >=18 Mbit/s.
- Three trials per path/direction for symmetric and slow-a: receiver throughput
  0.85–1.05 times the cap in EACH trial, not offered sender throughput.
- Low-rate B probes inside the A load observation window.
- A down while B continues, then recovery; symmetric B-down checks. Each failed
  path has 0/10 echoes; unaffected/recovered paths require >=99/100.
- Initial link/profile restoration, stable management snapshots and owned cleanup.

If iperf JSON differs on the installed version, report the saved raw client/server
data. Do not add sender-only fallback, fabricate fields or relax durations/bands.

## 4. Evidence and meaning of results

Each verify creates a fresh attempt directory in /run/mfl-network-lab/RUN/.
It retains command argv/namespace/timing/exit/stdout/stderr, process identities,
raw probe outcomes, client/server iperf JSON, qdisc/class/link counters and bounded
pcaps. Evidence is capped at 25 MiB, with space reserved for a failure summary.

A failed stage, timeout, missing evidence, unavailable inspection, source-file
change or restoration failure cannot yield successful verification.
Repeated attempts preserve old failures; export will not hide them.

Normal-user export copies to a NEW directory and writes acceptance_report.json
and acceptance_report.md with artifact hashes. Existing destinations/symlinks
are refused. Exported FAILED/NOT_RUN reports exit nonzero even though diagnostic
files may have been successfully copied.

run_complete means one full verification passed. full_run_passed additionally
requires cleanup. phase_complete deliberately remains false until two fresh
full runs, regression results and reviewer approval are established externally.
The pilot's successful isolation result is not a complete Phase 1 result.

Share the console output and exported acceptance_report.json. Keep the full raw
artifact directory available if the reviewer requests a particular sample.
Logs are ignored by Git; do not commit them or authentication details.

## 5. Regression after cleanup

```bash
python tools/run_tests.py --group core --output-dir logs/network-lab-regression
python tools/run_tests.py --group all --output-dir logs/network-lab-regression
```

Do not weaken existing camera/performance tests if they fail intermittently.
Report the exact failure and preserve its log.

Exit codes: 0 completed requested operation; 1 measurement/execution failure;
2 invalid input/collision/unverified ownership; 3 missing platform/privilege/tool.

No changes in this phase are made to src/, main, requirements, or the original
Windows checkout. Publication on linux-compat-tests is for review and VM testing,
not a claim that Linux real-network verification has already succeeded.
