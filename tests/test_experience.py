import json
import numpy as np
import pytest
from fastapi.testclient import TestClient
from lorasim.api import create_app
from lorasim.config import Profile
from lorasim.experience import ExperienceOptions, ExperienceError, experience, selected_devices
from lorasim.components import make
from lorasim.mobility_environment import MobilityEnvironment


def profile(points, count=1):
    return Profile.model_validate({'duration_s':200,'area_m':[200,100], 'groups':[{
        'name':'test','count':count,'mobility':{'kind':'waypoints','options':{'mode':'timed','points':points}},
        'timing':{'kind':'periodic','options':{'interval_s':1,'start':{'mode':'fixed','at_s':.5}}}}]})


def options(**kw):return ExperienceOptions(time_samples=256,**kw)


def test_equal_stationary_moving_counts_do_not_imply_equal_site_experience():
    one=experience(profile([[0,0,0,1.5],[100,0,0,1.5],[200,100,0,1.5]]),options())['devices'][0]
    two=experience(profile([[0,0,0,1.5],[50,0,0,1.5],[100,50,0,1.5],[150,50,0,1.5],[200,100,0,1.5]]),options())['devices'][0]
    assert one['states']==two['states']=={'stationary':100,'moving':100,'unknown':0}
    assert one['dominant_site_fraction']==.5 and two['dominant_site_fraction']==.25
    assert one['effective_stationary_sites']==1 and two['effective_stationary_sites']==2
    assert one['longest_same_site_streak']==100 and two['longest_same_site_streak']==50
    assert one['stationary_sites'][0]['stationary_fraction']==1


def test_stationary_distribution_is_concentrated_without_timing_bias():
    p=Profile(duration_s=300)
    r=experience(p,options())
    for row in r['devices']:
        assert row['dominant_site_fraction']==1
        assert row['spatial_tv']==0
        assert row['effective_stationary_sites']==1
    assert r['aggregate']['mean_dominant_site_fraction']==1


def test_equal_tx_positions_after_return_are_not_classified_stationary():
    p=profile([[0,0,0,1.5]])
    p.groups[0].placement='explicit';p.groups[0].positions=[(0,0,1.5)]
    p.groups[0].mobility.options={'mode':'route','destinations':[[1,0,1.5],[0,0,1.5]],'speed_m_s':1,'pause_s':0,'repeat':True}
    p.groups[0].timing.options={'interval_s':2,'start':{'mode':'fixed','at_s':.5}}
    r=experience(p,options())['devices'][0]
    assert r['states']['moving']==100 and not r['stationary_sites']
    assert max(r['tx_grid'])==100
    # Exact round-trip boundaries also are not stationary when the next leg starts.
    p.groups[0].timing.options['start']['at_s']=0
    r=experience(p,options(concentration_basis='spatial_cell'))['devices'][0]
    assert r['states']['moving']==100 and r['concentration_state']=='concentrated'


def test_window_end_exclusive_no_generations_is_not_zero_bias():
    p=profile([[0,0,0,1.5]])
    p.groups[0].timing.options={'interval_s':100,'start':{'mode':'fixed','at_s':50}}
    r=experience(p,options(start_s=0,end_s=50))
    assert r['devices'][0]['generations']==0
    assert r['devices'][0]['spatial_tv'] is None
    assert r['aggregate']['mean_dominant_site_fraction'] is None
    assert r['warnings']


def test_sampling_resolves_assignments_and_matches_initial_rng():
    p=Profile.model_validate({'groups':[{'name':'x','count':20},{'name':'y','count':5}],
        'assignments':[{'ids':[17],'timing':{'kind':'periodic','options':{'interval_s':42}}}]})
    i,g,xyz,specs,_=next(selected_devices(p,options(device_id=17)))
    expected=np.random.default_rng(np.random.SeedSequence([p.seed,0,900])).uniform([0,0],p.area_m,(20,2))[17]
    assert np.allclose(xyz[:2],expected)
    assert specs['timing'].options['interval_s']==42
    i,g,xyz,specs,_=next(selected_devices(p,options(device_id=23)))
    expected=np.random.default_rng(np.random.SeedSequence([p.seed,20,900])).uniform([0,0],p.area_m,(5,2))[3]
    assert np.allclose(xyz[:2],expected)


def test_request_is_reproducible_bounded_and_preserves_profile():
    p=Profile.model_validate({'duration_s':1000,'groups':[{'name':'x','count':4,'mobility':{'kind':'waypoints'},'timing':{'kind':'periodic','options':{'interval_s':10}}}]})
    before=p.model_dump_json()
    a,b=experience(p,options()),experience(p,options())
    a.pop('wall_s');b.pop('wall_s')
    assert a==b and before==p.model_dump_json()
    assert sum(a['devices'][0]['states'].values())==100
    assert a['devices'][0]!=a['devices'][1]
    with pytest.raises(ExperienceError,match='生成回数'):
        experience(p,options(max_generations=2))


@pytest.mark.parametrize('kind,opts',[('stationary',{}),('random_walk',{}),('markov',{}),('waypoints',{}),
    ('waypoints',{'mode':'route','destinations':[[10,10,1.5],[100,100,1.5]],'pause_s':2,'repeat':True}),
    ('waypoints',{'mode':'timed','points':[[0,10,10,1.5],[100,20,20,1.5],[150,20,20,1.5]]})])
def test_state_hook_never_advances_rng_or_changes_trajectory(kind,opts):
    from lorasim.config import Component
    def instance():return make(Component(kind=kind,options=opts),'mobility',initial=[10,10,1.5],area=[200,200],rng=np.random.default_rng(42),environment=MobilityEnvironment(1))
    a,b=instance(),instance()
    for t in np.linspace(0,300,30):
        x=a.position(t)
        assert a.motion_state(t) in ('moving','stationary')
        assert a.motion_state(t) in ('moving','stationary')
        assert np.array_equal(x,b.position(t))


def test_api_adds_no_run_and_surfaces_budget_error(tmp_path):
    c=TestClient(create_app(tmp_path));p=profile([[0,0,0,1.5]])
    r=c.post('/api/calculator/experience',json={'yaml':p.model_dump_json(),'options':{'max_generations':2}})
    assert r.status_code==422 and '上限' in r.text and 'EXPERIENCE' in r.text
    r=c.post('/api/calculator/experience',json={'yaml':p.model_dump_json(),'options':{'end_s':20}})
    assert r.status_code==200 and r.json()['devices'][0]['generations']==20
    assert c.get('/api/jobs').json()==[]


def test_selector_changes_do_not_change_generated_experience():
    p=profile([[0,0,0,1.5],[100,0,0,1.5],[200,100,0,1.5]])
    a=experience(p,options())
    # Invalid selector constructor is intentionally never loaded by this tool.
    p.groups[0].policy.kind='not-a-selector'
    b=experience(p,options())
    assert a['devices']==b['devices']
    assert a['concentrated_devices']==b['concentrated_devices']==1
    c=experience(p,options(concentration_threshold=.6))
    assert c['concentrated_devices']==0 and c['assessed_devices']==1


def test_unknown_state_and_unsafe_plugins_are_not_silently_approximated(monkeypatch):
    from lorasim.plugins.mobility.stationary import Stationary
    p=Profile(duration_s=300)
    monkeypatch.setattr(Stationary,'motion_state',lambda self,t:'unknown')
    r=experience(p,options())
    assert r['assessed_devices']==0 and r['devices'][0]['states']['unknown']>0
    monkeypatch.setattr(Stationary,'preview_safe',False)
    with pytest.raises(ExperienceError,match='preview_safe'):experience(p,options())


def test_poisson_uses_model_schedule_without_feedback():
    p=profile([[0,0,0,1.5]])
    p.groups[0].timing.kind='poisson';p.groups[0].timing.options={'mean_s':5}
    r=experience(p,options())['devices'][0]
    rng=np.random.default_rng(np.random.SeedSequence([p.seed,0,1]))
    times=np.cumsum(rng.exponential(5,8))
    assert np.allclose(r['first_generation_times'],times)
    assert sum(map(sum,r['timeline']))==r['generations']
