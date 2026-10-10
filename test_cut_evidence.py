from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cut_evidence import analyze


def event(path, up, start, verified, **extra):
    return dict(path=path,up=up,started_ns=start*1_000_000,verified_ns=verified*1_000_000,**extra)


def frame(id, path, ms, kind='new_frame'):
    return dict(id=id,path=path,session='test',received_ns=ms*1_000_000,delivery_kind=kind)


def run(events, frames, sensors=(), commands=(), executions=(), end=100):
    return analyze(events,frames,sensors,commands,executions,end*1_000_000)


def test_new_frames_not_quality_upgrades_and_exclude_mutation_periods():
    events=[event('a',False,10,20,origin='auto',pulse_id=1),event('a',True,70,80)]
    result=run(events,[frame(1,'a',9),frame(2,'a',15),frame(2,'b',21,'quality_upgrade'),
        frame(2,'b',22),frame(3,'b',30),frame(4,'a',75)])
    cut=result['cuts'][0]
    assert result['fresh_frame_count']==4 and result['ignored_frame_callbacks']==2
    assert cut['origin']=='auto' and cut['pulse_id']==1
    assert cut['last_frame_before_verified_cut']['id']==2
    assert cut['first_alternative_frame']['id']==3
    assert cut['last_to_alternative_arrival_gap_ms']==15
    assert cut['verified_cut_to_alternative_ms']==10
    assert cut['verified_down_hold_ms']==50
    assert cut['evidence_window_end_ns']==70_000_000
    assert result['phases'][0]['new_frames']==1
    assert result['phases'][0]['max_no_new_frame_interval_ms']==40
    assert cut['cycle']['start_ns']==10_000_000 and cut['cycle']['end_ns']==80_000_000
    assert cut['cycle']['new_frames']==3
    assert cut['cycle']['minimum_verified_enabled_paths']==2


def test_all_down_is_separate_from_single_path_and_multiple_cuts():
    result=run([event('a',False,10,11),event('b',False,20,21),
        event('c',False,30,31),event('c',True,90,91)],
        [frame(1,'a',5),frame(2,'b',15),frame(3,'c',25),frame(4,'c',95)])
    assert [cut['category'] for cut in result['cuts']]==['single_path_cut','two_paths_cut','all_paths_outage']
    assert result['single_path_cut_count']==1 and result['all_paths_outage_count']==1
    assert result['max_single_path_no_new_frame_interval_ms']==5
    assert result['phases'][2]['max_no_new_frame_interval_ms']==59
    assert not result['cuts'][2]['alternative_delivery_observed']
    assert result['cuts'][0]['restored'] is False
    assert result['cuts'][0]['cycle']['contains_all_paths_outage']
    assert result['max_isolated_single_path_cycle_no_new_frame_interval_ms'] is None


def test_receipt_execution_and_sensor_confirmation_are_distinct():
    commands=[dict(id=1,sent_ns=25_000_000,receipt='RECEIVED'),
        dict(id=2,sent_ns=30_000_000,receipt='RECEIVED'),
        dict(id=3,sent_ns=35_000_000,receipt='PENDING')]
    executions=[dict(id=1,received_ns=26_000_000,executed_ns=27_000_000,outcome='applied'),
        dict(id=2,received_ns=36_000_000,executed_ns=None,outcome='expired')]
    sensors=[dict(axis='yaw',sequence=1,command_id=1,received_ns=28_000_000)]
    result=run([event('a',False,10,20)],[],sensors,commands,executions)
    phase=result['phases'][0]
    assert phase['commands_sent']==3 and phase['receipts']=={'RECEIVED':2,'PENDING':1}
    assert phase['commands'][0]['send_to_apply_ms']==2
    assert phase['commands'][0]['sensor_confirmation_ns']=={'yaw':28_000_000}
    assert phase['commands'][1]['robot_outcome']=='expired'
    assert phase['commands'][1]['send_to_apply_ms'] is None
    assert phase['commands'][2]['robot_outcome']=='unobserved'
    assert phase['sensors']['yaw']['end_age_ms']==72
    assert phase['sensors']['pitch']['end_age_ms'] is None
    assert result['cuts'][0]['first_alternative_frame'] is None


def test_window_edges_and_frames_from_cut_path_are_not_alternative_proof():
    result=run([event('a',False,10,20),event('a',True,50,60)],
        [frame(1,'a',20),frame(2,'a',30),frame(3,'b',50),frame(4,'b',61)])
    cut=result['cuts'][0]
    assert cut['cut_path_was_last_delivered'] is True
    assert cut['first_alternative_frame'] is None
    assert result['single_path_alternative_observed']==0
    assert result['phases'][0]['new_frames']==1
    assert result['phases'][0]['max_no_new_frame_interval_ms']==20


def test_no_cuts_is_valid_and_sessions_do_not_deduplicate_each_other():
    other=dict(frame(1,'b',15),session='restarted')
    result=run([],[frame(1,'a',10),other])
    assert result['cut_count']==0 and result['fresh_frame_count']==2
    assert result['max_single_path_no_new_frame_interval_ms'] is None


def test_command_during_mutation_is_retained_in_cycle_not_stable_phase():
    result=run([event('a',False,10,20),event('a',True,50,60)],[],
        commands=[dict(id=1,sent_ns=15_000_000,receipt='RECEIVED')])
    assert result['cuts'][0]['commands_sent']==0
    assert result['cuts'][0]['cycle']['commands_sent']==1
    assert result['cuts'][0]['cycle']['includes_mutation_periods']
