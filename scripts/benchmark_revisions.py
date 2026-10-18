"""Full tracking comparison with an unpacked earlier source tree.

python scripts/benchmark_revisions.py --baseline-source /path/to/v04 --output runs/revisions --rounds 2000
No untracked physics shortcuts. Results include setup, streaming writes, final analysis.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--baseline-source',required=True)
    ap.add_argument('--output',required=True)
    ap.add_argument('--n',type=int,default=500)
    ap.add_argument('--rounds',type=int,default=200)
    ap.add_argument('--repeat',type=int,default=1)
    ap.add_argument('--mobility',choices=['stationary','waypoints'],default='stationary')
    ap.add_argument('--child',action='store_true',help=argparse.SUPPRESS)
    a=ap.parse_args();root=Path(a.output).resolve()
    if a.child:
        from lorasim.config import Profile
        from lorasim.engine import Simulation
        import resource
        mobility={'kind':'stationary'} if a.mobility=='stationary' else {'kind':'waypoints','options':{'mode':'route','random_count':20,'speed_m_s':3,'pause_s':3000,'repeat':True}}
        p=Profile.model_validate(dict(seed=42,duration_s=a.rounds*300,groups=[dict(count=a.n,mobility=mobility,timing={'kind':'periodic','options':{'interval_s':300,'start':{'mode':'uniform','min_s':0,'max_s':300}}})]))
        before=time.perf_counter();summary=Simulation(p,root).run();wall=time.perf_counter()-before
        (root/'measurement.json').write_text(json.dumps(dict(wall_s=wall,counts=summary['counts'],max_rss_native_units=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),indent=2))
        return
    import pyarrow.parquet as pq
    from lorasim.tracking import SCHEMAS
    root.mkdir(parents=True,exist_ok=True)
    report=dict(platform=platform.platform(),python=sys.version,ed_count=a.n,ul_per_ed=a.rounds,mobility=a.mobility,runs=[],exact_table_equality=True)
    versions={'v04':Path(a.baseline_source).resolve(),'v05':Path(__file__).resolve().parents[1]}
    for repetition in range(a.repeat):
        for name,source in versions.items():
            target=root/f'{name}-{repetition}'
            command=[sys.executable,str(Path(__file__).resolve()),'--child','--baseline-source',str(source),'--output',str(target),'--n',str(a.n),'--rounds',str(a.rounds),'--mobility',a.mobility]
            subprocess.run(command,env={**os.environ,'PYTHONPATH':str(source)},check=True,cwd=source)
            row=dict(version=name,repetition=repetition,**json.loads((target/'measurement.json').read_text()))
            report['runs'].append(row);print(json.dumps(row),flush=True)
            (root/'benchmark.json').write_text(json.dumps(report,indent=2))
        left,right=root/f'v04-{repetition}',root/f'v05-{repetition}'
        for table in SCHEMAS:
            x=sorted((left/table).glob('*.parquet'));y=sorted((right/table).glob('*.parquet'))
            assert len(x)==len(y),(table,'parts')
            for p,q in zip(x,y):assert pq.read_table(p).equals(pq.read_table(q)),(table,p,q)
        assert report['runs'][-2]['counts']==report['runs'][-1]['counts']
        (root/'benchmark.json').write_text(json.dumps(report,indent=2))
    print('All tracking tables and event counts exactly equal.',flush=True)


if __name__=='__main__':main()
