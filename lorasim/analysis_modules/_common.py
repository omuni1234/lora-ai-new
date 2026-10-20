"""Shared bounded analysis helpers. Never instantiate a selector or mobility."""
from collections import Counter
import hashlib
import json
import numpy as np
from lorasim.config import load_profile

CAUSES = ['Received','Interference','LostBecauseTx','UnderSensitivity','NoMoreReceivers','MixedLoss','WrongFrequency']
CAUSE_LABELS = ['受信成功','干渉','GW送信中','感度未満','復調器不足','複合損失','周波数不一致']


def profile(c, r):
    return c.cached(('profile',r['id']), lambda: load_profile(c.roots[r['id']]/'profile.yaml'))


def identities(c, r):
    """Actual effective per-ED implementations including assignment overrides."""
    def read():
        p=profile(c,r); policies=[]; mobility=[]; signatures=[]
        for g in p.groups:
            for _ in range(g.count):
                ed=len(policies)
                specs={k:getattr(g,k) for k in ('policy','mobility')}
                for a in p.assignments:
                    if (not a.groups or g.name in a.groups) and (not a.ids or ed in a.ids) and (not a.tags or set(a.tags)<=set(g.tags)):
                        specs.update({k:getattr(a,k) for k in specs if getattr(a,k) is not None})
                specs={k:p.component(v,k) for k,v in specs.items()}
                policies.append(specs['policy'].kind)
                mobility.append(specs['mobility'].kind)
                signatures.append(json.dumps(specs['policy'].model_dump(mode='json'),sort_keys=True))
        counts=Counter(policies)
        label=next(iter(counts)) if len(counts)==1 else '混在: '+', '.join(f'{k} ({v} ED)' for k,v in sorted(counts.items()))
        # Do not merge algorithm hyperparameters or common action spaces silently.
        signature=hashlib.sha256(json.dumps([sorted((k,v/len(signatures)) for k,v in Counter(signatures).items()),p.action_space.model_dump()],sort_keys=True).encode()).hexdigest()[:10]
        return dict(algorithm=label,mobility=', '.join(sorted(set(mobility))),mobility_by_ed=mobility,signature=signature)
    return c.cached(('identities',r['id']),read)


def title(c,r):
    return f"{identities(c,r)['algorithm']} · {r['experiment_name']} · {r['ed_count']} ED · {r['id'][:8]}"


def records(c,r,table,columns):
    for b in c.batches(r['id'],table,columns):
        yield from zip(*[b.column(k).to_pylist() for k in columns])


def state_for(c,r,ed,state):
    if state in ('moving','stationary'): return state
    # Only the exact built-in stationary implementation is safe for old data.
    kind=identities(c,r)['mobility_by_ed'][ed]
    return 'stationary' if kind in ('stationary','lorasim.plugins.mobility.stationary:Stationary') else 'unknown'


def uplinks(c,r):
    # Arrow omits absent requested columns in old Parquet files. Fill explicitly.
    keys=['device_id','tx_index','time_ns','outcome','x_m','y_m','z_m','motion_state']
    for b in c.batches(r['id'],'uplinks',keys):
        cols=b.to_pydict()
        for row in zip(*(cols.get(k,[None]*b.num_rows) for k in keys)):
            yield row


def table(title,columns,rows):
    return dict(type='table',title=title,columns=columns,rows=rows)


def line(title,series,x_label,y_label,**kwargs):
    return dict(type='lines',title=title,series=series,x_label=x_label,y_label=y_label,**kwargs)


def endpoints(start,stop,bins,n=1):
    if stop<start: return np.array([],dtype=np.int64)
    count=max(1,min(bins,1_000_000//max(1,n)))
    if count==1: return np.array([stop],dtype=np.int64)
    return np.unique(np.linspace(start,stop,min(count,stop-start+1),dtype=np.int64))
