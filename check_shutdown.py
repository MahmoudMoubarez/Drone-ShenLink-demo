"""Exercise the manual bridge, then verify supervisor/endpoint graceful stop."""
import json
from pathlib import Path
import subprocess
import sys
import argparse

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--output',default='stage3-shutdown01')
parser.add_argument('--port',type=int,default=8791)
parser.add_argument('--panorama',default='/home/edgelif2/work/viewer-360/insta360-corrected-square-pixels.mp4')
parser.add_argument('--measure-rate',action='store_true')
args = parser.parse_args()
output = root / args.output
if output.exists():
    raise SystemExit('Existing evidence; choose a new output directory')
command = [sys.executable,'-I','-B',str(root/'smoke_endpoints.py'),'both',
           '--release','/home/edgelif2/work/multiflow-v1-release',
           '--panorama',args.panorama,
           '--output',str(output),'--interactive','--seconds','60','--http-port',str(args.port),
           '--base-port','29300']
process = subprocess.Popen(command)
try:
    subprocess.run([sys.executable,'-I','-B',str(root/'check_manual_bridge.py'),
                    f'http://127.0.0.1:{args.port}'],check=True,timeout=25)
    if args.measure_rate:
        subprocess.run([sys.executable,'-I','-B',str(root/'check_display_rate.py'),
                        f'http://127.0.0.1:{args.port}'],check=True,timeout=20)
    process.terminate()
    assert process.wait(15)==0
    for role in ('robot','station'):
        report = json.loads((output/f'{role}.json').read_text())
        assert report['cleanup']=='PASS' and not report['errors'],report
    print('PASS: graceful stop releases both endpoints and panorama worker.')
finally:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
