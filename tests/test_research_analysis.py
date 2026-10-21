"""Hand-computed fixtures, old-schema behavior and recording equivalence."""
import json
import math
from pathlib import Path
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from lorasim.analysis import compare_runs
from lorasim.analysis_modules.channel_distribution import kl
from lorasim.analysis_modules import inventory
from lorasim.config import Profile, dump_profile
from lorasim.engine import Simulation
from lorasim.tracking import Tracker


def setup(root,n=2,mobility='stationary',policy='fixed',duration=10):
    root.mkdir(exist_ok=True)
    p=Profile.model_validate(dict(duration_s=duration,area_m=[100,100],gateways=[dict(id='gw0',position=[0,0,0])],
        groups=[dict(count=n,mobility=dict(kind=mobility),policy=dict(kind=policy))]))
    (root/'profile.yaml').write_text(dump_profile(p))
    (root/'summary.json').write_text(json.dumps(dict(sim_time_s=duration)))
    return Tracker(root,batch_rows=2)


def descriptor(root,ident='r',eid='e'): return dict(root=root,id=ident,experiment_id=eid,experiment_name=eid)

def result(roots,kind,options=None,**kwargs):
    value=compare_runs(roots,modules=[dict(kind=kind,options=options or {})],**kwargs)['modules'][0]
    assert value['status']=='completed',value
    json.dumps(value,allow_nan=False)
    return value['data']


def ul(t,ed,index,outcome='Received',x=10,state='stationary'):
    t.write('uplinks',device_id=ed,tx_index=index,time_ns=index*10**9,outcome=outcome,x_m=x,y_m=0.,z_m=0.,motion_state=state)


def tx(t,ed,index,ch=868100000,sf=7,x=10,y=10):
    t.write('transmissions',direction='UL',device_id=ed,tx_index=index,frequency_hz=ch,sf=sf,x_m=x,y_m=y)


def test_abstract_ed_count_tables_and_prr_line_do_not_merge_settings(tmp_path):
    runs=[]
    for i,(n,received,seed) in enumerate([(2,1,1),(2,2,2),(3,3,1)]):
        root=tmp_path/str(i);t=setup(root,n=n)
        p=Profile.model_validate(__import__('yaml').safe_load((root/'profile.yaml').read_text()));p.seed=seed
        (root/'profile.yaml').write_text(dump_profile(p))
        for ed in range(n):ul(t,ed,1,'Received' if ed<received else 'Interference')
        t.close();runs.append(descriptor(root,str(i)))
    abstract=result(runs,'abstract')
    assert len(abstract['views'])==2 and len(abstract['views'][0]['rows'])==2
    lines=result(runs,'prr_by_count')['views'][0]['series']
    assert len(lines)==1
    assert lines[0]['points']==[dict(x=2,y=.75,low=.5,high=1.,n=2),dict(x=3,y=1.,low=1.,high=1.,n=1)]
    # Change a fixed selector constant: same name, different configuration -> separate series.
    p.groups[0].policy.options={'sf':9}
    (runs[-1]['root']/'profile.yaml').write_text(dump_profile(p))
    assert len(result(runs,'prr_by_count')['views'][0]['series'])==2


def test_sf_heatmap_uses_actual_ul_position_counts_range_and_device(tmp_path):
    t=setup(tmp_path)
    tx(t,0,1,sf=7);tx(t,0,2,sf=12);tx(t,1,2,sf=12);tx(t,1,3,sf=9,x=100,y=100)
    tx(t,0,2,sf=8,x=-1)
    t.write('transmissions',direction='DL',device_id=0,tx_index=2,frequency_hz=868100000,sf=8,x_m=10,y_m=10)
    t.close();runs=[descriptor(tmp_path)]
    data=result(runs,'spatial_sf',{'grid_size':2,'sf':12},start=2,end=2)
    first=data['views'][0]['cells'][0]
    assert first==dict(value=1.,n=2,counts=[0,0,0,0,0,2])
    assert all(c['value'] is None for c in data['views'][0]['cells'][1:])
    assert data['notes']
    modal=result(runs,'spatial_sf',{'grid_size':2,'device_id':0})['views'][0]['cells'][0]
    assert modal['value']==7 and modal['n']==2  # explicit tie rule, all SF counts retained


def test_distance_prr_equal_ed_and_cumulative_and_intervals(tmp_path):
    t=setup(tmp_path,mobility='random_walk')
    ul(t,0,1);ul(t,0,2,'Interference');ul(t,0,3,'Interference');ul(t,1,1)
    ul(t,1,2,x=40,state='moving');ul(t,1,3,x=40,state='unknown');ul(t,0,4,'Censored')
    t.close();runs=[descriptor(tmp_path)]
    data=result(runs,'stationary_distance',dict(distance_edges_m=[0,20,100],bins=4))
    points=data['views'][0]['series'][0]['points']
    assert points[-1]['y']==pytest.approx(2/3) and points[-1]['n']==2 and points[-1]['attempts']==4
    assert all(p['y'] is None for p in data['views'][1]['series'][0]['points'])
    assert data['views'][-1]['rows'][0][1:]==[4,1,1,0]
    window=result(runs,'stationary_distance',dict(distance_edges_m=[0,20,100],bins=3,cumulative=False),start=2,end=3)
    assert all(p['y']==0 for p in window['views'][0]['series'][0]['points'])
    timed=result(runs,'stationary_distance',dict(distance_edges_m=[0,20],axis='time_s',bins=11))
    assert timed['views'][0]['series'][0]['points'][-1]['y']==pytest.approx(2/3)


def test_old_moving_data_is_unknown_and_old_stationary_safe(tmp_path):
    for mobility in ['stationary','random_walk']:
        root=tmp_path/mobility;t=setup(root,mobility=mobility);ul(t,0,1);t.close()
        path=root/'uplinks/part-000000.parquet';old=pq.read_table(path).drop(['motion_state']);pq.write_table(old,path)
        data=result([descriptor(root)],'stationary_distance',dict(distance_edges_m=[0,100]))
        assert data['views'][-1]['rows'][0][1:]==([1,0,0,0] if mobility=='stationary' else [0,0,1,0])
    assert result([descriptor(root)],'sema_pool')['notes']


def test_kl_distinguishes_per_ed_bias_from_pooled_uniform_and_outside_support(tmp_path):
    t=setup(tmp_path)
    for i in range(1,4):tx(t,0,i);tx(t,1,i,ch=868300000)
    t.close();runs=[descriptor(tmp_path)]
    options={'reference_channels_hz':[868100000,868300000]}
    data=result(runs,'channel_distribution',options)
    row=data['views'][1]['rows'][0]
    assert row[6]==pytest.approx(math.log(2)) and row[7]==0 and row[8]==pytest.approx(2)
    outside=result(runs,'channel_distribution',{'reference_channels_hz':[868100000]})
    assert outside['views'][0]['items'][0]['value'] is None
    assert '∞' in outside['views'][1]['rows'][0][6] and outside['notes']
    assert kl({1:1},{1:0,2:1}) is None


def test_js_is_bounded_symmetric_distribution_difference(tmp_path):
    runs=[]
    for i,ch in enumerate([868100000,868300000,868100000]):
        root=tmp_path/str(i);t=setup(root);tx(t,0,1,ch);t.close();runs.append(descriptor(root,str(i)))
    rows=result(runs,'channel_js')['views'][0]['rows']
    assert [r[-1] for r in rows]==pytest.approx([math.log(2),0,math.log(2)])


def test_sema_exact_changes_per_ed_and_aggregate_survival(tmp_path):
    t=setup(tmp_path);arms=[['A','A'],['B','C']]
    for ed in (0,1):
        ul(t,ed,1);ul(t,ed,2)
        t.write('selector_states',device_id=ed,time_ns=0,tx_index=0,experience=0,arms=json.dumps(arms),active_arms=[0,1])
    t.write('selector_states',device_id=0,time_ns=2*10**9,tx_index=2,experience=2,arms=json.dumps(arms),active_arms=[0])
    t.close();runs=[descriptor(tmp_path)]
    data=result(runs,'sema_pool',dict(bins=3))
    second=data['views'][0]['series'][1]['points']
    assert next(p['y'] for p in second if p['x']==2)==.5
    assert second[-1]['y']==.5
    assert data['views'][2]['series'][0]['points'][-1]['y']==1.5
    individual=result(runs,'sema_pool',dict(device_id=0,axis='tx_index',bins=2))
    assert individual['views'][0]['series'][1]['points'][-1]['y']==0
    assert len(individual['views'][-1]['rows'])==2
    assert result(runs,'sema_pool',dict(device_id=99))['views']==[]


def test_simulation_records_states_and_sema_trace_without_changing_outcomes(tmp_path):
    spec=dict(duration_s=20,mac=dict(duty_cycle=False),groups=[dict(count=2,observe_position=True,policy=dict(kind='sema',options=dict(batch_size=2,hidden_sizes=[2],replay_capacity=4)),timing=dict(kind='periodic',options=dict(interval_s=5,first_s=0)))])
    p=Profile.model_validate(spec)
    sim=Simulation(p,tmp_path/'traced');summary=sim.run()
    states=pq.read_table(tmp_path/'traced/selector_states').to_pylist()
    assert len(states)>=2 and all(x['tx_index']==0 for x in states[:2])
    assert all(x['motion_state']=='stationary' for x in pq.read_table(tmp_path/'traced/uplinks').to_pylist())
    assert json.loads((tmp_path/'traced/manifest.json').read_text())['schema_version']==5
    # Read-only diagnostics must not alter RNGs, radio events or learning.
    reference=Simulation(p,tmp_path/'reference');reference._selector_trace=lambda d:None
    reference.run()
    for name in ('transmissions','uplinks','receptions','feedback'):
        assert pq.read_table(tmp_path/'traced'/name).equals(pq.read_table(tmp_path/'reference'/name))
    assert (tmp_path/'traced/policy_state.jsonl').read_bytes()==(tmp_path/'reference/policy_state.jsonl').read_bytes()


def test_new_modules_expose_explanations_and_validate_distances():
    from lorasim.analysis_modules.stationary_distance import Settings
    for edges in ([5,0],[0,1,1],[-1,2]):
        with pytest.raises(ValueError):Settings(distance_edges_m=edges)
    for key in ['stationary_distance','spatial_sf','sema_pool','channel_distribution']:
        for prop in inventory()[key]['schema']['properties'].values():assert prop.get('description')

def test_bounded_single_endpoint_is_final_not_first():
    from lorasim.analysis_modules._common import endpoints
    assert endpoints(1,100,80,1_000_000).tolist()==[100]
