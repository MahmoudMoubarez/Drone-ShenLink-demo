import pytest
from camera_motion import CameraMotion


def test_continuous_rotation_stop_and_lease():
    now=[0];model=CameraMotion(lambda:now[0])
    model.apply({'id':1,'mode':'velocity','yaw_rate':25,'pitch_rate':0,'expires_ns':300_000_000})
    values=[]
    for i in range(1,6):
        now[0]=i*40_000_000
        values.append(model.snapshot()['yaw'])
    assert values==pytest.approx([1,2,3,4,5])
    model.apply({'id':2,'mode':'velocity','yaw_rate':0,'pitch_rate':0,'expires_ns':500_000_000})
    now[0]=800_000_000
    assert model.snapshot()['yaw']==pytest.approx(5)
    model.apply({'id':3,'mode':'velocity','yaw_rate':25,'pitch_rate':0,'expires_ns':1_100_000_000})
    now[0]=2_000_000_000
    assert model.snapshot()['yaw']==pytest.approx(12.5)
    now[0]=3_000_000_000
    assert model.snapshot()['yaw']==pytest.approx(12.5)


def test_expired_backlog_cannot_restart_motion_and_mouse_uses_current_pose():
    now=[1_000_000_000];model=CameraMotion(lambda:now[0])
    model.apply({'id':1,'mode':'velocity','yaw_rate':25,'pitch_rate':0,'expires_ns':999_000_000})
    now[0]+=40_000_000
    assert model.snapshot()['yaw']==0
    assert model.snapshot()['expired_commands']==1
    model.apply({'id':2,'mode':'absolute','yaw':20,'pitch':74})
    model.apply({'id':3,'mode':'delta','yaw_delta':3,'pitch_delta':5})
    assert model.snapshot()['yaw']==23
    assert model.snapshot()['pitch']==75
    with pytest.raises(ValueError):
        model.apply({'id':3,'yaw':0,'pitch':0})


def test_mouse_target_is_continuous_latest_wins_and_expires():
    now=[0];model=CameraMotion(lambda:now[0])
    model.apply({'id':1,'mode':'target','yaw':60,'pitch':20,'expires_ns':300_000_000})
    assert model.snapshot()['yaw']==0
    angles=[]
    for i in range(1,6):
        now[0]=i*40_000_000
        angles.append(model.snapshot()['yaw'])
    assert all(0<b-a<=3.6 for a,b in zip([0]+angles,angles))
    before=angles[-1]
    model.apply({'id':2,'mode':'target','yaw':-20,'pitch':0,'expires_ns':500_000_000})
    assert model.snapshot()['yaw']==before
    assert model.target==(-20,0)
    now[0]=600_000_000;stopped=model.snapshot()['yaw']
    now[0]=900_000_000;assert model.snapshot()['yaw']==stopped
    model.apply({'id':3,'mode':'target','yaw':100,'pitch':0,'expires_ns':899_000_000})
    now[0]+=40_000_000;assert model.snapshot()['yaw']==stopped


def test_mouse_release_and_wrap_and_arrow_regression():
    now=[0];model=CameraMotion(lambda:now[0])
    model.apply({'id':1,'mode':'absolute','yaw':179,'pitch':0})
    model.apply({'id':2,'mode':'target','yaw':-179,'pitch':0,'expires_ns':300_000_000})
    now[0]=100_000_000;pose=model.snapshot()['yaw']
    assert pose>179 or pose<-179
    model.apply({'id':3,'mode':'velocity','yaw_rate':0,'pitch_rate':0,'expires_ns':400_000_000})
    now[0]=200_000_000;assert model.snapshot()['yaw']==pose
    model.apply({'id':4,'mode':'velocity','yaw_rate':25,'pitch_rate':0,'expires_ns':500_000_000})
    now[0]=240_000_000
    assert model.snapshot()['yaw']==pytest.approx((pose+1+180)%360-180)
