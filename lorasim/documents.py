"""Read-only local documentation catalogue; works from source, Docker and wheels."""
from pathlib import Path
import re
import sysconfig
from urllib.parse import urlsplit, unquote, quote
from markdown_it import MarkdownIt
from fastapi import HTTPException
from fastapi.responses import FileResponse
from . import __version__


def documentation_root():
    source=Path(__file__).resolve().parent.parent
    installed=Path(sysconfig.get_path('data'))/'share'/'lorasim'
    return source if (source/'docs/INDEX.md').is_file() else installed


def files(root):
    root=Path(root).resolve()
    return {p.relative_to(root).as_posix():p for p in [root/'README.md',*sorted((root/'docs').glob('*.md'))]
            if p.is_file() and p.resolve().is_relative_to(root)}


def category(name):
    if 'VALIDATION' in name or 'PAPERS' in name: return '検証・論文との対応'
    if any(k in name for k in ('ANALYSIS','METRICS','TRACKING')): return '分析・データ仕様'
    if any(k in name for k in ('DEVELOPER','PLUGINS','ACTION_SPACE')): return '開発者向け'
    if any(k in name for k in ('ALGORITHMS','SEMA','ADR','MARKOV','WAYPOINTS','ENERGY','FIDELITY')): return 'モデル・理論'
    return '使い方・運用'


def catalogue(root):
    result=[]
    for name,p in files(root).items():
        source=p.read_text(encoding='utf-8')
        title=next((line[2:] for line in source.splitlines() if line.startswith('# ')),p.stem)
        historical='VALIDATION' in name and name!='docs/VALIDATION-v0141.md'
        result.append(dict(id=name,title=title,category=category(name),historical=historical,
                           search_text=source,version=__version__))
    return result


def read_document(root, name):
    root=Path(root).resolve();allowed=files(root)
    if name not in allowed: raise HTTPException(404,'Document not found')
    path=allowed[name];source=path.read_text(encoding='utf-8')
    md=MarkdownIt('commonmark',{'html':False,'linkify':False}).enable('table').enable('strikethrough')
    tokens=md.parse(source);headings=[];used={}
    def walk(tokens):
        for i,t in enumerate(tokens):
            if t.type=='heading_open':
                text=tokens[i+1].content
                slug=re.sub(r'[^\w\-\s]','',text.lower()).strip().replace(' ','-') or 'section'
                used[slug]=used.get(slug,0)+1
                anchor=slug if used[slug]==1 else f'{slug}-{used[slug]}'
                t.attrSet('id',anchor);headings.append(dict(id=anchor,title=text,level=int(t.tag[1:])))
            if t.type in ('link_open','image'):
                key='href' if t.type=='link_open' else 'src';url=t.attrGet(key) or '';parsed=urlsplit(url)
                if parsed.scheme in ('https','http','mailto'):
                    if t.type=='image':t.attrSet('src','');t.attrSet('alt','外部画像: '+url)
                    else:t.attrSet('target','_blank');t.attrSet('rel','noopener noreferrer')
                elif not parsed.scheme and not parsed.netloc:
                    target=(path.parent/unquote(parsed.path)).resolve() if parsed.path else path
                    relative=target.relative_to(root).as_posix() if target.is_relative_to(root) else ''
                    if t.type=='link_open' and relative in allowed:
                        t.attrSet('href','#document='+quote(relative));t.attrSet('data-document',relative)
                        if parsed.fragment:t.attrSet('data-section',unquote(parsed.fragment))
                    elif t.type=='image' and target.is_file() and target.parent==root/'docs' and target.suffix.lower() in ('.png','.jpg','.jpeg','.svg'):
                        # Fetch with the same optional Bearer token as the rest of the GUI.
                        t.attrSet('src','');t.attrSet('data-doc-asset',target.name)
                    else:
                        t.attrs.pop(key,None);t.attrSet('title','同梱ファイル（GUI閲覧対象外）: '+url)
                else:
                    t.attrs.pop(key,None)
            if t.children:walk(t.children)
    walk(tokens)
    return dict(id=name,title=next((x['title'] for x in headings),'ドキュメント'),source=source,
                html=md.renderer.render(tokens,md.options,{}),headings=headings,version=__version__,
                historical='VALIDATION' in name and name!='docs/VALIDATION-v0141.md')


def mount_documents(app, dependencies, root=None):
    root=Path(root) if root is not None else documentation_root()
    @app.get('/api/documents',dependencies=dependencies)
    def list_documents():
        return dict(version=__version__,documents=catalogue(root))
    @app.get('/api/documents/content',dependencies=dependencies)
    def content(id: str='docs/INDEX.md'):
        return read_document(root,id)
    @app.get('/api/documents/assets/{name}',dependencies=dependencies)
    def asset(name: str):
        path=(root/'docs'/name).resolve()
        if path.parent!=(root/'docs').resolve() or path.suffix.lower() not in ('.png','.jpg','.jpeg','.svg') or not path.is_file():
            raise HTTPException(404,'Asset not found')
        return FileResponse(path)
