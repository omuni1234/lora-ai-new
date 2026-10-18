"""Bounded Parquet scans. No packet-sized joins or whole-dataset DataFrames."""
from collections import Counter, defaultdict
from pathlib import Path
import math
import json
import numpy as np
import pyarrow.parquet as pq
from .config import load_profile


def batches(root, table, columns=None):
    for path in sorted((Path(root)/table).glob('*.parquet')):
        yield from pq.ParquetFile(path).iter_batches(batch_size=8192,columns=columns)


def fairness(values):
    x = np.asarray(values,dtype=float)
    if len(x)==0 or x.sum()==0:
        return {"jain":None,"gini":None,"min_max":None,"cv":None,"p10":None,"median":None}
    ordered = np.sort(x)
    n = len(x)
    return {"jain":float(x.sum()**2/(n*np.dot(x,x))),
            "gini":float((2*np.dot(np.arange(1,n+1),ordered)/(n*x.sum()))-(n+1)/n),
            "min_max":float(x.min()/x.max()),"cv":float(x.std()/x.mean()),
            "p10":float(np.quantile(x,.1)),"median":float(np.median(x))}


def analyze_run(root, start=1, end=None, include_mcu=False, series=True, bins=100):
    root = Path(root)
    p = load_profile(root/'profile.yaml')
    n = sum(g.count for g in p.groups)
    if start < 1 or (end is not None and end < start) or not 1 <= bins <= 500:
        raise ValueError('invalid transmission range or bin count')
    if not (root/'uplinks').exists():
        raise ValueError('This run uses tracking schema v1. Re-run with v0.2 to obtain transmission-indexed analysis.')
    # Memory O(ED count + bounded bins * ED count), independent of packet count.
    totals = np.zeros(n,dtype=np.int64)
    success = np.zeros(n,dtype=np.int64)
    causes = {}
    censored = 0
    maximum = 0
    for batch in batches(root,'uplinks',['device_id','tx_index','outcome']):
        cols = batch.to_pydict()
        for ed,ordinal,outcome in zip(*cols.values()):
            maximum = max(maximum,ordinal)
            if ordinal<start or (end is not None and ordinal>end):
                continue
            if outcome=='Censored':
                censored += 1
                continue
            totals[ed] += 1
            success[ed] += outcome=='Received'
            if outcome not in causes:
                causes[outcome] = np.zeros(n,dtype=np.int64)
            causes[outcome][ed] += 1
    eligible = totals>0
    prr = success[eligible]/totals[eligible]
    fractions = {k:float(np.mean(v[eligible]/totals[eligible])) for k,v in sorted(causes.items())}
    result = {"name":p.name,"ed_count":n,"eligible_ed_count":int(eligible.sum()),
              "inactive_ed_count":int((~eligible).sum()),"tx_start":start,"tx_end":end,
              "maximum_tx_index":maximum,"uplinks":int(totals.sum()),"censored":censored,
              "mean_ed_prr":float(prr.mean()) if len(prr) else None,
              "pooled_prr":float(success.sum()/totals.sum()) if totals.sum() else None,
              "fractions":fractions,"fairness":fairness(prr),"include_mcu":include_mcu}
    energy = Counter()
    energy_ed = np.zeros(n)
    startup = Counter()
    for batch in batches(root,'energy',['device_id','tx_index','component','state','energy_j']):
        c = batch.to_pydict()
        for ed,ordinal,component,state,joules in zip(*c.values()):
            if component=='mcu' and not include_mcu:
                continue
            if ordinal==0:
                startup[component] += joules
            if ordinal>=start and (end is None or ordinal<=end):
                energy[f'{component}:{state}'] += joules
                energy_ed[ed] += joules
    result['energy'] = {"by_state_j":dict(energy),"total_j":float(energy_ed.sum()),
                        "mean_ed_j":float(energy_ed.mean()),"startup_excluded_j":dict(startup),
                        "j_per_received_uplink":float(energy_ed.sum()/success.sum()) if success.sum() else None,
                        "fairness":fairness(energy_ed)}
    result['series'] = []
    if series and maximum>=start:
        stop = min(maximum,end) if end is not None else maximum
        result['series'],result['series_bin_width'] = time_series(root,n,start,stop,bins)

    return result


def time_series(root,n,start,stop,bins):
    if stop<start:
        return [],1
    # Dense count arrays avoid millions of Python Counter objects.
    count_bins = max(1,min(bins,1_000_000//max(n,1)))
    width = max(1,math.ceil((stop-start+1)/count_bins))
    count_bins = math.ceil((stop-start+1)/width)
    totals = np.zeros((count_bins,n),dtype=np.int64)
    causes = {}
    for batch in batches(root,'uplinks',['device_id','tx_index','outcome']):
        c = batch.to_pydict()
        for ed,ordinal,outcome in zip(*c.values()):
            if start<=ordinal<=stop and outcome!='Censored':
                index = (ordinal-start)//width
                totals[index,ed] += 1
                if outcome not in causes:
                    causes[outcome] = np.zeros_like(totals)
                causes[outcome][index,ed] += 1
    result = []
    for i in range(count_bins):
        eligible = totals[i]>0
        if not eligible.any():
            continue
        parts = {k:float(np.mean(a[i,eligible]/totals[i,eligible])) for k,a in causes.items()}
        result.append({"start":start+i*width,"end":min(stop,start+(i+1)*width-1),
                       "eligible_ed_count":int(eligible.sum()),"fractions":parts})
    return result,width


def compare_runs(runs, start=1, end=None, include_mcu=False, bins=100, ed_count=None):
    reports = []
    for run in runs:
        report = analyze_run(run['root'],start,end,include_mcu,series=False,bins=bins)
        report.update({k:run[k] for k in ('id','experiment_id','experiment_name')})
        if ed_count is None or report['ed_count']==ed_count:
            reports.append(report)
    # Each included ED has equal weight even across repeat runs, not each packet.
    grouped = defaultdict(list)
    for r in reports:
        grouped[(r['experiment_id'],r['ed_count'])].append(r)
    by_count, by_time = [], []
    for (eid,n),rs in grouped.items():
        weight = sum(r['eligible_ed_count'] for r in rs)
        parts = Counter()
        for r in rs:
            for key,value in r['fractions'].items():
                parts[key] += value*r['eligible_ed_count']/weight if weight else 0
        stop = max(r['maximum_tx_index'] for r in rs)
        if end is not None:
            stop = min(stop,end)
        temporal = defaultdict(list)
        for r in rs:
            source = next(run['root'] for run in runs if run['id']==r['id'])
            r['series'],r['series_bin_width'] = time_series(source,n,start,stop,bins)
            for point in r['series']:
                temporal[(point['start'],point['end'])].append(point)
        combined = []
        for (lo,hi),points in sorted(temporal.items()):
            eligible = sum(point['eligible_ed_count'] for point in points)
            fractions = Counter()
            for point in points:
                for k,v in point['fractions'].items():
                    fractions[k] += v*point['eligible_ed_count']/eligible
            combined.append({'start':lo,'end':hi,'eligible_ed_count':eligible,'fractions':dict(fractions)})
        by_time.append({'experiment_id':eid,'experiment_name':rs[0]['experiment_name'],'ed_count':n,'series':combined})
        by_count.append({"experiment_id":eid,"experiment_name":rs[0]['experiment_name'],"ed_count":n,
                         "eligible_ed_count":weight,"run_count":len(rs),"fractions":dict(parts)})
    return {"runs":reports,"by_time":by_time,"by_ed_count":sorted(by_count,key=lambda x:(x['experiment_name'],x['ed_count'])),
            "definitions":{"weighting":"equal ED; devices with no completed uplink in range are excluded from PRR/fairness",
              "success":"at least one GW received the uplink; ACK success is a separate metric",
              "mixed_loss":"all GWs failed with different causes", "censored":"unknown at horizon; excluded from fractions",
              "energy":"radio/idle epochs [uplink k, uplink k+1); MCU work charged to its attempted transmission; startup ordinal 0 excluded"}}
