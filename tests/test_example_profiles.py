from pathlib import Path
from fastapi.testclient import TestClient
from fastapi import HTTPException
import pytest
import yaml
from lorasim.api import create_app
from lorasim.example_profiles import catalogue, read_example, examples_root


def test_catalog_excludes_model_libraries_and_import_inlines_refs():
    root=examples_root();items=catalogue(root)
    assert len([r for r in items if r['id'].startswith('tvt/')])==77
    assert all(not r['id'].startswith('models/') for r in items)
    # Existing split model-file examples must become browser-portable too.
    paths=[p for p in root.rglob('*.yaml') if isinstance(yaml.safe_load(p.read_text()),dict)
           and yaml.safe_load(p.read_text()).get('model_files')]
    assert paths
    for path in paths:
        data=read_example(root,path.relative_to(root).as_posix())
        assert data['profile']['model_files']==[] and data['profile']['models']


def test_example_api_auth_listing_load_validate_and_path_safety(tmp_path,monkeypatch):
    monkeypatch.setenv('LORASIM_API_TOKEN','test-token')
    c=TestClient(create_app(tmp_path/'store'))
    assert c.get('/api/examples').status_code==401
    assert c.get('/api/examples/content?id=tvt/00-smoke-sema.yaml').status_code==401
    h={'Authorization':'Bearer test-token'}
    assert c.get('/api/examples',headers=h).status_code==200
    data=c.get('/api/examples/content',params={'id':'tvt/00-smoke-sema.yaml'},headers=h).json()
    assert data['profile']['name'].endswith('SMOKE')
    assert c.post('/api/validate',json={'yaml':data['yaml']},headers=h).status_code==200
    assert c.get('/api/examples/content',params={'id':'../../pyproject.toml'},headers=h).status_code==404
    root=tmp_path/'examples';root.mkdir();outside=tmp_path/'secret.yaml';outside.write_text('groups: [{}]')
    (root/'linked.yaml').symlink_to(outside)
    assert catalogue(root)==[]
    (root/'bad.yaml').write_text('groups: [{}]\nmodel_files: [../secret.yaml]\n')
    with pytest.raises(HTTPException) as exc:read_example(root,'bad.yaml')
    assert exc.value.status_code==422


def test_independent_example_submissions_keep_settings_and_queue_order(tmp_path):
    c=TestClient(create_app(tmp_path/'store'))
    paths=['tvt/00-smoke-sema.yaml','tvt/epsilon-reference-n50-w64.yaml']
    ids=[]
    for path in paths:
        data=c.get('/api/examples/content',params={'id':path}).json()
        response=c.post('/api/jobs',json={'yaml':data['yaml'],'experiment_id':None})
        assert response.status_code==201
        job=c.get('/api/jobs/'+response.json()['id']).json()
        assert job['status']=='queued'
        saved=yaml.safe_load(job['profile'])
        assert saved['seed']==data['profile']['seed']
        assert saved['groups']==data['profile']['groups']
        ids.append(job['id'])
    from lorasim.jobs import Store
    store=Store(tmp_path/'store')
    assert len({store.get(j)['experiment_id'] for j in ids})==2
    # Existing worker claims the individually committed jobs in queue order.
    assert [store.claim(),store.claim()]==ids


def test_loading_one_example_does_not_reparse_unrelated_yaml(tmp_path,monkeypatch):
    import lorasim.example_profiles as examples
    (tmp_path/'one.yaml').write_text('groups: [{count: 1}]')
    (tmp_path/'models.yaml').write_text('models: {}')
    def forbidden(*args):raise AssertionError('catalogue must not be parsed per selected YAML')
    monkeypatch.setattr(examples,'catalogue',forbidden)
    assert read_example(tmp_path,'one.yaml')['profile']['groups'][0]['count']==1
    with pytest.raises(HTTPException) as exc:read_example(tmp_path,'models.yaml')
    assert exc.value.status_code==404
