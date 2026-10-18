from pathlib import Path
import numpy as np
import pyarrow.parquet as pq
import pytest
from lorasim.config import Profile
from lorasim.engine import Simulation
from lorasim.components import RandomWalk,Waypoints,Observation


def profile(**kw):
    data=dict(duration_s=10,channel={'reference_loss_db':7.7,'exponent':2},mac={'duty_cycle':False},
              gateways=[{'id':'g','position':[0,0,0]}],
              groups=[{'name':'g','count':1,'placement':'explicit','positions':[[100,0,0]],'confirmed':True,
                       'timing':{'kind':'periodic','options':{'interval_s':60}}}])
    data.update(kw)
    return Profile.model_validate(data)


def run(tmp_path,p,name='run'):
    s=Simulation(p,tmp_path/name);result=s.run()
    def rows(t):return pq.read_table(tmp_path/name/t).to_pylist()
    return result,rows


def test_class_a_ack_and_feedback_no_privileged_data(tmp_path):
    result,rows=run(tmp_path,profile())
    assert result['counts']['ack_received']==1
    tx=rows('transmissions');assert [r['direction'] for r in tx]==['UL','DL']
    assert tx[1]['start_ns']==tx[0]['end_ns']+1_000_000_334
    f=rows('feedback')[0];assert f['ack'] is True and f['dl_rssi_dbm'] is not None
    assert 'gw_rssi' not in Observation.__dataclass_fields__


def test_loss_retries_and_no_rssi_leak(tmp_path):
    p=profile(channel={'reference_loss_db':200})
    p.groups[0].max_attempts=2
    result,rows=run(tmp_path,p)
    assert result['counts']['uplinks_sent']==2
    assert all(r['outcome']=='UnderSensitivity' for r in rows('receptions'))
    assert all(r['ack'] is False and r['dl_rssi_dbm'] is None for r in rows('feedback'))


def test_simultaneous_collision_and_demodulator_limit(tmp_path):
    p=profile()
    p.groups[0].confirmed=False
    p.groups[0].count=3;p.groups[0].positions=[(100,0,0)]*3
    p.gateways[0].demodulators=2
    _,rows=run(tmp_path,p)
    assert sorted(r['outcome'] for r in rows('receptions'))==['Interference','Interference','NoMoreReceivers']
    assert all(r['ack'] is None for r in rows('feedback'))


def test_gw_transmission_interrupts_locked_uplink(tmp_path):
    p=profile(duration_s=10)
    other=p.groups[0].model_copy(deep=True)
    other.name='slow';other.policy.options={'sf':12};other.timing.options={'interval_s':60,'first_s':.5}
    p.groups.append(other)
    _,rows=run(tmp_path,p)
    assert any(r['outcome']=='LostBecauseTx' for r in rows('receptions'))


def test_rx2_fallback_and_rx1_suppression(tmp_path):
    p=profile()
    s=Simulation(p,tmp_path/'rx2');s.gw_duty[0][p.band(868100000)]=5_000_000_000
    p.mac.duty_cycle=True
    s.run();tx=pq.read_table(tmp_path/'rx2'/'transmissions').to_pylist()
    assert tx[1]['frequency_hz']==869525000
    fb=pq.read_table(tmp_path/'rx2'/'feedback').to_pylist()
    assert fb[0]['ack'] is True


def test_downlink_loss_does_not_schedule_rx2_retry(tmp_path):
    # GW sensitivity is 6 dB better than the ED; UL can succeed while DL fails.
    p=profile(channel={'reference_loss_db':140,'exponent':2})
    p.groups[0].positions=[(0,0,0)]
    result,rows=run(tmp_path,p)
    assert result['counts']['uplinks_received_any_gw']==1
    assert result['counts']['downlinks_sent']==1
    assert rows('feedback')[0]['ack'] is False


def test_duty_cycle_defers_and_horizon_is_not_loss(tmp_path):
    p=profile(duration_s=3,mac={'duty_cycle':True})
    p.groups[0].timing.options={'interval_s':.5}
    result,rows=run(tmp_path,p)
    assert result['counts']['uplinks_sent']==1
    assert any(r['event']=='DutyCycleDeferred' for r in rows('events'))
    p=profile(duration_s=.01)
    _,rows=run(tmp_path,p,'truncated')
    assert rows('receptions')[0]['outcome']=='SimulationEnd'
    assert rows('feedback')==[]


def test_vector_scalar_seed_parity_and_bounded_parts(tmp_path):
    from lorasim.config import load_profile
    p=load_profile(Path(__file__).parents[1]/'examples/03-mobile-learning.yaml')
    p.duration_s=120;p.tracking.batch_rows=7
    _,a=run(tmp_path,p,'a')
    p.channel_backend='scalar'
    _,b=run(tmp_path,p,'b')
    for table in ['transmissions','events']:
        assert a(table)==b(table)
    for x,y in zip(a('feedback'),b('feedback'),strict=True):
        px,py=x.pop('dl_rssi_dbm'),y.pop('dl_rssi_dbm')
        assert x==y
        assert px==py if px is None or py is None else abs(px-py)<1e-12
    x,y=a('receptions'),b('receptions')
    assert [(r['packet_id'],r['outcome']) for r in x]==[(r['packet_id'],r['outcome']) for r in y]
    assert np.allclose([r['rx_power_dbm'] for r in x],[r['rx_power_dbm'] for r in y],rtol=0,atol=1e-12)
    assert all(pq.read_metadata(f).num_rows<=7 for f in (tmp_path/'a'/'transmissions').glob('*.parquet'))


def test_mobility_waypoint_and_reflection():
    w=Waypoints([0,0,0],[10,10],np.random.default_rng(1),[[0,0,0,0],[10,10,10,2]])
    assert np.array_equal(w.position(5),[5,5,1])
    r=RandomWalk([5,5,1],[10,10],np.random.default_rng(1),speed_m_s=100,step_s=3)
    for t in np.arange(0,100,.5):
        pos=r.position(t);assert (pos[:2]>=0).all() and (pos[:2]<=10).all()


def test_assignment_last_match_and_independent_models(tmp_path):
    p=profile();g=p.groups[0];g.count=2;g.positions=[[0,0,0],[1,0,0]]
    from lorasim.config import Assignment,Component
    p.assignments=[Assignment(ids=[1],policy=Component(kind='fixed',options={'sf':12}))]
    s=Simulation(p,tmp_path/'x')
    assert s.devices[0].policy is not s.devices[1].policy
    assert s.devices[1].policy.radio.sf==12


def test_invalid_profile_and_action_rejected(tmp_path):
    with pytest.raises(ValueError): Profile.model_validate({'unknown':1})
    with pytest.raises(ValueError): Profile.model_validate({'duration_s':float('nan')})
    p=profile();p.groups[0].policy.options={'tp_dbm':27}
    with pytest.raises(ValueError,match='sub-band'): run(tmp_path,p)


def test_under_sensitivity_signal_still_interferes(tmp_path):
    p=profile(channel={'reference_loss_db':144.01,'exponent':2})
    p.groups[0].positions=[(0,0,0)]
    p.groups[0].confirmed=False
    other=p.groups[0].model_copy(deep=True);other.name='stronger';other.policy.options={'tp_dbm':14}
    p.groups[0].policy.options={'tp_dbm':13.99}
    # At GW: -130.02 (rejected) and -130.01 (rejected); increase reference power 0.02 dB.
    p.channel.reference_loss_db=143.99
    p.groups[0].policy.options={'tp_dbm':13.98}
    p.groups.append(other)
    _,rows=run(tmp_path,p)
    assert sorted(r['outcome'] for r in rows('receptions'))==['Interference','UnderSensitivity']


def test_cancellation_flushes_without_fabricating_feedback(tmp_path):
    p=profile()
    sim=Simulation(p,tmp_path/'cancel',cancelled=lambda:True)
    result=sim.run()
    assert result['status']=='cancelled'
    assert pq.read_table(tmp_path/'cancel'/'feedback').num_rows==0
