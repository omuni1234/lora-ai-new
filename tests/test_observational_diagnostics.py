"""Diagnostics may observe existing state, never change radio/learning/mobility."""
import copy
import json
from types import SimpleNamespace

import numpy as np
import pyarrow.parquet as pq
import pytest

from lorasim.config import Profile
from lorasim.engine import Attempt, Simulation
from lorasim.plugins.mobility.waypoints import Waypoints
from lorasim.plugins.selector._dqn import DQNSettings, Learner
from lorasim.plugins.selector._dqn_vector import VectorLearner


def assert_learner_equal(a, b):
    assert a.snapshot() == b.snapshot()
    assert a.rng.bit_generator.state == b.rng.bit_generator.state
    np.testing.assert_array_equal(a.data[:a.size], b.data[:b.size])
    for left, right in ((a.net.params, b.net.params), (a.target, b.target), (a.m, b.m), (a.v, b.v)):
        for x, y in zip(left, right):
            np.testing.assert_array_equal(x, y)


def test_scalar_learner_matches_optional_extension_through_growth_and_wrap():
    o = DQNSettings(hidden_sizes=[4], batch_size=4, replay_capacity=130, target_update=3)
    scalar = Learner(np.random.default_rng(5), 18, o)
    vector = VectorLearner(np.random.default_rng(5), 18, o)
    for i in range(300):
        x, y = -120 + i % 40, -120 + (i + 1) % 40
        a = scalar.choose(x)
        assert a == vector.choose(x)
        scalar.learn(x, a, (i % 3 - 1) / 6, y)
        vector.learn(x, a, (i % 3 - 1) / 6, y)
        assert_learner_equal(scalar, vector)


def test_waypoint_epoch_reads_existing_state_without_advancing_or_rng_draws():
    def make():
        return Waypoints([0, 0, 1.5], [100, 80], np.random.default_rng(3),
                         waypoint_count=8, speed_min_m_s=10, speed_max_m_s=20,
                         pause_min_s=2, pause_max_s=4)
    logged, original = make(), make()
    states = set()
    for t in np.arange(0, 200, .5):
        np.testing.assert_array_equal(logged.position(t), original.position(t))
        states.add(logged.motion_state(t))
        before = copy.deepcopy(logged.rng.bit_generator.state)
        expected = logged.arrive if logged.resting else logged.depart
        for _ in range(3):
            assert logged.motion_epoch_start() == expected <= t
        assert logged.rng.bit_generator.state == before == original.rng.bit_generator.state
        assert logged.last_query == original.last_query == t
    assert states == {'moving', 'stationary'}
    single = Waypoints([0, 0, 0], [1, 1], np.random.default_rng(0), waypoint_count=1)
    single.position(1000)
    assert single.motion_epoch_start() == 0
    for mode in ('timed', 'route'):
        m = Waypoints([0, 0, 0], [2000, 2000], np.random.default_rng(0), mode=mode)
        m.position(5)
        assert m.motion_epoch_start() is None  # Do not fabricate a state epoch.


@pytest.mark.parametrize('kind', ['fixed', 'random', 'bandit', 'dqn', 'sema',
                                 'adr', 'badr', 'gadr', 'linucb_sf', 'lora_mab', 'd_lora'])
def test_diagnostics_preserve_existing_simulation_and_full_learner_state(tmp_path, kind):
    p = Profile.model_validate(dict(seed=34, duration_s=103, area_m=[2000, 2000],
        mac=dict(duty_cycle=False), energy=dict(mcu=dict(enabled=True, mode='fixed')),
        gateways=[dict(position=[500, 500, 15]), dict(position=[24000, 24000, 15])],
        groups=[dict(count=4, observe_position=True, max_attempts=1,
            policy=dict(kind=kind, action_space={}, options=dict(hidden_sizes=[4], batch_size=2,
                replay_capacity=16) if kind in ('dqn', 'sema') else {}),
            timing=dict(kind='periodic', options=dict(interval_s=10,
                start=dict(mode='uniform', min_s=0, max_s=3))),
            mobility=dict(kind='waypoints', options=dict(waypoint_count=8, speed_min_m_s=100,
                speed_max_m_s=120, pause_min_s=2, pause_max_s=4)))]))
    sims = []
    for enabled in (False, True):
        p.tracking.policy_diagnostics = enabled
        sim = Simulation(p.model_copy(deep=True), tmp_path / str(enabled))
        sim.run()
        sims.append(sim)
    for table in ('devices', 'uplinks', 'transmissions', 'receptions', 'feedback', 'energy', 'events', 'selector_states'):
        a, b = (pq.read_table(tmp_path / str(v) / table) for v in (False, True))
        drop = [k for k in ('selected_algorithm', 'detected_motion_state') if k in a.column_names]
        assert a.drop(drop).equals(b.drop(drop)), table
    assert (tmp_path/'False/policy_state.jsonl').read_bytes() == (tmp_path/'True/policy_state.jsonl').read_bytes()
    for a, b in zip(sims[0].devices, sims[1].devices):
        assert a.mobility.rng.bit_generator.state == b.mobility.rng.bit_generator.state
        if kind == 'dqn':
            assert_learner_equal(a.policy.learner, b.policy.learner)
        elif kind == 'sema':
            for key in ('A', 'B', 'C'):
                assert_learner_equal(a.policy.learners[key], b.policy.learners[key])
    ul = pq.read_table(tmp_path/'True/uplinks').to_pylist()
    assert all(v['motion_epoch_start_ns'] is not None for v in ul)
    assert any(v['outcome'] == 'MixedLoss' for v in ul)


@pytest.mark.parametrize(('outcomes', 'censored', 'expected'), [
    ({'a': 'Received', 'b': 'UnderSensitivity'}, False, 'Received'),
    ({'a': 'Interference', 'b': 'Interference'}, False, 'Interference'),
    ({'a': 'Interference', 'b': 'UnderSensitivity'}, False, 'MixedLoss'),
    ({'a': 'LostBecauseTx', 'b': 'NoMoreReceivers'}, False, 'MixedLoss'),
    ({'a': 'UnderSensitivity'}, False, 'Censored'),
    ({'a': 'Interference', 'b': 'SimulationEnd'}, False, 'Censored'),
    ({'a': 'Interference', 'b': 'UnderSensitivity'}, True, 'Censored'),
    ({'a': 'Received'}, True, 'Received'),
])
def test_original_multi_gateway_ul_classification(outcomes, censored, expected):
    rows = []
    sim = SimpleNamespace(pending_uplinks={1: True}, p=SimpleNamespace(gateways=['a', 'b']),
        devices=[SimpleNamespace(policy_label='fixed')],
        tracker=SimpleNamespace(write=lambda table, **row: rows.append((table, row))))
    ctx = Attempt(1, 0, 1, 0, packet_id=1, outcomes=outcomes)
    Simulation._uplink_result(sim, ctx, censored)
    assert len(rows) == 1
    assert rows[0][1]['outcome'] == expected
    assert json.loads(rows[0][1]['gw_outcomes']) == outcomes
    Simulation._uplink_result(sim, ctx, censored)
    assert len(rows) == 1  # One network UL, even with multiple GW observations.
