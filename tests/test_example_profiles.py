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
