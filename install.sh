#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ $(uname -s) != Linux || $EUID == 0 ]]; then
  echo 'Use an ordinary Ubuntu account (not root); sudo will request your password.' >&2
  exit 1
fi
source /etc/os-release
if [[ $ID != ubuntu || $VERSION_ID != 24.04 ]]; then
  echo 'This installer targets Ubuntu 24.04. Other systems are not validated.' >&2
  exit 1
fi
sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv python3-pip python3-setuptools python3-wheel python3-gi python3-numpy python3-opencv python3-pil gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav iproute2 iperf3 tcpdump util-linux procps
/usr/bin/python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install --no-build-isolation --no-deps ./vendor/shenlink
.venv/bin/python run_demo.py --check
echo 'Installed. Start with: python3 run_demo.py'
