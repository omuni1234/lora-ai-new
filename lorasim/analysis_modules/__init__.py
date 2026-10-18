"""Discover independent analysis modules using the same convention as models."""
from functools import lru_cache
from importlib import import_module
import pkgutil
import os
import json
from pydantic import Field
from lorasim.interfaces import Options
from lorasim.ui_metadata import describe_schema
from .base import AnalysisModule

class Selection(Options):
    kind: str
    enabled: bool=True
    options: dict=Field(default_factory=dict)

DEFAULT_MODULES=['outcomes','convergence','fairness','energy']

@lru_cache(maxsize=1)
def bundled():
    result={}
    for item in sorted(pkgutil.iter_modules(__path__),key=lambda x:x.name):
        if item.name.startswith('_'): continue
        module=import_module(__name__+'.'+item.name)
        if hasattr(module,'PLUGIN'): result[item.name]=module.PLUGIN
    return result

def constructor(kind):
    if kind in bundled(): return bundled()[kind]
    trusted={cls.__module__+':'+cls.__name__:cls for cls in bundled().values()}
    if kind in trusted: return trusted[kind]
    if ':' not in kind: raise ValueError('unknown analysis module: '+kind)
    if os.getenv('LORASIM_ALLOW_PLUGINS')!='1':
        raise ValueError('custom Python code requires LORASIM_ALLOW_PLUGINS=1 on API and worker')
    module,name=kind.split(':',1)
    cls=getattr(import_module(module),name)
    if not isinstance(cls,type) or not issubclass(cls,AnalysisModule):
        raise ValueError('analysis plugin must inherit AnalysisModule')
    return cls

def metadata(kind):
    cls=constructor(kind)
    return dict(kind=kind,label=cls.label,description=cls.description,sources=cls.sources,
                schema=describe_schema(cls.Options.model_json_schema()),defaults=cls.Options().model_dump(mode='json'))

def inventory(): return {k:metadata(k) for k in bundled()}

def prepare(selections):
    selections=selections if selections is not None else [dict(kind=k) for k in DEFAULT_MODULES]
    result=[];seen=set()
    for raw in selections:
        item=Selection.model_validate(raw)
        if item.kind in seen: raise ValueError('duplicate analysis module: '+item.kind)
        seen.add(item.kind)
        if not item.enabled: continue
        cls=constructor(item.kind)
        result.append((item.kind,cls,cls.Options.model_validate(item.options)))
    return result

def execute(selected,context):
    from lorasim.diagnostics import exception_diagnostics
    results=[]
    for kind,cls,options in selected:
        output=dict(kind=kind,label=cls.label,description=cls.description,sources=cls.sources,options=options.model_dump(mode='json'))
        try:
            data=cls().compute(context,options)
            json.dumps(data,allow_nan=False)  # Catch non-finite plugin results locally.
            output.update(status='completed',data=data)
        except Exception as exc:
            output.update(status='failed',diagnostics=exception_diagnostics(exc))
        results.append(output)
    return results
