import math
import numpy as np
import pyarrow.parquet as pq
import pytest
from lorasim.config import ActionSpace, Profile, Component
from lorasim.components import make
from lorasim.interfaces import Observation, Feedback
from lorasim.engine import Simulation
from lorasim.tracking import SCHEMAS
from lorasim.plugins.selector._dqn import Network, Learner, DQNSettings
from lorasim.plugins.selector.dqn import DQN
from lorasim.plugins.selector.sema import SEMA
from lorasim.plugins.mobility.markov import Markov, Settings as MarkovSettings
from lorasim.registry import inventory


def obs(t=0,position=(0,0,0)):
    return Observation(t,0,0,None,None,None,None,position)


def feedback(ack=True,rssi=-90):
    return Feedback(1,ack,rssi,.1,14,1,ack,100,100,14)


@pytest.mark.parametrize('kind',['dqn','sema','linucb_sf_ch','lora_mab_sf_ch','random_sf_ch','bandit_sf_ch','fixed_sf_ch'])
def test_common_space_and_ui(kind):
    space=ActionSpace(spreading_factors=[8,10],frequencies_hz=[868300000,868500000],tp_dbm=11,cr=2)
    p=make(Component(kind=kind),'policy',rng=np.random.default_rng(2),action_space=space)
    assert {(r.sf,r.frequency_hz,r.tp_dbm,r.cr) for r in p.actions}=={(s,f,11,2) for s in [8,10] for f in [868300000,868500000]}
    r=p.select(obs())
    assert r.sf in [8,10] and r.frequency_hz in space.frequencies_hz
    meta=inventory()['policy'][kind]
    assert meta['label'] and meta['description']
    for field in meta['schema']['properties'].values(): assert field.get('description')


@pytest.mark.parametrize('space',[
    {'spreading_factors':[7,7]}, {'spreading_factors':[6]},
    {'frequencies_hz':[868100000,868100000]}, {'frequencies_hz':[868200000]},
    {'frequencies_hz':[869525000],'tp_dbm':28}, {'tp_dbm':15}])
def test_invalid_space(space):
    with pytest.raises(ValueError): Profile.model_validate(dict(action_space=space,groups=[{'policy':{'kind':'dqn'}}]))


def test_disabled_subband_rejected_and_legacy_region_unchanged():
    with pytest.raises(ValueError,match='disabled'):
        Profile.model_validate(dict(ul_sub_bands=['band4'],groups=[{'policy':{'kind':'dqn'}}]))
    Profile.model_validate(dict(ul_sub_bands=['band4']))  # legacy selectors do not use common space


def test_dqn_gradient_finite_differences():
    net=Network(np.random.default_rng(4),[1,3,2])
    x=np.array([[-.3],[.7],[1.2]]);a=np.array([0,1,0]);y=np.array([1.,-.2,.8])
    loss,grads=net.gradients(x,a,y)
    for p,g in zip(net.params,grads):
        for idx in np.ndindex(p.shape):
            old=p[idx];eps=1e-6
            p[idx]=old+eps;hi=np.mean((net.forward(x)[np.arange(3),a]-y)**2)
            p[idx]=old-eps;lo=np.mean((net.forward(x)[np.arange(3),a]-y)**2)
            p[idx]=old
            assert g[idx]==pytest.approx((hi-lo)/(2*eps),abs=1e-8)


def test_adam_target_and_ring_buffer():
    o=DQNSettings(hidden_sizes=[2],batch_size=1,replay_capacity=3,target_update=2,learning_rate=.002)
    p=Learner(np.random.default_rng(3),2,o)
    old=[x.copy() for x in p.net.params]
    target=1+o.discount*p.net.forward(np.array([[.4]])).max()
    _,g=p.net.gradients(np.array([[.2]]),np.array([1]),np.array([target]))
    p.learn(.2,1,1.,.4)
    for actual,initial,grad,held in zip(p.net.params,old,g,p.target):
        np.testing.assert_allclose(actual,initial-o.learning_rate*grad/(np.abs(grad)+o.adam_epsilon),atol=1e-14)
        np.testing.assert_array_equal(held,initial)
    p.learn(.4,0,-1.,.6)
    for a,b in zip(p.net.params,p.target): np.testing.assert_array_equal(a,b)
    for i in range(10): p.learn(float(i),0,0.,float(i+1))
    assert p.size==3 and p.data.shape==(3,4) and p.seen==12


def test_dqn_observation_reward_and_no_oracle():
    p=DQN(np.random.default_rng(2),ActionSpace(spreading_factors=[12]),batch_size=8,initial_rssi_dbm=-115)
    p.select(obs());p.observe(feedback(False,100))
    assert p.rssi==-115
    np.testing.assert_array_equal(p.learner.data[0],[ -115,p.learner.data[0,1],-1,-115])
    p.select(obs());p.observe(feedback(True,-90))
    assert p.rssi==-90 and p.learner.data[1,2]==pytest.approx(1/6)
    assert p.learner.data[1,3]==-90  # DL RSSI, never forged GW SNR/RSSI fields


def test_sema_pools_reset_and_elimination():
    p=SEMA(np.random.default_rng(1),ActionSpace(),batch_size=8)
    p.active=[4]  # stationary epsilon, moving A
    p.select(obs());p.observe(feedback())
    assert [p.learners[k].seen for k in ('A','B','C')]==[1,1,0]
    assert p.greedy_steps==1
    p.select(obs(1,(10,0,0)));p.observe(feedback(False))
    assert [p.learners[k].seen for k in ('A','B','C')]==[2,1,1]
    p.select(obs(2,(10,0,0)))
    assert p.greedy_resets==1 and p.greedy_steps==0 and not p.greedy_q.any()
    p.active=list(range(6));p.counts[:]=1000;p.rewards[:]=0;p.rewards[0]=1000
    p.eliminate()
    assert p.active==[0]
    p.active=[0,1];p.counts[1]=0;p.eliminate()
    assert p.active==[0,1]  # unseen arms cannot be eliminated


def test_sema_position_required(tmp_path):
    with pytest.raises(ValueError,match='observe_position'):
        Simulation(Profile.model_validate({'groups':[{'policy':{'kind':'sema'}}]}),tmp_path)


def test_markov_deterministic_transitions_and_continuous_travel():
    m=Markov([0,0,0],[100,100],np.random.default_rng(2),points=[[0,0,0],[10,0,0]],
        transition_matrix=[[0,1],[1,0]],decision_interval_s=5,speed_m_s=2)
    for t,x in [(0,0),(4,0),(5,0),(7,4),(10,10),(14,10),(15,10),(17,6),(20,0)]:
        np.testing.assert_array_equal(m.position(t),[x,0,0])
    with pytest.raises(ValueError):m.position(19)


def test_markov_absorbing_and_query_schedule_independent():
    opts=dict(random_count=5,stay_probability=.6,decision_interval_s=3,speed_m_s=5,speed_jitter_m_s=2)
    a=Markov([0,0,1],[20,20],np.random.default_rng(9),**opts)
    b=Markov([0,0,1],[20,20],np.random.default_rng(9),**opts)
    for t in np.linspace(0,999,1000):a.position(t)
    np.testing.assert_array_equal(a.position(1000),b.position(1000))
    m=Markov([0,0,0],[100,100],np.random.default_rng(1),points=[[10,0,0]],speed_m_s=2)
    np.testing.assert_array_equal(m.position(2),[4,0,0])
    np.testing.assert_array_equal(m.position(1e8),[10,0,0])
    assert math.isinf(m.leave)


@pytest.mark.parametrize('matrix',[[[.2,.2],[0,1]],[[1,0]],[[1.1,-.1],[0,1]],[[float('nan'),0],[0,1]]])
def test_invalid_markov_matrix(matrix):
    with pytest.raises(ValueError):MarkovSettings(transition_matrix=matrix)


@pytest.mark.parametrize('kind',['sema','dqn','linucb_sf_ch','lora_mab_sf_ch'])
def test_models_reference_optimized_and_preview(tmp_path,kind):
    options={'hidden_sizes':[4], 'batch_size':2,'replay_capacity':10} if kind in {'sema','dqn'} else {}
    p=Profile.model_validate(dict(duration_s=130,seed=3,mac={'duty_cycle':False},groups=[dict(count=3,observe_position=True,
        policy={'kind':kind,'options':options},mobility={'kind':'markov','options':{'random_count':3,'decision_interval_s':4,'speed_m_s':200}},
        timing={'kind':'periodic','options':{'interval_s':10,'start':{'mode':'uniform','min_s':0,'max_s':10}}})]))
    p.execution.optimized=False;a=Simulation(p,tmp_path/'a').run()
    p.execution.optimized=True;b=Simulation(p,tmp_path/'b').run()
    assert a['counts']==b['counts']
    for table in SCHEMAS:assert pq.read_table(tmp_path/'a'/table).equals(pq.read_table(tmp_path/'b'/table)),table
    assert (tmp_path/'a'/'policy_state.jsonl').read_text()==(tmp_path/'b'/'policy_state.jsonl').read_text()
    from lorasim.visualization import preflight
    assert preflight(p,limit=2,samples=3)['devices']


def test_first_sema_selection_uses_time_zero_position():
    p=SEMA(np.random.default_rng(1),ActionSpace())
    p.initialize(obs())
    p.select(obs(10,(10,0,0)))
    assert p.moving


def test_preview_memory_bound():
    from lorasim.visualization import preflight
    p=Profile.model_validate({'groups':[{'count':200,'policy':{'kind':'dqn','options':{'hidden_sizes':[512]*4}}}]})
    result=preflight(p,limit=200,samples=1)
    assert all(any('64 MiB' in w for w in d['warnings']) for d in result['devices'])
