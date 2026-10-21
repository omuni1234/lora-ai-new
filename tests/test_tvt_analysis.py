"""Hand-computed metric fixtures, censoring and independent-seed grouping."""
import json
import numpy as np
import pyarrow.parquet as pq
import pytest
from lorasim.analysis import compare_runs
from lorasim.config import Profile, dump_profile
from lorasim.tracking import Tracker


def fixture(root,n=3,kind='sema',seed=0,**changes):
    root.mkdir(exist_ok=True)
    p=Profile.model_validate(dict(seed=seed,duration_s=100,area_m=[100,100],gateways=[dict(position=[0,0,0])],
        groups=[dict(count=n,observe_position=True,policy=dict(kind=kind))],**changes))
    (root/'profile.yaml').write_text(dump_profile(p))
    (root/'summary.json').write_text(json.dumps(dict(sim_time_s=100)))
    return Tracker(root,2)


def run(root):return dict(root=root,id=root.name,experiment_id=root.name,experiment_name=root.name)


def result(roots,kind,options=None,**kwargs):
    r=compare_runs([run(root) for root in roots],modules=[dict(kind=kind,options=options or {})],**kwargs)['modules'][0]
    assert r['status']=='completed',r
    json.dumps(r,allow_nan=False)
    return r['data']


def ul(t,ed,tx,ok=True,**extra):
    t.write('uplinks',device_id=ed,tx_index=tx,outcome='Censored' if ok is None else 'Received' if ok else 'Interference',**extra)


def test_quality_equal_ed_bottom_ceil_cdf_ack_separate_and_zero(tmp_path):
    t=fixture(tmp_path)
    for ed,tx,ok in [(0,1,True),(0,2,False),(0,3,False),(1,1,True),(2,1,None)]:ul(t,ed,tx,ok)
    t.write('feedback',device_id=0,tx_index=1,ack=False);t.write('feedback',device_id=1,tx_index=1,ack=True)
    t.close();data=result([tmp_path],'tvt_quality')
    row=data['views'][0]['rows'][0]
    assert row[1:3]==[2,3]
    assert row[3:]==pytest.approx([2/3,.8,1/3,.5,.5,2])
    assert [(p['x'],p['y']) for p in data['views'][1]['series'][0]['points']]==[(0,0),(1/3,.5),(1,1)]
    zero=tmp_path/'zero';t=fixture(zero);ul(t,0,1,False);t.close()
    z=result([zero],'tvt_quality')['views'][0]['rows'][0]
    assert z[3]==0 and z[4] is None and z[5:7]==[0,0]


def test_exact_cpu_quantiles_pairing_range_and_old_schema(tmp_path):
    t=fixture(tmp_path)
    for tx in range(1,5):
        ul(t,0,tx)
        t.write('policy_timing',device_id=0,tx_index=tx,operation='inference',cpu_ns=1000*tx,meta_cpu_ns=0)
        if tx<4:t.write('policy_timing',device_id=0,tx_index=tx,operation='training',cpu_ns=10000,meta_cpu_ns=1000)
    t.write('policy_timing',device_id=1,tx_index=0,operation='inference',cpu_ns=9999999,meta_cpu_ns=0)
    t.close()
    data=result([tmp_path],'tvt_cpu');rows={r[1]:r[2:] for r in data['views'][0]['rows']}
    assert rows['inference']==pytest.approx([4,2.5,3.85,4])
    assert rows['decision_plus_meta']==pytest.approx([3,3,3.9,4])
    assert data['views'][1]['series'][0]['points'][0]['y']==1
    rows={r[1]:r[2:] for r in result([tmp_path],'tvt_cpu',start=3,end=4)['views'][0]['rows']}
    assert rows['decision_plus_meta']==[1,4,4,4]
    old=tmp_path/'old';t=fixture(old);ul(t,0,1);t.close()
    assert result([old],'tvt_cpu')['views'][0]['rows']==[]


def test_nonoverlap_learning_windows_equal_ed_and_zero_normalization(tmp_path):
    t=fixture(tmp_path)
    for ed,tx,ok in [(0,1,True),(0,2,False),(1,1,True),(0,3,False),(1,3,False),(0,4,None)]:ul(t,ed,tx,ok)
    t.close();data=result([tmp_path],'tvt_learning',dict(window=2,normalize=True,tail=1))
    assert [p['y'] for p in data['views'][0]['series'][0]['points']]==[.75,0]
    assert all(p['y'] is None for p in data['views'][1]['series'][0]['points'])


def test_event_aligned_windows_initial_burnin_censoring_gaps_and_range(tmp_path):
    t=fixture(tmp_path)
    rows=[(1,0,'stationary',True),(2,10,'moving',True),(3,10,'moving',False),
          (4,20,'stationary',True),(5,20,'stationary',None),(6,30,'moving',False),(7,30,'moving',True),
          (8,40,'stationary',True),(10,40,'stationary',True)]
    # Completion records may arrive out of transmission order.
    for tx,epoch,state,ok in reversed(rows):ul(t,0,tx,ok,motion_epoch_start_ns=epoch,motion_state=state)
    t.close()
    opts=dict(horizons=[2,3],burnin=0)
    data=result([tmp_path],'tvt_transitions',opts)
    out={(r[1],r[2]):r[3:] for r in data['views'][0]['rows']}
    assert out[('moving',2)]==[2,0,.5]
    assert out[('moving',3)]==[0,2,None]
    assert out[('stationary',2)]==[0,2,None]  # censoring and missing tx9
    data=result([tmp_path],'tvt_transitions',dict(horizons=[2],burnin=3),start=3,end=6)
    out={r[1]:r[3:] for r in data['views'][0]['rows']}
    assert out['stationary']==[0,1,None] and out['moving']==[0,1,None]
    # Legacy run lacks epoch columns: unknown, not an invented transition.
    for path in (tmp_path/'uplinks').glob('*.parquet'):
        pq.write_table(pq.read_table(path).drop(['motion_epoch_start_ns']),path)
    data=result([tmp_path],'tvt_transitions',opts)
    assert all(r[3:]==[0,0,None] for r in data['views'][0]['rows']) and data['notes']


def test_settlement_is_retrospective_and_selection_measures_actual_use(tmp_path):
    t=fixture(tmp_path)
    for ed,tx,algo,state,detected,ok in [(0,1,'A','moving','stationary',True),(0,2,'B','stationary','stationary',True),
        (1,1,'A','moving','moving',False),(1,2,'A','stationary','moving',True)]:
        ul(t,ed,tx,ok,motion_state=state,detected_motion_state=detected,selected_algorithm=algo,x_m=10,y_m=0,z_m=0)
    for ed,tx,active in [(0,0,[0,1]),(0,2,[1]),(1,0,[0,1])]:
        t.write('selector_states',device_id=ed,tx_index=tx,active_arms=active)
    t.close();s=result([tmp_path],'tvt_settlement',dict(bins=2))
    assert [p['y'] for p in s['views'][0]['series'][0]['points']]==[.5,1]
    assert [r[2] for r in s['views'][1]['rows']]==[2,0]
    s=result([tmp_path],'tvt_selection',dict(distance_edges_m=[0,20],bins=2))
    moving=next(r for r in s['views'][0]['rows'] if r[1]=='moving')
    assert moving[4:]==['A',2,1,2,.5]
    assert len(s['views'][1]['rows'])==4
    assert all(r[-1]==.5 for r in s['views'][1]['rows'])


def test_seed_units_pairing_config_separation_and_duplicates(tmp_path):
    roots=[]
    for kind in ('sema','dqn'):
        for seed in (0,1):
            root=tmp_path/f'{kind}{seed}';t=fixture(root,kind=kind,seed=seed)
            for ed in range(3):ul(t,ed,1,kind=='sema' or ed<seed)
            t.close();roots.append(root)
    data=result(roots,'tvt_seed_comparison',dict(resamples=100))
    prr=[r for r in data['views'][1]['rows'] if r[2]=='prr'][0]
    assert prr[3:]==pytest.approx([2,5/6,2/3,1])
    assert len(data['views'][0]['rows'])==8
    # One different mobility/traffic/channel condition must not be paired.
    root=tmp_path/'different';t=fixture(root,kind='dqn',seed=2,channel=dict(exponent=4.));ul(t,0,1);t.close()
    d=result(roots+[root],'tvt_seed_comparison',dict(resamples=100))
    assert len(d['views'][1]['rows'])==4 and len({r[0] for r in d['views'][0]['rows']})==2
    root=tmp_path/'duplicate';t=fixture(root,kind='dqn',seed=0);ul(t,0,1);t.close()
    d=result(roots+[root],'tvt_seed_comparison',dict(resamples=100))
    assert d['views'][1]['rows']==[] and any('重複' in n for n in d['notes'])
