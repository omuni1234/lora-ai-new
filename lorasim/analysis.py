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


def analyze_run(root, start=1, end=None, include_mcu=False, series=True, bins=100, energy_enabled=True, per_device=False, fairness_enabled=True):
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
              "fractions":fractions,"fairness":fairness(prr) if fairness_enabled else {},"include_mcu":include_mcu}
    energy = Counter()
    energy_ed = np.zeros(n)
    startup = Counter()
    for batch in (batches(root,'energy',['device_id','tx_index','component','state','energy_j']) if energy_enabled else ()):
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
    if per_device:
        result['device_prr'] = prr.tolist()
    result['energy']['measured'] = energy_enabled and p.energy.radio.enabled
    result['series'] = []
    if series and maximum>=start:
        stop = min(maximum,end) if end is not None else maximum
        result['series'],result['series_bin_width'] = time_series(root,n,start,stop,bins)

    return result


def time_series(root,n,start,stop,bins):
    """One scan + prefix sums; each plotted t includes all completed attempts 1..t.

    start controls plotted endpoints only. ED weighting is applied AFTER prefix
    counts, so short and long histories carry equal weight. No packet joins.
    """
    if stop<start:
        return [],1
    count_bins=max(1,min(bins,1_000_000//max(n,1)))
    width=max(1,math.ceil((stop-start+1)/count_bins))
    endpoints=np.arange(start+width-1,stop+width,width,dtype=np.int64)
    endpoints=np.minimum(endpoints,stop)
    totals=np.zeros((len(endpoints),n),dtype=np.int64)
    causes={}
    for batch in batches(root,'uplinks',['device_id','tx_index','outcome']):
        c=batch.to_pydict()
        ed=np.asarray(c['device_id'],dtype=np.int64)
        ordinal=np.asarray(c['tx_index'],dtype=np.int64)
        outcomes=np.asarray(c['outcome'])
        valid=(ordinal>=1)&(ordinal<=stop)&(outcomes!='Censored')
        ed,ordinal,outcomes=ed[valid],ordinal[valid],outcomes[valid]
        index=np.searchsorted(endpoints,ordinal,side='left')
        np.add.at(totals,(index,ed),1)
        for outcome in np.unique(outcomes):
            if outcome not in causes: causes[str(outcome)]=np.zeros_like(totals)
            mask=outcomes==outcome
            np.add.at(causes[outcome],(index[mask],ed[mask]),1)
    np.cumsum(totals,axis=0,out=totals)
    for counts in causes.values(): np.cumsum(counts,axis=0,out=counts)
    result=[]
    for i,t in enumerate(endpoints):
        eligible=totals[i]>0
        if not eligible.any(): continue
        parts={k:float(np.mean(a[i,eligible]/totals[i,eligible])) for k,a in sorted(causes.items())}
        result.append({'start':1,'end':int(t),'eligible_ed_count':int(eligible.sum()),'fractions':parts})
    return result,width


def compare_runs(runs, start=1, end=None, include_mcu=False, bins=100, ed_count=None, modules=None):
    if start<1 or (end is not None and end<start) or not 1<=bins<=500:
        raise ValueError('invalid transmission range or bin count')
    from .analysis_modules import prepare, execute
    selected=prepare(modules)
    if not selected: raise ValueError('no enabled analysis modules')
    requirements=set().union(*(cls.requires for _,cls,_ in selected))
    reports = []
    for run in runs:
        report = analyze_run(run['root'],start,end,include_mcu,series=False,bins=bins,
                             energy_enabled='energy' in requirements, per_device='prr' in requirements,
                             fairness_enabled='fairness' in requirements)
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
        for r in (rs if 'series' in requirements else []):
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
    result = {"runs":reports,"by_time":by_time,"by_ed_count":sorted(by_count,key=lambda x:(x['experiment_name'],x['ed_count'])),
            "definitions":{"weighting":"equal ED; devices with no completed uplink in range are excluded from PRR/fairness",
              "success":"at least one GW received the uplink; ACK success is a separate metric",
              "mixed_loss":"all GWs failed with different causes", "censored":"unknown at horizon; excluded from fractions",
              "energy":"radio/idle epochs [uplink k, uplink k+1); MCU work charged to its attempted transmission; startup ordinal 0 excluded"}}

    context = AnalysisContext(result,{run['id']:Path(run['root']) for run in runs},start,end)
    result['modules']=execute(selected,context)
    result['definitions']['convergence']='cumulative 1..t, equal eligible ED weight; tx_start limits plotted endpoints only'
    for report in reports: report.pop('device_prr',None)
    return result


class AnalysisContext:
    """Per-request shared reports/cache; no simulator or selector is instantiated."""
    def __init__(self,result,roots,start,end):
        self.result,self.roots,self.start,self.end=result,roots,start,end
        self._cache={}
    @property
    def runs(self): return self.result['runs']
    def selected(self,index): return index>=self.start and (self.end is None or index<=self.end)
    def batches(self,run_id,table,columns=None): return batches(self.roots[run_id],table,columns)
    def cached(self,key,loader):
        if key not in self._cache: self._cache[key]=loader()
        return self._cache[key]
    def radio(self,run):
        def scan():
            count,total,retries=0,0.,0
            elapsed=self.observed_duration(run)
            sf_time,channel_time=Counter(),Counter()
            for batch in self.batches(run['id'],'transmissions',['direction','tx_index','start_ns','end_ns','sf','frequency_hz','attempt']):
                c=batch.to_pydict()
                for direction,index,begin,finish,sf,ch,attempt in zip(*c.values()):
                    if direction!='UL' or not self.selected(index): continue
                    duration=max(0,min(finish,elapsed*1e9)-begin)/1e9 if elapsed is not None else (finish-begin)/1e9
                    count+=1; total+=duration; retries+=attempt>1
                    sf_time['SF'+str(sf)]+=duration
                    channel_time[f'{ch/1e6:g} MHz']+=duration
            return dict(attempts=count,airtime_basis="observed" if elapsed is not None else "scheduled",total_airtime_s=total,mean_airtime_s=total/count if count else None,
                        retransmission_fraction=retries/count if count else None,
                        sf_airtime_s=dict(sf_time),channel_airtime_s=dict(channel_time))
        return self.cached(('radio',run['id']),scan)
    def delivery(self,run):
        def scan():
            p=load_profile(self.roots[run['id']]/'profile.yaml')
            payload=np.repeat([g.payload_bytes for g in p.groups],[g.count for g in p.groups])
            bits=0
            for batch in self.batches(run['id'],'uplinks',['device_id','tx_index','outcome']):
                for ed,index,outcome in zip(*batch.to_pydict().values()):
                    if self.selected(index) and outcome=='Received': bits+=8*int(payload[ed])
            elapsed=self.observed_duration(run)
            # The horizon is a common denominator, NOT tx_end - tx_start.
            return dict(received_payload_bits=bits,observed_duration_s=elapsed,
                        received_payload_bps=bits/elapsed if elapsed else None)
        return self.cached(('delivery',run['id']),scan)

    def observed_duration(self,run):
        def read():
            path=self.roots[run['id']]/'summary.json'
            return json.loads(path.read_text()).get('sim_time_s') if path.exists() else None
        return self.cached(('duration',run['id']),read)
