# ShenLink mission demo

Receive robot video and sensors, send camera commands, and cut paths while the real ShenLink SDK manages delivery.

## Install and run — Ubuntu 24.04

Download/extract the repository. Open a terminal inside the folder as an ordinary sudo-enabled user.

Install once:

```bash
bash install.sh
```

Run:

```bash
python3 run_demo.py
```

Enter the Linux sudo password. Wait for READY, then open **http://127.0.0.1:8791** on the same computer. Keep the terminal open. Ctrl+C stops the demo and triggers ownership-checked cleanup. Sessions last ten minutes; restart with the same command. Optional: `--seconds 900` (15 minutes), `--port 8792`.

The repository includes ShenLink **0.1.0rc1**, its source snapshot, the full video and all GUI pages. No Pi-specific folders, separate SDK download, submodule or Git LFS is required. Internet is needed once for Ubuntu dependency installation.

## Try the demo

1. Operator Station: drag the view or hold arrows; observe received video and sensor direction.
2. Cut the path shown by FRAME VIA; watch subsequent delivery through available alternatives.
3. Try automatic cuts. Pulses are serialized; requested cut rate is not necessarily the measured rate.
4. Robot page shows data sources and received commands; Library page explains runtime state.
5. Cut all paths to observe stopped delivery/stale sensors; restore one to test recovery.

## Automated acceptance

```bash
python3 run_demo.py --check
python3 run_demo.py --self-check
```

The first only verifies bundle/dependencies. The second actually starts the SDK and owned kernel-emulated paths, runs HTTP integration tests, exits and prints a report. Check its final status and cleanup result. Then manually test smooth dragging/arrows, single-path cuts, satellite-only operation and all-path recovery.

**This new portable packaging still requires Linux end-to-end acceptance before presentation. Windows checks are not equivalent.**

## Requirements and scope

- Target: Ubuntu 24.04 with network namespaces, veth, HTB and netem; ordinary sudo-enabled account. Prefer Linux desktop or Linux VM. Containers, WSL and Codespaces need separate capability testing and are not certified here.
- **Windows native is unsupported.** A Windows browser can view a demo running on Linux through an SSH tunnel. Do not run the installer in PowerShell.
- Existing usable IPv4 default route is required by the safety guard. The launcher does not change Wi-Fi/default routes/Tailscale.
- The robot endpoint projects recorded 360-degree footage and streams it through the actual SDK in real time; sensors are simulated yaw/pitch, not physical GPS. The station does not synthesize a fallback video.
- Kernel emulation is independent of the SDK. Wi-Fi/Mobile/Satellite are scenario labels, not real radios or typical military rates: uplink 10/6/4 Mbps, downlink 30/50/25 Mbps, RTT 10/20/60 ms.
- One shared operator/session. Loopback listener only; no authentication for public access. Do not expose as a production service.
- Reports: `/run/shenlink-mission-lab/<run-id>/gui/`. Preserve evidence before reboot.
- `Connection refused`: wait for READY and inspect terminal errors. `Address already in use`: stop the previous session or choose another port. `No usable IPv4 default route`: check normal networking; do not guess a gateway.

## Bundled source and media

Exact snapshot hashes: `assets/bundle-manifest.json`. SDK base commit: `3ce39bd581b636b6fc419621af0438a170068818`, plus local ShenLink naming/export changes. This is a release candidate, not a new final release.

Video is 1920×960, 25 FPS, approximately 231 seconds; viewport output remains 640×360. It was re-encoded from the local corrected panorama, not copied byte-for-byte from the accepted Pi file. Media credit/license: [CREDITS.md](CREDITS.md). Source-code distribution licensing still needs owner selection; no open-source license is implied.

Historical notes: [docs/development-history.md](docs/development-history.md). Old machine-specific commands there are historical, not this quick start.
