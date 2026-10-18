"""Compare CSV emitted by the actual ns-3 binary against the Python primitives."""
import argparse
import csv
import json
import numpy as np
from lorasim.config import Radio, Channel
from lorasim.phy import airtime_ns, links, destroyed_by


def compare(path):
    counts = {"AIR":0,"PATH":0,"COL":0,"GW":0}
    gw = {}
    for row in csv.reader(open(path)):
        if not row or row[0] not in counts:
            continue
        kind = row[0]
        if kind=="AIR":
            size,sf,cr,expected = map(int,row[1:])
            assert airtime_ns(size,Radio(sf=sf,cr=cr))==expected,row
        elif kind=="PATH":
            distance,expected_power,expected_delay=map(float,row[1:])
            power,_,_,delay=links(np.zeros(3),np.array([[distance,0,0]]),14,Channel())
            assert abs(power[0]-expected_power)<1e-10,row
            assert delay[0]==expected_delay,row
        elif kind=="COL":
            sf,other=int(row[1]),int(row[2]); delta,start=float(row[3]),float(row[4]); expected=int(row[5])
            e=[0.]*6;e[other-7]=max(0,1-start)*10**((-100+delta-30)/10)
            assert destroyed_by(sf,1e-13,e)==expected,row
        else:
            gw.setdefault(row[1],[]).append(row[2:])
        counts[kind]+=1
    assert counts['AIR']==144 and counts['PATH']==7 and counts['COL']==864,counts
    expected = {
        "sensitivity":[["1","UnderSensitivity","0"],["2","Received","1000000000"]],
        "capacity":[["3","NoMoreReceivers","0"],["1","Received","1000000000"],["2","Received","1000000000"]],
        "interference":[["1","Interference","1000000000"],["2","Interference","1500000000"]],
        "tx_interrupt":[["1","LostBecauseTx","500000000"]]
    }
    assert gw==expected,(gw,expected)
    return {"passed":True,"counts":counts,"note":"PHY primitives and gateway reference fixtures only; full MAC trajectory equivalence is not asserted"}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('csv');p.add_argument('--output');args=p.parse_args()
    result=compare(args.csv);text=json.dumps(result,indent=2);print(text)
    if args.output:open(args.output,'w').write(text+'\n')
