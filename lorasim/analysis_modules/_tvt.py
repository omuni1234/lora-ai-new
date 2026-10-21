"""Bounded shared helpers for GUI analyses. Never execute a simulation plugin."""
from collections import defaultdict
from contextlib import contextmanager
import hashlib
import json
import math
import sqlite3
import tempfile
from pathlib import Path
import numpy as np
from ._common import profile


def records(c,r,table,keys):
    for batch in c.batches(r['id'],table,keys):
        cols=batch.to_pydict()
        yield from (dict(zip(keys,v)) for v in zip(*(cols.get(k,[None]*batch.num_rows) for k in keys)))


def quality(values,threshold):
    x=np.sort(np.asarray(values,dtype=float));n=len(x)
    return dict(prr=float(x.mean()) if n else None,
        jain=float(x.sum()**2/(n*np.dot(x,x))) if n and np.dot(x,x)>0 else None,
        bottom10=float(x[:max(1,math.ceil(n*.1))].mean()) if n else None,
        coverage=float(np.mean(x>=threshold)) if n else None)


def bootstrap(values,resamples=5000):
    x=np.asarray(values,dtype=float)
    if not len(x):return None,None,None
    mean=float(x.mean())
    if len(x)<2:return mean,None,None
    rng=np.random.default_rng(2026)
    means=np.concatenate([x[rng.integers(len(x),size=(min(250,resamples-i),len(x)))].mean(axis=1)
                          for i in range(0,resamples,250)])
    low,high=np.quantile(means,[.025,.975])
    return mean,float(low),float(high)


@contextmanager
def scratch_database():
    with tempfile.TemporaryDirectory(prefix='lorasim-analysis-') as d:
        db=sqlite3.connect(str(Path(d)/'trace.sqlite'))
        try:yield db
        finally:db.close()


def cpu_stats(c,r):
    def scan():
        with scratch_database() as db:
            db.execute('CREATE TABLE t(ed INTEGER, tx INTEGER, op TEXT, cpu REAL, meta REAL, PRIMARY KEY(ed,tx,op))')
            for batch in c.batches(r['id'],'policy_timing',['device_id','tx_index','operation','cpu_ns','meta_cpu_ns']):
                db.executemany('INSERT INTO t VALUES(?,?,?,?,?)',
                    ((v['device_id'],v['tx_index'],v['operation'],v['cpu_ns']/1000,v['meta_cpu_ns']/1000 if v['meta_cpu_ns'] is not None else 0.)
                     for v in batch.to_pylist() if v['tx_index'] and c.selected(v['tx_index']) and v['cpu_ns'] is not None))
            db.execute('CREATE TABLE values_by_kind(kind TEXT,value REAL)')
            db.execute('INSERT INTO values_by_kind SELECT op,cpu FROM t')
            db.execute("INSERT INTO values_by_kind SELECT 'meta_update',meta FROM t WHERE op='training'")
            db.execute("INSERT INTO values_by_kind SELECT 'decision_plus_meta',i.cpu+t.meta FROM t i JOIN t ON i.ed=t.ed AND i.tx=t.tx WHERE i.op='inference' AND t.op='training'")
            db.execute('CREATE INDEX ranked ON values_by_kind(kind,value)')
            data={}
            for kind,n,mean,maximum in db.execute('SELECT kind,COUNT(*),AVG(value),MAX(value) FROM values_by_kind GROUP BY kind').fetchall():
                h=.95*(n-1);lo=int(math.floor(h));hi=int(math.ceil(h))
                vals=[v[0] for v in db.execute('SELECT value FROM values_by_kind WHERE kind=? ORDER BY value LIMIT ? OFFSET ?',(kind,hi-lo+1,lo))]
                data[kind]=dict(n=n,mean_us=mean,p95_us=vals[0]+(h-lo)*(vals[-1]-vals[0]),max_us=maximum)
            stop=min(c.end or r['maximum_tx_index'],r['maximum_tx_index'])
            width=max(1,math.ceil((stop-c.start+1)/100))
            series=defaultdict(list)
            for op,bucket,n,mean in db.execute('SELECT op,(tx-?)/?,COUNT(*),AVG(cpu) FROM t GROUP BY op,(tx-?)/? ORDER BY op,(tx-?)/?',(c.start,width,c.start,width,c.start,width)):
                series[op].append(dict(x=min(stop,c.start+(bucket+1)*width-1),y=mean,n=n))
            return dict(metrics=data,series=dict(series))
    return c.cached(('tvt_cpu',r['id']),scan)


def comparison_identity(c,r):
    """Remove only the policy/seed/name, resolve actual per-ED assignments."""
    p=profile(c,r);data=p.model_dump(mode='json')
    for key in ('name','seed','models','model_files','assignments','notifications'):
        data.pop(key,None)
    policies=[];devices=[]
    for g in p.groups:
        for _ in range(g.count):
            ed=len(devices);spec={k:getattr(g,k) for k in ('policy','mobility','timing')}
            for a in p.assignments:
                if (not a.groups or g.name in a.groups) and (not a.ids or ed in a.ids) and (not a.tags or set(a.tags)<=set(g.tags)):
                    spec.update({k:getattr(a,k) for k in spec if getattr(a,k) is not None})
            spec={k:p.component(v,k).model_dump(mode='json') for k,v in spec.items()}
            policies.append(spec.pop('policy'));devices.append(spec)
    for g in data['groups']:
        for key in ('policy','mobility','timing'):g.pop(key,None)
    data['effective_devices']=devices
    manifest=c.roots[r['id']]/'manifest.json'
    if manifest.exists():
        m=json.loads(manifest.read_text());data['implementation']=m.get('source_sha256');data['dependencies']=m.get('dependencies')
    digest=lambda value:hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()[:16]
    return digest(data),digest(policies),{x['kind'] for x in policies},p.seed
