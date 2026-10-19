import itertools
import math
import numpy as np
import pytest
from lorasim.config import ActionSpace, Component, Profile
from lorasim.components import make
from lorasim.engine import Simulation
from lorasim.interfaces import Feedback, Observation
from lorasim.plugins.selector.d_lora import DLoRa


OBS = Observation(0,0,0,None,None,None,None,None)


def feedback(ack): return Feedback(1,ack,None,.1,14,1)
def model(seed=2, **options):
    return DLoRa(np.random.default_rng(seed),ActionSpace(spreading_factors=[7,9],powers_dbm=[2,14]),**options)


def test_paper_rewards_eq19_to22_and_all_base_arms_explored():
    m = model(sf_weight_xi=.8,power_weight_eta=.3)
    for j in range(30):
        radio = m.select(OBS)
        indices = m.pending.copy()
        old = [a[i] for a,i in zip(m.means,indices)]
        ack = j%2 == 0
        m.observe(feedback(ack))
        expected = [float(ack),float(ack)+.8*(radio.sf/2**radio.sf)/(7/2**7+9/2**9),float(ack)+.3*(1-radio.tp_dbm/16)]
        np.testing.assert_allclose(m.last_rewards,expected)
        for n,mean,i,r,prev in zip(m.counts,m.means,indices,expected,old):
            assert mean[i] == pytest.approx(prev+(r-prev)/n[i])
        if j == 2: assert all(np.all(c>0) for c in m.counts)
    assert m.updates == 30 and all(c.sum()==30 for c in m.counts)
    m.observe(feedback(True))
    assert m.updates == 30  # consume each feedback only once
    m.select(OBS); m.observe(feedback(None))
    assert m.updates == 30


def test_separable_argmax_equals_cartesian_cucb_eq23_24():
    m = model()
    for j in range(10): m.select(OBS); m.observe(feedback(j%3==0))
    scores = [mean+m.options.exploration_c*np.sqrt(math.log(m.updates)/(2*count)) for mean,count in zip(m.means,m.counts)]
    maximum = max(sum(s[i] for s,i in zip(scores,ids)) for ids in itertools.product(*(range(len(s)) for s in scores)))
    m.select(OBS)
    assert sum(s[i] for s,i in zip(scores,m.pending)) == pytest.approx(maximum)


def test_fixed_tp_cr_common_space_and_independent_devices(tmp_path):
    p = Profile.model_validate(dict(duration_s=90,groups=[{'count':2,'policy':{'kind':'d_lora'},'confirmed':True}],mac={'duty_cycle':False}))
    sim = Simulation(p,tmp_path)
    a,b = [d.policy for d in sim.devices]
    assert a is not b
    for x,y in zip(a.means+a.counts,b.means+b.counts): assert not np.shares_memory(x,y)
    a.select(OBS); a.observe(feedback(True))
    assert b.updates == 0 and all(not np.any(c) for c in b.counts)
    for _ in range(10):
        radio = a.select(OBS); a.observe(feedback(False))
        assert radio.cr==1 and radio.tp_dbm==14
    assert len(a.counts[0])==3 and len(a.counts[1])==6 and len(a.counts[2])==1


def test_invalid_cr_power_and_ack_contract(tmp_path):
    with pytest.raises(ValueError,match='fixed dimension'):
        Profile.model_validate({'action_space':{'coding_rates':[1,2]},'groups':[{'policy':{'kind':'d_lora'}}]})
    with pytest.raises(ValueError,match='positive powers'):
        DLoRa(np.random.default_rng(1),ActionSpace(powers_dbm=[-1,0,2]))
    m = DLoRa(np.random.default_rng(1),ActionSpace(powers_dbm=[-1,0,2]),power_reward_unit='mw')
    assert np.isfinite(m.bonuses[2]).all()
    with pytest.raises(ValueError,match='requires confirmed'):
        Simulation(Profile.model_validate({'groups':[{'confirmed':False,'policy':{'kind':'d_lora'}}]}),tmp_path)


@pytest.mark.parametrize('kind',['adr','badr','gadr'])
def test_legacy_random_channels_are_independent_of_control_rule(kind):
    channels = [868100000,868500000]
    hopping = make(Component(kind=kind,options={'channel_selection':'uniform','frequencies_hz':channels}),'policy',rng=np.random.default_rng(7))
    original = make(Component(kind=kind),'policy',rng=np.random.default_rng(7))
    frequencies = []
    for _ in range(80):
        a,b = hopping.select(OBS),original.select(OBS)
        assert (a.sf,a.tp_dbm,a.cr)==(b.sf,b.tp_dbm,b.cr)
        frequencies.append(a.frequency_hz)
    assert set(frequencies)==set(channels)
    assert 20 < frequencies.count(channels[0]) < 60
    if hasattr(hopping,'select_attempt'):
        assert {hopping.select_attempt(OBS,False).frequency_hz for _ in range(40)}==set(channels)
