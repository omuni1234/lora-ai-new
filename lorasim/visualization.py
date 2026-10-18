"""Read-only, request-driven sampling. The simulation has no visualization loop."""
from pathlib import Path
import math
import numpy as np
import pyarrow.parquet as pq
from .components import make


def layout(profile, at_s=0., limit=500):
    """Reconstruct a deterministic, evenly spaced ED sample with independent RNGs."""
    total = sum(g.count for g in profile.groups)
    stride = max(1,math.ceil(total/limit))
    result, offset, skipped = [],0,0
    for group in profile.groups:
        rng = np.random.default_rng(np.random.SeedSequence([profile.seed,offset,900]))
        # Uniform placement RNG is consumed in bounded chunks, matching Simulation.
        for begin in range(0,group.count,4096):
            size = min(4096,group.count-begin)
            positions = rng.uniform([0,0],profile.area_m,size=(size,2)) if group.placement=='uniform' else None
            for local in range(size):
                j = begin+local; i = offset+j
                if i % stride:
                    continue
                if group.placement=='explicit':
                    initial = group.positions[j]
                elif group.placement=='grid':
                    side = math.ceil(math.sqrt(group.count))
                    initial = ((j%side+.5)*profile.area_m[0]/side,(j//side+.5)*profile.area_m[1]/side,1.5)
                else:
                    initial = (*positions[local],1.5)
                mobility = group.mobility
                for a in profile.assignments:
                    if (not a.groups or group.name in a.groups) and (not a.ids or i in a.ids) and (not a.tags or set(a.tags)<=set(group.tags)) and a.mobility is not None:
                        mobility = a.mobility
                spec = profile.component(mobility,'mobility')
                xyz,known = initial,at_s==0
                # Do not execute user Python in a visualization request.
                if spec.kind in {'stationary','waypoints','random_walk'}:
                    steps = at_s/spec.options.get('step_s',10.) if spec.kind=='random_walk' else 0
                    if steps <= 10000:
                        model = make(spec,'mobility',initial=initial,area=profile.area_m,
                                     rng=np.random.default_rng(np.random.SeedSequence([profile.seed,i,2])))
                        xyz = model.position(at_s).tolist(); known = True
                if not known:
                    skipped += 1
                result.append({'id':i,'group':group.name,'xyz':list(xyz),'position_at_requested_time':known})
        offset += group.count
    return {'devices':result,'ed_count':total,'sample_stride':stride,'at_s':at_s,'positions_unavailable':skipped,
            'duration_s':profile.duration_s,'gateways':[g.model_dump() for g in profile.gateways],'area_m':profile.area_m}


def traces(root, limit=1000, before_s=None):
    # At most 8 completed parts, at most 2 row groups per part. No whole-run scan.
    rows = []
    paths = sorted((Path(root)/'transmissions').glob('*.parquet'))
    scanned = 0
    for path in reversed(paths):
        pf = pq.ParquetFile(path)
        if before_s is not None and pf.metadata.num_row_groups:
            col = pf.schema_arrow.get_field_index('start_ns')
            stats = pf.metadata.row_group(0).column(col).statistics
            if stats and stats.min > before_s*1e9:
                continue
        scanned += 1
        for rg in reversed(range(max(0,pf.num_row_groups-2),pf.num_row_groups)):
            batch = pf.read_row_group(rg,columns=['device_id','packet_id','direction','start_ns','end_ns','sf','tx_power_dbm','x_m','y_m','z_m'])
            for row in reversed(batch.to_pylist()):
                if before_s is None or row['start_ns']<=before_s*1e9:
                    rows.append(row)
                    if len(rows)>=limit:
                        break
            if len(rows)>=limit:
                break
        if scanned>=8 or len(rows)>=limit:
            break
    return {'transmissions':list(reversed(rows)),'sampled':True,'completed_parts':len(paths),
            'note':'Bounded tail of committed Parquet parts; connecting TX positions is a sampled trajectory, not the full path.'}


def packet_details(root, packet_id):
    """Parquet statistics prune parts/row groups; return only one packet's records."""
    result = {}
    for table in ('transmissions','receptions'):
        rows = []
        for path in sorted((Path(root)/table).glob('*.parquet')):
            pf = pq.ParquetFile(path)
            column = pf.schema_arrow.get_field_index('packet_id')
            for group in range(pf.num_row_groups):
                stats = pf.metadata.row_group(group).column(column).statistics
                if stats and not stats.min<=packet_id<=stats.max:
                    continue
                import pyarrow.compute as pc
                data = pf.read_row_group(group)
                rows.extend(data.filter(pc.equal(data['packet_id'],packet_id)).to_pylist())
        result[table] = rows
    return result


def preflight(profile, horizon_s=None, limit=80, samples=12):
    """Bounded dry run: configuration inspection only, no radio/MAC/feedback/events."""
    from .interfaces import Observation, Policy
    from .registry import constructor
    from .config import Radio
    horizon = min(profile.duration_s, 600. if horizon_s is None else horizon_s)
    view = layout(profile,0,limit)
    view.update(mode='preflight',horizon_s=horizon,transmissions=[],warnings=[],
                action_space={**profile.action_space.model_dump(), 'action_count':len(profile.action_space.radios())},
                note='生成予定・未学習の選択例。MAC待機、再送、干渉、ACK、学習を模擬しないため実送信時刻・最終性能とは異なります。')
    groups = {g.name:g for g in profile.groups}
    for d in view['devices']:
        group = groups[d['group']]
        specs = {k:getattr(group,k) for k in ('policy','timing','mobility')}
        for a in profile.assignments:
            if (not a.groups or group.name in a.groups) and (not a.ids or d['id'] in a.ids) and (not a.tags or set(a.tags)<=set(group.tags)):
                specs.update({k:getattr(a,k) for k in specs if getattr(a,k) is not None})
        resolved = {k:profile.component(v,k) for k,v in specs.items()}
        d['models'] = {k:{'label':specs[k].ref or v.kind,**v.model_dump()} for k,v in resolved.items()}
        d['schedule'],d['path'],d['warnings'] = [],[],[]
        instances = {}
        for k,stream in [('policy',0),('timing',1),('mobility',2)]:
            spec=resolved[k]
            cls=constructor(k,spec.kind)
            if hasattr(cls,'Options'):
                d['models'][k]['options']=cls.Options.model_validate(spec.options).model_dump(mode='json')
            if not getattr(cls,'preview_safe',False):
                d['warnings'].append(f'{k}: preview_safe未宣言のため呼び出しを省略')
                continue
            if k=='policy' and hasattr(cls,'preview_bytes') and cls.preview_bytes(spec.options,profile.action_space)*len(view['devices'])>67108864:
                d['warnings'].append('policy: プレビューのモデルメモリ上限64 MiBを超えるため選択例を省略')
                continue
            # Bound random-walk stepping independently of experiment duration.
            if k=='mobility' and hasattr(cls,'preview_cost') and cls.preview_cost(spec.options,horizon)>10000/max(1,len(view['devices'])):
                d['warnings'].append('mobility: プレビューの計算上限を超えるため軌跡を省略')
                continue
            context={'rng':np.random.default_rng(np.random.SeedSequence([profile.seed,d['id'],stream]))}
            if k=='policy':context['action_space']=profile.action_space
            if k=='mobility':context.update(initial=d['xyz'],area=profile.area_m)
            instances[k]=make(spec,k,**context)
        obs=Observation(0.,d['id'],0,None,None,None,None,tuple(d['xyz']) if group.observe_position else None)
        if 'policy' in instances and callable(getattr(instances['policy'],'initialize',None)) and getattr(type(instances['policy']),'initialize',None) is not Policy.initialize:
            instances['policy'].initialize(obs)
        previous=None
        if 'timing' in instances:
            for j in range(samples):
                t=instances['timing'].next_time(obs,previous)
                if t is None:break
                t=float(t)
                if not math.isfinite(t) or t<obs.time_s or (previous is not None and t<=previous):
                    raise ValueError('timing preview must return None or finite future time')
                if j==0:d['first_generation_s']=t
                if t>horizon:break
                d['schedule'].append({'time_s':t,'tx_index':j+1})
                previous=t
                obs=Observation(t,d['id'],j+1,None,None,None,None,None)
        times=sorted(set([float(t) for t in np.linspace(0,horizon,samples)]+[x['time_s'] for x in d['schedule']]))
        positions={}
        for t in times:
            xyz=list(map(float,instances['mobility'].position(t))) if 'mobility' in instances else list(d['xyz'])
            if len(xyz)!=3 or not all(map(math.isfinite,xyz)):
                raise ValueError('mobility must return three finite coordinates')
            positions[t]=xyz
            d['path'].append({'time_s':t,'xyz':xyz})
        for row in d['schedule']:
            xyz=positions[row['time_s']]
            obs=Observation(row['time_s'],d['id'],row['tx_index']-1,None,None,None,None,tuple(xyz) if group.observe_position else None)
            if 'policy' in instances:
                radio=Radio.model_validate(instances['policy'].select(obs))
                band=profile.direction_band(radio.frequency_hz,'ul')
                if radio.tp_dbm>profile.sub_bands[band].max_tp_dbm:
                    raise ValueError('preview policy TP exceeds sub-band maximum')
                row['radio']=radio.model_dump()
            row['xyz']=xyz
            view['transmissions'].append({'device_id':d['id'],'direction':'UL','start_ns':int(row['time_s']*1e9),
                                         'x_m':xyz[0],'y_m':xyz[1],'z_m':xyz[2],**row})
        if d['warnings']:view['warnings'].append({'device_id':d['id'],'messages':d['warnings']})
    return view
