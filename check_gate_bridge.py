"""Rootless real-SDK gate harness regression; NOT evidence of routed outages."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from gate_bridge import atomic_json

parser = argparse.ArgumentParser()
parser.add_argument('--output',required=True)
args = parser.parse_args()
if Path(args.output).name!=args.output or args.output in ('.','..'):
    raise SystemExit('Use a new plain directory name')
directory = ROOT/args.output
directory.mkdir()
endpoints = directory/'endpoints'
endpoints.mkdir()
children = []
try:
    for role in ('station','robot'):
        children.append(subprocess.Popen([sys.executable,'-I','-B',str(ROOT/'smoke_endpoints.py'),role,
            '--release','/home/edgelif2/work/multiflow-v1-release',
            '--panorama','/home/edgelif2/work/viewer-360/insta360-station-25fps.mp4',
            '--output',str(endpoints),'--gate-dir',str(endpoints),'--seconds','60','--base-port','29600']))
    def state():
        if any(p.poll() is not None for p in children):
            raise RuntimeError('Endpoint exited early')
        file = endpoints/'station-state.json'
        return json.loads(file.read_text()) if file.exists() else None
    deadline = time.monotonic()+20
    while not (state() or {}).get('frames',0)>=20:
        if time.monotonic()>deadline:
            raise TimeoutError('Startup')
        time.sleep(.1)
    for index in range(1,9):
        yaw = (index%3-1)*20
        atomic_json(directory/'control.json',dict(id=index,yaw=yaw,pitch=0))
        deadline = time.monotonic()+4
        while True:
            value = state()
            readings = value['sensors']
            if all(readings.get(axis,{}).get('command_id')==index for axis in ('yaw','pitch')) and any(
                    row['command_witness']==index for row in value['recent_frames']):
                assert readings['yaw']['degrees']==yaw and readings['pitch']['degrees']==0
                assert value['tickets'][str(index)]=='RECEIVED'
                assert all(r['path'] in 'abc' and r['session'] for r in value['recent_frames'])
                assert all(readings[axis]['sequence']>0 for axis in ('yaw','pitch'))
                break
            assert time.monotonic()<deadline,value
            time.sleep(.05)
    print('PASS: gate IPC -> station SDK command -> robot -> sensor and video witness; 8 commands.',flush=True)
finally:
    for child in children:
        if child.poll() is None:
            child.terminate()
    for child in children:
        try:
            child.wait(15)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    for role in ('robot','station'):
        report = json.loads((endpoints/(role+'.json')).read_text())
        assert report['status']=='GATE_COMPLETED' and report['cleanup']=='PASS' and not report['errors'],report
        if role=='robot':
            assert report['executed']['executions']==8 and report['executed']['expired_commands']==0
    assert all(child.returncode==0 for child in children)
    print('PASS: gate endpoints stopped cleanly. Scope remains unshaped loopback.',flush=True)
