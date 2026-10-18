"""Constrained control rules, DL boundary, and legacy behaviour are separate contracts."""
import math
import numpy as np
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from lorasim.api import create_app
from lorasim.components import make
from lorasim.config import ActionSpace, Component, Profile, Radio
from lorasim.engine import Simulation
from lorasim.plugins.selector._adr_space import ADRSpace
from lorasim.plugins.selector.adr import ADR, ADRServer
from lorasim.plugins.selector.gadr import GADRServer
from lorasim.registry import metadata

SPACE = ActionSpace(spreading_factors=[8,10,11], frequencies_hz=[868300000,868500000],
                    coding_rates=[2], powers_dbm=[11])


def create(kind, **options):
    return make(Component(kind=kind, action_space={}, options=options), 'policy',
                rng=np.random.default_rng(5), action_space=SPACE)


@pytest.mark.parametrize('kind', ['adr','badr','gadr'])
def test_all_paths_stay_in_effective_space(kind):
    p=create(kind)
    assert metadata('policy',kind)['fixed_space_dimensions']==('coding_rates','powers_dbm')
    assert len(p.action_keys)==6
    channels=set()
    for i in range(360):
        r=p.select(None); channels.add(r.frequency_hz)
        assert (r.sf,r.frequency_hz,r.tp_dbm,r.cr) in p.action_keys
        if isinstance(p, ADR):
            retry=p.select_attempt(None,new_frame=False)
            assert (retry.sf,retry.frequency_hz,retry.tp_dbm,retry.cr) in p.action_keys
            p.sent(True)
            if i%70==0:
                command=p.create_server().request(i,r,[-100])
                p.downlink(command)
    assert channels==set(SPACE.frequencies_hz)


def test_sf_projection_retains_original_step_budget():
    c=ADRSpace(SPACE)
    r=Radio(sf=10,tp_dbm=11,cr=2,frequency_hz=868500000)
    assert c.command(r,1).sf==10  # raw SF9 is not permitted; cannot buy a two-SF jump
    assert c.command(r,2).sf==8
    assert c.command(r,100).sf==8
    assert c.command(r,-20)==r  # original negative-step action was TP-only
    assert c.project_sf(12)==11


@pytest.mark.parametrize('server_class,options', [(ADRServer,(2,'max','max',True,0)), (GADRServer,(2,'max','avg',True,10))])
def test_history_duplicates_and_control_differ_only_after_step_calculation(server_class,options):
    space=ADRSpace(SPACE); server=server_class(options,space)
    radio=Radio(sf=11,tp_dbm=11,cr=2,frequency_hz=868500000)
    assert server.request(0,radio,[-95]) is None
    assert server.request(0,radio,[-95]) is None
    assert len(server.history)==1
    command=server.request(1,radio,[-95])
    assert command.sf==8 and command.tp_dbm==11 and command.cr==2
    # Low SNR cannot raise fixed TP, nor invent an NS-side SF increase.
    poor=server_class(options,space)
    poor.request(0,radio,[-145]); command=poor.request(1,radio,[-145])
    assert command is None if server_class is ADRServer else command==radio


def test_gaussian_filter_and_rounding_are_unchanged():
    # A three-SF-wide allowed gap probes the SF step budget after the filter.
    space=ADRSpace(ActionSpace(spreading_factors=[8,11],powers_dbm=[14]))
    r=Radio(sf=11)
    noise=-174+10*math.log10(125000)+6
    for cls,opts in [(ADRServer,(4,'max','max',False,0)),(GADRServer,(4,'max','avg',False,10))]:
        legacy=cls(opts); bound=cls(opts,space)
        for i,snr in enumerate([-10.,-9.,-8.,25.]):
            a=legacy.request(i,r,[snr+noise]); b=bound.request(i,r,[snr+noise])
        if a is None:
            assert b is None
        else:
            projected=space.constrain(a)
            assert b==projected if cls is GADRServer or projected.sf!=r.sf else b is None
        assert list(bound.history)==list(legacy.history)


def test_backoff_fixed_power_skips_sf_gaps_and_keeps_channels():
    p=create('adr',ack_limit=1,ack_delay=1)
    p.downlink(Radio(sf=8,tp_dbm=11)); p.counter=2
    assert p.select(None).sf==8 and p.radio.tp_dbm==11
    p.counter=3; assert p.select(None).sf==10
    p.counter=4; assert p.select(None).sf==11
    p.counter=5; assert p.select(None).frequency_hz in SPACE.frequencies_hz
    assert p.reset_repetitions
    p.downlink(None); assert p.counter==0


def test_badr_projection_preserves_slots_and_random_phase():
    p=create('badr')
    assert p.sequence==[11,8,10,8,10,8]
    assert [p.select(None).sf for _ in range(12)]==p.sequence*2
    a=create('badr',random_phase=True);b=create('badr',random_phase=True)
    assert [a.select(None) for _ in range(20)]==[b.select(None) for _ in range(20)]


@pytest.mark.parametrize('kind',['adr','badr','gadr'])
@pytest.mark.parametrize('dimension',['coding_rates','powers_dbm'])
def test_multiple_fixed_values_are_japanese_errors_even_without_construct(tmp_path,kind,dimension):
    c=TestClient(create_app(tmp_path))
    import json
    text=json.dumps({'action_space':{dimension:[1,2] if dimension=='coding_rates' else [11,14]},
                     'groups':[{'policy':{'kind':kind,'action_space':{}}}]})
    for route in ['diagnostics','validate','jobs']:
        r=c.post('/api/'+route,json={'yaml':text})
        assert r.status_code==422
        d=r.json()['diagnostics'][0]
        assert d['code']=='ACTION_FIXED_REQUIRED' and '固定' in d['message'] and d['hint']
    valid={'kind':kind,'action_space':{'powers_dbm':[11],'coding_rates':[2]}}
    assert c.put('/api/models/policy/constrained',json=valid).status_code==200


@pytest.mark.parametrize('kind',['adr','gadr'])
@pytest.mark.parametrize('miss',[False,True])
def test_simulation_applies_constrained_command_only_after_dl(tmp_path,kind,miss):
    p=Profile.model_validate(dict(duration_s=45,area_m=[100,100],action_space=SPACE.model_dump(),
        mac={'duty_cycle':False,'backhaul_delay_s':.4 if miss else 0},gateways=[{'position':[50,50,15]}],
        groups=[{'count':1,'confirmed':False,'placement':'grid',
                 'policy':{'kind':kind,'action_space':{},'options':{'history_range':2,'backoff':False}},
                 'timing':{'kind':'periodic','options':{'interval_s':8}}}]))
    sim=Simulation(p,tmp_path); sim.run()
    tx=pq.read_table(tmp_path/'transmissions').to_pylist()
    ul=[x for x in tx if x['direction']=='UL']
    assert ul[0]['sf']==ul[1]['sf']==11
    assert {x['sf'] for x in ul}==({11} if miss else {8,11})
    assert all(x['tx_power_dbm']==11 and x['cr']==2 and x['frequency_hz'] in SPACE.frequencies_hz for x in ul)
    assert all(x['link_adr_tp_dbm']==11 for x in tx if x['direction']=='DL' and x['link_adr_sf'] is not None)


def test_references_and_assignments_have_separate_server_histories(tmp_path):
    p=Profile.model_validate(dict(models={'policy':{'baseline':{'kind':'gadr','action_space':{}}}},
        groups=[{'count':2,'policy':{'ref':'baseline'}}],assignments=[{'ids':[1],'policy':{'ref':'baseline'}}]))
    sim=Simulation(p,tmp_path)
    first,second=sim.adr_servers.values()
    first.request(0,Radio(),[-100])
    assert len(first.history)==1 and len(second.history)==0
    assert sim.devices[0].policy.rng is not sim.devices[1].policy.rng
