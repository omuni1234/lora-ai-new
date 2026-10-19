import hashlib
import os
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lorasim.api import create_app
from lorasim.web_assets import ASSETS, mount_ui


def asset_urls(html):
    return re.findall(r'(?:src|href)="(/static/[^\"]+)"', html)


def test_html_and_static_files_have_upgrade_safe_cache_contract(tmp_path):
    client = TestClient(create_app(tmp_path))
    response = client.get('/')
    assert response.status_code == 200 and 'no-store' in response.headers['cache-control']
    urls = asset_urls(response.text)
    assert len(urls) == len(ASSETS)
    for url, name in zip(urls, ASSETS):
        content = client.get(url)
        assert content.status_code == 200
        digest = hashlib.sha256(content.content).hexdigest()[:16]
        assert url == f'/static/{name}?v={digest}'
        assert 'must-revalidate' in content.headers['cache-control']
        cached = client.get(url, headers={'If-None-Match':content.headers['etag']})
        assert cached.status_code == 304
        assert 'no-cache' in cached.headers['cache-control']
    # Opening the static file directly must not bypass the versioned shell.
    assert client.get('/static/index.html').text == response.text
    assert client.get('/static/app.js').headers['cache-control'].startswith('no-cache')


def test_changed_content_changes_url_even_with_preserved_mtime_and_size(tmp_path):
    static = tmp_path/'static'
    static.mkdir()
    for name in ASSETS:
        (static/name).write_text('old-content')
    (static/'index.html').write_text(''.join(f'<script src="/static/{name}"></script>' for name in ASSETS))
    app = FastAPI()
    mount_ui(app, static)
    client = TestClient(app)
    before = asset_urls(client.get('/').text)
    original = (static/'app.js').stat()
    (static/'app.js').write_text('new-content')
    os.utime(static/'app.js', ns=(original.st_atime_ns, original.st_mtime_ns))
    after = asset_urls(client.get('/').text)
    assert before[:-1] == after[:-1] and before[-1] != after[-1]
    assert client.get(after[-1]).text == 'new-content'


def test_shell_preserves_script_dependency_order(tmp_path):
    html = TestClient(create_app(tmp_path)).get('/').text
    assert html.index('/static/planning.js?') < html.index('/static/calculator.js?')
    assert html.index('/static/calculator.js?') < html.index('/static/app.js?')
    assert html.index('/static/preview-map.js?') < html.index('/static/app.js?')
    assert 'id="mapViewport"' in html
