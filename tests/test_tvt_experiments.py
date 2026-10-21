import json
import numpy as np
import pyarrow.parquet as pq
import pytest

from lorasim.config import ActionSpace
from lorasim.engine import Simulation
from lorasim.experiments import make_profile, plan, read_plan, run_plan
from lorasim.experiment_analysis import analyze_run, analyze_plan, quality, interval
from lorasim.interfaces import Observation, Feedback
from lorasim.plugins.selector.dqn_mobility import MobilityDQN
from lorasim.plugins.selector.dqn_state_split import StateSplitDQN
from lorasim.plugins.selector._dqn import Learner, DQNSettings
from lorasim.plugins.mobility.waypoints import Waypoints
from lorasim.tracking import Tracker


def obs(t,pos):
    return Observation(t,0,0,None,None,None,None,pos)


@pytest.mark.parametrize('cls',[MobilityDQN,StateSplitDQN])
def test_local_movement_and_training(cls):
    p=cls(np.random.default_rng(1),ActionSpace(),hidden_sizes=[4],batch_size=1)
    p.initialize(obs(0,(0,0,0)))
    p.select(obs(60,(4,0,0)))
    assert p.moving
    p.observe(Feedback(65,True,-90,.1,14,1))
    assert p.learner.seen==1
    if cls is MobilityDQN:
        np.testing.assert_equal(p.learner.data[0,[0,1,4,5]],[-120,1,-90,1])
        assert p.learner.net.params[0].shape==(2,4)
    else:
        assert p.learners['B'].seen==0 and p.learners['C'].seen==1
    p.select(obs(120,(4,0,0)))
    assert not p.moving
    p.observe(Feedback(125,False,None,.1,14,1))
    if cls is StateSplitDQN:
        assert p.learners['B'].seen==1 and p.learners['C'].seen==1
    assert p.rssi==-90


def test_vector_replay_ring_and_growth():
    p=Learner(np.random.default_rng(0),2,DQNSettings(hidden_sizes=[2],batch_size=2,replay_capacity=130),input_dim=2)
    for i in range(140):
        p.learn([i/140,i%2],i%2,.1,[(i+1)/140,(i+1)%2])
    assert p.data.shape==(130,6) and p.size==130 and p.seen==140
    assert np.isfinite(p.loss)


def test_initial_pause_and_exact_epoch():
    m=Waypoints([0,0,1.5],[100,100],np.random.default_rng(0),initial_pause=True,pause_min_s=10,pause_max_s=10)
    start=m.position(0)
    np.testing.assert_equal(m.position(9),start)
    assert m.motion_state(9)=='stationary' and m.motion_epoch_start()==0
    m.position(10)
    assert m.motion_state(10)=='moving' and m.motion_epoch_start()==10
    arrival=m.arrive
    m.position(arrival)
    assert m.motion_state(arrival)=='stationary' and m.motion_epoch_start()==arrival


def test_plan_matrix_and_tamper_detection(tmp_path):
    runs=plan(tmp_path/'full','all',[0,1])
    assert len({r['id'] for r in runs})==len(runs)
    assert {r['n'] for r in runs}=={50,100,150,200}
    assert {r['width'] for r in runs}=={16,32,64}
    path=tmp_path/'full'/runs[0]['profile']
    path.write_text(path.read_text()+'\n# modified')
    with pytest.raises(ValueError,match='changed'):
        read_plan(tmp_path/'full')


def test_timing_does_not_change_radio_or_exogenous_conditions(tmp_path):
    profiles=[make_profile(m,4,2,messages=8,smoke=True) for m in ('sema','dqn_mobility')]
    sims=[Simulation(p,tmp_path/str(i)) for i,p in enumerate(profiles)]
    for a,b in zip(sims[0].devices,sims[1].devices):
        np.testing.assert_equal(a.mobility.position(50000),b.mobility.position(50000))
    # Fresh simulations: above trajectory probes cannot run backwards.
    p=profiles[0]
    Simulation(p,tmp_path/'timed').run()
    p=p.model_copy(deep=True);p.execution.record_policy_timing=False
    Simulation(p,tmp_path/'plain').run()
    for name in ('uplinks','transmissions','feedback','selector_states'):
        assert pq.read_table(tmp_path/'timed'/name).equals(pq.read_table(tmp_path/'plain'/name))
    assert pq.read_table(tmp_path/'timed/policy_timing').num_rows==64
    assert pq.read_table(tmp_path/'plain/policy_timing').num_rows==0
    from collections import Counter
    counts=Counter(pq.read_table(tmp_path/'timed/uplinks')['device_id'].to_pylist())
    assert sorted(counts.values())==[8]*4


def test_metrics_and_seed_uncertainty():
    q=quality([0,.5,1,1])
    assert q['mean_prr']==.625 and q['bottom10_prr']==0 and q['threshold_fraction']==.5
    assert q['jain']==pytest.approx(6.25/9)
    assert quality([0,0])['jain'] is None
    assert interval([1])['ci_low'] is None
    assert interval([1,1,1])['ci_low']==1


def test_event_alignment_censoring_and_tail(tmp_path):
    tracker=Tracker(tmp_path)
    # Intentionally reverse completion order. Episode at t=0 must be excluded.
    data=[(1,True,'stationary',0),(2,False,'moving',100),(3,True,'moving',100),
          (4,False,'stationary',300),(5,True,'moving',400)]
    for tx,received,state,epoch in reversed(data):
        tracker.write('uplinks',device_id=0,tx_index=tx,packet_id=tx,received=received,
                      outcome='Received' if received else 'Interfered',motion_state=state,motion_epoch_start_ns=epoch)
        tracker.write('feedback',packet_id=tx,ack=received)
    tracker.close()
    spec=dict(tail=2,threshold=.7,transition_burnin=0,horizons=[1,2])
    metrics,eds,events=analyze_run(tmp_path,spec,1,5)
    assert metrics['mean_prr']==.5 and eds[0]['transmissions']==2
    assert metrics['moving_h2_loss']==.5
    assert metrics['moving_h2_complete']==1 and metrics['moving_h2_incomplete']==1
    assert metrics['stationary_h2_complete']==0
    assert len(events)==6
    with pytest.raises(ValueError,match='incomplete transmit'):
        analyze_run(tmp_path,spec,1,6)


def test_plan_run_resume_and_partial_report(tmp_path):
    root=tmp_path/'smoke'
    plan(root,'basic',[0],True)
    run_plan(root,only='-sema-',limit=1)
    run_plan(root,only='-sema-',limit=1)  # resume, no overwrite
    with pytest.raises(ValueError,match='missing'):
        analyze_plan(root)
    analyze_plan(root,True)
    meta=json.loads((root/'report/analysis.json').read_text())
    assert meta['smoke'] and meta['partial']
    assert (root/'report/per_ed.csv').exists()
