import numpy as np
import pytest
from lorasim.config import Profile
from lorasim.engine import Simulation
from lorasim.mobility_environment import MobilityEnvironment
from lorasim.plugins.mobility.waypoints import Waypoints, Settings
from lorasim.visualization import layout, preflight


def model(seed=1, environment=None, **options):
    return Waypoints([99,99,9], [100,80], np.random.default_rng(seed), environment=environment, **options)


def test_shared_points_and_independent_initial_choices_and_legs():
    environment = MobilityEnvironment(42)
    models = [model(i, environment, waypoint_count=100, random_seed=9) for i in range(30)]
    points = models[0].targets
    assert points.shape == (100,3) and not points.flags.writeable
    assert np.all((points[:,:2]>=0)&(points[:,:2]<[100,80]))
    assert len({tuple(m.position(0)) for m in models}) > 10
    for m in models:
        assert m.targets is points
        assert np.any(np.all(points == m.position(0), axis=1))
        assert np.any(m.target != m.origin)
    assert len({m.speed for m in models}) == len(models)
    # A separate simulation/environment is reproducible without sharing mutable state.
    again = model(0, MobilityEnvironment(42), waypoint_count=100, random_seed=9)
    np.testing.assert_array_equal(again.targets, points)
    np.testing.assert_array_equal(again.position(100), models[0].position(100))


def test_piecewise_linear_constant_speed_then_fresh_arrival_pause():
    m = model(3, waypoint_count=4, speed_min_m_s=2, speed_max_m_s=2, pause_min_s=7, pause_max_s=7)
    origin, target, arrival = m.origin.copy(), m.target.copy(), m.arrive
    np.testing.assert_allclose(m.position(arrival/2), (origin+target)/2)
    assert np.linalg.norm(target-origin)/arrival == pytest.approx(2)
    np.testing.assert_array_equal(m.position(arrival), target)
    assert m.leave == pytest.approx(arrival+7)
    np.testing.assert_array_equal(m.position(arrival+6.99), target)
    np.testing.assert_array_equal(m.position(arrival+7), target)
    assert m.depart == pytest.approx(arrival+7) and not m.resting
    assert np.any(m.target != target)


def test_every_visit_independent_and_query_sampling_does_not_change_path():
    opts = dict(waypoint_count=2, speed_min_m_s=.7, speed_max_m_s=4, pause_min_s=2, pause_max_s=9)
    sparse, dense = model(5, **opts), model(5, **opts)
    pauses, speeds = [], []
    for _ in range(100):
        speeds.append(dense.speed)
        dense.position(dense.arrive)
        pauses.append(dense.leave-dense.arrive)
        dense.position(dense.leave)
    assert min(speeds)>=.7 and max(speeds)<=4 and len(set(speeds))==100
    assert min(pauses)>=2 and max(pauses)<=9 and len(set(pauses))==100
    # All revisits are to the same two points; dwell is not a per-point table.
    np.testing.assert_array_equal(sparse.position(dense.last_query), dense.position(dense.last_query))
    assert sparse.speed == dense.speed and sparse.rng.bit_generator.state == dense.rng.bit_generator.state
    assert not hasattr(dense, 'a')  # no growing history
    with pytest.raises(ValueError, match='chronological'):
        dense.position(0)


def test_one_point_and_legacy_timed_and_bounds():
    m = model(4, waypoint_count=1, pause_min_s=0, pause_max_s=0)
    np.testing.assert_array_equal(m.position(0),m.position(1e12))
    old = model(points=[[0,0,0,0],[10,10,0,0]])
    assert old.options.mode == 'timed'
    np.testing.assert_array_equal(old.position(5), [5,0,0])
    for opts in [{'waypoint_count':0}, {'speed_min_m_s':0}, {'speed_min_m_s':4,'speed_max_m_s':2}, {'pause_min_s':10,'pause_max_s':2}]:
        with pytest.raises(ValueError): Settings(**opts)


@pytest.mark.parametrize('kind',['waypoints','lorasim.plugins.mobility.waypoints:Waypoints'])
def test_preview_and_engine_share_initial_points_with_assignment(tmp_path,kind):
    p = Profile.model_validate(dict(seed=77,duration_s=100,area_m=[100,80],
        models={'mobility':{'shared':{'kind':kind,'options':{'waypoint_count':100}}}},
        groups=[{'count':9,'mobility':{'ref':'shared'}}], assignments=[{'ids':[2],'mobility':{'ref':'shared'}}]))
    sim = Simulation(p,tmp_path/'unused')
    view = preflight(p,60,limit=9,samples=5)
    assert view['waypoint_count']==100 and len(view['waypoints'])==100
    for d, shown in zip(sim.devices, view['devices']):
        np.testing.assert_array_equal(d.mobility.position(0), shown['xyz'])
        for sample in shown['path']:
            np.testing.assert_array_equal(d.mobility.position(sample['time_s']), sample['xyz'])
    assert not (tmp_path/'unused').exists()
    assert len(layout(p,0,limit=2)['devices'])==2


def test_preview_transition_work_is_bounded():
    p = Profile.model_validate(dict(duration_s=1e8, area_m=[.01,.01],
        groups=[{'count':2,'mobility':{'kind':'waypoints','options':{'waypoint_count':2,'speed_min_m_s':1,'speed_max_m_s':2,'pause_min_s':0,'pause_max_s':0}}}]))
    result = preflight(p,1e8,limit=2,samples=2)
    assert result['warnings']
    assert any('上限' in warning for d in result['devices'] for warning in d['warnings'])


def test_optimized_and_reference_with_many_transitions_and_learning(tmp_path):
    import pyarrow.parquet as pq
    p = Profile.model_validate(dict(seed=39, duration_s=110, area_m=[80,60],
        gateways=[{'position':[40,30,15]}], mac={'duty_cycle':False},
        action_space={'powers_dbm':[2,8,14]}, groups=[{
            'count':6, 'confirmed':True, 'observe_position':True, 'max_attempts':2,
            'policy':{'kind':'d_lora'},
            'timing':{'kind':'periodic','options':{'interval_s':9,'start':{'mode':'uniform','min_s':0,'max_s':5}}},
            'mobility':{'kind':'waypoints','options':{'waypoint_count':8,'speed_min_m_s':15,'speed_max_m_s':25,'pause_min_s':0,'pause_max_s':1}}
        }]))
    for optimized, folder in [(False,'reference'),(True,'optimized')]:
        p.execution.optimized = optimized
        Simulation(p,tmp_path/folder).run()
    for table in ('devices','uplinks','transmissions','receptions','feedback','events','energy'):
        assert pq.read_table(tmp_path/'reference'/table).equals(pq.read_table(tmp_path/'optimized'/table)), table
    assert (tmp_path/'reference'/'policy_state.jsonl').read_bytes() == (tmp_path/'optimized'/'policy_state.jsonl').read_bytes()
