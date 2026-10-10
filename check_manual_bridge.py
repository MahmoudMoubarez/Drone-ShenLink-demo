"""Check the temporary browser bridge using HTTP, real SDK feedback and video."""
import json
import re
import time
import sys
from urllib.request import Request, urlopen

base = sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8791'
deadline = time.monotonic()+15
while True:
    try:
        with urlopen(base,timeout=2) as response:
            html = response.read().decode()
        break
    except OSError:
        if time.monotonic()>deadline:
            raise
        time.sleep(.2)
token = re.search("const token='([0-9a-f]+)'",html)[1]
for index,(yaw,pitch) in enumerate(((20,10),(-20,-10),(0,0)),1):
    command = Request(base+'/command',data=json.dumps({'yaw':yaw,'pitch':pitch}).encode(),
                      headers={'X-Session':token,'Content-Type':'application/json'})
    with urlopen(command,timeout=3) as response:
        assert json.load(response)['id']==index
    until = time.monotonic()+4
    while True:
        with urlopen(base+'/state',timeout=3) as response:
            state = json.load(response)
        if (state['sensors'].get('yaw',{}).get('command_id')==index and state['witness']==index):
            assert state['sensors']['yaw']['degrees']==yaw
            assert state['sensors']['pitch']['degrees']==pitch
            assert not state['stale'] and not state['errors']
            break
        assert time.monotonic()<until,state
        time.sleep(.05)
def post(value):
    command=Request(base+'/command',data=json.dumps(value).encode(),
                    headers={'X-Session':token,'Content-Type':'application/json'})
    with urlopen(command,timeout=3) as response:
        return json.load(response)['id']
def state_now():
    with urlopen(base+'/state',timeout=3) as response:
        return json.load(response)
positions=[]
for _ in range(8):
    post({'mode':'velocity','yaw_rate':25,'pitch_rate':0})
    time.sleep(.06)
    positions.append(state_now()['sensors']['yaw']['degrees'])
assert len(set(positions))>=3,positions
assert all(b>=a and b-a<6 for a,b in zip(positions,positions[1:])),positions
stop_id=post({'mode':'velocity','yaw_rate':0,'pitch_rate':0})
until=time.monotonic()+3
while state_now()['sensors']['yaw']['command_id']<stop_id:
    assert time.monotonic()<until
    time.sleep(.04)
stopped_yaw=state_now()['sensors']['yaw']['degrees']
time.sleep(.25)
assert abs(state_now()['sensors']['yaw']['degrees']-stopped_yaw)<.1
post({'mode':'velocity','yaw_rate':25,'pitch_rate':0})
time.sleep(.5)
expired_yaw=state_now()['sensors']['yaw']['degrees']
assert 0<expired_yaw-stopped_yaw<=8, (stopped_yaw,expired_yaw)
time.sleep(.25)
assert abs(state_now()['sensors']['yaw']['degrees']-expired_yaw)<.1
print('PASS: continuous sensor angle progression, explicit stop and automatic lease-expiry stop.')
with urlopen(base+'/frame',timeout=3) as response:
    assert response.headers['Content-Type']=='image/jpeg'
    assert response.read(2)==b'\xff\xd8'
with urlopen(base+'/stream',timeout=5) as response:
    assert 'multipart/x-mixed-replace' in response.headers['Content-Type']
    for _ in range(3):
        line=response.readline()
        while line!=b'--frame\r\n':
            assert line
            line=response.readline()
        assert response.readline()==b'Content-Type: image/jpeg\r\n'
        size=int(response.readline().split(b':')[1])
        assert response.readline()==b'\r\n'
        content=response.read(size)
        assert content.startswith(b'\xff\xd8')
        assert response.read(2)==b'\r\n'
print('PASS: HTTP commands executed on robot, sensors and video witness returned through SDK, JPEG available.')
