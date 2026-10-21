"""Reproducible TVT experiment matrix. python -m lorasim.experiments --help."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from .config import Profile, dump_profile, load_profile
from .tracking import atomic_json

BASIC = ('sema','dqn','epsilon','ucb1','lora_mab_sf_ch','linucb_sf_ch','random_sf_ch')
CONTROLS = ('sema','dqn','dqn_mobility','dqn_state_split')
SCENARIOS = {'reference':(3000,18000), 'stationary_dominant':(18000,36000),
             'moving_dominant':(0,600), 'mixed':None}


def policy(method,width):
    if method in ('epsilon','ucb1'):
        return dict(kind='sf_reward_bandit',options={'algorithm':method})
    return dict(kind=method,options={'hidden_sizes':[width,width]} if method in CONTROLS else {})


def make_profile(method,n,seed,scenario='reference',width=64,messages=40000,smoke=False):
    """All methods have identical topology, mobility/traffic RNG streams and MAC."""
    groups=[]
    partitions = [('mixed_stationary',n//2,SCENARIOS['stationary_dominant']),
                  ('mixed_moving',n-n//2,SCENARIOS['moving_dominant'])] if scenario=='mixed' else [(scenario,n,SCENARIOS[scenario])]
    for name,count,pause in partitions:
        if not count:
            continue
        groups.append(dict(name=name,count=count,payload_bytes=40,confirmed=True,max_attempts=1,
            observe_position=True,policy=policy(method,width),
            timing=dict(kind='periodic',options=dict(interval_s=60,max_messages=messages,
                start=dict(mode='uniform',min_s=0,max_s=60))),
            mobility=dict(kind='waypoints',options=dict(mode='random_waypoint',waypoint_count=400,
                initial_pause=True,speed_min_m_s=1,speed_max_m_s=5,
                pause_min_s=pause[0],pause_max_s=pause[1]))))
    return Profile.model_validate(dict(name=f'{"smoke" if smoke else "tvt"}-{scenario}-{method}-w{width}-n{n}-s{seed}',
        seed=seed,duration_s=60*messages+30,area_m=[24000,24000],
        gateways=[dict(id=f'gw{i}',position=[x,y,15],demodulators=8)
                  for i,(x,y) in enumerate(((6000,6000),(6000,18000),(18000,6000),(18000,18000)))],
        mac=dict(duty_cycle=False),execution=dict(record_policy_timing=True),groups=groups))


def plan(root,suite,seeds,smoke=False):
    root=Path(root)
    if root.exists() and any(root.iterdir()):
        raise ValueError('plan directory must be empty; plans are immutable')
    if not seeds or len(set(seeds))!=len(seeds):
        raise ValueError('provide distinct seeds')
    cases=set()
    if suite in ('basic','all'):
        cases.update((m,n,'reference',64) for m in BASIC for n in (50,100,150,200))
    if suite in ('additional','all'):
        cases.update((m,n,s,64) for m in CONTROLS for n in (100,150) for s in SCENARIOS)
        cases.update((m,n,'reference',w) for m in CONTROLS for n in (100,150) for w in (16,32,64))
    if smoke:
        cases={(m,4,s,w) for m,_,s,w in cases}
    root.mkdir(parents=True,exist_ok=True)
    (root/'profiles').mkdir()
    runs=[]
    for method,n,scenario,width in sorted(cases):
        for seed in seeds:
            p=make_profile(method,n,seed,scenario,width,messages=12 if smoke else 40000,smoke=smoke)
            text=dump_profile(p)
            rel=f'profiles/{p.name}.yaml'
            (root/rel).write_text(text)
            runs.append(dict(id=p.name,profile=rel,sha256=hashlib.sha256(text.encode()).hexdigest(),
                method=method,n=n,scenario=scenario,width=width,seed=seed))
    atomic_json(root/'plan.json',dict(version=1,smoke=smoke,suite=suite,seeds=seeds,
        analysis=dict(tail=10 if smoke else 2000,threshold=.7,transition_burnin=0 if smoke else 2000,horizons=[10,50,100]),
        runs=runs))
    return runs


def read_plan(root):
    root=Path(root)
    data=json.loads((root/'plan.json').read_text())
    for row in data['runs']:
        if hashlib.sha256((root/row['profile']).read_bytes()).hexdigest()!=row['sha256']:
            raise ValueError(f'profile changed after planning: {row["id"]}; create a new plan')
    return data


def run_plan(root,only='',limit=None,shards=1,shard_index=0):
    from .engine import Simulation
    if shards<1 or not 0<=shard_index<shards or (limit is not None and limit<1):
        raise ValueError('invalid shard or limit')
    root=Path(root)
    data=read_plan(root)
    selected=[r for i,r in enumerate(data['runs']) if i%shards==shard_index and only in r['id']]
    if not selected:
        raise ValueError('no matching runs')
    done=0
    for row in selected:
        out=root/'runs'/row['id']
        if out.exists():
            result=out/'experiment-result.json'
            if not result.exists():
                raise ValueError(f'incomplete run {out}; move it aside before retrying')
            old=json.loads(result.read_text())
            if old['profile_sha256']!=row['sha256']:
                raise ValueError(f'profile mismatch: {out}')
            manifest=json.loads((out/'manifest.json').read_text())
            current={str(p.relative_to(Path(__file__).parent)):hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in Path(__file__).parent.rglob('*.py')}
            if manifest['source_sha256']!=current:
                raise ValueError('source changed; use a new plan directory to avoid mixing implementations')
            continue
        print(f'Running {row["id"]}',flush=True)
        summary=Simulation(load_profile(root/row['profile']),out).run()
        if summary['status']!='completed':
            raise RuntimeError(f'run did not complete: {out}')
        atomic_json(out/'experiment-result.json',dict(profile_sha256=row['sha256'],
            environment={k:os.getenv(k) for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS')}))
        done+=1
        if limit and done>=limit:
            break


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('plan');p.add_argument('--output',required=True)
    p.add_argument('--suite',choices=['basic','additional','all'],default='all')
    p.add_argument('--seeds',nargs='+',type=int,default=[0,1,2,3,4]);p.add_argument('--smoke',action='store_true')
    p=sub.add_parser('run');p.add_argument('directory');p.add_argument('--only',default='');p.add_argument('--limit',type=int)
    p.add_argument('--shards',type=int,default=1);p.add_argument('--shard-index',type=int,default=0)
    p=sub.add_parser('analyze');p.add_argument('directory');p.add_argument('--allow-partial',action='store_true')
    args=parser.parse_args()
    if args.command=='plan':
        rows=plan(args.output,args.suite,args.seeds,args.smoke);print(f'{len(rows)} runs planned')
    elif args.command=='run':
        run_plan(args.directory,args.only,args.limit,args.shards,args.shard_index)
    else:
        from .experiment_analysis import analyze_plan
        analyze_plan(Path(args.directory),args.allow_partial)


if __name__=='__main__':
    main()
