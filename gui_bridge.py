"""Local-only bridges across owned network namespaces, not application paths.

The TCP bridge forwards the station's HTTP bytes unchanged. Network mutations
use a separate authenticated Unix endpoint and fixed owned lab paths only.
"""
import http.client
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import select
import socket
import socketserver
import threading
import time


class UnixHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.host)


class NetworkClient:
    def __init__(self,directory):
        self.directory = Path(directory)
        self.token = json.loads((self.directory/'network-session.json').read_text())['token']

    def request(self,method,path,value=None):
        connection = UnixHTTP(str(self.directory/'network.sock'),timeout=15)
        try:
            payload = None if value is None else json.dumps(value)
            connection.request(method,path,body=payload,headers={'X-Session':self.token,'Content-Type':'application/json'})
            response = connection.getresponse()
            value = json.loads(response.read(16384))
            if response.status!=200:
                raise RuntimeError(value.get('error','Network controller failed'))
            return value
        finally:
            connection.close()


class ThreadingUnixServer(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):
    daemon_threads = True
    block_on_close = False


class NetworkControl:
    def __init__(self,directory,token,profiles,apply,stopped):
        self.path = Path(directory)/'network.sock'
        self.token,self.profiles,self.apply,self.stopped = token,profiles,apply,stopped
        self.lock = threading.Lock()
        self.actions = threading.Lock()
        self.up = {p:True for p in profiles}
        self.busy = False
        self.error = None
        self.events = []
        self.pulses = 0
        self.pulse_start_ns = None
        controller = self
        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                self.request.settimeout(5)
                super().setup()
            def log_message(self,*args):
                pass
            def reply(self,code,value):
                data = json.dumps(value).encode()
                self.send_response(code)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def authorized(self):
                if self.headers.get('X-Session')!=controller.token:
                    self.reply(403,dict(error='Invalid network session'))
                    return False
                return True
            def do_GET(self):
                if not self.authorized():
                    return
                if self.path!='/state':
                    return self.reply(404,dict(error='Unknown network route'))
                self.reply(200,controller.snapshot())
            def do_POST(self):
                if not self.authorized():
                    return
                if self.path not in ('/path','/pulse'):
                    return self.reply(404,dict(error='Unknown network route'))
                try:
                    size = int(self.headers.get('Content-Length','0'))
                    if not 1<=size<=256:
                        raise ValueError('Invalid control size')
                    value = json.loads(self.rfile.read(size))
                    pulse = self.path=='/pulse'
                    valid = (set(value) in ({'path','duration_ms'}, {'path','duration_ms','selection'}) and type(value['duration_ms']) is int and 50<=value['duration_ms']<=5000) if pulse else (set(value)=={'path','up'} and type(value['up']) is bool)
                    if not valid or value['path'] not in controller.profiles:
                        raise ValueError('Only an owned path and boolean state are allowed')
                    if 'selection' in value:
                        selection=value['selection']
                        if (not isinstance(selection,dict) or set(selection)!={'sampled_ns','frame_id','path'}
                            or type(selection['sampled_ns']) is not int or type(selection['frame_id']) is not int
                            or selection['path']!=value['path']):
                            raise ValueError('Invalid frame selection metadata')
                except Exception as exc:
                    return self.reply(400,dict(error=str(exc)))
                if not controller.actions.acquire(blocking=False):
                    return self.reply(409,dict(error='A verified network change is in progress'))
                try:
                    with controller.lock:
                        if controller.stopped.is_set() or controller.error:
                            return self.reply(503,dict(error='Network controller stopped'))
                        if pulse and (not controller.up[value['path']] or sum(controller.up.values())<2):
                            return self.reply(409,dict(error='Auto needs an enabled target and another enabled path'))
                        unchanged = False if pulse else controller.up[value['path']]==value['up']
                        controller.busy = not unchanged
                    if not unchanged:
                        if pulse:
                            with controller.lock:
                                if controller.pulse_start_ns is None:
                                    controller.pulse_start_ns=time.monotonic_ns()
                                pulse_id=controller.pulses+1
                            try:
                                controller.change(value['path'],False,origin='auto',pulse_id=pulse_id,selection=value.get('selection'))
                                controller.stopped.wait(value['duration_ms']/1000)
                            finally:
                                # Restore even on shutdown, disconnected UI, or failed down verification.
                                controller.change(value['path'],True,origin='auto',pulse_id=pulse_id)
                            with controller.lock:
                                controller.pulses+=1
                        else:
                            controller.change(value['path'],value['up'])
                    with controller.lock:
                        controller.busy = False
                    self.reply(200,controller.snapshot())
                except Exception as exc:
                    with controller.lock:
                        controller.error = str(exc)
                    controller.stopped.set()
                    self.reply(503,dict(error=str(exc)))
                finally:
                    with controller.lock:
                        controller.busy = False
                    controller.actions.release()
        if self.path.exists():
            raise FileExistsError('Existing network socket refused')
        self.server = ThreadingUnixServer(str(self.path),Handler)
        os.chown(self.path,os.geteuid(),Path(directory).stat().st_gid)
        os.chmod(self.path,0o660)
        self.inode = self.path.stat().st_ino
        self.worker = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.worker.start()

    def snapshot(self):
        with self.lock:
            return dict(paths={p:dict(profile,up=self.up[p]) for p,profile in self.profiles.items()},
                busy=self.busy,error=self.error,changes=len(self.events),auto_supported=True,
                pulses=self.pulses,pulse_rate=(self.pulses/((time.monotonic_ns()-self.pulse_start_ns)/1e9) if self.pulse_start_ns and self.pulses else 0),
                scope='Verified kernel-emulator state; capacities/RTT are configured profiles, not live throughput measurements.')

    def change(self,path,up,*,origin='manual',pulse_id=None,selection=None):
        start=time.monotonic_ns()
        self.apply(path,'up' if up else 'down')
        with self.lock:
            before=dict(self.up)
            self.up[path]=up
            self.events.append(dict(event_id=len(self.events)+1,path=path,up=up,
                started_ns=start,verified_ns=time.monotonic_ns(),origin=origin,pulse_id=pulse_id,
                selection=selection,paths_before=before,paths_after=dict(self.up)))

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(3)
        # A finite in-progress mutation must finish before lab cleanup.
        if not self.actions.acquire(timeout=10):
            raise RuntimeError('Network change did not quiesce')
        self.actions.release()
        if self.path.exists() and self.path.stat().st_ino==self.inode:
            self.path.unlink()


class StationProxy:
    def __init__(self,port,unix_path):
        self.stop = threading.Event()
        self.slots = threading.BoundedSemaphore(12)
        proxy = self
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                if not proxy.slots.acquire(blocking=False):
                    return
                target = socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
                try:
                    target.settimeout(2)
                    target.connect(str(unix_path))
                    self.request.settimeout(2)
                    while not proxy.stop.is_set():
                        readable,_,_ = select.select([self.request,target],[],[],.2)
                        for source in readable:
                            data = source.recv(65536)
                            if not data:
                                return
                            (target if source is self.request else self.request).sendall(data)
                except OSError:
                    pass
                finally:
                    target.close()
                    proxy.slots.release()
        class Server(socketserver.ThreadingMixIn,socketserver.TCPServer):
            daemon_threads = True
            block_on_close = False
            allow_reuse_address = False
        self.server = Server(('127.0.0.1',port),Handler)
        self.worker = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.worker.start()

    def close(self):
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(3)
