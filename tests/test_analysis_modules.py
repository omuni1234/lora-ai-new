import json
import sys
import numpy as np
import pytest
from fastapi.testclient import TestClient
from lorasim.config import Profile, dump_profile
from lorasim.tracking import Tracker
from lorasim.analysis import time_series, compare_runs
from lorasim.analysis_modules import inventory
from lorasim.api import create_app


def fixture(root,n=3):
    root.mkdir(exist_ok=True)
    (root/'profile.yaml').write_text(dump_profile(Profile.model_validate({'duration_s':20,'groups':[{'count':n,'payload_bytes':10}]})))
    (root/'summary.json').write_text(json.dumps({'sim_time_s':20}))
    return Tracker(root,2)


def run(root,id='r',eid='e'):
    return dict(root=root,id=id,experiment_id=eid,experiment_name=eid)


def test_prefix_not_window_and_equal_ed_not_packet_weight(tmp_path):
    t=fixture(tmp_path)
    # ED0 1/3; ED1 1/1; ED2 entirely censored. Prefix average = 2/3, pooled = 1/2.
    for ed,index,outcome in [(0,1,'Received'),(1,1,'Received'),(0,2,'Interference'),(2,1,'Censored'),(0,3,'Interference')]:
        t.write('uplinks',device_id=ed,tx_index=index,outcome=outcome)
    t.close()
    points,width=time_series(tmp_path,3,2,3,2)
    assert width==1 and [p['start'] for p in points]==[1,1]
    assert [p['end'] for p in points]==[2,3]
    assert points[0]['fractions']['Received']==.75
    assert points[1]['fractions']['Received']==pytest.approx(2/3)
    assert points[1]['eligible_ed_count']==2
    report=compare_runs([run(tmp_path)],start=2,end=3)
    assert report['runs'][0]['mean_ed_prr']==0  # non-prefix metrics keep requested interval
    assert report['by_time'][0]['series'][-1]['fractions']['Received']==pytest.approx(2/3)


def test_prefix_matches_brute_force_random_unequal_histories(tmp_path):
    t=fixture(tmp_path,7);rng=np.random.default_rng(9);rows=[]
    for ed in range(7):
        for index in range(1,int(rng.integers(20,70))):
            rows.append((ed,index,str(rng.choice(['Received','Interference','Censored']))))
    rng.shuffle(rows)  # Independent of append/finish ordering and part boundaries.
    for ed,i,o in rows:t.write('uplinks',device_id=ed,tx_index=i,outcome=o)
    t.close()
    points,_=time_series(tmp_path,7,20,60,11)
    for p in points:
        means=[]
        for ed in range(7):
            eligible=[o for e,i,o in rows if e==ed and 1<=i<=p['end'] and o!='Censored']
            if eligible:means.append(eligible.count('Received')/len(eligible))
        assert p['fractions']['Received']==pytest.approx(np.mean(means))
        assert sum(p['fractions'].values())==pytest.approx(1)


def test_selective_scans_and_module_toggles(tmp_path,monkeypatch):
    t=fixture(tmp_path);t.write('uplinks',device_id=0,tx_index=1,outcome='Received');t.close()
    import lorasim.analysis as analysis
    original=analysis.batches;calls=[]
    def tracked(root,table,columns=None):
        calls.append(table)
        return original(root,table,columns)
    monkeypatch.setattr(analysis,'batches',tracked)
    r=compare_runs([run(tmp_path)],modules=[{'kind':'outcomes'},{'kind':'convergence','enabled':False},{'kind':'energy','enabled':False}])
    assert [m['kind'] for m in r['modules']]==['outcomes']
    assert calls==['uplinks'] and not r['runs'][0]['energy']['measured']
    with pytest.raises(ValueError,match='no enabled'):compare_runs([run(tmp_path)],modules=[])


def test_metric_units_denominators_mcu_and_zero_success(tmp_path):
    t=fixture(tmp_path)
    for ed,o in [(0,'Received'),(1,'Interference')]:t.write('uplinks',device_id=ed,tx_index=1,outcome=o)
    t.write('energy',device_id=0,tx_index=1,component='radio',state='TX',energy_j=4.)
    t.write('energy',device_id=0,tx_index=1,component='mcu',state='training',energy_j=2.)
    t.write('energy',device_id=0,tx_index=0,component='radio',state='STANDBY',energy_j=100.)
    for sf,ch,attempt,sec in [(7,868100000,1,1),(9,868300000,2,3)]:
        t.write('transmissions',device_id=0,direction='UL',tx_index=1,start_ns=0,end_ns=sec*10**9,sf=sf,frequency_hz=ch,attempt=attempt)
    t.close()
    selections=[{'kind':'efficiency'},{'kind':'airtime'},{'kind':'reliability','options':{'threshold':.5}}]
    for include,energy in [(False,4),(True,6)]:
        r=compare_runs([run(tmp_path)],include_mcu=include,modules=selections)
        assert all(x['status']=='completed' for x in r['modules'])
        eff=r['modules'][0]['data']['rows'][0]
        assert eff['j_per_received_bit']==energy/80 and eff['received_payload_bps']==4
        air=r['modules'][1]['data']['rows'][0]
        assert air['mean_airtime_s']==2 and air['retransmission_fraction']==.5
        rel=r['modules'][2]['data']['rows'][0]
        assert rel['below_threshold']==.5 and rel['p10']==pytest.approx(.1)
        assert 'device_prr' not in r['runs'][0]  # internal per-ED arrays not sent over HTTP


def test_custom_module_discovery_options_and_failure_isolation(tmp_path,monkeypatch):
    code='''from lorasim.analysis_modules.base import AnalysisModule
from lorasim.interfaces import Options
from pydantic import Field
class Settings(Options):
    multiplier: float=Field(2,gt=0,title="倍率",description="表示値の倍率")
class Metric(AnalysisModule):
    Options=Settings
    label="研究者の分析"
    def compute(self,c,o): return {"views":[{"type":"table","title":"検証","columns":["値"],"rows":[[len(c.runs)*o.multiplier]]}]}
class Broken(AnalysisModule):
    def compute(self,c,o): raise ValueError("test failure")
'''
    (tmp_path/'custom_metrics.py').write_text(code);monkeypatch.syspath_prepend(str(tmp_path))
    t=fixture(tmp_path/'data');t.close();c=TestClient(create_app(tmp_path/'store'))
    assert c.get('/api/analysis/modules/schema?kind=custom_metrics:Metric').status_code==422
    monkeypatch.setenv('LORASIM_ALLOW_PLUGINS','1')
    assert c.get('/api/analysis/modules/schema?kind=custom_metrics:Metric').json()['defaults']['multiplier']==2
    r=compare_runs([run(tmp_path/'data')],modules=[{'kind':'custom_metrics:Broken'},{'kind':'custom_metrics:Metric','options':{'multiplier':3}}])
    assert r['modules'][0]['status']=='failed' and r['modules'][0]['diagnostics'][0]['hint']
    assert r['modules'][1]['data']['views'][0]['rows']==[[3]]
    sys.modules.pop('custom_metrics',None)


def test_registry_schemas_and_invalid_options():
    modules=inventory()
    assert {'outcomes','convergence','fairness','energy','reliability','airtime','efficiency','abstract','prr_by_count','spatial_sf','sema_pool','stationary_distance','channel_distribution','channel_js','mobility_outcomes'} == set(modules)
    from lorasim.analysis_modules import prepare
    with pytest.raises(ValueError):prepare([{'kind':'reliability','options':{'threshold':1.1}}])
    with pytest.raises(ValueError):prepare([{'kind':'outcomes'},{'kind':'outcomes'}])


def test_airtime_is_clipped_at_observed_horizon(tmp_path):
    t=fixture(tmp_path)
    t.write('transmissions',direction='UL',tx_index=1,start_ns=19*10**9,end_ns=23*10**9,sf=12,frequency_hz=868100000,attempt=1)
    t.close()
    r=compare_runs([run(tmp_path)],modules=[{'kind':'airtime'}])
    data=r['modules'][0]['data']['rows'][0]
    assert data['total_airtime_s']==1 and data['airtime_basis']=='observed'


def test_empty_or_all_censored_metrics_are_not_success(tmp_path):
    t=fixture(tmp_path);t.write('uplinks',device_id=0,tx_index=1,outcome='Censored');t.close()
    r=compare_runs([run(tmp_path)],modules=[{'kind':k} for k in inventory()])
    assert r['runs'][0]['mean_ed_prr'] is None and r['runs'][0]['censored']==1
    assert all(m['status']=='completed' for m in r['modules'])
    eff=next(m for m in r['modules'] if m['kind']=='efficiency')['data']['rows'][0]
    assert eff['j_per_received_bit'] is None and eff['received_bit_per_j'] is None
