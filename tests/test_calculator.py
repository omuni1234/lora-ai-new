import math

import numpy as np
import pytest
from fastapi.testclient import TestClient

from lorasim.api import create_app
from lorasim.calculator import CalculatorRequest, calculate
from lorasim.config import Channel, Radio
from lorasim.phy import GW_SENSITIVITY, ED_SENSITIVITY, links


def test_default_link_budget_curve_and_range_match_scalar_phy():
    request = CalculatorRequest()
    data = calculate(request)
    assert data['range_m'] == pytest.approx(10**((14-7.7+130)/37.6))
    assert data['probe']['received_dbm'] == pytest.approx(-106.5)
    assert data['probe']['path_loss_db'] == pytest.approx(120.5)
    assert data['probe']['margin_db'] == pytest.approx(23.5)
    assert data['probe']['meets_required']
    distances = np.array(data['distances_m'])
    pos = np.column_stack((distances, np.zeros((len(distances), 2))))
    scalar = links(np.zeros(3), pos, 14, Channel(), backend='scalar')[0]
    np.testing.assert_allclose(data['received_dbm'], scalar, rtol=0, atol=1e-12)
    assert distances[0] == 0 and distances[-1] == data['max_distance_m']
    assert np.all(np.diff(data['received_dbm']) <= 0)
    assert data['received_dbm'][0] == pytest.approx(6.3)
    assert len(distances) <= 453


@pytest.mark.parametrize('direction,table', [('uplink', GW_SENSITIVITY), ('downlink', ED_SENSITIVITY)])
@pytest.mark.parametrize('sf', range(7, 13))
def test_all_receiver_thresholds_and_custom_channel(direction, table, sf):
    channel = Channel(exponent=2.1, reference_distance_m=2, reference_loss_db=40)
    request = CalculatorRequest(direction=direction, radio=Radio(sf=sf, tp_dbm=8), channel=channel, margin_db=6)
    result = calculate(request)
    assert result['sensitivity_dbm'] == table[sf-7]
    assert result['required_dbm'] == table[sf-7]+6
    radius = result['range_m']
    probe = np.array([[radius*.999,0,0],[radius,0,0],[radius*1.001,0,0]])
    powers = links(np.zeros(3), probe, 8, channel)[0]
    assert powers[0] > result['required_dbm'] > powers[2]
    assert powers[1] == pytest.approx(result['required_dbm'], abs=1e-12)


def test_cr_only_changes_airtime_and_sf_tp_margin_change_distance():
    cr1 = calculate(CalculatorRequest(phy_bytes=20))
    cr4 = calculate(CalculatorRequest(phy_bytes=20, radio=Radio(cr=4)))
    assert cr1['airtime_ms'] == 56.576
    assert cr4['airtime_ms'] > cr1['airtime_ms']
    assert cr4['range_m'] == cr1['range_m']
    assert cr4['received_dbm'] == cr1['received_dbm']
    assert calculate(CalculatorRequest(radio=Radio(sf=12)))['range_m'] > cr1['range_m']
    assert calculate(CalculatorRequest(radio=Radio(tp_dbm=8)))['range_m'] < cr1['range_m']
    assert calculate(CalculatorRequest(margin_db=10))['range_m'] < cr1['range_m']


def test_no_coverage_equality_and_finite_display_window():
    request = CalculatorRequest(channel=Channel(reference_loss_db=144.01), probe_distance_m=0)
    result = calculate(request)
    assert result['range_state'] == 'below_at_reference' and result['range_m'] is None
    assert result['probe']['meets_required'] is False
    equality = calculate(CalculatorRequest(channel=Channel(reference_loss_db=144, reference_distance_m=2), probe_distance_m=2))
    assert equality['range_m'] == pytest.approx(2) and equality['probe']['meets_required']
    beyond = calculate(CalculatorRequest(max_distance_m=50, probe_distance_m=500))
    assert beyond['range_m'] > 50
    assert beyond['distances_m'][-1] == 50
    assert beyond['probe']['distance_m'] == 500


def test_extreme_inputs_are_bounded_and_never_return_infinity():
    result = calculate(CalculatorRequest(channel=Channel(exponent=1e-300)))
    assert result['range_state'] == 'beyond_numeric_range'
    assert result['range_m'] is None and result['max_distance_m'] == 10_000_000
    assert len(result['distances_m']) <= 453
    assert all(math.isfinite(x) for x in result['received_dbm'])
    with pytest.raises(ValueError, match='calculator numeric range'):
        calculate(CalculatorRequest(channel=Channel(exponent=1e308)))


def test_api_is_read_only_and_does_not_construct_models(tmp_path, monkeypatch):
    from lorasim.jobs import Store
    def forbidden(*args, **kwargs):
        raise AssertionError('Calculator must not construct profiles, models, or simulations')
    monkeypatch.setattr(Store, 'check_profile', forbidden)
    monkeypatch.setattr('lorasim.engine.Simulation.__init__', forbidden)
    client = TestClient(create_app(tmp_path))
    response = client.post('/api/calculator', json={'radio': {'sf':12,'tp_dbm':10,'cr':4}})
    assert response.status_code == 200
    assert response.json()['sensitivity_dbm'] == -142.5
    assert client.get('/api/jobs').json() == []
    assert client.get('/api/profiles').json() == []
    invalid = client.post('/api/calculator', json={'radio': {'sf':6}})
    assert invalid.status_code == 422
    diagnostic = invalid.json()['diagnostics'][0]
    assert diagnostic['hint'] and 'SF' in diagnostic['message']
    for body in [{'max_distance_m':0}, {'probe_distance_m':-1}, {'margin_db':-1},
                 {'phy_bytes':256}, {'radio':{'bandwidth_hz':250000}}, {'channel':{'exponent':'NaN'}}]:
        assert client.post('/api/calculator', json=body).status_code == 422
    numerical = client.post('/api/calculator', json={'channel':{'exponent':1e308}})
    assert numerical.status_code == 422
    assert numerical.json()['diagnostics'][0]['code'] == 'CALCULATOR_NUMERIC'


def test_calculator_requires_authentication(tmp_path, monkeypatch):
    monkeypatch.setenv('LORASIM_API_TOKEN','test')
    client = TestClient(create_app(tmp_path))
    assert client.post('/api/calculator',json={}).status_code == 401
    assert client.post('/api/calculator',json={},headers={'Authorization':'Bearer test'}).status_code == 200
