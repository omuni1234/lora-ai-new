"""Synthetic cumulative-analysis benchmark (not a simulation throughput claim)."""
import argparse
import json
from pathlib import Path
import platform
import resource
import time
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from lorasim.analysis import time_series


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    parser.add_argument('--devices',type=int,default=500)
    parser.add_argument('--rounds',type=int,default=2000)
    parser.add_argument('--bins',type=int,default=100)
    args=parser.parse_args()
    root=Path(args.output);(root/'uplinks').mkdir(parents=True,exist_ok=False)
    total=args.devices*args.rounds
    for part,start in enumerate(range(0,total,100000)):
        i=np.arange(start,min(total,start+100000),dtype=np.int64)
        table=pa.table(dict(device_id=i%args.devices,tx_index=i//args.devices+1,
                            outcome=np.where((i//args.devices)%4==0,'Interference','Received')))
        pq.write_table(table,root/'uplinks'/f'part-{part:06}.parquet',compression='zstd')
    before=time.perf_counter()
    points,width=time_series(root,args.devices,1,args.rounds,args.bins)
    elapsed=time.perf_counter()-before
    expected=1-(args.rounds+3)//4/args.rounds
    assert abs(points[-1]['fractions']['Received']-expected)<1e-12
    result=dict(rows=total,devices=args.devices,rounds=args.rounds,points=len(points),width=width,
                elapsed_s=elapsed,rows_per_s=total/elapsed,platform=platform.platform(),python=platform.python_version(),
                process_max_rss_native_units=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                note='Synthetic Parquet; elapsed excludes fixture creation; RSS includes fixture generation, macOS bytes/Linux KiB.')
    (root/'benchmark.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if __name__=='__main__':main()
