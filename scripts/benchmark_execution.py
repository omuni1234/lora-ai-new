"""Compare complete, identically tracked runs; single simulations, no seed sweeps.

Run from the repo after pip install -e .:
  python scripts/benchmark_execution.py --output runs/speed --n 500 --rounds 40
20,000 transmissions PER ED: --rounds 20000 (10 million ULs for 500 EDs).
"""
import argparse
import json
from pathlib import Path
import platform
import resource
import sys
import time
import pyarrow.parquet as pq
from lorasim.config import Profile
from lorasim.engine import Simulation
from lorasim.tracking import SCHEMAS


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    parser.add_argument('--n',type=int,default=500)
    parser.add_argument('--rounds',type=int,default=40)
    parser.add_argument('--mobility',choices=['stationary','random_walk'],default='stationary')
    parser.add_argument('--modes',nargs='+',choices=['reference','optimized','multicore'],default=['reference','optimized','multicore'])
    parser.add_argument('--writer-processes',type=int,default=2)
    parser.add_argument('--batch-rows',type=int,default=4096)
    parser.add_argument('--repeat',type=int,default=1)
    args=parser.parse_args()
    root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    p=Profile.model_validate(dict(name='execution-benchmark',seed=42,duration_s=args.rounds*300,tracking={'batch_rows':args.batch_rows},groups=[dict(count=args.n,confirmed=True,mobility={'kind':args.mobility},timing={'kind':'periodic','options':{'interval_s':300,'start':{'mode':'uniform','min_s':0,'max_s':300}}})]))
    report=dict(platform=platform.platform(),python=sys.version,workload={'ed_count':args.n,'rounds_per_ed':args.rounds,'expected_total_ul':args.n*args.rounds,'mobility':args.mobility,'batch_rows':args.batch_rows},runs=[])
    for repetition in range(args.repeat):
        for mode in args.modes:
            p.execution.optimized=mode!='reference'
            p.execution.writer_processes=args.writer_processes if mode=='multicore' else 0
            out=root/f'{mode}-{repetition}'
            before=resource.getrusage(resource.RUSAGE_SELF);child_before=resource.getrusage(resource.RUSAGE_CHILDREN)
            start=time.perf_counter();s=Simulation(p,out).run();wall=time.perf_counter()-start
            after=resource.getrusage(resource.RUSAGE_SELF);child_after=resource.getrusage(resource.RUSAGE_CHILDREN)
            row=dict(mode=mode,repetition=repetition,wall_including_setup_s=wall,wall_simulation_s=s['wall_s'],self_cpu_s=after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,children_cpu_s=max(0.,child_after.ru_utime+child_after.ru_stime-child_before.ru_utime-child_before.ru_stime),counts=s['counts'],output=str(out))
            report['runs'].append(row)
            (root/'benchmark.json').write_text(json.dumps(report,indent=2))
            print(json.dumps(row),flush=True)
    # Stream equality one file pair at a time. Identical batch size is intentional.
    baseline=root/f'{args.modes[0]}-0'
    report['exact_table_equality']=True
    for row in report['runs'][1:]:
        for table in SCHEMAS:
            files=sorted((baseline/table).glob('*.parquet'));other=sorted((Path(row['output'])/table).glob('*.parquet'))
            assert len(files)==len(other),(table,'part count')
            for x,y in zip(files,other):
                assert pq.read_table(x).equals(pq.read_table(y)),(table,x,y)
    (root/'benchmark.json').write_text(json.dumps(report,indent=2))
    print('All tracking tables exactly equal.',flush=True)


if __name__=='__main__':
    main()
