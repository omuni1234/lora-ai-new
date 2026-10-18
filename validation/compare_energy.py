"""Compare actual compiled ns-3 LoraRadioEnergyModel output against Python."""
import argparse
import csv
import json
import tempfile
from pathlib import Path
from lorasim.energy import RadioMeter
from lorasim.config import RadioEnergy
from lorasim.tracking import Tracker
p=argparse.ArgumentParser();p.add_argument('csv');p.add_argument('--output');a=p.parse_args()
checks=[]
with tempfile.TemporaryDirectory() as tmp:
    for kind,key,expected in csv.reader(Path(a.csv).open()):
        expected=float(expected)
        if kind=='CURRENT':
            meter=RadioMeter(RadioEnergy(tx_current_model='linear'));tracker=Tracker(Path(tmp)/('current'+key))
            meter.transition(0,'TX',1,tracker,0,float(key));actual=meter.current();tracker.close()
        else:
            meter=RadioMeter(RadioEnergy(tx_current_model='linear' if int(key) else 'constant'));tracker=Tracker(Path(tmp)/('energy'+key))
            for t,state in [(1,'TX'),(3,'SLEEP'),(4,'RX_ENABLED'),(5,'RX_ACTIVE'),(6,'STANDBY'),(7,'STANDBY')]:
                meter.transition(int(t*1e9),state,1,tracker,0,14 if state=='TX' else None)
            actual=meter.total;tracker.close()
        checks.append({'kind':kind,'case':key,'ns3':expected,'python':actual,'absolute_error':abs(expected-actual)})
result={'cases':len(checks),'passed':all(x['absolute_error']<1e-12 for x in checks),'checks':checks}
if a.output:Path(a.output).write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'cases':result['cases'],'passed':result['passed'],'maximum_absolute_error':max(x['absolute_error'] for x in checks)}))
if not result['passed']:raise SystemExit(1)
