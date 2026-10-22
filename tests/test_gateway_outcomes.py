import json
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from lorasim.analysis import compare_runs
from lorasim.config import Profile, dump_profile
from lorasim.tracking import Tracker


def fixture(root):
    p = Profile.model_validate(dict(groups=[dict(count=2)], gateways=[
        dict(id='near', position=[0, 0, 0]), dict(id='far', position=[2000, 2000, 15])]))
    (root/'profile.yaml').write_text(dump_profile(p))
    (root/'summary.json').write_text('{"sim_time_s": 600}')
    t = Tracker(root, 2)
    def write(ed, idx, outcome, a, b):
        t.write('uplinks', device_id=ed, tx_index=idx, outcome=outcome,
                gw_outcomes=json.dumps(dict(near=a, far=b)))
    write(0, 1, 'MixedLoss', 'Interference', 'UnderSensitivity')
    write(0, 2, 'Received', 'Received', 'UnderSensitivity')
    write(0, 3, 'MixedLoss', 'LostBecauseTx', 'UnderSensitivity')
    write(1, 1, 'MixedLoss', 'UnderSensitivity', 'Interference')
    write(1, 2, 'Interference', 'Interference', 'Interference')
    write(1, 3, 'Censored', 'SimulationEnd', 'UnderSensitivity')
    t.close()
    return [dict(root=root, id='run', experiment_id='e', experiment_name='audit')]


def analyze(runs, options=None, **kwargs):
    report = compare_runs(runs, modules=[dict(kind='gateway_outcomes', options=options or {}),
                                       dict(kind='outcomes'), dict(kind='convergence'), dict(kind='abstract')], **kwargs)
    assert all(m['status'] == 'completed' for m in report['modules']), report
    return report, report['modules'][0]['data']


def test_gateway_distribution_equal_ed_and_preserves_network_prr(tmp_path):
    runs = fixture(tmp_path)
    report, data = analyze(runs)
    plot = data['views'][0]
    assert plot['labels'] == ['near', 'far']
    assert plot['items'][0] == pytest.approx(dict(Received=1/6, Interference=5/12,
                                               LostBecauseTx=1/6, UnderSensitivity=1/4))
    assert plot['items'][1] == dict(UnderSensitivity=.5, Interference=.5)
    assert report['runs'][0]['mean_ed_prr'] == pytest.approx(1/6)
    assert report['runs'][0]['fractions']['MixedLoss'] == pytest.approx(7/12)
    assert data['views'][1]['rows'][0][3:] == [5, 2, 5, 0, 1]
    for m in report['modules'][1:]:
        assert any('新しいPHY損失要因ではなく' in n for n in m['data']['notes'])


def test_conditional_mixed_only_and_range_and_gateway_filter(tmp_path):
    runs = fixture(tmp_path)
    _, data = analyze(runs, dict(population='mixed_only'))
    assert data['views'][0]['items'][0] == dict(Interference=.25, LostBecauseTx=.25, UnderSensitivity=.5)
    assert data['views'][1]['rows'][0][3:] == [3, 2, 3, 0, 1]
    _, data = analyze(runs, dict(population='network_failures'))
    assert data['views'][0]['items'][0] == dict(Interference=.5, LostBecauseTx=.25, UnderSensitivity=.25)
    _, data = analyze(runs, dict(gateway_ids=['near', 'absent']), start=2, end=2)
    assert data['views'][0]['labels'] == ['near']
    assert data['views'][0]['items'][0] == dict(Received=.5, Interference=.5)
    assert any('absent' in n for n in data['notes'])


@pytest.mark.parametrize('value', [None, 'invalid-json', '[]', '{"near":"SimulationEnd"}', '{"near":"MixedLoss"}'])
def test_missing_gateway_data_is_not_reconstructed_from_network_outcome(tmp_path, value):
    runs = fixture(tmp_path)
    for part in (tmp_path/'uplinks').glob('*.parquet'):
        a = pq.read_table(part)
        a = a.set_column(a.schema.get_field_index('gw_outcomes'), 'gw_outcomes', pa.array([value]*a.num_rows, type=pa.string()))
        pq.write_table(a, part)
    report, data = analyze(runs)
    assert report['runs'][0]['mean_ed_prr'] == pytest.approx(1/6)
    assert all(v['type'] == 'table' for v in data['views'])
    assert data['views'][0]['rows'][0][3:] == [5, 0, 0, 5, 1]
    assert data['views'][1]['rows'] == []


def test_legacy_parquet_without_gw_outcomes_and_single_gateway(tmp_path):
    runs = fixture(tmp_path)
    p = Profile.model_validate(dict(groups=[dict(count=2)]))
    (tmp_path/'profile.yaml').write_text(dump_profile(p))
    for part in (tmp_path/'uplinks').glob('*.parquet'):
        a = pq.read_table(part).drop(['gw_outcomes'])
        pq.write_table(a, part)
    _, data = analyze(runs)
    assert data['views'][0]['rows'][0][3:] == [5, 0, 0, 5, 1]
