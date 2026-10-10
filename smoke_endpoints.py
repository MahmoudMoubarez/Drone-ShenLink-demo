"""Stage 2: real independent SDK, two processes, native video and control feedback.

Loopback by default; the network gate supplies isolated namespace addresses.
"""
import argparse
import importlib
import json
from pathlib import Path
import socket
import signal
import subprocess
import sys
import threading
import time

STOPPED = threading.Event()


def run(args):
    source = args.release.resolve() / 'src'
    sys.path.insert(0, str(source))
    from multiflow_comm.v1 import (CommandSender, CommandReceiver, CommandSenderSettings,
        CommandReceiverSettings, SensorSender, SensorReceiver, SensorSenderSettings,
        SensorReceiverSettings, MeasurementExpectation, TelemetrySample, SampleStatus,
        UdpTransport, TransportState)
    from video_stream.v1 import (VideoSender, VideoReceiver, CameraSource,
        SenderSettings, ReceiverSettings, ReceiverRequirements)
    modules = {}
    for name in ('multiflow_comm.v1', 'video_stream.v1'):
        location = Path(importlib.import_module(name).__file__).resolve()
        if not location.is_relative_to(source):
            raise RuntimeError(f'Wrong SDK: {location}')
        modules[name] = str(location)
    def paths(flow, sender):
        offset = {'video': 0, 'sensors': 6, 'commands': 12}[flow]
        network = json.loads(args.network_config.read_text()) if args.network_config else None
        if network is not None:
            from ipaddress import ip_address
            if set(network) != set('abc') or args.role not in ('robot','station'):
                raise ValueError('Invalid namespace path configuration')
            for value in network.values():
                if set(value) != {'robot','station'}:
                    raise ValueError('Invalid endpoint address fields')
                for address in value.values():
                    if ip_address(address).version != 4:
                        raise ValueError('IPv4 namespace endpoints required')
        peer_role = 'station' if args.role == 'robot' else 'robot'
        return [UdpTransport(path,
            (network[path][args.role] if network else '127.0.0.1', args.base_port+offset+2*i+int(not sender)),
            (network[path][peer_role] if network else '127.0.0.1', args.base_port+offset+2*i+int(sender)))
            for i, path in enumerate('abc')]

    lock = threading.Lock()
    errors, observations, frames = [], {}, []
    sensor_events, execution_events = [], []
    newest = [None]
    max_video_gap_ms = [0.0]
    # Only the live judge GUI uses the constrained satellite-friendly profile.
    video_min_kbps = 1200 if args.ui_dir else 1800 if args.panorama else 600
    video_max_kbps = 1800 if args.ui_dir else 3000 if args.panorama else 1800
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from camera_motion import CameraMotion
    motion=CameraMotion()
    endpoints = []
    source_adapter = None
    report = {'role': args.role, 'status': 'FAIL', 'modules': modules}
    def error(exc):
        with lock:
            errors.append(str(exc))
    def command(message):
        value = message.decode_object()
        with lock:
            received_ns = time.monotonic_ns()
            try:
                motion.apply(value)
                if args.interactive or args.network_config:
                    execution_events.append(dict(id=value['id'],received_ns=received_ns,
                        outcome='applied' if motion.state['command_id']==value['id'] else 'expired',
                        executed_ns=motion.state['executed_ns'] if motion.state['command_id']==value['id'] else None))
            except Exception as exc:
                execution_events.append(dict(id=value.get('id'),received_ns=received_ns,outcome='error',error=str(exc)))
                errors.append(str(exc))
    def observation(value):
        with lock:
            observations[value.sample.measurement_id] = dict(json.loads(value.sample.value_bytes),
                received_ns=time.monotonic_ns(), sequence=value.sample.sample_sequence)
            if args.ui_dir:
                sensor_events.append(dict(observations[value.sample.measurement_id],axis=value.sample.measurement_id))
    def frame(value):
        with lock:
            received_ns = time.monotonic_ns()
            if frames:
                max_video_gap_ms[0] = max(max_video_gap_ms[0], (received_ns-frames[-1]['received_ns'])/1e6)
            newest[0] = value
            frames.append({'id': value.frame_id, 'width': value.width, 'height': value.height,
                           'path': value.path_id, 'session': value.session_id,
                           'delivery_kind': value.delivery_kind,
                           'received_ns': received_ns,
                           'command_witness': sum((1<<bit) for bit in range(8)
                               if value.data[10*value.stride+(bit*20+10)*3]>127) if args.panorama else None})
    def wait(predicate, timeout=12):
        deadline = time.monotonic()+timeout
        while not predicate():
            if STOPPED.is_set():
                raise RuntimeError('Test interrupted')
            if errors:
                raise RuntimeError(str(errors))
            if time.monotonic() >= deadline:
                raise TimeoutError('Endpoint condition timed out')
            time.sleep(.01)

    try:
        if args.role == 'robot':
            sensor = SensorSender(SensorSenderSettings(paths('sensors', True)), on_error=error)
            receiver = CommandReceiver(CommandReceiverSettings(paths('commands', False)),
                                       on_command=command, on_error=error)
            if args.panorama:
                sys.path.insert(0,str(Path(__file__).resolve().parent))
                from panorama import PanoramaSource
                def snapshot():
                    with lock:
                        return motion.snapshot()
                source_adapter = PanoramaSource(args.panorama,snapshot)
            else:
                source_adapter = CameraSource(test_pattern=True,width=640,height=360,fps=25)
            video = VideoSender(SenderSettings(source_adapter,paths('video', True),min_fps=25), on_error=error)
            endpoints = [receiver, sensor, video]
            for endpoint in endpoints:
                endpoint.start()
            deadline, sequence = time.monotonic()+args.seconds, 0
            while time.monotonic() < deadline and not STOPPED.is_set():
                if errors:
                    raise RuntimeError(str(errors))
                if args.panorama and source_adapter.error:
                    raise RuntimeError(source_adapter.error)
                sequence += 1
                with lock:
                    snapshot = motion.snapshot()
                for axis in ('yaw', 'pitch'):
                    sensor.publish(TelemetrySample('robot', axis, 'stage2-boot', sequence,
                        SampleStatus.GOOD, json.dumps({'degrees': snapshot[axis],
                            'command_id': snapshot['command_id'],
                            'executed_ns': snapshot['executed_ns']}).encode(),
                        'json', 'demo.orientation', 1, unit='degrees'))
                STOPPED.wait(.04)
            with lock:
                final_motion=motion.snapshot()
            report.update(executed=final_motion, status=('GATE_COMPLETED' if args.gate_dir else 'MANUAL_COMPLETED' if args.interactive else
                          'PASS' if motion.state['executions']==8 else 'FAIL'))
        else:
            sensor = SensorReceiver(SensorReceiverSettings(paths('sensors', False)),
                                    on_observation=observation, on_error=error)
            for axis in ('yaw', 'pitch'):
                sensor.register_measurement('robot', axis,
                    MeasurementExpectation('demo.orientation', 1, 'degrees', 'json'))
            sender = CommandSender(CommandSenderSettings(paths('commands', True)), on_error=error)
            video = VideoReceiver(ReceiverSettings(paths('video', False),
                requirements=ReceiverRequirements(min_resolution=(640,360), max_resolution=(640,360),
                    min_fps=25, max_fps=25, min_bitrate_kbps=video_min_kbps,
                    max_bitrate_kbps=video_max_kbps)),
                on_frame=frame, on_error=error)
            endpoints = [sensor, sender, video]
            for endpoint in endpoints:
                endpoint.start()
            wait(lambda: all(state == TransportState.CONNECTED for state in sender.path_states.values()))
            wait(lambda: len(frames)>=10 and all(axis in observations for axis in ('yaw', 'pitch')))
            checks = []
            sequence = ((10,0),(20,0),(20,10),(20,-10),(-20,-10),(-20,10),(0,10),(0,0))
            if args.gate_dir:
                from gate_bridge import gate_session
                def gate_snapshot():
                    with lock:
                        return {'frames':len(frames), 'recent_frames':list(frames[-300:]),
                                'sensors':dict(observations), 'errors':list(errors),
                                'at_ns':time.monotonic_ns()}
                checks = gate_session(sender,gate_snapshot,args.seconds-5,STOPPED,args.gate_dir)
                sequence = ()
            if args.interactive:
                sys.path.insert(0,str(Path(__file__).resolve().parent))
                from manual_preview import serve
                def snapshot():
                    with lock:
                        times=[f['received_ns'] for f in frames[-150:] if f['received_ns']>=time.monotonic_ns()-3_000_000_000]
                        return {'frames':len(frames),'sensors':dict(observations),'errors':list(errors),
                                'sdk_receive_fps':(len(times)-1)*1e9/(times[-1]-times[0]) if len(times)>1 else 0,
                                'last_frame_path':frames[-1]['path'] if frames else None,
                                'last_frame_id':frames[-1]['id'] if frames else None,
                                'session_id':frames[-1]['session'] if frames else None,
                                'resolution':[frames[-1]['width'],frames[-1]['height']] if frames else None,
                                'video_age_ms':(time.monotonic_ns()-frames[-1]['received_ns'])/1e6 if frames else None,
                                'max_video_gap_ms':max_video_gap_ms[0],
                                'video_bitrate_max_kbps':video_max_kbps,
                                'command_paths':{path:state.name for path,state in sender.path_states.items()},
                                'sensor_age_ms':{axis:(time.monotonic_ns()-value['received_ns'])/1e6 for axis,value in observations.items()},
                                'witness':frames[-1]['command_witness'] if frames else None}
                def latest():
                    with lock:
                        return newest[0]
                checks = serve(sender,snapshot,latest,args.seconds-5,args.http_port,STOPPED,
                    ui_dir=args.ui_dir,unix_path=args.http_socket,network_dir=args.network_control_dir)
                sequence = ()
            for index, (yaw,pitch) in enumerate(sequence, 1):
                before = len(frames)
                sent_ns = time.monotonic_ns()
                ticket = sender.send_object({'id': index, 'yaw': yaw, 'pitch': pitch})
                wait(ticket.is_done)
                if ticket.status.value != 'RECEIVED':
                    raise AssertionError(f'No command receipt: {ticket.status}')
                wait(lambda: all(observations.get(axis,{}).get('command_id')==index
                                 for axis in ('yaw','pitch')))
                for axis, expected in (('yaw',yaw), ('pitch',pitch)):
                    if observations[axis]['degrees'] != expected:
                        raise AssertionError('Executed orientation disagrees with received sensor')
                    if observations[axis]['executed_ns'] < sent_ns:
                        raise AssertionError('Execution precedes sending')
                wait(lambda: len(frames)>before)
                if args.panorama:
                    wait(lambda: any(f['command_witness']==index for f in frames[before:]))
                checks.append({'id': index, 'receipt': ticket.status.value,
                               'feedback_matches': True, 'video_continued': True,
                               'video_execution_witness': bool(args.panorama)})
            time.sleep(1)
            report.update(status='GATE_COMPLETED' if args.gate_dir else 'MANUAL_COMPLETED' if args.interactive else 'PASS', command_checks=checks, decoded_frames=len(frames),
                          dimensions=sorted({(f['width'],f['height']) for f in frames}),
                          sensors=dict(observations), video_orientation_linked=bool(args.panorama),
                          max_video_gap_ms=max_video_gap_ms[0],
                          video_bitrate_range_kbps=[video_min_kbps,video_max_kbps])
            if args.gate_dir or args.ui_dir:
                report['frame_observations'] = frames
    except Exception as exc:
        report['failure'] = repr(exc)
    finally:
        for endpoint in reversed(endpoints):
            try:
                endpoint.stop()
            except Exception as exc:
                errors.append(f'Cleanup: {exc}')
        if args.panorama and source_adapter:
            try:
                source_adapter.close()
            except Exception as exc:
                errors.append(f'Source cleanup: {exc}')
        report['errors'] = errors
        if args.interactive or args.network_config:
            with lock:
                report.update(evidence_end_ns=time.monotonic_ns(),sensor_observations=list(sensor_events),
                    command_execution_events=list(execution_events))
        report['cleanup'] = 'FAIL' if errors else 'PASS'
        if errors:
            report['status'] = 'FAIL'
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / f'{args.role}.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({'role': args.role, 'status': report['status'], 'errors': errors}), flush=True)
    return 0 if report['status'] in ('PASS','MANUAL_COMPLETED','GATE_COMPLETED') else 1


def main():
    for sig in (signal.SIGINT,signal.SIGTERM):
        signal.signal(sig,lambda *_: STOPPED.set())
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=('both', 'robot', 'station'))
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base-port', type=int, default=29100)
    parser.add_argument('--seconds', type=float, default=18)
    parser.add_argument('--panorama', type=Path)
    parser.add_argument('--interactive', action='store_true')
    parser.add_argument('--http-port', type=int, default=8791)
    parser.add_argument('--network-config', type=Path)
    parser.add_argument('--gate-dir', type=Path)
    parser.add_argument('--ui-dir',type=Path)
    parser.add_argument('--http-socket',type=Path)
    parser.add_argument('--network-control-dir',type=Path)
    args = parser.parse_args()
    if args.seconds < 15 or not 1024 <= args.base_port <= 65517:
        parser.error('Use at least 15 seconds and a base port within 1024..65517')
    if args.interactive and not args.panorama:
        parser.error('Manual camera check requires --panorama')
    if args.gate_dir and (args.interactive or args.role == 'both'):
        parser.error('The network-gate runner owns gate endpoint processes')
    if args.network_config and args.role == 'both':
        parser.error('Namespace addresses require separately launched endpoints')
    if args.role != 'both':
        return run(args)
    if args.output.exists():
        parser.error('Use a new output directory; existing evidence will not be overwritten')
    # Refuse to collide with any of the existing demo endpoints.
    reservations = []
    try:
        for port in range(args.base_port,args.base_port+18):
            sock = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
            reservations.append(sock)
            sock.bind(('127.0.0.1',port))
    finally:
        for sock in reservations:
            sock.close()
    children = []
    try:
        for role in ('station','robot'):
            command = [sys.executable,'-I','-B',str(Path(__file__).resolve()),
                role,'--release',str(args.release.resolve()),'--output',str(args.output.resolve()),
                '--base-port',str(args.base_port),'--seconds',str(args.seconds)]
            if args.panorama:
                command.extend(['--panorama',str(args.panorama.resolve())])
            if args.interactive:
                command.extend(['--interactive','--http-port',str(args.http_port)])
            children.append(subprocess.Popen(command))
        deadline = time.monotonic()+args.seconds+30
        while any(child.poll() is None for child in children):
            if STOPPED.wait(.1):
                for child in children:
                    if child.poll() is None:
                        child.terminate()
                break
            if time.monotonic()>deadline:
                raise TimeoutError('Child processes did not complete')
        codes = [child.wait(timeout=12) for child in children]
        return 0 if all(code==0 for code in codes) else 1
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(8)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


if __name__ == '__main__':
    raise SystemExit(main())
