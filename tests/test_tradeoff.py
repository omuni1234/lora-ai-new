import json
import math
import numpy as np
import pytest
from fastapi.testclient import TestClient
from lorasim.api import create_app
from lorasim.config import Profile, Radio
from lorasim.tradeoff import ForecastOptions, ForecastError, forecast, overlap_success, erlang_b


def profile(n=40,confirmed=False,**kwargs):
    data={'duration_s':60000,'groups':[{'count':n,'confirmed':confirmed,
          'timing':{'kind':'periodic','options':{'interval_s':600,'random_phase':True}}}]}
    data.update(kwargs)
    return Profile.model_validate(data)


def quick(p,**kwargs):
    return forecast(p,ForecastOptions(device_samples=8,overlap_trials=64,compare=False,**kwargs))


def test_single_class_a_device_does_not_interfere_with_itself_or_own_ack():
    for confirmed in (False,True):
        r=quick(profile(1,confirmed))['scenarios'][0]
        assert r['fractions']['Received']==pytest.approx(1,abs=1e-10)
        assert r['fractions']['LostBecauseTx']==0
        assert r['fractions']['Interference']==0


def test_compound_poisson_agrees_with_unslotted_aloha_formula():
    radios=[Radio(),Radio()];times=np.ones(2);power=np.zeros((2,1));rate=np.array([0,1.])
    q=overlap_success(radios,times,power,rate,np.array([0,1]),np.ones(2),32768,17,'aloha',[500000])
    assert q[0,0]==pytest.approx(math.exp(-2),abs=.008)
    assert q[1,0]==1


def test_capture_aggregates_individually_weak_interferers():
    q=overlap_success([Radio(),Radio()],np.ones(2),np.array([[0.],[-10.]]),np.array([0,6.]),
        np.array([0,1]),np.ones(2),4096,17,'goursaud',[500000])
    assert q[0,0]<.5 # each interferer alone cannot exceed the 6 dB energy threshold
    q=overlap_success([Radio(sf=7),Radio(sf=8)],np.ones(2),np.zeros((2,1)),np.array([0,6.]),
        np.array([0,1]),np.ones(2),4096,17,'aloha',[500000])
    assert q[0,0]==1


def test_sensitivity_frequency_and_no_downlinks_are_distinct():
    p=profile(1,False,gateways=[{'position':[1e7,1e7,0]}])
    r=quick(p)['scenarios'][0]
    assert r['fractions']['UnderSensitivity']==1 and r['downlinks_per_s']==0
    p.gateways[0].frequencies_hz=[]
    r=quick(p)['scenarios'][0]
    assert r['fractions']['WrongFrequency']==1


def test_renewal_probabilities_normalize_and_dc_limits_gw_tx():
    p=profile(500,True)
    data=forecast(p,ForecastOptions(device_samples=16,overlap_trials=96))
    for r in data['scenarios']:
        if 'unavailable' in r:continue
        assert sum(r['fractions'].values())==pytest.approx(1)
        assert all(0<=v<=1 for v in r['fractions'].values())
        assert r['gateway_tx_fraction'][0]<=.11001
    base=data['scenarios'][0]
    off=next(x for x in data['scenarios'] if x['id']=='unconfirmed')
    assert base['fractions']['LostBecauseTx']>0
    assert off['fractions']['LostBecauseTx']==0
    assert off['fractions']['Received']>base['fractions']['Received']
    channels=next(x for x in data['scenarios'] if x['id']=='all_channels')
    assert channels['fractions']['Interference']<base['fractions']['Interference']


def test_multi_gateway_bounds_and_mixed_reasons():
    p=profile(1,False,gateways=[{'position':[1000,1000,15]},{'position':[1e7,1e7,15],'frequencies_hz':[]}])
    r=quick(p)
    assert r['scenarios'][0]['fractions']['Received']==1
    assert any('独立近似' in w for w in r['warnings'])
    p.gateways[0].position=(1e7,1e7,15)
    assert quick(p)['scenarios'][0]['fractions']['MixedLoss']==1


def test_deterministic_bounded_and_does_not_mutate_profile():
    p=profile(500)
    before=p.model_dump_json();a=quick(p);b=quick(p)
    a.pop('wall_s');b.pop('wall_s')
    assert a==b and p.model_dump_json()==before
    assert a['sampled_devices']<=8
    p.duration_s=1e9
    c=quick(p)
    assert c['scenarios']==b['scenarios'] # stationary workload, no simulation length loop


def test_sync_and_learning_assumptions_visible():
    p=profile(8)
    p.groups[0].timing.options={'interval_s':600}
    p.groups[0].policy.kind='lora_mab'
    r=quick(p)
    assert any('同期' in w for w in r['warnings'])
    assert any('学習' in w for w in r['warnings'])


def test_erlang_and_work_budget():
    assert erlang_b(0,8)==0 and erlang_b(1,1)==.5
    with pytest.raises(ForecastError):
        overlap_success([Radio(),Radio()],np.ones(2),np.zeros((2,1)),np.ones(2)*1e6,
                        np.arange(2),np.ones(2),64,0,'aloha',[5000000])


def test_api_is_read_only_and_authenticated(tmp_path,monkeypatch):
    monkeypatch.setenv('LORASIM_API_TOKEN','test-token')
    c=TestClient(create_app(tmp_path));body={'yaml':profile(8).model_dump_json(),'options':{'compare':False}}
    assert c.post('/api/calculator/forecast',json=body).status_code==401
    headers={'Authorization':'Bearer test-token'}
    r=c.post('/api/calculator/forecast',json=body,headers=headers)
    assert r.status_code==200,r.text
    assert c.get('/api/jobs',headers=headers).json()==[]
    body['options']['overlap_trials']=1
    r=c.post('/api/calculator/forecast',json=body,headers=headers)
    assert r.status_code==422 and r.json()['diagnostics']


def test_candidate_cartesian_index_preserves_radio_order():
    from lorasim.tradeoff import RadioCandidates
    from lorasim.config import ActionSpace
    space=ActionSpace(spreading_factors=[7,9],frequencies_hz=[868100000,868300000],coding_rates=[1,3],powers_dbm=[8,14])
    lazy=RadioCandidates.space(space)
    assert len(lazy)==16
    assert list(lazy)==space.radios()


def test_loss_competition_does_not_change_intrinsic_coverage():
    p=profile(80,True,area_m=[10000,10000],gateways=[{'position':[5000,5000,15]}])
    d=forecast(p,ForecastOptions(device_samples=8,overlap_trials=64))
    base=d['scenarios'][0];off=next(r for r in d['scenarios'] if r['id']=='unconfirmed')
    assert base['coverage_gap']==off['coverage_gap']
    assert base['intrinsic_interference']==off['intrinsic_interference']
    assert base['fractions']['UnderSensitivity']<=off['fractions']['UnderSensitivity']


def test_no_transmission_before_end_has_explicit_error():
    p=profile(8);p.groups[0].timing.options={'interval_s':600,'first_s':1000000}
    with pytest.raises(ForecastError,match='初回送信'):quick(p)


@pytest.mark.parametrize('kind,options,shared',[
    ('fixed',{'preamble_symbols':32,'crc':False},{}),
    ('fixed_sf_ch',{'sf':10,'frequency_hz':868300000},None),
    ('fixed',{'sf':11,'cr':3},None),
])
def test_fixed_distribution_matches_real_selector(kind,options,shared):
    from lorasim.components import make
    from lorasim.config import Component
    from lorasim.tradeoff import candidates
    p=profile(1)
    spec=Component(kind=kind,options=options,action_space=shared)
    predicted=candidates(p,spec,'profile',set())
    actual=make(spec,'policy',rng=np.random.default_rng(0),action_space=p.action_space).select(None)
    assert predicted==[actual]
