import math
import numpy as np
import pytest
from lorasim.config import Radio, Channel
from lorasim.phy import airtime_ns,links,destroyed_by,GOURSAUD


def test_known_airtime_and_cr():
    assert airtime_ns(20,Radio(sf=7))==56576000
    assert airtime_ns(20,Radio(sf=12))==1318912000
    assert airtime_ns(20,Radio(sf=7,cr=4))>airtime_ns(20,Radio(sf=7,cr=1))


def test_reference_distance_floor_and_delay():
    power,loss,distance,delay=links(np.zeros(3),np.array([[0,0,0],[.5,0,0],[1000,0,0]]),14,Channel())
    assert power[0]==power[1]==6.3
    assert power[2]==pytest.approx(14-7.7-3.76*30)
    assert delay[2]==3336


@pytest.mark.parametrize('sf',range(7,13))
@pytest.mark.parametrize('interferer',range(7,13))
def test_capture_threshold(sf,interferer):
    threshold=GOURSAUD[sf-7,interferer-7]
    e=np.zeros(6);e[interferer-7]=10**(-(threshold+.001)/10)
    assert destroyed_by(sf,1,e)==0
    e[interferer-7]=10**(-(threshold-.001)/10)
    assert destroyed_by(sf,1,e)==interferer


def test_aloha_is_same_sf_only():
    assert destroyed_by(7,1,[0,1e20,0,0,0,0],'aloha')==0
    assert destroyed_by(7,1,[1e-30,0,0,0,0,0],'aloha')==7
