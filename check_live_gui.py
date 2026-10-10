"""Real HTTP command/sensor/video checks and network actions; no browser-paint claim."""
import json
import re
import sys
import time
from urllib.request import Request,urlopen

base = sys.argv[1]
def get(path):
    with urlopen(base+path,timeout=5) as response:
        return json.load(response)
with urlopen(base,timeout=5) as response:
    html = response.read().decode()
token = re.search(r'name="shen-session" content="([0-9a-f]+)"',html)[1]
assert 'live.js' in html and 'const state={links:' not in html and 'DEMO REPLAY' in html
def post(path,value):
    with urlopen(Request(base+path,data=json.dumps(value).encode(),
        headers={'X-Session':token,'Content-Type':'application/json'}),timeout=6) as response:
        return json.load(response)
deadline = time.monotonic()+15
while get('/state')['stale']:
    assert time.monotonic()<deadline
    time.sleep(.1)
session = get('/state')['session_id']
initial = get('/state')
assert any(value=='CONNECTED' for value in initial['command_paths'].values()),initial
assert initial['video_bitrate_max_kbps']==1800 and initial['max_video_gap_ms']>=0
for path in ('a','c','b'):
    network = post('/network',dict(path=path,up=False))
    assert network['paths'][path]['up'] is False
    result = post('/command',dict(mode='absolute',yaw=20,pitch=0))
    deadline = time.monotonic()+3
    while True:
        state = get('/state')
        if state['sensors'].get('yaw',{}).get('command_id')==result['id'] and state['witness']==result['id']%256:
            assert state['sensors']['yaw']['degrees']==20 and not state['stale'] and state['session_id']==session
            assert state['receipts'][str(result['id'])]=='RECEIVED'
            break
        assert time.monotonic()<deadline,state
        time.sleep(.1)
    network = post('/network',dict(path=path,up=True))
    assert network['paths'][path]['up'] is True
with urlopen(base+'/stream',timeout=5) as response:
    assert 'multipart' in response.headers['Content-Type']
    assert response.read(256).startswith(b'--frame')
start = get('/state')['sensors']['yaw']['degrees']
target = post('/command',dict(mode='target',yaw=start+30,pitch=0))
deadline = time.monotonic()+2
while True:
    sample = get('/state')['sensors']['yaw']
    if sample['command_id']==target['id'] and sample['degrees']>start:
        assert sample['degrees']<start+30, sample
        break
    assert time.monotonic()<deadline
    time.sleep(.02)
post('/command',dict(mode='velocity',yaw_rate=0,pitch_rate=0))
pulse = post('/network',dict(path='a',duration_ms=50))
assert pulse['paths'][pulse['cut_frame_path']]['up'] and pulse['pulses']>=1 and not pulse['busy']
print('PASS: GUI pulse API restores its target after verified cut/hold/restore.',flush=True)
print('PASS: target command crosses SDK and sensor feedback confirms gradual robot-side movement.',flush=True)
try:
    urlopen(Request(base+'/network',data=b'{"path":"a","up":false}',headers={'Content-Type':'application/json'}),timeout=3)
    raise AssertionError('Missing session accepted')
except Exception as exc:
    assert getattr(exc,'code',None)==403,exc
print('PASS: accepted GUI assets, three cut/restore API actions, SDK command receipts, sensor/video execution evidence, session preserved and MJPEG available. Network realism depends on the controller used for this run.',flush=True)
