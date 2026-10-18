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
