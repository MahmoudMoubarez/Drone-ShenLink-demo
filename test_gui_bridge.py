import json
from pathlib import Path
import sys
import threading
import time
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parent))
from gui_bridge import NetworkControl,NetworkClient,UnixHTTP
from gate_bridge import atomic_json
from network_gate import PROFILES

pytestmark = pytest.mark.skipif(sys.platform!='linux',reason='Unix HTTP bridge is a Pi/Linux component')


def test_network_control_session_scope_and_idempotency(tmp_path):
    calls = []
    stopped = threading.Event()
    atomic_json(tmp_path/'network-session.json',dict(token='test-session'))
    control = NetworkControl(tmp_path,'test-session',PROFILES,lambda p,s:calls.append((p,s)),stopped)
    client = NetworkClient(tmp_path)
    def raw(value,token='test-session'):
        connection = UnixHTTP(str(tmp_path/'network.sock'),timeout=3)
        try:
            connection.request('POST','/path',body=json.dumps(value),headers={'X-Session':token})
            response = connection.getresponse()
            response.read()
            return response.status
        finally:
            connection.close()
    try:
        assert raw(dict(path='a',up=False),token='wrong')==403
        assert raw(dict(path='eth0',up=False))==400
        assert raw(dict(path='a',up='false'))==400
        assert raw(dict(path='a',up=False,command='rm'))==400
        assert calls==[]
        assert client.request('POST','/path',dict(path='a',up=False))['paths']['a']['up'] is False
        client.request('POST','/path',dict(path='a',up=False))
        assert calls==[('a','down')]
        client.request('POST','/path',dict(path='a',up=True))
        assert calls==[('a','down'),('a','up')]
        assert client.request('GET','/state')['auto_supported'] is True
        stopped.set()
        assert raw(dict(path='b',up=False))==503
        assert len(calls)==2
    finally:
        control.close()


def test_auto_pulse_restores_and_refuses_only_remaining_path(tmp_path):
    calls=[]
    atomic_json(tmp_path/'network-session.json',dict(token='test-session'))
    control=NetworkControl(tmp_path,'test-session',PROFILES,lambda p,s:calls.append((p,s)),threading.Event())
    client=NetworkClient(tmp_path)
    try:
        result=client.request('POST','/pulse',dict(path='a',duration_ms=50))
        assert calls==[('a','down'),('a','up')]
        assert result['paths']['a']['up'] and not result['busy'] and result['pulses']==1
        assert result['pulse_rate']>0
        assert [row['origin'] for row in control.events]==['auto','auto']
        assert [row['pulse_id'] for row in control.events]==[1,1]
        assert control.events[0]['paths_after']==dict(a=False,b=True,c=True)
        assert control.events[1]['paths_after']==dict(a=True,b=True,c=True)
        started=time.monotonic()
        long_result=client.request('POST','/pulse',dict(path='a',duration_ms=5000))
        assert time.monotonic()-started>=5 and long_result['paths']['a']['up']
        with pytest.raises(RuntimeError):
            client.request('POST','/pulse',dict(path='a',duration_ms=5001))
        client.request('POST','/path',dict(path='a',up=False))
        client.request('POST','/path',dict(path='b',up=False))
        with pytest.raises(RuntimeError,match='another enabled path'):
            client.request('POST','/pulse',dict(path='c',duration_ms=50))
        with pytest.raises(RuntimeError):
            client.request('POST','/pulse',dict(path='c',duration_ms=True))
        assert ('c','down') not in calls
    finally:
        control.close()
    assert not (tmp_path/'network.sock').exists()


def test_conflicting_changes_do_not_race(tmp_path):
    started,release = threading.Event(),threading.Event()
    calls = []
    def apply(path,state):
        calls.append((path,state))
        started.set()
        assert release.wait(3)
    atomic_json(tmp_path/'network-session.json',dict(token='test-session'))
    control = NetworkControl(tmp_path,'test-session',PROFILES,apply,threading.Event())
    client = NetworkClient(tmp_path)
    errors = []
    def change():
        try:
            client.request('POST','/path',dict(path='a',up=False))
        except Exception as exc:
            errors.append(str(exc))
    worker = threading.Thread(target=change)
    try:
        worker.start()
        assert started.wait(2)
        assert client.request('GET','/state')['busy'] is True
        with pytest.raises(RuntimeError,match='in progress'):
            client.request('POST','/path',dict(path='b',up=False))
        release.set()
        worker.join(3)
        assert not errors and calls==[('a','down')]
    finally:
        release.set()
        worker.join(3)
        control.close()


def test_auto_pulse_shutdown_restores(tmp_path):
    stopped=threading.Event();calls=[]
    def apply(path,state):
        calls.append((path,state))
        if state=='down':stopped.set()
    atomic_json(tmp_path/'network-session.json',dict(token='test-session'))
    control=NetworkControl(tmp_path,'test-session',PROFILES,apply,stopped)
    try:
        result=NetworkClient(tmp_path).request('POST','/pulse',dict(path='c',duration_ms=5000))
        assert calls==[('c','down'),('c','up')] and result['paths']['c']['up']
    finally:
        control.close()
