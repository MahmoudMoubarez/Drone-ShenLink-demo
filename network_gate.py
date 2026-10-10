"""Owned Linux netem gate for video, commands, sensors together.

Uses an unchanged copy of the previous lab's ownership/management safety guards.
No physical radio claims; no root GUI or changes to management interfaces.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import signal
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from gate_bridge import atomic_json

PROFILES = {
    'a': dict(name='Wi-Fi',uplink_mbps=10,downlink_mbps=30,rtt_ms=10,jitter_ms=1),
    'b': dict(name='Mobile',uplink_mbps=6,downlink_mbps=50,rtt_ms=20,jitter_ms=2),
    'c': dict(name='Satellite',uplink_mbps=4,downlink_mbps=25,rtt_ms=60,jitter_ms=5),
}


def load_lab():
    source = ROOT/'owned-lab/tools/network_lab.py'
    spec = importlib.util.spec_from_file_location('mission_owned_lab',source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PATHS['c'] = dict(robot='198.18.30.1',gateway_r='198.18.30.2',
        gateway_s='198.18.31.1',station='198.18.31.2',robot_dev='rc',router_r='cr',
        router_s='cs',station_dev='sc',echo=5303,iperf=5203)
    module.JOURNAL_PARENT_DIR = '/run/shenlink-mission-lab'
    module.PROFILES = ('unshaped','baseline')
    def namespaces(run):
        module.validate_run_id(run)
        return {role:'shm-'+letter+'-'+run for role,letter in
                [('robot','r'),('station','s'),('a','a'),('b','b'),('c','c')]}
    module.namespaces = namespaces
    previous_hashes = module.implementation_hashes
    def hashes():
        values = previous_hashes()
        for filename in ('network_gate.py','gate_bridge.py','smoke_endpoints.py','panorama.py','camera_motion.py'):
            values['mission/'+filename] = hashlib.sha256((ROOT/filename).read_bytes()).hexdigest()
        for filename in ('gui_bridge.py','gui_runtime.py','manual_preview.py','cut_evidence.py'):
            file = ROOT/filename
            if file.is_file():
                values['mission/'+filename] = hashlib.sha256(file.read_bytes()).hexdigest()
        return values
    module.implementation_hashes = hashes
    def shaping(ns,dev,profile,path):
        if profile not in module.PROFILES or path not in module.PATHS:
            raise module.Unsafe('Invalid mission profile')
        layout = module.PATHS[path]
        if dev not in (layout['router_r'],layout['router_s']):
            raise module.Unsafe('Invalid mission router interface')
        prefix = ['ip','netns','exec',ns,'tc']
        if profile == 'unshaped':
            return [prefix+['qdisc','del','dev',dev,'root']]
        p = PROFILES[path]
        rate = str(p['uplink_mbps'] if dev==layout['router_s'] else p['downlink_mbps'])+'mbit'
        return [prefix+['qdisc','replace','dev',dev,'root','handle','1:','htb','default','10'],
            prefix+['class','replace','dev',dev,'parent','1:','classid','1:10','htb',
                'rate',rate,'ceil',rate,'burst','32kb','cburst','32kb','quantum','1500'],
            prefix+['qdisc','replace','dev',dev,'parent','1:10','handle','10:','netem',
                'delay',str(p['rtt_ms']//2)+'ms',str(p['jitter_ms'])+'ms','limit','1000']]
    module.shaping_commands = shaping
    return module


def phase_metrics(state, start_ns, end_ns):
    # Only new capture IDs; a quality upgrade is not another video frame.
    rows = [r for r in state['recent_frames'] if start_ns<=r['received_ns']<=end_ns
            and r.get('delivery_kind')!='quality_upgrade']
    unique = []
    seen = set()
    for row in rows:
        key = (row['session'],row['id'])
        if key not in seen:
            seen.add(key)
            unique.append(row)
    times = [start_ns]+[r['received_ns'] for r in unique]+[end_ns]
    duration = (end_ns-start_ns)/1e9
    return dict(new_frames=len(unique),sdk_fps=len(unique)/duration,
        max_gap_ms=max((b-a)/1e6 for a,b in zip(times,times[1:])),
        delivered_paths=sorted({r['path'] for r in unique}),
        sessions=sorted({r['session'] for r in unique}),
        sensor_age_ms={axis:(end_ns-value['received_ns'])/1e6
                       for axis,value in state['sensors'].items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',default=secrets.token_hex(4))
    parser.add_argument('--release',type=Path,default=Path('/home/edgelif2/work/multiflow-v1-release'))
    parser.add_argument('--panorama',type=Path,default=Path('/home/edgelif2/work/viewer-360/insta360-station-25fps.mp4'))
    parser.add_argument('--python',type=Path,default=Path('/home/edgelif2/.cache/mfl-video-runtime/standalone-v1-env/bin/python'))
    parser.add_argument('--plan',action='store_true')
    parser.add_argument('--cleanup',action='store_true')
    parser.add_argument('--probe-role',choices=('server','probe'))
    parser.add_argument('--path',choices=tuple(PROFILES))
    args = parser.parse_args()
    module = load_lab()
    module.validate_run_id(args.run_id)
    if args.plan:
        print(json.dumps(dict(profiles=PROFILES,plan=module.pure_plan(args.run_id)),indent=2))
        return 0
    if args.probe_role:
        if sys.platform!='linux' or os.geteuid()==0 or args.path is None:
            raise SystemExit('Probes require an unprivileged owned Linux worker')
        if args.probe_role=='server':
            module.echo_server(args.run_id,args.path,90)
        else:
            print(json.dumps(module.echo_probe(args.run_id,args.path,20,interval=.04)),flush=True)
        return 0
    if args.cleanup:
        print(json.dumps(module.Lab(args.run_id).cleanup(),indent=2))
        return 0
    # Check prerequisites before creating resources. User input is never a shell command.
    if sys.platform!='linux' or os.geteuid()!=0 or int(os.environ.get('SUDO_UID','-1'))<=0:
        raise SystemExit('Run using sudo from your normal Pi account; --plan works without sudo.')
    for path in (args.python,args.release/'src/video_stream/v1/__init__.py',args.panorama):
        if not path.is_file():
            raise SystemExit('Missing input: '+str(path))
    lab = module.Lab(args.run_id,create=True)
    report = dict(run_id=args.run_id,status='FAILED',cleanup='NOT_RUN',profiles=PROFILES,
        scope='Owned kernel netem; same Pi, recorded 360 source, independent SDK. Not physical radios or browser paint.',
        limitations=['No throughput calibration or full release acceptance.',
            'Satellite uplink is configured at 4 Mbps; capacity is an emulator scenario, not measured radio throughput. Satellite-only smooth video requires verification.'],
        gates=dict(max_video_gap_ms=500,max_sensor_age_ms=500,command_confirmation_s=2),
        phases=[],events=[],link_checks={})
    stopped = threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):
        signal.signal(sig,lambda *_:stopped.set())
    lab.deadline = time.monotonic()+180
    workers = {}
    directory = None
    try:
        lab.setup()
        lab.profile('baseline')
        lab.topology_check()
        lab.management_safe()
        artifact = lab.directory/'mission'
        artifact.mkdir(mode=0o750)
        os.chown(artifact,0,lab.user['gid'])
        lab.attempt = artifact
        directory = artifact/'endpoints'
        directory.mkdir(mode=0o750)
        os.chown(directory,lab.user['uid'],lab.user['gid'])
        report['report'] = str(artifact/'summary.json')
        module.atomic_json(artifact/'network.json',{p:{role:layout[role] for role in ('robot','station')}
                    for p,layout in module.PATHS.items()},lab.user['gid'])
        def guard():
            if stopped.is_set():
                raise InterruptedError('Stopped by user')
            if time.monotonic()>lab.deadline:
                raise TimeoutError('Whole gate bound exceeded')
            for role,child in workers.items():
                if child.poll() is not None:
                    raise RuntimeError(role+' exited early')
        def wait(seconds):
            deadline = time.monotonic()+seconds
            while time.monotonic()<deadline:
                guard()
                stopped.wait(min(.1,max(0,deadline-time.monotonic())))
        # Small echo traversal/RTT sanity check, not bandwidth calibration.
        for p in PROFILES:
            child = lab.start([str(args.python),'-I','-B',str(Path(__file__).resolve()),
                '--run-id',args.run_id,'--probe-role','server','--path',p],'station',label='echo-'+p)
            import select
            if not select.select([child.stdout],[],[],3)[0]:
                raise TimeoutError('Echo server readiness')
            if json.loads(child.stdout.readline())!={'ready':True,'path':p}:
                raise ValueError('Unexpected echo readiness')
            probe = lab.start([str(args.python),'-I','-B',str(Path(__file__).resolve()),
                '--run-id',args.run_id,'--probe-role','probe','--path',p],'robot',label='probe-'+p)
            result = json.loads(lab.finish(probe,10))
            lab.stop(child)
            tolerance = PROFILES[p]['jitter_ms']*2+5
            passed = module.probe_ok(result,required=19,count=20) and abs(result['median_ms']-PROFILES[p]['rtt_ms'])<=tolerance
            report['link_checks'][p] = dict(status='PASS' if passed else 'FAIL',probe=result)
            if not passed:
                raise AssertionError('Link delay/integrity sanity check failed: '+p)
        for role in ('station','robot'):
            workers[role] = lab.start([str(args.python),'-I','-B',str(ROOT/'smoke_endpoints.py'),
                role,'--release',str(args.release),'--panorama',str(args.panorama),
                '--output',str(directory),'--seconds','110','--base-port','29400',
                '--network-config',str(artifact/'network.json'),'--gate-dir',str(directory)],role,label='mission-'+role)
        def state():
            guard()
            file = directory/'station-state.json'
            if not file.exists():
                return None
            value = json.loads(file.read_text())
            if value['errors']:
                raise RuntimeError(str(value['errors']))
            return value
        deadline = time.monotonic()+25
        while not (state() or {}).get('frames',0)>=20:
            if time.monotonic()>deadline:
                raise TimeoutError('SDK video/sensor startup')
            wait(.1)
        sequence = 0
        sessions = set()
        def phase(name,seconds=4,command=True):
            nonlocal sequence
            start = time.monotonic_ns()
            index = None
            if command:
                sequence += 1
                index = sequence
                module.atomic_json(artifact/'control.json',dict(id=index,yaw=(index%3-1)*20,pitch=0),lab.user['gid'])
            confirmed_ns = None
            deadline = time.monotonic()+seconds
            while time.monotonic()<deadline:
                value = state()
                if index and confirmed_ns is None and value:
                    readings = value['sensors']
                    match = all(readings.get(axis,{}).get('command_id')==index for axis in ('yaw','pitch'))
                    witness = any(row['command_witness']==index for row in value['recent_frames'] if row['received_ns']>=start)
                    if match and witness and value['tickets'].get(str(index))=='RECEIVED':
                        if readings['yaw']['degrees']!=(index%3-1)*20 or readings['pitch']['degrees']!=0:
                            raise AssertionError('Executed pose differs from command')
                        confirmed_ns = time.monotonic_ns()
                wait(.1)
            value = state()
            end = value['at_ns']  # evidence sample clock, not a future coordinator time
            metrics = phase_metrics(value,start,end)
            sessions.update(metrics['sessions'])
            metrics.update(name=name,command_id=index,confirmation_ms=None if confirmed_ns is None else (confirmed_ns-start)/1e6)
            if command:
                passed = (metrics['new_frames']>0 and metrics['max_gap_ms']<=500
                    and set(metrics['sensor_age_ms'])=={'yaw','pitch'} and max(metrics['sensor_age_ms'].values())<=500
                    and metrics['confirmation_ms'] is not None and metrics['confirmation_ms']<=2000)
                metrics['functional_continuity'] = 'PASS' if passed else 'FAIL'
                metrics['requested_25fps_met'] = metrics['sdk_fps']>=24.5
            report['phases'].append(metrics)
            print(json.dumps(metrics),flush=True)
            return metrics
        def fault(path,status):
            started = time.monotonic_ns()
            lab.sensor_packet_fault(path,status)
            report['events'].append(dict(path=path,state=status,started_ns=started,verified_ns=time.monotonic_ns()))
        phase('healthy')
        for p in 'acb':
            fault(p,'down')
            phase(p+'_down')
            fault(p,'up')
            phase(p+'_restored')
        # Negative control: no alternate path means no fresh data. Never hide it.
        for p in 'abc':
            fault(p,'down')
        wait(2)
        before = state()
        wait(2)
        after = state()
        dark = dict(name='all_down',new_frames=after['frames']-before['frames'],
            sensor_age_ms={axis:(after['at_ns']-r['received_ns'])/1e6 for axis,r in after['sensors'].items()})
        dark['correctly_no_fresh_data'] = (dark['new_frames']==0 and set(dark['sensor_age_ms'])=={'yaw','pitch'}
                                        and min(dark['sensor_age_ms'].values())>=1000)
        report['phases'].append(dark)
        for p in 'abc':
            fault(p,'up')
        phase('all_restored',6)
        if any(p.get('functional_continuity')=='FAIL' for p in report['phases']) or not dark['correctly_no_fresh_data']:
            raise AssertionError('Continuity gate failed; inspect phase evidence')
        if len(sessions)!=1:
            raise AssertionError('Application video session restarted')
        report['video_session_preserved'] = True
        report['status'] = 'FUNCTIONAL_GATE_PASS'
        report['requested_25fps_met_every_phase'] = all(p['requested_25fps_met'] for p in report['phases'] if 'requested_25fps_met' in p)
    except Exception as exc:
        report['error'] = type(exc).__name__+': '+str(exc)
    finally:
        cleanup_errors = []
        for child,item in list(lab.children.values()):
            if not item['exited']:
                try:
                    lab.stop(child)
                except Exception as exc:
                    cleanup_errors.append(str(exc))
        if workers and directory:
            report['endpoint_shutdown'] = {}
            for role,child in workers.items():
                file = directory/(role+'.json')
                try:
                    endpoint = json.loads(file.read_text())
                    report['endpoint_shutdown'][role] = dict(exit=child.returncode,
                        status=endpoint['status'],cleanup=endpoint['cleanup'],errors=endpoint['errors'])
                    if child.returncode!=0 or endpoint['cleanup']!='PASS' or endpoint['errors'] or endpoint['status']!='GATE_COMPLETED':
                        cleanup_errors.append(role+' endpoint failed to stop cleanly')
                    if role=='robot' and report['status']=='FUNCTIONAL_GATE_PASS':
                        execution = endpoint['executed']
                        if execution['executions']!=sequence or execution['expired_commands']!=0:
                            cleanup_errors.append('Robot execution count or command expiry differs from gate evidence')
                except Exception as exc:
                    cleanup_errors.append(role+' shutdown report unavailable: '+str(exc))
        try:
            report['cleanup'] = lab.cleanup()['cleanup_status']
        except Exception as exc:
            cleanup_errors.append(str(exc))
            report['cleanup'] = 'FAIL'
        if cleanup_errors:
            report['cleanup_errors'] = cleanup_errors
            report['status'] = 'FAILED'
        if directory:
            lab.write('summary.json',report)
        print(json.dumps(report,indent=2),flush=True)
    return 0 if report['status']=='FUNCTIONAL_GATE_PASS' and report['cleanup']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
