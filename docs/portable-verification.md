# Portable packaging verification — 2026-10-11

Implemented: bundled ShenLink 0.1.0rc1 source snapshot and full 25-FPS panorama, manifest checks, Ubuntu installer, ordinary-user launcher, portable runtime paths, README and attribution. Existing SDK engine, motion controls, network profiles and GUI behavior were not redesigned.

Windows local results: **22 tests passed** (portable bundle, video decode/projection, camera motion, cut evidence, network-gate plan/bridge tests). Media: 1920×960, 25 FPS, 230.72 seconds, 63,510,363 bytes. New file re-encoded from the local corrected panorama using H.264 CRF 23; not byte-identical to the accepted Pi source. Installer shell syntax was checked separately when a local Bash was available.

Not yet executed: apt installation in clean Ubuntu, native GStreamer video streaming through owned kernel netem, GUI HTTP integration and shutdown cleanup on this new bundled snapshot, or manual browser acceptance. Windows full GUI-test collection fails because socketserver.UnixStreamServer is unavailable; this is why Windows native runtime is explicitly unsupported. WSL inventory showed no installed Linux distributions. Passing Windows tests does not certify first-run Ubuntu/Codespaces behavior.

Next gate on Ubuntu 24.04: bash install.sh; python3 run_demo.py --self-check; then python3 run_demo.py and manually check arrows, drag, cuts, Auto, satellite-only and full outage recovery. Require final success and cleanup PASS. Do not publish as fully validated until this gate passes.

No GitHub push performed. SDK source licensing is not yet selected; media credits are in CREDITS.md.
