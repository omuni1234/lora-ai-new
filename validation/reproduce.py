"""Clone pinned upstreams and run the compiled reference oracle. Requires git/C++/CMake.
python validation/reproduce.py --work /tmp/lorasim-reference
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
p=argparse.ArgumentParser();p.add_argument('--work',required=True);p.add_argument('--jobs',default='4');a=p.parse_args()
root=Path(a.work).resolve();root.mkdir(parents=True,exist_ok=True)
project=Path(__file__).resolve().parents[1]
lock=json.loads((project/'lorasim/upstream.json').read_text())
def run(args,**kw):return subprocess.run(args,check=True,**kw)
for name,target in [('ns3',root/'ns3'),('lorawan',root/'ns3/contrib/lorawan')]:
    if not target.exists():
        run(['git','clone','--filter=blob:none','--no-checkout',lock[name]['url'],str(target)])
        run(['git','checkout',lock[name]['commit']],cwd=target)
    actual=subprocess.check_output(['git','rev-parse','HEAD'],cwd=target,text=True).strip()
    if actual!=lock[name]['commit']:raise RuntimeError(f'{target}: wrong revision; use an empty work directory')
# Build-only fix: this upstream .cc uses NS_LOG_* without directly including log.h.
f=root/'ns3/contrib/lorawan/model/logical-lora-channel.cc'
s=f.read_text()
if '#include "ns3/log.h"' not in s:
    f.write_text(s.replace('#include "logical-lora-channel.h"','#include "logical-lora-channel.h"\n#include "ns3/log.h"'))
for name in ['ns3-oracle.cc','ns3-benchmark.cc','ns3-energy-oracle.cc']:
    shutil.copy(project/'validation'/name,root/'ns3/scratch'/name.replace('ns3-','lorasim-'))
run(['./ns3','configure','--enable-tests','--enable-modules','lorawan','--disable-examples','--build-profile','release'],cwd=root/'ns3')
run(['./ns3','build','-j',a.jobs],cwd=root/'ns3')
with (root/'oracle.csv').open('w') as out:
    run(['./ns3','run','--no-build','lorasim-oracle'],cwd=root/'ns3',stdout=out)
run([sys.executable,str(project/'validation/compare_oracle.py'),str(root/'oracle.csv'),'--output',str(root/'oracle-result.json')])
run(['./test.py','--suite=lorawan','--no-build'],cwd=root/'ns3')

with (root/'energy-oracle.csv').open('w') as out:
    run(['./ns3','run','--no-build','lorasim-energy-oracle'],cwd=root/'ns3',stdout=out)
run([sys.executable,str(project/'validation/compare_energy.py'),str(root/'energy-oracle.csv'),'--output',str(root/'energy-result.json')])
