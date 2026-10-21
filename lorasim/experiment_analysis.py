"""Disk-backed experiment analysis; independent seeds are statistical replicates."""
from collections import defaultdict
import csv
import json
import math
from pathlib import Path
import sqlite3
import tempfile

import numpy as np
import pyarrow.parquet as pq
from .tracking import atomic_json


def rows(root,table,columns):
    for path in sorted((Path(root)/table).glob('*.parquet')):
        for batch in pq.ParquetFile(path).iter_batches(columns=columns,batch_size=8192):
            yield from batch.to_pylist()


def quality(values,threshold=.7):
    x=np.asarray(values,dtype=float)
    if not len(x):
        raise ValueError('no ED PRRs')
    return dict(mean_prr=float(x.mean()),jain=float(x.sum()**2/(len(x)*(x*x).sum())) if (x*x).sum() else None,
                bottom10_prr=float(np.sort(x)[:max(1,math.ceil(.1*len(x)))].mean()),
                threshold_fraction=float((x>=threshold).mean()))


def interval(values):
    x=np.asarray(values,dtype=float)
    if not len(x):
        return dict(mean=None,ci_low=None,ci_high=None,n=0)
    mean=float(x.mean())
    if len(x)<2:
        return dict(mean=mean,ci_low=None,ci_high=None,n=len(x))
    rng=np.random.default_rng(2026)
    boot=x[rng.integers(len(x),size=(10000,len(x)))].mean(axis=1)
    lo,hi=np.quantile(boot,[.025,.975])
    return dict(mean=mean,ci_low=float(lo),ci_high=float(hi),n=len(x))


def analyze_run(root,spec,expected_n,expected_messages):
    """Sort outcomes by ED and TX index on disk, not by reception completion time."""
    root=Path(root)
    with tempfile.TemporaryDirectory(prefix='tvt-analysis-') as tmp:
        db=sqlite3.connect(str(Path(tmp)/'outcomes.sqlite'))
        try:
            db.execute('CREATE TABLE ul(ed INTEGER, tx INTEGER, packet INTEGER, received INTEGER, outcome TEXT, state TEXT, epoch INTEGER, PRIMARY KEY(ed,tx))')
            for batch_path in sorted((root/'uplinks').glob('*.parquet')):
                for batch in pq.ParquetFile(batch_path).iter_batches(batch_size=8192):
                    db.executemany('INSERT INTO ul VALUES(?,?,?,?,?,?,?)',
                        ((r['device_id'],r['tx_index'],r['packet_id'],int(r['received']),r['outcome'],r['motion_state'],r['motion_epoch_start_ns']) for r in batch.to_pylist()))
            db.execute('CREATE TABLE ack(packet INTEGER PRIMARY KEY, ack INTEGER)')
            db.executemany('INSERT INTO ack VALUES(?,?)',((r['packet_id'],r['ack']) for r in rows(root,'feedback',['packet_id','ack'])))
            db.commit()
            counts=db.execute('SELECT ed,COUNT(*),MAX(tx) FROM ul GROUP BY ed').fetchall()
            if len(counts)!=expected_n or any(n!=expected_messages or end!=expected_messages for _,n,end in counts):
                raise ValueError(f'{root}: incomplete transmit counts; refusing biased tail analysis')
            per_ed=[];events=[];state_counts=defaultdict(lambda:[0,0]);ack_total=ack_ok=0
            for ed,n,end in counts:
                tail=[];episode=None;prefix=[];episode_start_tx=None
                def finish():
                    if episode is None or episode[1] is None or episode[1]<=0 or episode_start_tx<=spec['transition_burnin']:
                        return
                    for h in spec['horizons']:
                        complete=len(prefix)>=h and all(v is not None for v in prefix[:h])
                        events.append(dict(device_id=ed,state=episode[0],epoch_start_ns=episode[1],horizon=h,
                            complete=complete,observed=min(len(prefix),h),loss=1-sum(prefix[:h])/h if complete else None))
                query='SELECT u.tx,u.received,u.outcome,u.state,u.epoch,a.ack FROM ul u LEFT JOIN ack a ON a.packet=u.packet WHERE u.ed=? ORDER BY u.tx'
                for tx,received,outcome,state,epoch,ack in db.execute(query,(ed,)):
                    key=(state,epoch)
                    if key!=episode:
                        finish();episode=key;prefix=[];episode_start_tx=tx
                    if len(prefix)<max(spec['horizons']):
                        prefix.append(None if outcome=='Censored' else received)
                    if tx>end-spec['tail']:
                        if outcome=='Censored' or ack is None:
                            raise ValueError(f'{root}: censored tail or missing ACK feedback')
                        tail.append(received);ack_total+=1;ack_ok+=ack
                        state_counts[state][0]+=received;state_counts[state][1]+=1
                finish()
                per_ed.append(dict(device_id=ed,prr=sum(tail)/len(tail),transmissions=len(tail)))
            metrics=quality([r['prr'] for r in per_ed],spec['threshold'])
            metrics['ack_success_rate']=ack_ok/ack_total
            for state,(ok,total) in state_counts.items():
                metrics[f'{state}_prr']=ok/total
                metrics[f'{state}_tx_fraction']=total/ack_total
            timing=defaultdict(list)
            for r in rows(root,'policy_timing',['device_id','tx_index','operation','cpu_ns','meta_cpu_ns']):
                if expected_messages-spec['tail']<r['tx_index']<=expected_messages:
                    timing[r['operation']].append(r['cpu_ns']/1000)
                    if r['operation']=='training':
                        timing['meta_update'].append(r['meta_cpu_ns']/1000)
            for name,values in timing.items():
                metrics[name+'_cpu_us']=float(np.mean(values))
                metrics[name+'_p95_cpu_us']=float(np.quantile(values,.95))
            if 'inference_cpu_us' in metrics and 'meta_update_cpu_us' in metrics:
                metrics['decision_plus_meta_cpu_us']=metrics['inference_cpu_us']+metrics['meta_update_cpu_us']
            for state in ('stationary','moving'):
                for h in spec['horizons']:
                    subset=[e for e in events if e['state']==state and e['horizon']==h]
                    complete=[e['loss'] for e in subset if e['complete']]
                    metrics[f'{state}_h{h}_loss']=float(np.mean(complete)) if complete else None
                    metrics[f'{state}_h{h}_complete']=len(complete)
                    metrics[f'{state}_h{h}_incomplete']=len(subset)-len(complete)
            return metrics,per_ed,events
        finally:
            db.close()


def write_csv(path,records):
    keys=list(dict.fromkeys(k for r in records for k in r))
    with Path(path).open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=keys);writer.writeheader();writer.writerows(records)


def analyze_plan(root,allow_partial=False):
    from .experiments import read_plan
    root=Path(root);plan=read_plan(root)
    missing=[r['id'] for r in plan['runs'] if not (root/'runs'/r['id']/'experiment-result.json').exists()]
    if missing and not allow_partial:
        raise ValueError(f'{len(missing)} runs missing; use --allow-partial only for diagnostics')
    dest=root/'report';dest.mkdir(exist_ok=True)
    (dest/'analysis.json').unlink(missing_ok=True)
    event_fields=['run','device_id','state','epoch_start_ns','horizon','complete','observed','loss']
    with (dest/'transitions.csv').open('w',newline='') as f:
        csv.DictWriter(f,fieldnames=event_fields).writeheader()
    finished=[];ed_rows=[];source=None
    for row in plan['runs']:
        out=root/'runs'/row['id']
        if not (out/'experiment-result.json').exists():
            continue
        marker=json.loads((out/'experiment-result.json').read_text())
        manifest=json.loads((out/'manifest.json').read_text())
        if marker['profile_sha256']!=row['sha256'] or manifest['profile_sha256']!=row['sha256']:
            raise ValueError('run profile mismatch')
        if source is not None and source!=manifest['source_sha256']:
            raise ValueError('cannot pool different source versions')
        source=manifest['source_sha256']
        metrics,eds,events=analyze_run(out,plan['analysis'],row['n'],12 if plan['smoke'] else 40000)
        finished.append({**row,**metrics})
        ed_rows.extend(dict(run=row['id'],**r) for r in eds)
        with (dest/'transitions.csv').open('a',newline='') as f:
            csv.DictWriter(f,fieldnames=event_fields).writerows(dict(run=row['id'],**r) for r in events)
    if missing and not allow_partial:
        raise ValueError(f'{len(missing)} runs missing; use --allow-partial only for diagnostics')
    if not finished:
        raise ValueError('no completed runs')
    groups=defaultdict(list)
    for r in finished:
        groups[(r['scenario'],r['n'],r['width'],r['method'])].append(r)
    aggregate=[];paired=[]
    # Include states absent in the first run (e.g. a short smoke run).
    metrics=sorted(set(k for r in finished for k in r if k not in plan['runs'][0]))
    for (scenario,n,width,method),runs in sorted(groups.items()):
        base=dict(scenario=scenario,n=n,width=width,method=method)
        for metric in metrics:
            vals=[r[metric] for r in runs if r.get(metric) is not None]
            aggregate.append(dict(**base,metric=metric,**{('replicates' if k=='n' else k):v for k,v in interval(vals).items()}))
        if method=='sema':
            continue
        sema={r['seed']:r for r in groups.get((scenario,n,width,'sema'),[])}
        for metric in metrics:
            pairs=[(sema[r['seed']][metric],r[metric]) for r in runs if r['seed'] in sema and r.get(metric) is not None and sema[r['seed']].get(metric) is not None]
            stats=interval([a-b for a,b in pairs])
            paired.append(dict(**base,metric=metric,**{('pairs' if k=='n' else k):v for k,v in stats.items()}))
    dest=root/'report';dest.mkdir(exist_ok=True)
    for name,data in [('runs',finished),('per_ed',ed_rows),('aggregate',aggregate),('paired_sema_minus_baseline',paired)]:
        write_csv(dest/f'{name}.csv',data)
    atomic_json(dest/'analysis.json',dict(smoke=plan['smoke'],partial=bool(missing),missing=missing,
        spec=plan['analysis'],confidence='95% percentile bootstrap, 10000 resamples over independent run seeds; paired differences use seed intersections; no multiplicity adjustment',
        timing='Host process CPU microseconds, not MCU latency. Training includes replay and meta update.',
        transitions='Within the same physical motion episode, first H transmissions; initial episode excluded; incomplete windows reported separately; episodes with no transmissions are unobserved.',
        source_sha256=source))
    print(f'Report: {dest} ({len(finished)} runs; {len(missing)} missing)')
