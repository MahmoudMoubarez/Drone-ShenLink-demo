import numpy as np
import pytest
from panorama import Projector


def test_projection_orientation_and_wrap():
    p = Projector(101,101)
    shape = (180,360,3)
    u,v = p.maps(shape,0,0)
    assert abs(u[50,50]-180.5)<.01
    assert abs(v[50,50]-89.5)<.01
    right,_ = p.maps(shape,90,0)
    assert abs(right[50,50]-u[50,50]-90)<.01
    _,up = p.maps(shape,0,30)
    assert up[50,50]<v[50,50]
    a = p.maps(shape,-180,0)
    b = p.maps(shape,180,0)
    assert np.allclose(a[0],b[0],atol=.001)


def test_render_and_bad_angles():
    p = Projector(100,60)
    image = np.full((180,360,3),127,dtype=np.uint8)
    assert np.all(p.render(image,179,0)==127)
    with pytest.raises(ValueError):
        p.maps(image.shape,0,90)
    with pytest.raises(ValueError):
        p.maps(image.shape,float('nan'),0)
