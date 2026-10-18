import json
from pathlib import Path
import numpy as np
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from lorasim.config import Profile, Radio, RadioEnergy, load_profile, dump_profile
from lorasim.components import Observation
from lorasim.algorithms import ADR, ADRServer, BlindADR
from lorasim.energy import RadioMeter
from lorasim.engine import Simulation
from lorasim.tracking import Tracker
from lorasim.analysis import analyze_run, fairness
from lorasim.api import create_app
from lorasim.jobs import Store
from lorasim.visualization import layout


def profile(**kwargs):
    data={'name':'extension-test','duration_s':45,'area_m':[100,100], 'mac':{'duty_cycle':False},
          'gateways':[{'id':'gw','position':[50,50,15]}],
          'groups':[{'name':'ed','count':1,'placement':'grid','confirmed':False,
                     'policy':{'kind':'adr','options':{'history_range':2}},
                     'timing':{'kind':'periodic','options':{'interval_s':8}}}]}
    data.update(kwargs)
    return Profile.model_validate(data)


def test_catalog_references_files_override_cycle_and_snapshot(tmp_path):
    (tmp_path/'models.yaml').write_text('policy:\n  baseline: {kind: fixed, options: {sf: 9, tp_dbm: 10}}\n')
    (tmp_path/'profile.yaml').write_text('model_files: [models.yaml]\ngroups:\n - name: x\n   policy: {ref: baseline, options: {tp_dbm: 12}}\n')
    p=load_profile(tmp_path/'profile.yaml')
    assert p.model_files==[]
    c=p.component(p.groups[0].policy,'policy')
    assert c.kind=='fixed' and c.options=={'sf':9,'tp_dbm':12}
    assert load_profile(dump_profile(p),text=True).component(p.groups[0].policy,'policy')==c
    p.models['policy']['baseline'].ref='baseline'
    with pytest.raises(ValueError,match='cyclic'):p.component(p.groups[0].policy,'policy')
    with pytest.raises(ValueError,match='CLI-only'):load_profile('model_files: [/etc/passwd]',text=True)
    store=Store(tmp_path/'db');store.save_model('policy','base',{'kind':'fixed','options':{'sf':9}})
    text='groups: [{name: ed, policy: {ref: base}}]'
    jid=store.submit(text)
    store.save_model('policy','base',{'kind':'fixed','options':{'sf':12}})
    old=load_profile(store.get(jid)['profile'],text=True)
    assert old.component(old.groups[0].policy,'policy').options['sf']==9


def test_adr_server_vectors_and_history():
    server=ADRServer((2,'max','max',True,0))
    assert server.request(0,Radio(sf=12),[-100,-120]) is None
    assert server.request(0,Radio(sf=12),[-100]) is None  # repeat frame
    r=server.request(1,Radio(sf=12),[-100])
    assert (r.sf,r.tp_dbm)==(7,2)
    poor=ADRServer((1,'min','avg',True,0))
    r=poor.request(0,Radio(sf=7,tp_dbm=2),[-140])
    assert (r.sf,r.tp_dbm)==(7,14)
    no_power=ADRServer((1,'max','max',False,0))
    assert no_power.request(0,Radio(sf=7,tp_dbm=14),[-100]) is None


def test_adr_backoff_and_any_downlink_reset():
    policy=ADR(np.random.default_rng(1),initial={'sf':10,'tp_dbm':8})
    obs=Observation(0,0,0,None,None,None,None,None)
    for _ in range(64):policy.sent(True)
    assert policy.ack_requested
    policy.counter=96
    assert policy.select(obs).tp_dbm==14
    assert policy.select(obs).sf==10
    policy.counter=128
    assert policy.select(obs).sf==11
    policy.counter=160
    assert policy.select(obs).sf==12
    policy.counter=192
    policy.select(obs)
    assert policy.default_channels and policy.reset_repetitions
    policy.downlink(None)
    assert policy.counter==0 and not policy.ack_requested
    policy.sent(False)
    assert policy.counter==0


def test_badr_sequence_is_repeatable():
    p=BlindADR(np.random.default_rng(1),sequence=[12,7,10],tp_dbm=10)
    assert [p.select(None).sf for _ in range(7)]==[12,7,10,12,7,10,12]


def test_adr_changes_only_after_received_downlink(tmp_path):
    p=profile();sim=Simulation(p,tmp_path/'ok');sim.run()
    tx=pq.read_table(tmp_path/'ok/transmissions').to_pylist()
    ul=[x for x in tx if x['direction']=='UL'];dl=[x for x in tx if x['direction']=='DL']
    assert ul[0]['sf']==ul[1]['sf']==12
    assert ul[2]['sf']==7 and ul[2]['tx_power_dbm']==2
    assert dl[0]['link_adr_sf']==7 and dl[0]['phy_bytes']==14
    assert ul[2]['phy_bytes']==31 # 20 + model header 9 + LinkADRAns 2
    events=pq.read_table(tmp_path/'ok/events').to_pylist()
    assert any(x['event']=='LinkADRReqApplied' for x in events)
    blocked=profile(mac={'duty_cycle':False,'backhaul_delay_s':.4})
    Simulation(blocked,tmp_path/'miss').run()
    tx=pq.read_table(tmp_path/'miss/transmissions').to_pylist()
    assert {x['sf'] for x in tx if x['direction']=='UL'}=={12}


@pytest.mark.parametrize('linear',[False,True])
def test_radio_meter_integral_all_states(tmp_path,linear):
    config=RadioEnergy(tx_current_model='linear' if linear else 'constant')
    meter=RadioMeter(config);tracker=Tracker(tmp_path)
    for when,state in [(1,'TX'),(3,'SLEEP'),(4,'RX_ENABLED'),(5,'RX_ACTIVE'),(6,'STANDBY'),(7,'STANDBY')]:
        meter.transition(int(when*1e9),state,1,tracker,0,14 if state=='TX' else None)
    tracker.close()
    tx_current=10**(-1.6)/(3.3*.452750)+.014646 if linear else .028
    expected=3.*(2*.0014+2*tx_current+1e-7+2*.0108)
    assert meter.total==pytest.approx(expected,abs=1e-14)
    assert sum(pq.read_table(tmp_path/'energy')['energy_j'].to_pylist())==pytest.approx(expected)


def test_energy_sum_mcu_separation_horizon(tmp_path):
    p=profile(duration_s=10,energy={'mcu':{'enabled':True,'inference_s':.01,'training_s':.02,'active_power_w':.1,'idle_power_w':.001}})
    summary=Simulation(p,tmp_path).run()
    rows=pq.read_table(tmp_path/'energy').to_pylist()
    assert sum(r['energy_j'] for r in rows if r['component']=='radio')==pytest.approx(summary['energy']['radio_j'])
    assert sum(r['energy_j'] for r in rows if r['component']=='mcu')==pytest.approx(summary['energy']['mcu_j'])
    radio=[r for r in rows if r['component']=='radio']
    assert sum(r['duration_s'] for r in radio)==pytest.approx(10)
    assert max(r['end_ns'] for r in radio)==10_000_000_000
    base=analyze_run(tmp_path,include_mcu=False)
    plus=analyze_run(tmp_path,include_mcu=True)
    assert plus['energy']['total_j']>base['energy']['total_j']


def test_equal_ed_weight_range_censored_and_fairness(tmp_path):
    p=profile(groups=[{'name':'ed','count':3}]);(tmp_path/'profile.yaml').write_text(dump_profile(p))
    tr=Tracker(tmp_path,2)
    for ed,ordinal,outcome in [(0,1,'Received'),(0,2,'Received'),(0,3,'Received'),(1,1,'Interference'),(1,2,'Censored')]:
        tr.write('uplinks',device_id=ed,tx_index=ordinal,outcome=outcome)
    tr.close()
    result=analyze_run(tmp_path)
    assert result['mean_ed_prr']==.5 and result['pooled_prr']==.75
    assert result['fractions']=={'Interference':.5,'Received':.5}
    assert result['censored']==1 and result['inactive_ed_count']==1
    assert result['fairness']['jain']==.5 and result['fairness']['gini']==.5
    assert analyze_run(tmp_path,start=2)['mean_ed_prr']==1.
    assert fairness([0,0])['jain'] is None
    assert fairness([1,1,1])['gini']==pytest.approx(0)
    assert result['series'][0]['fractions']['Received']==.5


def test_experiment_scope_and_visualization_read_only(tmp_path):
    store=Store(tmp_path);e1=store.create_experiment('A');e2=store.create_experiment('B')
    p=profile(duration_s=5)
    ids=[]
    for eid in [e1,e2]:
        jid=store.submit(dump_profile(p),eid);ids.append(jid)
        result=Simulation(p,tmp_path/'runs'/jid).run();store.finish(jid,'completed',result)
    c=TestClient(create_app(tmp_path))
    r=c.post('/api/analysis',json={'experiment_ids':[e1]}).json()
    assert len(r['runs'])==1 and r['runs'][0]['id']==ids[0]
    assert c.post('/api/analysis',json={'experiment_ids':[]}).status_code==422
    assert c.post('/api/analysis',json={'experiment_ids':[e1],'tx_start':10,'tx_end':1}).status_code==422
    paths=list((tmp_path/'runs'/ids[0]).rglob('*'));before={x:x.stat().st_mtime_ns for x in paths if x.is_file()}
    assert c.get('/api/jobs/'+ids[0]+'/visualization?traffic=false').status_code==200
    assert before=={x:x.stat().st_mtime_ns for x in before}
    assert c.get('/api/jobs/'+ids[0]+'/abstract').json()['ed_count']==1


def test_layout_reconstructs_mobility_without_touching_simulator(tmp_path):
    p=profile(groups=[{'name':'ed','count':15,'mobility':{'kind':'random_walk','options':{'speed_m_s':1,'step_s':2}}}])
    sim=Simulation(p,tmp_path)
    view=layout(p,20,limit=5)
    for d in view['devices']:
        assert d['xyz']==pytest.approx(sim.devices[d['id']].mobility.position(20))


def test_deferred_inference_without_transmission_is_separate(tmp_path):
    p=profile(duration_s=4,mac={'duty_cycle':True},energy={'mcu':{'enabled':True}},
              groups=[{'name':'ed','count':1,'confirmed':False,
                       'timing':{'kind':'periodic','options':{'interval_s':.1}}}])
    Simulation(p,tmp_path).run()
    rows=pq.read_table(tmp_path/'energy').to_pylist()
    assert any(r['state']=='inference_untransmitted' and r['tx_index']==0 for r in rows)
    assert max(r['tx_index'] for r in rows)==1


def test_adr_request_bit_is_the_transmitted_bit(tmp_path):
    p=profile(duration_s=20)
    p.groups[0].policy.options={'initial':{'sf':7,'tp_dbm':14},'history_range':100,'ack_limit':1,'ack_delay':32}
    Simulation(p,tmp_path).run()
    tx=pq.read_table(tmp_path/'transmissions').to_pylist()
    ul=[r for r in tx if r['direction']=='UL'];dl=[r for r in tx if r['direction']=='DL']
    assert not ul[0]['adr_ack_req'] and ul[1]['adr_ack_req']
    assert dl[0]['tx_index']==2
    assert not ul[2]['adr_ack_req']


def test_catalog_rejects_invalid_builtin(tmp_path):
    with pytest.raises(Exception):
        Store(tmp_path).save_model('policy','bad',{'kind':'fixed','options':{'sf':99}})


def test_cancelled_before_start_does_not_break_experiment_analysis(tmp_path):
    store=Store(tmp_path);eid=store.create_experiment('cancelled')
    jid=store.submit(dump_profile(profile()),eid);store.cancel(jid)
    c=TestClient(create_app(tmp_path))
    response=c.post('/api/analysis',json={'experiment_ids':[eid]})
    assert response.status_code==200
    assert response.json()['runs']==[]
    assert response.json()['skipped_runs'][0]['id']==jid
