"""Contract, trajectory, reward and non-interference checks for the TVT plugins."""
import math
from pathlib import Path
import numpy as np
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from lorasim.api import create_app
from lorasim.config import ActionSpace, Profile, load_profile
from lorasim.engine import Simulation
from lorasim.interfaces import Observation, Feedback
from lorasim.mobility_environment import MobilityEnvironment
from lorasim.plugins.application.finite_periodic import FinitePeriodic
from lorasim.plugins.mobility.asset_waypoint import AssetWaypoint
from lorasim.plugins.selector.dqn_mobility import MobilityDQN
from lorasim.plugins.selector.dqn_state_split import StateSplitDQN
from lorasim.plugins.selector.sf_reward_bandit import SFRewardBandit
from lorasim.plugins.selector.thesis_lora_mab import ThesisLoRaMAB
from lorasim.visualization import preflight

ROOT=Path(__file__).resolve().parents[1]


def obs(t=0,position=(0,0,0)):
    return Observation(t,0,0,None,None,None,None,position)


def feedback(ack=True,rssi=-90):
    return Feedback(1,ack,rssi,.1,14,1,bool(ack))


def test_initial_pause_shared_geometry_and_sampling_independence():
    env=MobilityEnvironment(12)
    def make():return AssetWaypoint([0,0,1.5],[100,80],np.random.default_rng(3),environment=env,
        waypoint_count=5,speed_min_m_s=2,speed_max_m_s=2,pause_min_s=7,pause_max_s=7)
    a,b=make(),make()
    assert a.targets is b.targets and not a.targets.flags.writeable
    initial=a.position(0)
    assert a.motion_state(0)=='stationary' and a.motion_epoch_start()==0
    np.testing.assert_array_equal(a.position(6.99),initial)
    a.position(7)
    assert a.motion_state(7)=='moving' and a.motion_epoch_start()==7
    np.testing.assert_allclose(a.position((7+a.arrive)/2),(a.origin+a.target)/2)
    a.position(a.arrive)
    assert a.motion_state(a.arrive)=='stationary' and a.motion_epoch_start()==a.arrive
    for t in range(math.ceil(a.last_query),1000):a.position(t)
    np.testing.assert_array_equal(a.position(1000),b.position(1000))
    assert a.rng.bit_generator.state==b.rng.bit_generator.state
    one=AssetWaypoint([0,0,0],[1,1],np.random.default_rng(0),waypoint_count=1)
    np.testing.assert_array_equal(one.position(0),one.position(1e12))
    assert one.motion_epoch_start()==0


def test_finite_periodic_stops_without_new_random_draws():
    a=FinitePeriodic(np.random.default_rng(1),max_messages=3,interval_s=60,start={'mode':'fixed','at_s':2})
    prev=None;times=[]
    for _ in range(3):
        prev=a.next_time(obs(prev or 0),prev);times.append(prev)
    assert times==[2,62,122]
    state=a.rng.bit_generator.state
    assert a.next_time(obs(130),prev) is None
    assert a.rng.bit_generator.state==state


@pytest.mark.parametrize('cls',[MobilityDQN,StateSplitDQN])
def test_control_uses_local_position_and_trains_correct_model(cls):
    p=cls(np.random.default_rng(4),ActionSpace(),batch_size=1,replay_capacity=3,hidden_sizes=[2])
    p.initialize(obs())
    p.select(obs(1,(.5,0,0)));p.observe(feedback(False,None))
    assert not p.moving and p.rssi==-120
    p.select(obs(2,(3,0,0)));p.observe(feedback(True,-93))
    assert p.moving and p.rssi==-93
    if cls is StateSplitDQN:
        assert [p.learners[k].seen for k in ('B','C')]==[1,1]
        assert p.learners['B'].data is not p.learners['C'].data
    else:
        assert p.learner.data.shape==(3,6)
        np.testing.assert_array_equal(p.learner.data[1,:2],[-120,1])
        np.testing.assert_array_equal(p.learner.data[1,4:],[-93,1])
        assert p.learner.updates==2 and math.isfinite(p.learner.loss)


def test_raw_ucb_and_cumulative_epsilon_thesis_definitions():
    space=ActionSpace(spreading_factors=[7,12],frequencies_hz=[868100000])
    p=SFRewardBandit(np.random.default_rng(4),space,algorithm='ucb1')
    assert p.select(obs()).sf==7
    p.observe(feedback(False));assert p.select(obs()).sf==12
    p.observe(feedback(True));np.testing.assert_allclose(p.sums,[-1,1/6])
    p.counts[:]=[100,10];p.sums[:]=[70,-5]
    expected=int(np.argmax(p.sums/p.counts+2*np.sqrt(np.log(110)/p.counts)))
    assert expected==0  # Normalizing [-1,1] to [0,1] would choose the other arm.
    assert p.select(obs()).sf==space.radios()[expected].sf
    # Exploitation uses sums, not average rewards. Remove exploration deterministically.
    p.o.algorithm='epsilon';p.o.epsilon_end=0;p.o.decay=1e-100
    p.counts[:]=[100,1];p.sums[:]=[2,1]
    assert p.select(obs()).sf==7


def test_thesis_loramab_selected_only_preupdate_sum_and_stability():
    space=ActionSpace(spreading_factors=[7,12],frequencies_hz=[868100000])
    p=ThesisLoRaMAB(np.random.default_rng(2),space,horizon=100)
    p.log_weights=np.log([2.,3.]);probs=p.probabilities();p.pending=(1,probs[1])
    expected=np.array([2.,3.*math.exp(p.gamma*(1/6)/(2*probs[1]))+math.e/100/2*5])
    p.observe(feedback(True))
    np.testing.assert_allclose(np.exp(p.log_weights)/np.exp(p.log_weights).sum(),expected/expected.sum())
    for i in range(3000):p.select(obs());p.observe(feedback(i%3==0))
    assert np.isfinite(p.log_weights).all() and p.probabilities().sum()==pytest.approx(1)
    assert (p.probabilities()>=p.gamma/2).all()


def small_profile(kind='sema'):
    return Profile.model_validate(dict(seed=3,duration_s=103,area_m=[80,60],gateways=[dict(position=[40,30,15])],
        mac=dict(duty_cycle=False),tracking=dict(policy_diagnostics=True),groups=[dict(count=3,observe_position=True,
        policy=dict(kind=kind,options=dict(hidden_sizes=[4],batch_size=2,replay_capacity=8) if kind in ('sema','dqn','dqn_mobility','dqn_state_split') else {}),
        mobility=dict(kind='asset_waypoint',options=dict(waypoint_count=8,speed_min_m_s=15,speed_max_m_s=20,pause_min_s=2,pause_max_s=4)),
        timing=dict(kind='finite_periodic',options=dict(max_messages=10,interval_s=10,start=dict(mode='uniform',min_s=0,max_s=3))))]))


@pytest.mark.parametrize('kind',['sema','dqn','dqn_mobility','dqn_state_split','sf_reward_bandit','thesis_lora_mab'])
def test_new_models_run_and_diagnostics_do_not_change_radio_or_learning(tmp_path,kind):
    p=small_profile(kind)
    for enabled in (False,True):
        p.tracking.policy_diagnostics=enabled
        Simulation(p,tmp_path/str(enabled)).run()
    for name in ('devices','uplinks','transmissions','receptions','feedback','energy','events'):
        a=pq.read_table(tmp_path/'False'/name);b=pq.read_table(tmp_path/'True'/name)
        drop=[k for k in ('selected_algorithm','detected_motion_state') if k in a.column_names]
        assert a.drop(drop).equals(b.drop(drop)),name
    assert (tmp_path/'False/policy_state.jsonl').read_bytes()==(tmp_path/'True/policy_state.jsonl').read_bytes()
    assert pq.read_table(tmp_path/'False/policy_timing').num_rows==0
    times=pq.read_table(tmp_path/'True/policy_timing').to_pylist()
    assert len(times)==60 and all(v['cpu_ns']>0 for v in times)
    assert {v['tx_index'] for v in times}==set(range(1,11))
    assert all(v['meta_cpu_ns']<=v['cpu_ns'] for v in times)
    ul=pq.read_table(tmp_path/'True/uplinks').to_pylist()
    assert {v['motion_state'] for v in ul}=={'moving','stationary'}
    assert all(v['motion_epoch_start_ns'] is not None and v['selected_algorithm'] for v in ul)


def test_preview_and_gui_inventory_support_new_modules(tmp_path):
    p=small_profile('dqn_mobility');sim=Simulation(p,tmp_path/'unused')
    view=preflight(p,90,limit=3,samples=5)
    for d,shown in zip(sim.devices,view['devices']):
        np.testing.assert_array_equal(d.mobility.position(0),shown['xyz'])
        for point in shown['path']:
            np.testing.assert_array_equal(d.mobility.position(point['time_s']),point['xyz'])
    assert view['waypoint_count']==8
    client=TestClient(create_app(tmp_path/'store'));plugins=client.get('/api/defaults').json()['plugins']
    for category,kinds in dict(policy=['dqn_mobility','dqn_state_split','sf_reward_bandit','thesis_lora_mab'],mobility=['asset_waypoint'],timing=['finite_periodic']).items():
        for kind in kinds:
            assert plugins[category][kind]['description']
            assert all(v.get('description') for v in plugins[category][kind]['schema']['properties'].values())


def test_full_yaml_matrix_common_settings_and_smoke(tmp_path):
    files=list((ROOT/'examples/tvt').glob('*.yaml'));assert len(files)==77
    for path in files:
        p=load_profile(path);assert p.area_m==(24000.,24000.) or list(p.area_m)==[24000,24000]
        assert len(p.gateways)==4 and not p.mac.duty_cycle
        assert len(p.action_space.radios())==18 and p.tracking.policy_diagnostics
        for g in p.groups:
            assert g.payload_bytes==40 and g.max_attempts==1 and g.confirmed
            assert p.component(g.timing,'timing').options['max_messages']==(16 if path.name.startswith('00-') else 40000)
    p=load_profile(ROOT/'examples/tvt/00-smoke-sema.yaml')
    result=Simulation(p,tmp_path/'smoke').run()
    assert result['status']=='completed'
    assert pq.read_table(tmp_path/'smoke/uplinks').num_rows==64
