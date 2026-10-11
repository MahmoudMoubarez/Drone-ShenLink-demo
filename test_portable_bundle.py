import subprocess
import sys

from preflight import verify_bundle
from run_demo import ROOT, runtime_command


def test_bundle_integrity():
    manifest = verify_bundle()
    assert manifest['sdk_version'] == '0.1.0rc1'
    assert 'assets/robot-camera-360.mp4' in manifest['sha256']
    expected = {p.relative_to(ROOT).as_posix() for p in (ROOT/'vendor/shenlink/src').rglob('*.py') if '__pycache__' not in p.parts}
    assert expected <= set(manifest['sha256'])
    assert (ROOT/'assets/robot-camera-360.mp4').stat().st_size < 100*1024*1024


def test_launcher_uses_bundled_paths():
    command = runtime_command(ROOT/'.venv/bin/python', 600, 8791, True)
    assert '--self-check' in command
    assert command[command.index('--release')+1] == str(ROOT/'vendor/shenlink')
    assert '/home/edgelif2' not in ' '.join(command)


def test_help_without_native_dependencies():
    result = subprocess.run([sys.executable, str(ROOT/'run_demo.py'), '--help'], capture_output=True, text=True)
    assert result.returncode == 0
    assert '--self-check' in result.stdout


def test_bundled_video_decodes_and_projects():
    import cv2
    from panorama import Projector
    cap = cv2.VideoCapture(str(ROOT/'assets/robot-camera-360.mp4'))
    try:
        assert abs(cap.get(cv2.CAP_PROP_FPS)-25) < .1
        projector = Projector()
        for second in (0, 60, 180, 230):
            cap.set(cv2.CAP_PROP_POS_MSEC, second*1000)
            ok, frame = cap.read()
            assert ok and frame.shape[:2] == (960, 1920)
            assert projector.render(frame, 40, -10).shape == (360, 640, 3)
    finally:
        cap.release()


def test_windows_guard():
    if sys.platform != 'win32':
        return
    result = subprocess.run([sys.executable, str(ROOT/'run_demo.py')], capture_output=True, text=True)
    assert result.returncode == 1
    assert 'Windows native is not supported' in result.stderr
