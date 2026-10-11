"""Integration regression: real SDK/HTTP bridge; mocked network mutations ONLY."""
import json
import argparse
from pathlib import Path
import secrets
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--port',type=int,default=8792)
parser.add_argument('--release',type=Path,default=ROOT/'vendor/shenlink')
parser.add_argument('--panorama',type=Path,default=ROOT/'assets/robot-camera-360.mp4')
args = parser.parse_args()
port = args.port
sys.path.insert(0,str(ROOT))
from gate_bridge import atomic_json
from gui_bridge import NetworkControl,StationProxy
from network_gate import PROFILES
from cut_evidence import analyze

directory = ROOT/('stage5-rootless-'+secrets.token_hex(4))
directory.mkdir()
endpoints = directory/'endpoints'
endpoints.mkdir()
token = secrets.token_hex(24)
atomic_json(directory/'network-session.json',dict(token=token))
stopped = threading.Event()
calls = []
controller = NetworkControl(directory,token,PROFILES,lambda p,s:calls.append((p,s)),stopped)
proxy = StationProxy(port,endpoints/'station.sock')
children = []
try:
    for role in ('station','robot'):
        command = [sys.executable,'-I','-B',str(ROOT/'smoke_endpoints.py'),role,
            '--release',str(args.release.resolve()),
            '--panorama',str(args.panorama.resolve()),
            '--output',str(endpoints),'--seconds','60','--base-port','29900','--interactive']
        if role=='station':
            command.extend(['--ui-dir',str(ROOT/'live-ui'),'--http-socket',str(endpoints/'station.sock'),
                '--network-control-dir',str(directory),'--http-port',str(port)])
        children.append(subprocess.Popen(command))
    import time
    deadline = time.monotonic()+20
    while not (endpoints/'station.sock').exists():
        assert all(child.poll() is None for child in children)
        assert time.monotonic()<deadline
        time.sleep(.1)
    subprocess.run([sys.executable,'-I','-B',str(ROOT/'check_live_gui.py'),f'http://127.0.0.1:{port}'],check=True,timeout=35)
    assert calls[:6]==[(p,s) for p in 'acb' for s in ('down','up')],calls
    assert len(calls)==8 and calls[-2][0]==calls[-1][0] and calls[-2][1]=='down' and calls[-1][1]=='up',calls
    print('Scope: NETWORK ACTIONS MOCKED; real SDK video/commands/sensors, Unix HTTP, TCP proxy and GUI API. Not netem acceptance.',flush=True)
finally:
    stopped.set()
    proxy.close()
    controller.close()
    for child in children:
        if child.poll() is None:
            child.terminate()
    for child in children:
        try:
            child.wait(15)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    reports={}
    for role in ('station','robot'):
        value = json.loads((endpoints/(role+'.json')).read_text())
        reports[role]=value
        assert value['status']=='MANUAL_COMPLETED' and value['cleanup']=='PASS' and not value['errors'],value
    assert all(child.returncode==0 for child in children)
    assert not (endpoints/'station.sock').exists() and not (directory/'network.sock').exists()
    station,robot=reports['station'],reports['robot']
    evidence=analyze(controller.events,station['frame_observations'],station['sensor_observations'],
        station['command_checks'][0]['command_events'],robot['command_execution_events'],station['evidence_end_ns'])
    assert evidence['cut_count']==4 and evidence['single_path_cut_count']==4,evidence
    assert evidence['all_paths_outage_count']==0 and evidence['fresh_frame_count']>10
    assert len(robot['command_execution_events'])==station['command_checks'][0]['manual_commands_sent']
    commands=[row for phase in evidence['phases'] for row in phase['commands']]
    assert len(commands)>=3 and all(row['robot_outcome']=='applied' and row['receipt']=='RECEIVED' for row in commands),commands
    assert any(set(row['sensor_confirmation_ns'])=={'yaw','pitch'} for row in commands)
    assert evidence['cuts'][-1]['origin']=='auto' and evidence['cuts'][-1]['selection']['path'] in 'abc'
    atomic_json(directory/'cut-evidence.json',evidence)
    print('PASS: recorded frame/sensor/command evidence and automatic target provenance; network mutations still MOCKED.',flush=True)
    print('PASS: GUI transport regression and graceful cleanup; original source preserved.',flush=True)
