import json
from pathlib import Path
import numpy as np
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from lorasim.api import create_app
from lorasim.components import make
from lorasim.config import Profile, Component, dump_profile
from lorasim.engine import Simulation
from lorasim.interfaces import Observation, Policy, Mobility, Application, NetworkPolicy
from lorasim.jobs import Store
from lorasim.registry import inventory, metadata, constructor
from lorasim.visualization import preflight

OBS = Observation(0,0,0,None,None,None,None,None)


def test_all_builtins_share_schema_and_interfaces():
    for category,items in inventory().items():
        base={'policy':Policy,'timing':Application,'mobility':Mobility}[category]
        for name,meta in items.items():
            cls=constructor(category,name)
            assert issubclass(cls,base)
            assert constructor(category,meta['module']) is cls
            assert 'lorasim.plugins.' in cls.__module__
            assert not meta['legacy']
            for prop in meta['schema'].get('properties',{}).values():
                assert prop['title'] and prop['description']
            context={'rng':np.random.default_rng(4)}
            if category=='mobility':context.update(initial=(1,1,1.5),area=(2000,2000))
            make(Component(kind=name,options=meta['defaults']),category,**context)
    assert issubclass(constructor('policy','adr'),NetworkPolicy)


def test_user_schema_constructed_like_builtin(monkeypatch):
    examples=Path(__file__).parents[1]/'examples'
    monkeypatch.syspath_prepend(str(examples))
    monkeypatch.delenv('LORASIM_ALLOW_PLUGINS',raising=False)
    with pytest.raises(ValueError,match='LORASIM_ALLOW_PLUGINS'):
        metadata('timing','exposed_models:UniformStart')
    monkeypatch.setenv('LORASIM_ALLOW_PLUGINS','1')
    meta=metadata('timing','exposed_models:UniformStart')
    assert meta['schema']['properties']['start_max_s']['title']=='開始時刻の上限 [s]'
    plugin=make(Component(kind=meta['kind'],options={'interval_s':17,'start_max_s':40}),'timing',rng=np.random.default_rng(3))
    start=plugin.next_time(OBS,None)
    assert 0<=start<40
    assert plugin.next_time(OBS,start)==start+17
    with pytest.raises(ValueError):
        make(Component(kind=meta['kind'],options={'interval_s':-1}),'timing',rng=np.random.default_rng(3))
    with pytest.raises(ValueError,match='category'):
        constructor('mobility',meta['kind'])


def test_start_uniform_independent_interval_and_stream_reproduction(tmp_path):
    p=Profile.model_validate({'seed':48,'duration_s':100,'groups':[
        {'count':8,'timing':{'kind':'periodic','options':{'interval_s':10,'start':{'mode':'uniform','min_s':20,'max_s':40}}}},
        {'count':4,'timing':{'kind':'periodic','options':{'interval_s':17,'start':{'mode':'fixed','at_s':6}}}}
    ]})
    sim=Simulation(p,tmp_path/'unused')
    view=preflight(p,80,limit=12,samples=4)
    starts=[d.timing.next_time(OBS,None) for d in sim.devices]
    assert all(20<=t<40 for t in starts[:8]) and len(set(starts[:8]))==8
    assert starts[8:]==[6]*4
    assert starts==[d['first_generation_s'] for d in view['devices']]
    assert view['devices'][0]['schedule'][1]['time_s']==starts[0]+10
    assert view['devices'][8]['schedule'][1]['time_s']==23
    assert view['devices'][0]['models']['policy']['options']['tp_dbm']==14
    assert not (tmp_path/'unused').exists()


def test_plugin_settings_invalid_and_legacy_start():
    for opts in [{'interval_s':0},{'interval_s':10,'jitter_s':10},{'start':{'mode':'uniform','min_s':9,'max_s':2}},{'unknown':1}]:
        with pytest.raises(ValueError):make(Component(kind='periodic',options=opts),'timing',rng=np.random.default_rng(1))
    seed=31
    expected=10+np.random.default_rng(seed).uniform(0,90)
    legacy=make(Component(kind='periodic',options={'interval_s':90,'first_s':10,'random_phase':True}),'timing',rng=np.random.default_rng(seed))
    assert legacy.next_time(OBS,None)==expected


def test_auto_ids_reserve_explicit_names_and_roundtrip():
    p=Profile.model_validate({'gateways':[{'position':[0,0,15]},{'id':'gw0','position':[5,5,15]},{'position':[9,9,15]}],
        'groups':[{'count':1},{'name':'group0','count':1}]})
    assert [g.id for g in p.gateways]==['gw1','gw0','gw2']
    assert [g.name for g in p.groups]==['group1','group0']
    assert Profile.model_validate(p.model_dump()).model_dump()==p.model_dump()
    with pytest.raises(ValueError,match='duplicate gateway'):
        Profile.model_validate({'gateways':[{'id':'gw0','position':[0,0,0]}]*2})


def radio_profile(**extra):
    return Profile.model_validate({'duration_s':8,'area_m':[10,10],'gateways':[{'position':[5,5,15]}],
        'mac':{'duty_cycle':False},'groups':[{'count':1,'confirmed':True}],**extra})


def test_uplink_direction_enforced(tmp_path):
    p=radio_profile(ul_sub_bands=['band4'])
    with pytest.raises(ValueError,match='UL.*disabled'):
        Simulation(p,tmp_path/'blocked').run()
    with pytest.raises(ValueError,match='UL.*disabled'):
        preflight(p)
    with pytest.raises(ValueError,match='DL.*disabled'):
        radio_profile(dl_sub_bands=['band2'])
    with pytest.raises(ValueError,match='known'):
        radio_profile(ul_sub_bands=['missing'])
    with pytest.raises(ValueError):
        radio_profile(ul_sub_bands=[])


def test_dl_disabled_rx1_falls_back_to_rx2(tmp_path):
    p=radio_profile(ul_sub_bands=['band2'],dl_sub_bands=['band4'])
    Simulation(p,tmp_path).run()
    tx=pq.read_table(tmp_path/'transmissions').to_pylist()
    dl=[r for r in tx if r['direction']=='DL']
    assert len(dl)==1 and dl[0]['frequency_hz']==869525000
    events=pq.read_table(tmp_path/'events').to_pylist()
    assert any(e['event']=='RX1SubBandDisabled' for e in events)
    assert pq.read_table(tmp_path/'feedback').to_pylist()[0]['ack']


def test_invalid_channel_registry():
    data=Profile().model_dump()
    data['sub_bands'][0]['channels_hz']=[868100000]
    with pytest.raises(ValueError,match='channels'):
        Profile.model_validate(data)
    data=Profile().model_dump();data['sub_bands'][1]['id']=data['sub_bands'][0]['id']
    with pytest.raises(ValueError,match='duplicate sub-band'):
        Profile.model_validate(data)


def test_custom_preflight_and_assignment(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]/'examples'))
    monkeypatch.setenv('LORASIM_ALLOW_PLUGINS','1')
    p=Profile.model_validate({'groups':[{'count':4,'mobility':{'kind':'exposed_models:LinearMotion'},'timing':{'kind':'exposed_models:UniformStart'},'policy':{'kind':'exposed_models:AckSelector'}}],
        'assignments':[{'ids':[2],'policy':{'kind':'fixed','options':{'sf':12}}}]})
    view=preflight(p,400,limit=4,samples=3)
    d=view['devices'][2]
    assert d['models']['policy']['kind']=='fixed'
    assert d['schedule'][0]['radio']['sf']==12
    assert d['path'][0]['xyz']!=d['path'][-1]['xyz']
    assert not view['warnings']


def test_preview_bounds_and_no_runtime_visualization_poll(tmp_path):
    p=Profile.model_validate({'duration_s':1e9,'groups':[{'count':1000,'mobility':{'kind':'random_walk','options':{'step_s':.01}}}]})
    result=preflight(p,1e9,limit=8,samples=3)
    assert len(result['devices'])<=8 and len(result['transmissions'])<=24
    assert len(result['warnings'])==8
    c=TestClient(create_app(tmp_path))
    assert c.post('/api/preview',json={'yaml':dump_profile(p),'limit':201}).status_code==422
    assert not list((tmp_path/'runs').iterdir())
    js=c.get('/static/app.js').text
    assert '/visualization' not in js and 'refreshMap' not in js


def test_schema_and_experiment_hover(tmp_path):
    store=Store(tmp_path);eid=store.create_experiment('hover','test')
    p=radio_profile();jid=store.submit(dump_profile(p),eid);store.claim()
    summary=Simulation(p,tmp_path/'runs'/jid).run();store.finish(jid,'completed',summary)
    c=TestClient(create_app(tmp_path))
    info=c.get('/api/experiments/'+eid+'/info').json()
    assert info['latest_run']['ed_count']==1 and info['wall_total_s']>0
    assert info['environment']['python'] and info['environment_run_id']==jid
    assert c.get('/api/experiments/missing/info').status_code==404
    schema=c.get('/api/schema').json()
    assert schema['$defs']['RadioEnergy']['properties']['voltage_v']['title']=='駆動電圧 [V]'
    assert c.get('/api/plugins/schema',params={'category':'timing','kind':'periodic'}).json()['schema']['$defs']['StartTime']['properties']['max_s']['title']=='開始時刻の上限 [s]'
    assert c.get('/api/plugins/schema',params={'category':'timing','kind':'bad'}).status_code==422
    manifest=json.loads((tmp_path/'runs'/jid/'manifest.json').read_text())
    assert 'plugins/selector/adr.py' in manifest['source_sha256']
