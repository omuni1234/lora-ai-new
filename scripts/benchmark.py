"""Repeatable serial measurement. Compare with validation/ns3-benchmark.cc.

Python writes all tracking tables; ns-3 reference counts PHY events only.
Both exclude configuration/setup time from the reported simulation wall time.
"""
import argparse
import json
from pathlib import Path
import platform
import resource
import time
from lorasim.config import Profile
from lorasim.engine import Simulation

p=argparse.ArgumentParser();p.add_argument('--n',type=int,default=1000);p.add_argument('--duration',type=float,default=3600);p.add_argument('--output',required=True);p.add_argument('--backend',default='numpy',choices=['numpy','scalar']);args=p.parse_args()
side=__import__('math').ceil(args.n**.5)
groups=[]
for i in range(args.n):
    groups.append({'name':f'ed{i}','count':1,'placement':'explicit','positions':[[(i%side+.5)*2000/side,(i//side+.5)*2000/side,1.5]],
                   'confirmed':False,'timing':{'kind':'periodic','options':{'interval_s':300,'first_s':i*.1}}})
profile=Profile.model_validate({'name':'deterministic-benchmark','duration_s':args.duration,'groups':groups,'channel_backend':args.backend})
sim=Simulation(profile,Path(args.output)/'run')
result=sim.run()
report={'platform':platform.platform(),'backend':args.backend,'n':args.n,'duration_s':args.duration,'wall_s':result['wall_s'],
        'sent':result['counts'].get('uplinks_sent',0),'received':result['counts'].get('uplinks_received_any_gw',0),
        'maxrss_native_units':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'summary':result}
Path(args.output,'benchmark.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
