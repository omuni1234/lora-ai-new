"""Keep HTML, scripts and styles coherent across Control Center upgrades."""
import hashlib
from pathlib import Path

from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles


ASSETS = ('style.css', 'planning.js', 'experience.js', 'calculator.js', 'preview-map.js', 'analysis-charts.js', 'profile-summary.js', 'documents.js', 'app.js')


class RevalidatingStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers['Cache-Control'] = 'no-cache, max-age=0, must-revalidate'
        return response


def index_response(directory: Path):
    # Hash contents, not version/mtime: ZIP extraction can preserve timestamps.
    # Read on page requests only. No work is added to simulation events/polling.
    html = (directory/'index.html').read_text(encoding='utf-8')
    for name in ASSETS:
        digest = hashlib.sha256((directory/name).read_bytes()).hexdigest()[:16]
        html = html.replace(f'"/static/{name}"', f'"/static/{name}?v={digest}"')
    return HTMLResponse(html, headers={'Cache-Control': 'no-store, max-age=0'})


def mount_ui(app, directory: Path):
    @app.get('/static/index.html', include_in_schema=False)
    def static_index():
        return RedirectResponse('/', headers={'Cache-Control':'no-store'})

    app.mount('/static', RevalidatingStaticFiles(directory=directory), name='static')

    @app.get('/', include_in_schema=False)
    def index():
        return index_response(directory)
