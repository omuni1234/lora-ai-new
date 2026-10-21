"""Regenerate checked-in Experiment YAMLs; no simulation or physics changes."""
from pathlib import Path
import yaml

BASIC=('sema','dqn','epsilon','ucb1','thesis_lora_mab','random_sf_ch','lora_mab_sf_ch','linucb_sf_ch')
CONTROLS=('sema','dqn','dqn_mobility','dqn_state_split')
SCENARIOS={'reference':(3000,18000),'long_pause':(18000,36000),'short_pause':(0,600),'mixed':None}
DQN=dict(hidden_sizes=[64,64],replay_capacity=10000,batch_size=128,discount=.5,learning_rate=.01,
         epsilon_start=1.,epsilon_end=.01,epsilon_decay=1000.,target_update=16,train_every=1,
         initial_rssi_dbm=-120.,rssi_offset_dbm=0.,rssi_scale_db=1.)


def experiment(method,n,scenario='reference',width=64,smoke=False):
    timing=dict(kind='finite_periodic',options=dict(interval_s=60,max_messages=16 if smoke else 40000,
        start=dict(mode='uniform',min_s=0,max_s=60)))
    mobility=dict(kind='asset_waypoint',options=dict(waypoint_count=400,waypoint_seed=0,waypoint_height_m=1.5,
        speed_min_m_s=1,speed_max_m_s=5,pause_min_s=3000,pause_max_s=18000))
    if method in ('epsilon','ucb1'):
        policy=dict(kind='sf_reward_bandit',options=dict(algorithm=method,confidence=2.,decay=20.,epsilon_end=.1))
    elif method in CONTROLS:
        policy=dict(kind=method,options={**DQN,'hidden_sizes':[width,width]})
    else:
        policy=dict(kind=method,options={'horizon':40000} if method=='thesis_lora_mab' else {})
    partitions=[('long_pause',n//2,SCENARIOS['long_pause']),('short_pause',n-n//2,SCENARIOS['short_pause'])] if scenario=='mixed' else [(scenario,n,SCENARIOS[scenario])]
    groups=[dict(name=label,count=count,placement='uniform',payload_bytes=40,confirmed=True,max_attempts=1,
                 observe_position=True,policy=dict(ref='algorithm'),timing=dict(ref='traffic'),
                 mobility=dict(ref='assets',options=dict(pause_min_s=pause[0],pause_max_s=pause[1])))
            for label,count,pause in partitions if count]
    return dict(name=f'TVT-{method}-{scenario}-N{n}-W{width}'+('-SMOKE' if smoke else ''),seed=0,
        duration_s=(16 if smoke else 40000)*60+30,area_m=[24000,24000],
        channel=dict(exponent=3.76,reference_distance_m=1,reference_loss_db=7.7,collision_matrix='goursaud'),
        mac=dict(duty_cycle=False),
        action_space=dict(spreading_factors=list(range(7,13)),frequencies_hz=[868100000,868300000,868500000],
                          coding_rates=[1],powers_dbm=[14]),
        gateways=[dict(id=f'gw{i}',position=[x,y,15],demodulators=8,frequencies_hz=[868100000,868300000,868500000])
                  for i,(x,y) in enumerate(((6000,6000),(18000,6000),(6000,18000),(18000,18000)))],
        models=dict(policy=dict(algorithm=policy),timing=dict(traffic=timing),mobility=dict(assets=mobility)),
        groups=groups,tracking=dict(policy_diagnostics=True))


def main():
    root=Path(__file__).resolve().parents[1]/'examples/tvt'
    cases={(m,n,'reference',64) for m in BASIC for n in (50,100,150,200)}
    cases.update((m,n,s,64) for m in CONTROLS for n in (100,150) for s in SCENARIOS)
    cases.update((m,n,'reference',w) for m in CONTROLS for n in (100,150) for w in (16,32))
    root.mkdir(exist_ok=True)
    for m,n,s,w in sorted(cases):
        header='# TVT Experiment: one independent network per method; change seed for repetitions.\n# Final-window analysis: tx_start=38001, tx_end=40000. PHY/MAC defaults remain the repository definitions.\n'
        (root/f'{m}-{s}-n{n}-w{w}.yaml').write_text(header+yaml.safe_dump(experiment(m,n,s,w),sort_keys=False,allow_unicode=True))
    (root/'00-smoke-sema.yaml').write_text('# Functional check only; not a scientific result.\n'+yaml.safe_dump(experiment('sema',4,smoke=True),sort_keys=False))
    print(f'{len(cases)} full experiments + 1 smoke example')


if __name__=='__main__':main()
