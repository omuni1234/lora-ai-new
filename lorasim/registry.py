"""One loader for bundled and user-defined model classes and their option schemas."""
from functools import lru_cache
from importlib import import_module
import inspect
import os
import pkgutil
from .interfaces import Policy, Mobility, Application, Plugin
from .ui_metadata import describe_schema

PACKAGES = {'policy':'selector','mobility':'mobility','timing':'application'}
BASES = {'policy':Policy,'mobility':Mobility,'timing':Application}

@lru_cache(maxsize=3)
def bundled(category):
    package = import_module('lorasim.plugins.'+PACKAGES[category])
    result = {}
    for item in sorted(pkgutil.iter_modules(package.__path__),key=lambda x:x.name):
        if not item.name.startswith('_'):
            module = import_module(package.__name__+'.'+item.name)
            if hasattr(module,'PLUGIN'):
                result[item.name] = module.PLUGIN
    return result


def constructor(category,kind):
    if category not in PACKAGES:
        raise ValueError('unknown plugin category: '+category)
    if kind in bundled(category):
        return bundled(category)[kind]
    if ':' not in kind:
        raise ValueError(f'unknown {category}: {kind}')
    module,name = kind.split(':',1)
    # Fully qualified names of bundled classes use exactly the same loader.
    trusted = {c.__module__+':'+c.__name__:c for c in bundled(category).values()}
    if kind in trusted:
        return trusted[kind]
    if os.getenv('LORASIM_ALLOW_PLUGINS') != '1':
        raise ValueError('custom Python code requires LORASIM_ALLOW_PLUGINS=1 on API and worker')
    cls = getattr(import_module(module),name)
    if not inspect.isclass(cls):
        raise ValueError('plugin must be a class')
    if issubclass(cls,Plugin) and not issubclass(cls,BASES[category]):
        raise ValueError('plugin interface does not match category')
    for method in {'policy':['select','observe'],'mobility':['position'],'timing':['next_time']}[category]:
        if not callable(getattr(cls,method,None)):
            raise ValueError(f'{category} plugin must implement {method}')
    return cls


def metadata(category,kind):
    cls = constructor(category,kind)
    options = getattr(cls,'Options',None)
    schema = describe_schema(options.model_json_schema()) if options else {'type':'object','additionalProperties':True}
    # GUI can initialize required fields without constructing a model or RNG.
    def defaults(s,defs):
        if '$ref' in s: return defaults(defs[s['$ref'].split('/')[-1]],defs)
        if 'default' in s: return s['default']
        if 'anyOf' in s: return None if any(x.get('type')=='null' for x in s['anyOf']) else defaults(s['anyOf'][0],defs)
        if 'enum' in s: return s['enum'][0]
        t=s.get('type')
        if t=='object': return {k:defaults(v,defs) for k,v in s.get('properties',{}).items()}
        if t=='array': return [defaults(x,defs) for x in s.get('prefixItems',[])]
        if t in ('number','integer'): return s.get('minimum',s.get('exclusiveMinimum',0)+1)
        return False if t=='boolean' else ''
    values = defaults(schema,schema.get('$defs',{}))
    if options:
        try: values=options().model_dump(mode='json')
        except ValueError: pass
    return {'kind':kind,'module':cls.__module__+':'+cls.__name__,'label':getattr(cls,'label','') or cls.__name__,
            'description':getattr(cls,'description',''),'schema':schema,'defaults':values,
            'uses_action_space':getattr(cls,'uses_action_space',False),
            'accepts_action_space':getattr(cls,'accepts_action_space',False) or getattr(cls,'uses_action_space',False),
            'space_options':getattr(cls,'space_options',[]),
            'fixed_space_dimensions':getattr(cls,'fixed_space_dimensions',()),
            'space_description':getattr(cls,'space_description',''),
            'deprecated':kind.endswith('_sf_ch'),
            'requires_position':getattr(cls,'requires_position',False),
            'preview_safe':getattr(cls,'preview_safe',False),'legacy':options is None}


def inventory():
    return {cat:{kind:metadata(cat,kind) for kind in bundled(cat)} for cat in PACKAGES}
