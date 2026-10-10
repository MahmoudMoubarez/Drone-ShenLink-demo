import json
from pathlib import Path
import sys
import threading

sys.path.insert(0,str(Path(__file__).resolve().parent))
from gate_bridge import atomic_json, gate_session
from network_gate import load_lab, phase_metrics, PROFILES


def test_owned_plan_and_directional_caps():
    lab = load_lab()
    plan = lab.pure_plan('c548c601')
    assert set(plan['addresses']) == set('abc')
    assert len(plan['namespaces']) == 5
    assert all(name.startswith('shm-') for name in plan['namespaces'].values())
    assert lab.JOURNAL_PARENT_DIR == '/run/shenlink-mission-lab'
    for path in 'abc':
        layout = lab.PATHS[path]
        for direction,dev in [('up',layout['router_s']),('down',layout['router_r'])]:
            commands = lab.shaping_commands('test',dev,'baseline',path)
            rate = commands[1][commands[1].index('rate')+1]
            assert rate == str(PROFILES[path]['uplink_mbps' if direction=='up' else 'downlink_mbps'])+'mbit'
            assert commands[2][commands[2].index('delay')+1] == str(PROFILES[path]['rtt_ms']//2)+'ms'
    assert 'tools/network_lab.py' in lab.implementation_hashes()
    # Only owned test devices. Never management eth0/wlan0/tailscale0.
    for command in plan['commands']['setup']:
        assert not any(value in ('eth0','wlan0','tailscale0') for value in command)


def test_invalid_scope_refused():
    import pytest
    lab = load_lab()
    for path,dev,profile in [('a','eth0','baseline'),('d','ar','baseline'),('a','ar','unknown')]:
        with pytest.raises(lab.Unsafe):
            lab.shaping_commands('test',dev,profile,path)
    with pytest.raises(lab.Unsafe):
        lab.pure_plan('../other')


def test_unique_frames_not_quality_upgrade_and_gap():
    rows = [dict(id=1,session='s',path='a',received_ns=100_000_000,delivery_kind='new_frame'),
            dict(id=1,session='s',path='b',received_ns=110_000_000,delivery_kind='quality_upgrade'),
            dict(id=2,session='s',path='b',received_ns=900_000_000,delivery_kind='new_frame')]
    result = phase_metrics(dict(recent_frames=rows,sensors={'yaw':dict(received_ns=950_000_000)}),0,1_000_000_000)
    assert result['new_frames']==2
    assert result['sdk_fps']==2
    assert result['max_gap_ms']==800
    assert result['sensor_age_ms']['yaw']==50
    assert result['delivered_paths']==['a','b']


def test_dark_window_reports_no_frames_and_stale_sensors():
    result = phase_metrics(dict(recent_frames=[],sensors={'yaw':dict(received_ns=0)}),1_000_000_000,3_000_000_000)
    assert result['new_frames']==0
    assert result['max_gap_ms']==2000
    assert result['sensor_age_ms']['yaw']==3000


def test_bridge_only_sends_once_and_collects_real_snapshot(tmp_path):
    directory = tmp_path/'endpoints'
    directory.mkdir()
    atomic_json(tmp_path/'control.json',dict(id=1,yaw=20,pitch=0))
    stopped = threading.Event()
    calls = []
    class Status:
        value = 'RECEIVED'
    class Ticket:
        status = Status()
    class Sender:
        def send_object(self,value):
            calls.append(value)
            return Ticket()
    count = 0
    def snapshot():
        nonlocal count
        count+=1
        if count==3:
            stopped.set()
        return dict(errors=[],frames=7,sensors={'yaw':dict(degrees=20)})
    checks = gate_session(Sender(),snapshot,2,stopped,directory)
    assert len(calls)==len(checks)==1
    assert calls[0]['expires_ns']>checks[0]['sent_ns']
    state = json.loads((directory/'station-state.json').read_text())
    assert state['frames']==7 and state['tickets']=={'1':'RECEIVED'}
