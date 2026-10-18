"""Per-device ownership and simulator -> selector -> Radio contracts."""
import copy
import json
import numpy as np
import pyarrow.parquet as pq
import pytest
from lorasim.config import Profile, ActionSpace, ActionSelection, Component
from lorasim.engine import Simulation
from lorasim.components import make
from lorasim.interfaces import Feedback, Observation
from lorasim.diagnostics import configuration_warnings


def obs(ed=0): return Observation(1,ed,0,None,None,None,None,(0,0,0))
def feedback(): return Feedback(2,True,-90,.1,14,1,True,-100,10,14)
def key(r): return r.sf,r.frequency_hz,r.cr,r.tp_dbm


def test_216_global_18_effective_and_legacy_scalar_migration():
    global_space=ActionSpace(coding_rates=[1,2,3,4],powers_dbm=[8,11,14])
    assert global_space.size==216 and len(set(map(key,global_space.radios())))==216
    effective=global_space.restrict(ActionSelection(coding_rates=[1],powers_dbm=[14]))
    assert effective.size==18 and global_space.size==216
    old=ActionSpace(tp_dbm=11,cr=2)
    assert old.powers_dbm==[11] and old.coding_rates==[2]
    assert 'tp_dbm' not in old.model_dump() and old.tp_dbm==11
    with pytest.raises(ValueError): ActionSpace(tp_dbm=11,powers_dbm=[14])
    with pytest.raises(ValueError): global_space.restrict(ActionSelection(powers_dbm=[10]))
    with pytest.raises(ValueError): global_space.restrict(ActionSelection(coding_rates=[1,1]))


@pytest.mark.parametrize('kind',['fixed','random','bandit','lora_mab','linucb_sf','dqn','sema'])
def test_same_model_accepts_216_or_18_actions(kind):
    space=ActionSpace(coding_rates=[1,2,3,4],powers_dbm=[8,11,14])
    opts={'hidden_sizes':[3],'batch_size':1,'replay_capacity':4} if kind in {'dqn','sema'} else {}
    for selection,size in [({},216),({'coding_rates':[1],'powers_dbm':[14]},18)]:
        p=make(Component(kind=kind,options=opts,action_space=selection),'policy',rng=np.random.default_rng(2),action_space=space)
        assert len(p.action_keys)==size
        if hasattr(p,'actions'): assert len(p.actions)==size
        for _ in range(3):
            r=p.select(obs());p.observe(feedback())
            assert (r.sf,r.frequency_hz,r.tp_dbm,r.cr) in p.action_keys
            if size==18: assert r.cr==1 and r.tp_dbm==14


def arrays(value,seen=None):
    seen=set() if seen is None else seen
    if id(value) in seen:return []
    seen.add(id(value))
    if isinstance(value,np.ndarray):return [value]
    if isinstance(value,dict):return [a for v in value.values() for a in arrays(v,seen)]
    if isinstance(value,(list,tuple)):return [a for v in value for a in arrays(v,seen)]
    if hasattr(value,'__dict__'):return arrays(vars(value),seen)
    return []


@pytest.mark.parametrize('kind',['bandit','bandit_sf_ch','lora_mab','linucb_sf','dqn','sema'])
def test_library_and_assignment_never_share_learning_state(tmp_path,kind):
    opts={'hidden_sizes':[3],'batch_size':1,'replay_capacity':4} if kind in {'dqn','sema'} else {}
    p=Profile.model_validate(dict(models={'policy':{'shared':{'kind':kind,'options':opts,'action_space':{}}}},
        groups=[{'name':'ed','count':2,'observe_position':True,'policy':{'ref':'shared'}}],
        assignments=[{'ids':[1],'policy':{'ref':'shared'}}]))
    simulation=Simulation(p,tmp_path)
    first,second=[d.policy for d in simulation.devices]
    assert first is not second
    a,b=arrays(first),arrays(second)
    assert a and b and not any(np.shares_memory(x,y) for x in a for y in b)
    before=[x.copy() for x in b]
    rng_state=copy.deepcopy(getattr(second,'rng',getattr(second,'learner',None)).bit_generator.state) if hasattr(getattr(second,'rng',None),'bit_generator') else None
    first.select(simulation.observation(simulation.devices[0]));first.observe(feedback())
    for x,y in zip(before,b):np.testing.assert_array_equal(x,y)
    if rng_state is not None: assert second.rng.bit_generator.state==rng_state
    assert simulation.observation(simulation.devices[1]).device_id==1
    assert simulation.observation(simulation.devices[1]).last_ack is None


def test_engine_feedback_ownership_and_checkpoint(tmp_path):
    p=Profile.model_validate(dict(duration_s=32,mac={'duty_cycle':False},groups=[{'count':2,
        'policy':{'kind':'bandit','action_space':{'spreading_factors':[7]},'options':{'epsilon':.2}},
        'timing':{'kind':'periodic','options':{'interval_s':10,'start':{'mode':'uniform','min_s':0,'max_s':2}}}}]))
    sim=Simulation(p,tmp_path);sim.run()
    feedbacks=pq.read_table(tmp_path/'feedback').to_pylist()
    state=[json.loads(line) for line in (tmp_path/'policy_state.jsonl').read_text().splitlines()]
    for row in state:
        assert sum(row['n'])==sum(f['device_id']==row['device_id'] and f['ack'] is not None for f in feedbacks)
    assert not np.shares_memory(sim.devices[0].policy.q,sim.devices[1].policy.q)


def test_effective_not_just_global_space_is_enforced(tmp_path):
    p=Profile.model_validate({'groups':[{'count':1,'policy':{'kind':'fixed','action_space':{'spreading_factors':[7]}}}]})
    sim=Simulation(p,tmp_path)
    sim.devices[0].policy.radio.sf=8  # still globally legal but not legal for this ED
    with pytest.raises(ValueError,match='outside the common'):sim.run()


def test_unsupported_or_wrong_category_space_is_not_silently_ignored():
    with pytest.raises(ValueError,match='does not accept'):Profile.model_validate({'groups':[{'policy':{'kind':'linucb_sf_tp','action_space':{}}}]})
    with pytest.raises(ValueError,match='only available'):make(Component(kind='stationary',action_space={}),'mobility',initial=(0,0,0),area=(1,1),rng=np.random.default_rng(1))
    p=Profile.model_validate({'groups':[{'policy':{'kind':'bandit','action_space':{},'options':{'actions':[{'sf':8}]}}}]})
    assert any(w['code']=='ACTION_OPTIONS_IGNORED' for w in configuration_warnings(p))


def test_reusable_library_space_checked_against_profile_on_use(tmp_path):
    from lorasim.jobs import Store
    store=Store(tmp_path)
    store.save_model('policy','reusable',{'kind':'bandit','action_space':{'powers_dbm':[11],'coding_rates':[2]}})
    spec=Component(ref='reusable')
    # Library can be saved before the experiment defines its candidates.
    from lorasim.config import load_profile
    text='groups: [{policy: {ref: reusable}}]'
    with pytest.raises(ValueError): store.parse_profile(text)
    p=store.parse_profile('action_space: {powers_dbm: [8, 11, 14], coding_rates: [1, 2]}\n'+text)
    assert p.component(p.groups[0].policy,'policy').action_space.powers_dbm==[11]
