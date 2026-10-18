"""Queue immediately runnable comparison Experiments. No plotting needed on workers.
python scripts/seed_comparison.py --data data --counts 20 40 60 --seeds 1 2
lorasim worker --data data --workers 2 --once
"""
import argparse
from lorasim.config import Profile, dump_profile
from lorasim.jobs import Store
p=argparse.ArgumentParser();p.add_argument('--data',default='data');p.add_argument('--counts',nargs='+',type=int,default=[20,40,60]);p.add_argument('--seeds',nargs='+',type=int,default=[1]);a=p.parse_args()
store=Store(a.data)
for kind,options in [('fixed',{'sf':9,'tp_dbm':14}),('adr',{'history_range':5}),('badr',{'random_phase':True})]:
    eid=store.create_experiment(kind.upper(),'Quick comparison: duty cycle disabled to expose contention; not a regulatory workload.')
    for n in a.counts:
        for seed in a.seeds:
            profile=Profile.model_validate({'name':f'{kind}-{n}-seed{seed}','seed':seed,'duration_s':600,
                'area_m':[3000,3000], 'gateways':[{'id':'gw-central','position':[1500,1500,15]}],
                'mac':{'duty_cycle':False},'energy':{'radio':{'tx_current_model':'linear'}},
                'groups':[{'name':kind,'count':n,'confirmed':False,'policy':{'kind':kind,'options':options},
                           'timing':{'kind':'periodic','options':{'interval_s':20,'random_phase':True}},
                           'mobility':{'kind':'random_walk','options':{'speed_m_s':1.5}}}]})
            print(store.submit(dump_profile(profile),eid))
