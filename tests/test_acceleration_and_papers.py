import math
import json
import numpy as np
import pyarrow.parquet as pq
import pytest
from lorasim.config import Profile, Radio
from lorasim.engine import Simulation
from lorasim.interfaces import Observation, Feedback
from lorasim.tracking import SCHEMAS, Tracker
from lorasim.plugins.mobility.waypoints import Waypoints
from lorasim.plugins.selector.lora_mab import LoRaMAB
from lorasim.plugins.selector.linucb_sf import LinUCBSF
from lorasim.plugins.selector.linucb_sf_tp import LinUCBSFTP
from lorasim.plugins.selector.gadr import GADRServer
from lorasim.registry import inventory


def obs(t=0,position=None,**kw):
    return Observation(t,0,0,None,None,None,None,position,**kw)


def compare(a,b):
    for table in SCHEMAS:
        assert pq.read_table(a/table).equals(pq.read_table(b/table)), table


@pytest.mark.parametrize('mobility', ['stationary','random_walk','waypoints','route'])
@pytest.mark.parametrize('speed', [299792458.,1000.])
def test_optimized_exact_parity(tmp_path,mobility,speed):
    # Slow propagation exercises intervening RX window / TX / receive-end events.
    model={'kind':mobility} if mobility!='route' else {'kind':'waypoints','options':{'mode':'route','random_count':5,'speed_m_s':200,'speed_jitter_m_s':10,'pause_s':1,'pause_jitter_s':.3,'repeat':True}}
    p=Profile.model_validate(dict(duration_s=110,seed=7,channel={'speed_m_s':speed},mac={'duty_cycle':False},groups=[dict(count=8,observe_position=True,max_attempts=2,mobility=model,timing={'kind':'periodic','options':{'interval_s':8,'start':{'mode':'uniform','min_s':0,'max_s':2}}})]))
    p.execution.optimized=False
    a=Simulation(p,tmp_path/'a').run()
    p.execution.optimized=True
    b=Simulation(p,tmp_path/'b').run()
    assert a['counts']==b['counts']
    assert a['energy']==b['energy']
    compare(tmp_path/'a',tmp_path/'b')


def test_equal_time_and_cutoff_batch(tmp_path):
    p=Profile.model_validate(dict(duration_s=5,mac={'duty_cycle':False},groups=[dict(count=4,placement='explicit',positions=[[1000,1000,15]]*4,timing={'kind':'periodic','options':{'interval_s':10}})]))
    p.execution.optimized=False
    a=Simulation(p,tmp_path/'a').run()
    p.execution.optimized=True
    b=Simulation(p,tmp_path/'b').run()
    assert a['counts']==b['counts']
    compare(tmp_path/'a',tmp_path/'b')


def test_parallel_writer_parity(tmp_path):
    p=Profile(duration_s=25)
    p.tracking.batch_rows=13
    Simulation(p,tmp_path/'a').run()
    p.execution.writer_processes=2
    Simulation(p,tmp_path/'b').run()
    compare(tmp_path/'a',tmp_path/'b')


def test_writer_failure_propagates(tmp_path):
    w=Tracker(tmp_path,batch_rows=1,processes=1)
    (tmp_path/'events'/'part-000000.tmp').mkdir()
    w.write('events',time_ns=0,event='test')
    with pytest.raises((OSError,IsADirectoryError)):
        w.close()


def test_route_speed_and_dwell():
    m=Waypoints([0,0,0],[100,100],np.random.default_rng(1),mode='route',destinations=[[10,0,0],[10,10,0]],speed_m_s=2,pause_s=10)
    for t,expected in [(2,[4,0,0]),(5,[10,0,0]),(14,[10,0,0]),(17,[10,4,0]),(100,[10,10,0])]:
        np.testing.assert_array_equal(m.position(t),expected)


def test_random_route_reproducible_bounded():
    opts=dict(mode='route',random_count=20,speed_m_s=2,speed_jitter_m_s=.5,pause_mode='uniform',pause_min_s=3,pause_max_s=8,pause_jitter_s=1,repeat=True)
    a=Waypoints([0,0,1.5],[20,10],np.random.default_rng(4),**opts)
    b=Waypoints([0,0,1.5],[20,10],np.random.default_rng(4),**opts)
    assert ((a.targets[:,:2]>=0)&(a.targets[:,:2]<=[20,10])).all()
    assert ((a.pauses>=3)&(a.pauses<=8)).all()
    for t in np.linspace(0,1000,40):
        np.testing.assert_array_equal(a.position(t),b.position(t))
    assert len(a.a)<=41  # no ever-growing path history


def test_repeated_route_allows_instantaneous_jittered_visits():
    m=Waypoints([0,0,0],[100,100],np.random.default_rng(2),mode='route',destinations=[[0,0,0]],pause_s=0,pause_jitter_s=1,repeat=True)
    # Seed 2 starts with negative jitter; zero-duration visits are valid.
    np.testing.assert_array_equal(m.position(10),[0,0,0])
    assert m.a[-1,0]>=10 and len(m.a)<=2


def test_exp3_paper_update_and_missing_ack():
    p=LoRaMAB(np.random.default_rng(1),spreading_factors=[7,8],gamma=.2)
    p.select(obs());a=p.chosen
    p.observe(Feedback(1,True,-80,.1,14,1))
    expected=.8*math.exp(.2)/(1+math.exp(.2))+.1
    assert p.probabilities[a]==pytest.approx(expected)
    previous=p.probabilities.copy()
    p.select(obs());p.observe(Feedback(2,False,None,.1,14,1))
    np.testing.assert_array_equal(previous,p.probabilities)
    p.select(obs());p.observe(Feedback(3,None,None,.1,14,1))
    np.testing.assert_array_equal(previous,p.probabilities)


def test_linucb_equations_and_rank_one():
    p=LinUCBSF(np.random.default_rng(1),ridge=2,epsilon=0)
    r=p.select(obs(position=(0,0,0),attempt=2,max_attempts=4))
    a,x=p.pending
    assert r.sf==12
    assert x[4]==pytest.approx(2/3)
    p.observe(Feedback(1,True,-80,.1,14,1,True,-120,-10,14))
    np.testing.assert_allclose(p.inverse[a],np.linalg.inv(2*np.eye(6)+np.outer(x,x)),rtol=1e-14,atol=1e-14)
    assert p.last_reward==pytest.approx(1-.1*.1)
    r=p.select(obs(10,(10,0,0)))
    assert r.sf>=9  # -10 dB cannot satisfy SF7/8 thresholds plus 2 dB margin
    np.testing.assert_allclose(p.pending[1][1:4],[math.tanh(-1),math.tanh(-6),.25])
    p.observe(Feedback(11,False,None,.1,14,1))
    assert p.snr==-10  # no future GW information on lost DL


def test_linucb_no_false_telemetry_and_joint_label():
    p=LinUCBSF(np.random.default_rng(1))
    p.select(obs());p.observe(Feedback(1,False,None,1,14,1,False,-10,40,14))
    assert p.snr is None
    q=LinUCBSFTP(np.random.default_rng(2))
    assert q.k==18 and q.joint
    assert len({(a.sf,a.tp_dbm) for a in q.actions})==18


def test_gadr_gaussian_filter_and_truncation():
    s=GADRServer((20,'max','avg',True,10.))
    # 19 measurements -10 and an outlier +50: retained mean is -10, not max +50.
    values=[-10.]*19+[50.]
    r=Radio(sf=12,tp_dbm=14)
    noise=-174+10*math.log10(125000)+6
    for i,v in enumerate(values):
        command=s.request(i,r,[v+noise])
    assert command == r  # margin=-10 -(-20)-10 = 0; still sends LinkADRReq
    # Negative margin -1 dB truncates to 0, unlike floor=-1.
    s=GADRServer((2,'max','avg',True,10.))
    r=Radio(sf=7,tp_dbm=12)
    s.request(0,r,[1.5+noise]);assert s.request(1,r,[1.5+noise]) == r


def test_telemetry_requires_received_dl(tmp_path):
    p=Profile.model_validate(dict(duration_s=220,mac={'duty_cycle':False},groups=[dict(count=1,placement='explicit',positions=[[1000,1000,1.5]],policy={'kind':'linucb_sf'},timing={'kind':'periodic','options':{'interval_s':30}})]))
    Simulation(p,tmp_path/'ok').run()
    f=pq.read_table(tmp_path/'ok'/'feedback').to_pylist()
    assert any(x['ul_snr_db'] is not None for x in f)
    t=pq.read_table(tmp_path/'ok'/'transmissions').to_pylist()
    assert all(x['phy_bytes']==18 and x['link_report_bytes']==9 for x in t if x['direction']=='DL')
    # Force DL attenuation only via allowed-band power; UL still decodes at GW.
    for band in p.sub_bands:
        band.max_tp_dbm=14
    p.mac.backhaul_delay_s=10  # ACK misses both receive windows
    Simulation(p,tmp_path/'lost').run()
    assert all(x['ul_snr_db'] is None for x in pq.read_table(tmp_path/'lost'/'feedback').to_pylist())
    state=json.loads((tmp_path/'lost'/'policy_state.jsonl').read_text())
    assert state['last_received_ul_snr_db'] is None


@pytest.mark.parametrize('kind',['linucb_sf','linucb_sf_tp','lora_mab'])
def test_learning_rejects_unconfirmed(tmp_path,kind):
    p=Profile.model_validate({'groups':[{'confirmed':False,'policy':{'kind':kind}}]})
    with pytest.raises(ValueError,match='confirmed'):
        Simulation(p,tmp_path/'run')


def test_all_plugins_expose_schema():
    plugins=inventory()
    assert {'gadr','linucb_sf','linucb_sf_tp','lora_mab'} <= set(plugins['policy'])
