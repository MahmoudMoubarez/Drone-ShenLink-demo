"""Read-only prerequisites; not an end-to-end network acceptance test."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent


def verify_bundle():
    manifest = json.loads((ROOT/'assets/bundle-manifest.json').read_text())
    for name, digest in manifest['sha256'].items():
        path = (ROOT/name).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise RuntimeError('Missing or unsafe bundle file: '+name)
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != digest:
            raise RuntimeError('Bundle checksum mismatch: '+name)
    return manifest


def main():
    verify_bundle()
    for tool in ('ip', 'tc', 'iperf3', 'tcpdump', 'setpriv', 'sysctl'):
        if not shutil.which(tool):
            raise RuntimeError('Missing system tool: '+tool)
    import gi
    gi.require_version('Gst', '1.0')
    gi.require_version('GstApp', '1.0')
    gi.require_version('GstVideo', '1.0')
    from gi.repository import Gst, GstApp, GstVideo
    Gst.init(None)
    for plugin in ('appsrc', 'appsink', 'x264enc', 'avdec_h264', 'h264parse', 'videoconvert', 'videorate', 'decodebin'):
        if Gst.ElementFactory.find(plugin) is None:
            raise RuntimeError('Missing GStreamer element: '+plugin)
    import cv2
    import numpy
    import shenlink.v1
    import shenlink.v1.video
    from importlib.metadata import version
    if version('ShenLink') != '0.1.0rc1':
        raise RuntimeError('Unexpected SDK version')
    cap = cv2.VideoCapture(str(ROOT/'assets/robot-camera-360.mp4'))
    try:
        ok, frame = cap.read()
        if not ok or frame.shape[:2] != (960, 1920) or abs(cap.get(cv2.CAP_PROP_FPS)-25) > .1:
            raise RuntimeError('Video cannot be decoded at expected resolution/FPS')
    finally:
        cap.release()
    print('Prerequisites PASS. Kernel topology/delivery/cleanup still require --self-check.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        raise SystemExit('Preflight failed: '+str(exc))
