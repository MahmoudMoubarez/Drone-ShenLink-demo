# ShenLink mission simulation — staged implementation

Uses the independent `multiflow-v1-release`, not the historical project.
The library source is unchanged. UI preview at port 8790 is still separate.

## Implemented

1. Pinned module paths, source hash snapshot and seven release tests.
2. Two processes: robot sends native video and yaw/pitch sensors, station sends
   eight ordered commands. Execution confirmed by received sensor values.
3. Robot-side 360 perspective projection and a temporary station viewer at 8791.
   Commands change the robot projection. The browser receives only decoded frames.
   Eight-bit black/white video witness carries command ID modulo 256 for checking
   execution through the actual video stream. It is a test marker, not the final HUD.

## Not implemented / not proven yet

- Differently shaped routes, shared capacities, faults and all-path recovery.
- Final GUI/backend integration, separate-host measurements, endurance tests.
- Smoothness and performance: smoke results are functional evidence only.
- The source is recorded footage, not a physical live camera. 640x360 output at
  requested 25 FPS is an initial integration profile, not a measured achieved FPS.
- Panorama render/decode overhead is application work, not library-only latency.
- Temporary viewer uses bounded latest-JPEG caching and a multipart stream;
  status polling is separate. This still adds JPEG/HTTP/browser display latency.
  Visible FPS counters measure SDK callbacks and preview encoding, not browser paint.
- No encryption on UDP library paths. Viewer binds loopback; access via SSH only.

## Pi manual check (after automated stage-three gate passes)

Use the existing standalone Python runtime. Launch `smoke_endpoints.py both` with
`--interactive --seconds 600 --panorama PATH --release PATH --output NEW_DIRECTORY`.
Forward local port 8791 to Pi 127.0.0.1:8791 over SSH.
Open http://127.0.0.1:8791 and hold arrows / drag the image / press Centre.
Check directions, release stopping motion, seam transitions, video blockiness,
and perceived control lag. This is not the final judge-facing interface.
Manual completion is recorded as MANUAL_COMPLETED, never an automatic PASS.

## Arrow motion

Arrow holds renew a robot-side angular velocity command (25 degrees/second).
Robot snapshots integrate real elapsed time; the camera is not animated locally.
Release, lost pointer capture and window blur send zero velocity. A 300 ms
station-issued lease also stops motion if renewals disappear. Expired queued
commands are ignored. This lease uses the shared host monotonic clock: it must
be replaced by verified clock mapping / an appropriate control-session design
before station and robot run on different hosts. It is not a safety certification.
Mouse dragging uses relative angle deltas so it starts from the robot's current
pose, not an outdated browser angle left over from arrow control.

## Display troubleshooting changes

- Disabled native image dragging and selection; pointer capture handles camera drag.
- One JPEG worker caches the latest received frame; clients do not encode on demand.
- Multipart stream avoids a serial state request + image request + 70ms delay.
- Display stays at the 640x360 integration profile's size instead of stretching it.
- Panorama profile bitrate is 1800..3000 kbps to reduce codec artifacts; this is
  a demo configuration change, not an engine optimization or shaped-path result.
- Prepared 25 FPS source keeps 1920x960 dimensions and the original input file.
  No extra spatial detail is claimed: a 360 panorama distributes its pixels over
  the entire sphere, not the current perspective viewport.

## 4K source experiment

Decision: retain `insta360-station-25fps.mp4` (1920x960 panorama) as the
accepted demo source. The 4K experiment is not the active profile: measured
SDK callback rate dropped from about 22.5 to 3 FPS on Pi 5. Preserve the
experiment as evidence only. Keep the continuous-arrow and mouse fixes.
Source-quality tuning is closed for now; next is shaped routes and fault tests,
before final GUI integration. This is not acceptance of the full demo/library.

`viewer-360/insta360-station-4k25.mp4` is a separate 30-second test clip,
3840x1920, square pixels, 25 FPS. It is derived from the original 7680x3840
EAC cubemap, converted to equirectangular projection, not upscaled from the
1920x960 copy. The clip loops in the robot source. Existing inputs are preserved.
Outgoing video remains 640x360 at requested 25 FPS and 1800..3000 kbps;
this experiment changes source detail and decoding cost only, not output size.
The preparation filter is `fps=25,v360=input=eac:output=equirect:w=3840:h=1920,setsar=1`,
with H.264, CRF 18, veryfast, yuv420p, no audio.

## Stage four: owned kernel-network gate

`network_gate.py` uses an unchanged vendored copy of the existing
ownership-checked `network_lab.py`; new namespaces/journals have their own
`shm-*` prefix and `/run/shenlink-mission-lab` directory. Management interfaces
and routes are not targets. Endpoint workers run as the invoking ordinary user.
Video, commands and sensors share each path's kernel capacity and delay.

The previous freshness benchmark's original directional profiles were:
Wi-Fi 6/30 Mbps, RTT 10 ms, jitter 1 ms; Mobile 4/50 Mbps, RTT 20 ms, jitter
2 ms; Satellite 1.4/25 Mbps, RTT 60 ms, jitter 5 ms (uplink/downlink).
These are scenario labels, not real radio interfaces. RTT is a configured target;
idle echo checks sanity-check delay/integrity, not throughput calibration.

Gate: healthy, cut/restore A then C then B, all paths blackholed, all restored.
Cuts apply verified 100% packet loss in both directions to the owned routers.
The source/output/bitrate stay unchanged. Satellite-only smooth video cannot be
promised in the original profile: 1.4 Mbps uplink was below the 1.8 Mbps video floor.
The current Satellite uplink is now 2.5 Mbps at the user's request; downlink,
RTT and jitter are unchanged. This is a new scenario, not a change to historical
results or a measured military-network average. The video maximum remains
3 Mbps, so satellite-only continuity still requires a new test.

Local orchestration files trigger the station to send real library commands;
no file bypasses robot execution or carries camera/sensor payloads. Evidence
includes command receipts, execution-confirmed sensor values, command-ID witness
inside decoded video, delivered frame path/session IDs, FPS and maximum gaps.
Quality upgrades are not counted as additional captured frames. A functional
continuity gate requires <=500 ms video gaps and sensor age and <=2 s confirmation.
Requested 25 FPS is reported separately and is not implied by functional PASS.
All-path outage must produce no new frames and stale sensors, never synthetic data.

Portable plan/unit tests and the unshaped regression are not acceptance of netem.
The sudo gate needs the Pi user's password. Run 14f66545 completed its functional
gate and cleanup successfully. This is not a guarantee of constant 25 FPS:
settled-phase rates were 21.5..24.1 FPS, with a maximum settled-phase gap of
137.2 ms. Automatic cut-rate controls remain pending.
Run on Pi: `sudo /usr/bin/python3 -I -B network_gate.py` (fresh random run ID).
Output gives the report path. On cleanup refusal, preserve the journal and review;
do not delete namespaces or journals by hand. `--run-id ID --cleanup` uses the
same ownership guards and refuses unresolved mutations.

## Stage five: Station GUI integration

`live-ui/` preserves the selected Station design, but removes its simulated
camera movement, sensor values and link selection. Video is decoded by the SDK
receiver and forwarded as MJPEG; commands go through the SDK to the robot;
displayed yaw/pitch are received sensor values. The source is recorded 360 footage,
explicitly labeled as demo replay, not a physical live camera.

`gui_runtime.py` launches isolated robot/station workers using the independent
release. A loopback TCP proxy forwards the Station's Unix HTTP socket without
creating management routes or bypassing the application transport. A separate,
authenticated Unix controller applies and verifies cuts only on owned lab paths.
The GUI changes a path's state only after verified controller feedback.
Wi-Fi/Mobile/Satellite bandwidth and RTT figures are configured emulator
profiles, not measurements of real radios. FRAME VIA means the delivered frame's
path, not the library's permanently preferred path. Old frames/sensors are
marked stale; they are not synthesized during an outage.

From Windows PowerShell:

```powershell
ssh -t -o ExitOnForwardFailure=yes -i "$env:USERPROFILE\.ssh\xubuntu_lab_ed25519" -L 8791:127.0.0.1:8791 edgelif2@100.75.198.67 "cd /home/edgelif2/work/shenlink-mission-simulation && sudo /usr/bin/python3 -I -B gui_runtime.py"
```

After READY, open http://127.0.0.1:8791. Keep the terminal open. Hold/release
arrows and drag the image, then cut and restore each path while watching video
and sensor feedback. Cutting all paths must show stale data, then recovering
a path must restore delivery. Ctrl+C stops and ownership-checked cleanup runs.
Default session duration is 600 seconds. Each run prints its report path.

Preflight: actual SDK video/commands/sensors over the Unix/TCP HTTP bridge passed
with a mocked network mutation controller; 11 Linux unit tests and the JS input
and verified-state tests passed. The original preview regression also passed,
measuring about 22.5 SDK callback FPS, not browser paint rate. These checks do not
constitute acceptance of real netem cuts through this GUI or visual user feedback.
`sudo ... gui_runtime.py --self-check` checks the HTTP integration against real
owned netem paths, but manual visual feedback is still required.

Remaining: manual real-network GUI acceptance, automatic cuts with measured
achievable rate, endurance and final validation. Auto controls are intentionally
disabled until their gate passes. Robot and library configuration pages remain
design previews; this stage connects only the Station, not those other pages.

### Satellite-only movement follow-up

The live judge GUI now negotiates 1200..1800 kbps video, leaving nominal headroom
below its 2.5 Mbps Satellite uplink. This is a demo profile change, not a general
SDK fix, measured wire-rate guarantee, or priority scheduler. The original gate
and temporary preview profiles are unchanged. The sensor/control lease is retained.
GUI control requires a CONNECTED command path and both sensor ages <=500 ms,
including time elapsed since the last HTTP response; stale video alone does not
block it. Stale sensors or loss of command connectivity still block movement.
The 300 ms robot command lease remains the final stop if renewals disappear.

The video overlay distinguishes delayed video from all verified paths cut and
an unreachable station server. The live CSS is loaded after the old preview
styles, preventing its obsolete ALL LINKS DISCONNECTED text from overriding it.
Diagnostics expose last video arrival age, SDK callback FPS, maximum delivery
gap since startup (including intentional outages), and configured bitrate ceiling.
They do not measure encoded wire throughput or browser paint. Final station
reports retain the maximum gap and negotiated bitrate range for comparison.
Real Satellite-only motion under netem still requires a new user-run test.

### Smooth mouse targets

The live Station drag now maintains an absolute target initialized from received
sensor orientation. Pointer deltas update this target; the latest target renews
every 50 ms while held. The robot integrates toward it in <=5 ms steps, with
90 degrees/s speed cap and 360 degrees/s² rate-change bound during approach.
Arrival clamping, explicit stop and lease expiry stop immediately. Target
replacement does not jump the current pose; expired commands cannot restart it.
Yaw takes the shortest wrapped direction and pitch stays within ±75 degrees.
Release, blur or lost capture stops target renewal and sends zero velocity;
the same-host 300 ms lease limits motion if the stop is lost. Fresh sensor/control
gating is retained. Arrow speed/integration, video and path profiles are unchanged.
No animation is added to the received image, and the SDK release is untouched.
The legacy temporary viewer still uses delta commands, for compatibility.

### Current uplink capacity scenario

At the user's request, the next scenario uses Wi-Fi 10 Mbps, Mobile 6 Mbps,
Satellite 4 Mbps uplinks. Downlinks remain 30/50/25 Mbps; RTT/jitter, camera
motion and video profile are unchanged. Earlier reports and A/B comparison
copies retain their original profiles. This does not establish field averages.

### Manual buttons and automatic pulses

Path cards no longer replace their DOM on each frame-path update, preserving
normal click activation. Auto is enabled by the live JS after initialization.
It requests a serialized backend pulse against the latest fresh delivered
frame's path; it pauses with fewer than two enabled paths or stale video.
The authenticated backend validates each pulse, applies/verifies loss, holds
for 50..500 ms, then restores/verifies in a finally block even if the tab closes
or shutdown is requested. Manual mode stops scheduling new pulses; the current
pulse finishes restoring first. Hidden tabs stop scheduling. No overlapping
mutations or auto-cutting the only enabled path are permitted.

Requested 0.5..5 cuts/s is a target, NOT a guarantee. Kernel mutation/readback
time and restore time are additional to the requested hold duration. The GUI
reports completed pulse count and session-average measured cycles/s, including
pauses since the first pulse. This does not measure library handover speed.
Final reports retain events and pulse statistics. Unit/rootless checks use
mocked mutations; real netem Auto acceptance requires the sudo user-run test.

Auto hold duration now supports 50..5000 ms. The Station backend selects the
latest SDK-delivered frame path when it receives the pulse request, overriding
the browser's potentially stale hint. It refuses frames older than 500 ms.
This is the active delivered-video path, not a permanent SDK primary; a race
between sampling and the verified kernel mutation remains possible. A cut is
not by itself proof of handover: observe subsequent fresh frames from other paths.
The GUI displays the last actual pulse target. Long holds reduce the achievable
cuts/s; pulses remain serial and the current hold restores before the next starts.

### Per-cut evidence (new runs only)

The final gui/summary.json contains `cut_evidence`: every cut has a manual/auto
origin, pulse ID where applicable, kernel mutation start/readback times, restore
times, last new frame before verified cut, and first alternative new frame during
the stable verified configuration. Automatic selection also records the sampled
frame ID/path. Selection and verification are not atomic: the report states
whether the cut target was still the last delivered path at verification.

Separate phases distinguish one path cut, two paths cut and all paths cut.
Each cut also retains its full cycle, including kernel mutation periods, with
an overlap flag if it included an all-path outage. The isolated single-path
cycle statistic excludes cycles overlapping fewer than two enabled paths; it
does not omit slow kernel transitions from that cycle's video-gap measurement.
Each phase ends at the next mutation START, not its readback end, so uncertain
mutation periods are not attributed to a verified configuration. Reports include
observed new-frame gaps (ignoring quality upgrades/duplicate capture IDs), sensor
arrival intervals/final ages, and command ticket status vs direct robot application
logs vs matching received sensor confirmations. No missing confirmation is inferred
from a later command ID. Applying a target is not reaching the target. In-flight
frames can arrive after a cut, and one alternate frame alone does not prove zero
freeze, causal handover, or uninterrupted play. An open cut is marked unrestored.

Raw metadata remains in endpoints/station.json and robot.json (no frame pixels
are added to logs). Ticket status is sampled at viewer shutdown; arrival/receipt
time is not measured. Same-host monotonic timestamps enable these correlations;
they cannot be compared across separate machines without clock alignment.
SDK arrival measurements are not browser paints or physical-radio measurements.
Network profiles, SDK, source resolution and camera motion are unchanged.

Before manual acceptance, run the evidence unit tests and rootless HTTP regression.
The latter uses real SDK traffic but MOCKED network mutations; it validates
instrumentation, not real failover. Then test sudo netem with all paths initially
restored, one cut at a time, Auto pulses, and an all-path outage separately. Stop
with Ctrl+C once and wait for cleanup/report. Historical reports are unchanged.
