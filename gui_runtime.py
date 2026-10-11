"""Run the accepted station GUI through the SDK in owned kernel-emulated paths."""
import argparse
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
from network_gate import load_lab,PROFILES
from gui_bridge import NetworkControl,StationProxy
from cut_evidence import analyze


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',default=secrets.token_hex(4))
    parser.add_argument('--seconds',type=int,default=600)
    parser.add_argument('--http-port',type=int,default=8791)
    parser.add_argument('--self-check',action='store_true')
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--release', default=str(ROOT/'vendor/shenlink'))
    parser.add_argument('--panorama', default=str(ROOT/'assets/robot-camera-360.mp4'))
    args = parser.parse_args()
    if not 20<=args.seconds<=900 or not 1024<=args.http_port<=65535:
        parser.error('Use 20..900 seconds and an unprivileged port')
    if sys.platform!='linux' or os.geteuid()!=0 or int(os.environ.get('SUDO_UID','-1'))<=0:
        raise SystemExit('Use python3 run_demo.py from an ordinary Linux account')
    python = str(Path(args.python).absolute())
    release = str(Path(args.release).resolve())
    panorama = str(Path(args.panorama).resolve())
    for file in (Path(python),Path(panorama),Path(release)/'src/video_stream/v1/__init__.py',ROOT/'live-ui/index.html',ROOT/'live-ui/live.js'):
        if not file.is_file():
            raise SystemExit('Missing input: '+str(file))
    # Reserve management-side loopback listener before any network mutation.
    module = load_lab()
    module.validate_run_id(args.run_id)
    proxy = StationProxy(args.http_port,Path(module.JOURNAL_PARENT_DIR)/args.run_id/'gui/endpoints/station.sock')
    stopped = threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):
        signal.signal(sig,lambda *_:stopped.set())
    lab = None
    controller = None
    directory = None
    children = {}
    report = dict(run_id=args.run_id,status='FAILED',cleanup='NOT_RUN',profiles=PROFILES,
        scope='Station GUI, real SDK and owned kernel netem; recorded camera source; serialized verified automatic pulses available.')
    try:
        lab = module.Lab(args.run_id,create=True)
        lab.deadline = time.monotonic()+args.seconds+90
        lab.setup()
        lab.profile('baseline')
        lab.topology_check()
        lab.management_safe()
        artifact = lab.directory/'gui'
        artifact.mkdir(mode=0o750)
        os.chown(artifact,0,lab.user['gid'])
        lab.attempt = artifact
        directory = artifact/'endpoints'
        directory.mkdir(mode=0o750)
        os.chown(directory,lab.user['uid'],lab.user['gid'])
        module.atomic_json(artifact/'network.json',{p:{r:layout[r] for r in ('robot','station')} for p,layout in module.PATHS.items()},lab.user['gid'])
        token = secrets.token_hex(24)
        module.atomic_json(artifact/'network-session.json',dict(token=token),lab.user['gid'])
        controller = NetworkControl(artifact,token,PROFILES,lab.sensor_packet_fault,stopped)
        for role in ('station','robot'):
            command = [python,'-I','-B',str(ROOT/'smoke_endpoints.py'),role,'--release',release,
                '--panorama',panorama,'--output',str(directory),'--seconds',str(args.seconds+20),
                '--base-port','29700','--network-config',str(artifact/'network.json'),'--interactive']
            if role=='station':
                command.extend(['--ui-dir',str(ROOT/'live-ui'),'--http-socket',str(directory/'station.sock'),
                    '--network-control-dir',str(artifact),'--http-port',str(args.http_port)])
            children[role] = lab.start(command,role,label='gui-'+role)
        ready_deadline = time.monotonic()+25
        while not (directory/'station.sock').exists():
            if stopped.wait(.1):
                raise InterruptedError('Stopped during startup')
            if any(child.poll() is not None for child in children.values()) or time.monotonic()>ready_deadline:
                raise RuntimeError('Station GUI startup failed')
        print(json.dumps(dict(url=f'http://127.0.0.1:{args.http_port}',status='READY',
            run_id=args.run_id,source='recorded-360',transport='independent SDK',network='owned kernel netem',
            automatic_cuts='SERIAL_VERIFIED_PULSES')),flush=True)
        if args.self_check:
            result = lab.exec([python,'-I','-B',str(ROOT/'check_live_gui.py'),f'http://127.0.0.1:{args.http_port}'],user=True,timeout=45)
            print(result['stdout'],flush=True)
            report['http_integration_check'] = 'PASS'
        else:
            deadline = time.monotonic()+args.seconds
            while not stopped.wait(.2) and time.monotonic()<deadline:
                if any(child.poll() is not None for child in children.values()):
                    raise RuntimeError('GUI endpoint exited before the session ended')
                if controller.error:
                    raise RuntimeError(controller.error)
        report['status'] = 'HTTP_CHECK_PASS' if args.self_check else 'MANUAL_COMPLETED_FEEDBACK_REQUIRED'
    except Exception as exc:
        report['error'] = type(exc).__name__+': '+str(exc)
    finally:
        stopped.set()
        errors = []
        proxy.close()
        if controller:
            try:
                controller.close()
                report['network_events'] = list(controller.events)
                report['auto_pulses'] = controller.pulses
                report['auto_measured_session_rate'] = controller.snapshot()['pulse_rate']
            except Exception as exc:
                errors.append(str(exc))
        if lab:
            for child,item in list(lab.children.values()):
                if not item['exited']:
                    try:
                        lab.stop(child)
                    except Exception as exc:
                        errors.append(str(exc))
            endpoint_reports = {}
            for role,child in children.items():
                try:
                    value = json.loads((directory/(role+'.json')).read_text())
                    endpoint_reports[role] = value
                    if child.returncode!=0 or value['cleanup']!='PASS' or value['errors'] or value['status']!='MANUAL_COMPLETED':
                        errors.append(role+' failed to stop cleanly')
                except Exception as exc:
                    errors.append(role+' shutdown evidence unavailable: '+str(exc))
            if controller and set(endpoint_reports)=={'station','robot'}:
                try:
                    station,robot = endpoint_reports['station'],endpoint_reports['robot']
                    report['cut_evidence'] = analyze(report['network_events'],station['frame_observations'],
                        station['sensor_observations'],station['command_checks'][0]['command_events'],
                        robot['command_execution_events'],station['evidence_end_ns'])
                except Exception as exc:
                    errors.append('Cut evidence unavailable: '+str(exc))
            try:
                report['cleanup'] = lab.cleanup()['cleanup_status']
            except Exception as exc:
                errors.append(str(exc))
                report['cleanup'] = 'FAIL'
            if errors:
                report.update(status='FAILED',cleanup_errors=errors)
            if directory:
                report['report'] = str(lab.attempt/'summary.json')
                lab.write('summary.json',report)
        print(json.dumps(report,indent=2),flush=True)
    return 0 if report['status'] in ('HTTP_CHECK_PASS','MANUAL_COMPLETED_FEEDBACK_REQUIRED') and report['cleanup']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
