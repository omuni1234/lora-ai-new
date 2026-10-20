from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from lorasim.api import create_app
from lorasim.documents import catalogue, documentation_root, read_document


def test_all_shipped_documents_render_with_headings_and_source():
    root=documentation_root();items=catalogue(root)
    assert len(items)>=40
    assert {'docs/INDEX.md','docs/RESEARCH_ANALYSIS.md','docs/TRACKING.md','README.md'}<= {x['id'] for x in items}
    for item in items:
        doc=read_document(root,item['id'])
        assert doc['source'] and doc['html'] and doc['headings']
        assert doc['historical']==item['historical']
    index=read_document(root,'docs/INDEX.md')
    assert 'data-document="docs/GUI.md"' in index['html']
    assert read_document(root,'docs/VALIDATION-v07.md')['historical']
    assert read_document(root,'docs/VALIDATION-v012.md')['historical']
    assert not read_document(root,'docs/VALIDATION-v013.md')['historical']


def test_html_is_disabled_links_are_local_and_arbitrary_files_are_unavailable(tmp_path):
    root=tmp_path/'root';(root/'docs').mkdir(parents=True)
    outside=tmp_path/'secret.md';outside.write_text('private')
    (root/'docs/leak.md').symlink_to(outside)
    (root/'docs/INDEX.md').write_text('''# 見出し
<script>alert(1)</script>

[unsafe](javascript:alert%281%29)
[escape](../../secret.md)
[local](other.md#節)

![remote](https://invalid.test/track.png)

| 項目 | 数値 |
| --- | --- |
| a | 1 |
''')
    (root/'docs/other.md').write_text('# 節\n内容')
    value=read_document(root,'docs/INDEX.md')['html']
    assert '<script>' not in value and 'href="javascript:' not in value
    assert 'href="../../secret.md"' not in value and 'src="https://' not in value
    assert 'data-document="docs/other.md"' in value and 'data-section="節"' in value
    assert '<table>' in value
    assert 'docs/leak.md' not in {x['id'] for x in catalogue(root)}
    for name in ('../secret.md','docs/leak.md','/etc/passwd'):
        with pytest.raises(HTTPException):read_document(root,name)


def test_document_routes_follow_optional_api_auth_and_asset_scope(tmp_path,monkeypatch):
    monkeypatch.setenv('LORASIM_API_TOKEN','test-token')
    client=TestClient(create_app(tmp_path));headers={'Authorization':'Bearer test-token'}
    assert client.get('/api/documents').status_code==401
    assert client.get('/api/documents/content').status_code==401
    assert client.get('/api/documents',headers=headers).status_code==200
    assert client.get('/api/documents/content',params={'id':'docs/GUI.md'},headers=headers).status_code==200
    assert client.get('/api/documents/content',params={'id':'../pyproject.toml'},headers=headers).status_code==404
    assert client.get('/api/documents/assets/FIDELITY.md',headers=headers).status_code==404
    assert client.get('/api/documents/assets/experience-v011.png',headers=headers).status_code==200
