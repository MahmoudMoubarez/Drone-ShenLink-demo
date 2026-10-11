"""One-file launcher for the real Linux mission demo."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def runtime_command(python, seconds, port, self_check=False):
    command = [str(python), '-I', '-B', str(ROOT/'gui_runtime.py'),
               '--python', str(python), '--release', str(ROOT/'vendor/shenlink'),
               '--panorama', str(ROOT/'assets/robot-camera-360.mp4'),
               '--seconds', str(seconds), '--http-port', str(port)]
    return command + (['--self-check'] if self_check else [])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Read-only prerequisites; not a network test')
    parser.add_argument('--self-check', action='store_true', help='Actual SDK/HTTP integration through kernel-emulated paths')
    parser.add_argument('--seconds', type=int, default=600)
    parser.add_argument('--port', type=int, default=8791)
    args = parser.parse_args()
    if not 20 <= args.seconds <= 900 or not 1024 <= args.port <= 65535:
        parser.error('Use 20..900 seconds and port 1024..65535')
    if sys.platform != 'linux':
        parser.exit(1, 'Requires Ubuntu 24.04; Windows native is not supported.\n')
    python = ROOT/'.venv/bin/python'
    if not python.is_file():
        parser.exit(1, 'Install first: bash install.sh\n')
    result = subprocess.run([str(python), str(ROOT/'preflight.py')])
    if result.returncode or args.check:
        return result.returncode
    if os.geteuid() == 0:
        parser.exit(1, 'Start as an ordinary account, without sudo; the launcher requests sudo itself.\n')
    print(f'Open http://127.0.0.1:{args.port} after READY. Session: {args.seconds}s; Ctrl+C stops it.', flush=True)
    return subprocess.call(['sudo', *runtime_command(python, args.seconds, args.port, args.self_check)])


if __name__ == '__main__':
    raise SystemExit(main())
