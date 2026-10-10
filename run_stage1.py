"""Pin the independent SDK and run its existing tests; not a video benchmark."""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--release', type=Path, default=Path(__file__).resolve().parents[1] / 'multiflow-v1-release')
    parser.add_argument('--report', type=Path, default=Path(__file__).with_name('stage1-local.json'))
    args = parser.parse_args()
    release = args.release.resolve()
    source = release / 'src'
    sys.path.insert(0, str(source))
    modules = {}
    for name in ('multiflow_comm.v1', 'video_stream.v1'):
        module = importlib.import_module(name)
        location = Path(module.__file__).resolve()
        if not location.is_relative_to(source):
            raise RuntimeError(f'Wrong library imported: {location}')
        modules[name] = str(location)
    files = sorted(source.rglob('*.py'))
    hashes = {str(file.relative_to(release)): hashlib.sha256(file.read_bytes()).hexdigest() for file in files}
    try:
        import gi
        gi.require_version('Gst', '1.0')
        from gi.repository import Gst
        Gst.init(None)
        native = {'available': True, 'version': Gst.version_string()}
    except (ImportError, ValueError) as error:
        native = {'available': False, 'reason': str(error)}
    import pytest
    result = pytest.main([str(release / 'tests'), '-q', '-p', 'no:cacheprovider'])
    report = {'stage': 1, 'status': 'PASS' if result == 0 else 'FAIL',
              'release': str(release), 'modules': modules, 'source_hashes': hashes,
              'native_video_runtime': native, 'test_exit_code': int(result),
              'limitations': ['Video media is not exercised by these release tests.',
                              'No performance or network-emulator claim.']}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('stage', 'status', 'native_video_runtime')}))
    return int(result)


if __name__ == '__main__':
    raise SystemExit(main())
