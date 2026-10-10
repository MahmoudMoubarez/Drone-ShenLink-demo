"""Temporary stage-three viewer. Not the final GUI or a performance benchmark."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import math
import mimetypes
from pathlib import Path
import secrets
import threading
import time


HTML = r'''<!doctype html><html><meta charset="utf-8"><title>ShenLink · camera check</title>
<style>body{margin:24px auto;max-width:1000px;font:16px system-ui;background:#f5f7f3;color:#192722}h1{font-size:24px}img{display:block;width:min(100%,640px);aspect-ratio:16/9;background:#ddd;border-radius:12px;touch-action:none;user-select:none;-webkit-user-drag:none;cursor:grab}img.dragging{cursor:grabbing}button{padding:12px;margin:5px;font:inherit}#state{white-space:pre-wrap}small{color:#61746a}</style>
<h1>Station · robot camera test</h1><p>Recorded 360° source → robot projection → ShenLink → station.</p>
<img id="image" draggable="false"><p>Hold arrows, or hold the left mouse button and drag. No local camera rendering.</p>
<div><button data-key="ArrowLeft">←</button><button data-key="ArrowUp">↑</button><button data-key="ArrowDown">↓</button><button data-key="ArrowRight">→</button><button id="reset">Centre</button></div>
<p id="state">Waiting for received frames and sensors…</p>
<small>The small black/white strip is an execution witness inside the transmitted video. This test has no cuts or network shaping yet.</small>
<script>
const token='__TOKEN__',image=document.getElementById('image'),state=document.getElementById('state');
const keys=new Set();let dx=0,dy=0,dirty=false,centre=false,velocityDirty=false,busy=false,drag=null;
function move(x,y){dx=Math.max(-180,Math.min(180,dx+x));dy=Math.max(-180,Math.min(180,dy+y));dirty=true;}
function press(key){keys.add(key);velocityDirty=true;}
function release(key){if(keys.delete(key))velocityDirty=true;}
window.addEventListener('keydown',e=>{if(e.key.startsWith('Arrow')){e.preventDefault();press(e.key);}});
window.addEventListener('keyup',e=>release(e.key));
window.addEventListener('blur',()=>{keys.clear();velocityDirty=true;drag=null;image.classList.remove('dragging');});
document.querySelectorAll('[data-key]').forEach(b=>{b.onpointerdown=e=>{e.preventDefault();b.setPointerCapture(e.pointerId);press(b.dataset.key);};b.onpointerup=b.onpointercancel=b.onlostpointercapture=()=>release(b.dataset.key);});
image.draggable=false;image.ondragstart=e=>e.preventDefault();
image.style.touchAction='none';image.onpointerdown=e=>{if(e.button!==0)return;e.preventDefault();drag=[e.clientX,e.clientY];image.classList.add('dragging');image.setPointerCapture(e.pointerId);};
image.onpointermove=e=>{if(drag){move((drag[0]-e.clientX)*.15,(e.clientY-drag[1])*.15);drag=[e.clientX,e.clientY];}};
image.onpointerup=image.onpointercancel=image.onlostpointercapture=()=>{drag=null;image.classList.remove('dragging');};
document.getElementById('reset').onclick=()=>{keys.clear();dx=dy=0;dirty=false;centre=true;velocityDirty=false;};
setInterval(async()=>{
 if(busy)return;let command;
 if(centre){command={mode:'absolute',yaw:0,pitch:0};centre=false;}
 else if(dirty){command={mode:'delta',yaw_delta:dx,pitch_delta:dy};dx=dy=0;dirty=false;}
 else if(keys.size||velocityDirty){command={mode:'velocity',yaw_rate:(keys.has('ArrowRight')-keys.has('ArrowLeft'))*25,pitch_rate:(keys.has('ArrowUp')-keys.has('ArrowDown'))*25};velocityDirty=false;}
 else return;
 busy=true;
 try{const r=await fetch('/command',{method:'POST',headers:{'Content-Type':'application/json','X-Session':token},body:JSON.stringify(command)});if(!r.ok)throw Error(await r.text());}
 catch(e){state.textContent='Command failed: '+e.message;}finally{busy=false;}
},50);
async function refresh(){try{
 const r=await fetch('/state');if(!r.ok)throw Error('Status HTTP '+r.status);const s=await r.json();
 state.textContent=(s.stale?'STALE / no fresh video':'RECEIVING VIDEO')+' · SDK '+(s.sdk_receive_fps??0).toFixed(1)+' FPS · preview encoder '+(s.preview_encode_fps??0).toFixed(1)+' FPS · frames '+s.frames+'\nReceived sensors: yaw '+(s.sensors.yaw?.degrees??'—')+'° · pitch '+(s.sensors.pitch?.degrees??'—')+'°\nCommand sent '+s.sent+' · sensor-confirmed '+(s.sensors.yaw?.command_id??'—')+' · witnessed in video '+s.witness;
 if(s.errors.length)state.textContent+='\nERROR: '+s.errors.join('; ');
 if(s.preview_error)state.textContent+='\nPreview error: '+s.preview_error;
 image.style.opacity=s.stale?'.4':'1';
 if(!s.stale&&!image.dataset.streaming){image.src='/stream';image.dataset.streaming='1';}
 }catch(e){state.textContent='Disconnected: '+e.message;image.style.opacity='.4';}setTimeout(refresh,350);}
image.onerror=()=>{delete image.dataset.streaming;};
refresh();</script></html>'''


def serve(sender, snapshot, latest, seconds, port, stopped, *, ui_dir=None, unix_path=None, network_dir=None):
    token = secrets.token_hex(24)
    guard = threading.Lock()
    sent = [0]
    tickets = []
    command_events = []
    network = None
    if network_dir:
        from gui_bridge import NetworkClient
        network = NetworkClient(network_dir)
    viewer_stopped=threading.Event()
    cache_guard=threading.Condition()
    cached={'jpeg':None,'version':0,'encoded_at':0,'frame':None,'error':None}
    encode_times=[]
    def encode():
        while not stopped.is_set() and not viewer_stopped.is_set():
            value=latest()
            identity=(value.session_id,value.frame_id,value.arrival_time) if value else None
            if value is None or identity==cached['frame']:
                viewer_stopped.wait(.005)
                continue
            try:
                buffer=BytesIO()
                value.to_image().save(buffer,format='JPEG',quality=90)
                with cache_guard:
                    cached.update(jpeg=buffer.getvalue(),version=cached['version']+1,
                                  frame=identity,encoded_at=time.monotonic())
                    encode_times.append(time.monotonic())
                    del encode_times[:-150]
                    cache_guard.notify_all()
            except Exception as exc:
                with cache_guard:
                    cached['error']=str(exc)
                viewer_stopped.wait(.05)
    encoder=threading.Thread(target=encode,name='preview-latest-jpeg',daemon=True)
    encoder.start()
    def reply(handler, code, value, kind='application/json'):
        data = value if isinstance(value,bytes) else json.dumps(value).encode()
        handler.send_response(code)
        handler.send_header('Content-Type',kind)
        handler.send_header('Content-Length',str(len(data)))
        handler.send_header('Cache-Control','no-store')
        handler.end_headers()
        handler.wfile.write(data)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def do_GET(self):
            from urllib.parse import urlsplit
            self.path = urlsplit(self.path).path
            if self.path in ('/','/index.html') or (ui_dir and self.path in ('/robot.html','/library.html')):
                page = 'index.html' if self.path=='/' else self.path[1:]
                html = (Path(ui_dir)/page).read_text(encoding='utf-8') if ui_dir else HTML
                return reply(self,200,html.replace('__TOKEN__',token).encode(),'text/html; charset=utf-8')
            if ui_dir and self.path[1:] in {'live.js','live.css','navigation.css','network-profiles.css','network-profiles.js','drone-qx3.png','robot-controls.css','robot-controls.js','robot-live.js','library.css','library-live.js'}:
                file = Path(ui_dir)/self.path[1:]
                if file.is_file():
                    return reply(self,200,file.read_bytes(),mimetypes.guess_type(str(file))[0] or 'application/octet-stream')
            if self.path=='/network':
                if not network:
                    return reply(self,503,dict(error='No kernel network controller in this run'))
                try:
                    return reply(self,200,network.request('GET','/state'))
                except Exception as exc:
                    return reply(self,503,dict(error=str(exc)))
            value = latest()
            if self.path=='/state':
                state = snapshot()
                state.update(sent=sent[0],stale=value is None or time.monotonic()-value.arrival_time>1)
                with guard:
                    state['receipts'] = {str(i):ticket.status.value for i,ticket in tickets}
                with cache_guard:
                    recent=[t for t in encode_times if t>=time.monotonic()-3]
                    state['preview_encode_fps']=(len(recent)-1)/(recent[-1]-recent[0]) if len(recent)>1 else 0
                    state['preview_error']=cached['error']
                return reply(self,200,state)
            if self.path=='/frame':
                with cache_guard:
                    jpeg=cached['jpeg']
                if jpeg:
                    return reply(self,200,jpeg,'image/jpeg')
            if self.path=='/stream':
                self.connection.settimeout(2)
                self.send_response(200)
                self.send_header('Content-Type','multipart/x-mixed-replace; boundary=frame')
                self.send_header('Cache-Control','no-store')
                self.end_headers()
                version=0
                try:
                    while not stopped.is_set() and not viewer_stopped.is_set():
                        with cache_guard:
                            cache_guard.wait_for(lambda: cached['version']!=version or stopped.is_set() or viewer_stopped.is_set(),timeout=.5)
                            if stopped.is_set() or viewer_stopped.is_set():
                                break
                            if version==cached['version'] or time.monotonic()-cached['encoded_at']>1:
                                continue
                            version,jpeg=cached['version'],cached['jpeg']
                        self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(jpeg)).encode()+b'\r\n\r\n'+jpeg+b'\r\n')
                        self.wfile.flush()
                except (OSError,TimeoutError):
                    pass
                return
            reply(self,404,{'error':'No frame / unknown route'})
        def do_POST(self):
            if self.path not in ('/command','/network') or self.headers.get('X-Session')!=token:
                return reply(self,403,{'error':'Invalid session'})
            try:
                size = int(self.headers.get('Content-Length','0'))
                if not 1<=size<=512:
                    raise ValueError('Invalid command size')
                value = json.loads(self.rfile.read(size))
                if self.path=='/network':
                    if not network:
                        return reply(self,503,dict(error='No kernel network controller'))
                    if 'duration_ms' in value:
                        if set(value)!={'path','duration_ms'}:
                            raise ValueError('Invalid pulse fields')
                        current=snapshot()
                        if current.get('video_age_ms') is None or current['video_age_ms']>500:
                            raise ValueError('Auto paused: no fresh delivered frame to identify active path')
                        active=current.get('last_frame_path')
                        if active not in ('a','b','c'):
                            raise ValueError('Unknown delivered frame path')
                        result=network.request('POST','/pulse',dict(path=active,duration_ms=value['duration_ms'],
                            selection=dict(sampled_ns=time.monotonic_ns(),frame_id=current['last_frame_id'],path=active)))
                        result['cut_frame_path']=active
                        return reply(self,200,result)
                    return reply(self,200,network.request('POST','/path',value))
                mode=value.get('mode','absolute')
                fields={'absolute':('yaw','pitch',180,75),
                        'target':('yaw','pitch',180,75),
                        'delta':('yaw_delta','pitch_delta',180,180),
                        'velocity':('yaw_rate','pitch_rate',25,25)}
                if mode not in fields:
                    raise ValueError('Invalid camera command mode')
                a,b,xlimit,ylimit=fields[mode]
                x,y=value[a],value[b]
                if any(type(v) not in (int,float) or not math.isfinite(v) for v in (x,y)) or abs(x)>xlimit or abs(y)>ylimit:
                    raise ValueError('Invalid camera command values')
                with guard:
                    index = sent[0]+1
                    sent_ns = time.monotonic_ns()
                    ticket = sender.send_object({'id':index,'mode':mode,a:x,b:y,
                        'expires_ns':sent_ns+300_000_000})
                    sent[0] = index
                    tickets.append((index,ticket))
                    command_events.append((dict(id=index,mode=mode,sent_ns=sent_ns),ticket))
                    del tickets[:-128]
                reply(self,202,{'id':index,'local_status':ticket.status.value})
            except Exception as exc:
                reply(self,400,{'error':str(exc)})
    if unix_path:
        from gui_bridge import ThreadingUnixServer
        if Path(unix_path).exists():
            raise FileExistsError('Existing station socket refused')
        server = ThreadingUnixServer(str(unix_path),Handler)
        socket_inode = Path(unix_path).stat().st_ino
    else:
        server = ThreadingHTTPServer(('127.0.0.1',port),Handler)
    worker = threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    print(json.dumps({'manual_preview':f'http://127.0.0.1:{port}','seconds':seconds}),flush=True)
    try:
        stopped.wait(seconds)
    finally:
        viewer_stopped.set()
        with cache_guard:
            cache_guard.notify_all()
        server.shutdown()
        server.server_close()
        worker.join(5)
        encoder.join(5)
        if unix_path and Path(unix_path).exists() and Path(unix_path).stat().st_ino==socket_inode:
            Path(unix_path).unlink()
    with guard:
        evidence=[dict(row,receipt=ticket.status.value) for row,ticket in command_events]
    return [{'manual_commands_sent':sent[0], 'visual_feedback_required':True,
             'command_events':evidence,'receipt_note':'Ticket status sampled at viewer shutdown; not receipt arrival time.'}]
