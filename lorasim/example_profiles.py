"""Read-only bundled Experiment catalogue, independent of user-saved profiles."""
from pathlib import Path
import sysconfig
import yaml
from fastapi import HTTPException
from .config import load_profile, dump_profile


def examples_root():
    source=Path(__file__).resolve().parent.parent/'examples'
    return source if source.is_dir() else Path(sysconfig.get_path('data'))/'share/lorasim/examples'


def files(root):
    root=Path(root).resolve()
    return {p.relative_to(root).as_posix():p for p in sorted(root.rglob('*'))
            if p.suffix in ('.yaml','.yml') and p.is_file() and p.resolve().is_relative_to(root)}


def catalogue(root):
    items=[]
    for name,path in files(root).items():
        try:
            data=yaml.safe_load(path.read_text(encoding='utf-8'))
        except (ValueError,yaml.YAMLError):
            continue
        # A model library is not an Experiment; do not execute/instantiate plugins.
        if isinstance(data,dict) and isinstance(data.get('groups'),list):
            items.append(dict(id=name,name=str(data.get('name') or path.stem)))
    return items


def read_example(root,name):
    root=Path(root).resolve()
    path=files(root).get(name)
    if path is None or name not in {r['id'] for r in catalogue(root)}:
        raise HTTPException(404,'Experiment example not found')
    data=yaml.safe_load(path.read_text(encoding='utf-8'))
    # Resolve trusted bundled model files only inside the examples directory.
    for ref in data.get('model_files',[]):
        target=(path.parent/ref).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(422,'Example model file must remain inside examples')
    p=load_profile(path)
    # Browser imports must be self-contained and independent of the saved catalog.
    p=p.model_copy(update={'model_files':[]})
    return dict(id=name,yaml=dump_profile(p),profile=p.model_dump(mode='json'))


def mount_examples(app,dependencies,root=None):
    root=Path(root) if root is not None else examples_root()
    @app.get('/api/examples',dependencies=dependencies)
    def list_examples():
        return catalogue(root)
    @app.get('/api/examples/content',dependencies=dependencies)
    def example_content(id:str):
        return read_example(root,id)
