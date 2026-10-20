"""Bounded generation-only sampling of each ED's spatial experience opportunities.

No selector, radio, MAC, ACK or training is executed. State classification reads
the mobility model's instantaneous state, never displacement between packets.
"""
from collections import Counter
import hashlib
import math
import time
from typing import Literal

import numpy as np
from pydantic import Field
from .config import Model
from .components import make
from .interfaces import Observation
from .mobility_environment import MobilityEnvironment, MobilityPreviewLimit
from .registry import constructor


class ExperienceOptions(Model):
    start_s: float = Field(0, ge=0, le=1e9)
    end_s: float | None = Field(None, gt=0, le=1e9)
    device_samples: int = Field(32, ge=1, le=128)
    device_id: int | None = Field(None, ge=0)
    max_generations: int = Field(5000, ge=1, le=20000)
    time_samples: int = Field(1024, ge=64, le=4096)
    grid_size: int = Field(16, ge=4, le=32)
    site_resolution_m: float = Field(1., gt=0, le=100000)
    concentration_threshold: float = Field(.3, gt=0, le=1)
    concentration_basis: Literal['stationary_site','spatial_cell'] = 'stationary_site'
    minimum_experiences: int = Field(30, ge=1, le=20000)
    seed: int = Field(73, ge=0, le=2**32-1)


class ExperienceError(ValueError):
    pass


STATES = ('stationary','moving','unknown')


def selected_devices(p, o):
    """Stratify by group; reproduce initial placement without constructing all EDs."""
    total=sum(g.count for g in p.groups)
    if o.device_id is not None and o.device_id >= total:
        raise ExperienceError(f'ED番号は0〜{total-1}で指定してください。')
    quota=[1]*len(p.groups)
    if o.device_id is None:
        if len(quota)>o.device_samples:
            raise ExperienceError('EDグループ数より標本ED数が少ないため確認できません。標本数を増やすかED番号を指定してください。')
        for _ in range(min(total,o.device_samples)-len(quota)):
            k=max((j for j,g in enumerate(p.groups) if quota[j]<g.count),key=lambda j:p.groups[j].count/quota[j])
            quota[k]+=1
    offset=0
    rng=np.random.default_rng(o.seed)
    for k,g in enumerate(p.groups):
        ids=[o.device_id-offset] if o.device_id is not None and offset<=o.device_id<offset+g.count else []
        if o.device_id is None:ids=sorted(rng.choice(g.count,quota[k],replace=False).tolist())
        for j in ids:
            i=offset+j
            if g.placement=='explicit':initial=g.positions[j]
            elif g.placement=='grid':
                side=math.ceil(math.sqrt(g.count))
                initial=((j%side+.5)*p.area_m[0]/side,(j//side+.5)*p.area_m[1]/side,1.5)
            else:
                placement=np.random.default_rng(np.random.SeedSequence([p.seed,offset,900]))
                placement.bit_generator.advance(2*j)
                initial=(*placement.uniform([0,0],p.area_m),1.5)
            specs={key:getattr(g,key) for key in ('mobility','timing')}
            for a in p.assignments:
                if (not a.groups or g.name in a.groups) and (not a.ids or i in a.ids) and (not a.tags or set(a.tags)<=set(g.tags)):
                    specs.update({key:getattr(a,key) for key in specs if getattr(a,key) is not None})
            yield i,g,initial,{key:p.component(spec,key) for key,spec in specs.items()},g.count/len(ids) if o.device_id is None else 1.
        offset+=g.count


def experience(profile, options=None):
    o=options or ExperienceOptions()
    end=min(profile.duration_s,86400.) if o.end_s is None else min(o.end_s,profile.duration_s)
    if o.start_s>=end:
        raise ExperienceError('確認開始時刻は終了時刻・実験時間より前にしてください。')
    started=time.perf_counter()
    environment=MobilityEnvironment(profile.seed,max_transitions=100000)
    rows=[];warnings=set();cost=0.;generation_budget=200000
    for i,g,initial,specs,weight in selected_devices(profile,o):
        instances={}
        for key,stream in (('mobility',2),('timing',1)):
            cls=constructor(key,specs[key].kind)
            if not getattr(cls,'preview_safe',False):
                raise ExperienceError(f'ED {i} の{key}はpreview_safe未宣言です。副作用のないプレビューに対応させるか別のモデルを選んでください。')
            if key=='mobility' and hasattr(cls,'preview_cost'):
                estimate=float(cls.preview_cost(specs[key].options,end))
                if not math.isfinite(estimate) or estimate<0:
                    raise ExperienceError(f'ED {i} の移動モデルのpreview_costが不正です。')
                cost+=estimate
                if cost>200000:
                    raise ExperienceError('移動処理が事前確認の計算上限を超えます。終了時刻を短くするか標本ED数を減らしてください。')
            context={'rng':np.random.default_rng(np.random.SeedSequence([profile.seed,i,stream]))}
            if key=='mobility':context.update(initial=initial,area=profile.area_m,environment=environment)
            try:instances[key]=make(specs[key],key,**context)
            except MobilityPreviewLimit:
                raise ExperienceError('移動先の遷移数が事前確認の上限に達しました。終了時刻・標本ED数を減らしてください。') from None
        mobility,app=instances['mobility'],instances['timing']
        # A custom subclass must explicitly declare its own state classification.
        state_fn=mobility.motion_state if 'motion_state' in type(mobility).__dict__ else lambda t:'unknown'
        queries=0
        def position(t):
            nonlocal queries
            queries+=1
            if queries%64==0 and time.perf_counter()-started>12:
                raise ExperienceError('事前確認の処理時間上限に達しました。確認時間・標本ED数を減らしてください。')
            try:xyz=np.asarray(mobility.position(float(t)),dtype=float)
            except MobilityPreviewLimit:
                raise ExperienceError('移動先の遷移数が事前確認の上限に達しました。終了時刻・標本ED数を減らしてください。') from None
            if xyz.shape!=(3,) or not np.isfinite(xyz).all():
                raise ExperienceError(f'ED {i} の位置が不正です。移動モデルは有限の3次元座標を返してください。')
            return xyz
        def cell(xyz):
            x,y=xyz[:2]
            if not (0<=x<=profile.area_m[0] and 0<=y<=profile.area_m[1]):return o.grid_size**2
            cx=min(o.grid_size-1,int(x/profile.area_m[0]*o.grid_size))
            cy=min(o.grid_size-1,int(y/profile.area_m[1]*o.grid_size))
            return cy*o.grid_size+cx
        tx=np.zeros(o.grid_size**2+1,dtype=int);residence=tx.copy()
        state_counts=Counter();time_states=Counter();sites={}
        # Stratification avoids a fixed reference clock aliasing with motion.
        rng=np.random.default_rng(np.random.SeedSequence([o.seed,i,941]))
        times=o.start_s+(np.arange(o.time_samples)+rng.random(o.time_samples))*(end-o.start_s)/o.time_samples
        baseline=0;generated=0;used=0;previous=None;longest=0;streak=0;last_site=None
        bins=np.zeros((40,3),dtype=int)
        first_times=[]
        xyz=position(0.)
        obs=Observation(0.,i,0,None,None,None,None,tuple(xyz) if g.observe_position else None)
        while True:
            t=app.next_time(obs,previous)
            if t is not None:
                t=float(t)
                if not math.isfinite(t) or t<obs.time_s or (previous is not None and t<=previous):
                    raise ExperienceError(f'ED {i} の送信予定時刻が不正です。next_timeは増加する有限時刻かNoneを返してください。')
            stop=t is None or t>=end
            until=end if stop else t
            while baseline<len(times) and times[baseline]<=until:
                xyz=position(times[baseline]);state=state_fn(float(times[baseline]))
                if state not in STATES:raise ExperienceError(f'ED {i} のmotion_stateはstationary/moving/unknownを返してください。')
                residence[cell(xyz)]+=1;time_states[state]+=1;baseline+=1
            if stop:break
            if generated>=o.max_generations or generation_budget<=0:
                raise ExperienceError('送信生成回数が事前確認の上限に達しました。終了時刻を短くするか、1台あたりの上限を増やしてください（全体上限20万回）。開始時刻より前の生成も上限に含みます。')
            generation_budget-=1;generated+=1
            xyz=position(t);state=state_fn(t)
            if state not in STATES:raise ExperienceError(f'ED {i} のmotion_stateはstationary/moving/unknownを返してください。')
            if t>=o.start_s:
                used+=1;tx[cell(xyz)]+=1;state_counts[state]+=1
                bins[min(39,int((t-o.start_s)/(end-o.start_s)*40)),STATES.index(state)]+=1
                if len(first_times)<8:first_times.append(t)
                site=None
                if state=='stationary':
                    site=tuple(math.floor(float(v)/o.site_resolution_m) for v in xyz)
                    if site not in sites:sites[site]={'xyz':xyz.tolist(),'count':0}
                    sites[site]['count']+=1
                streak=streak+1 if site is not None and site==last_site else int(site is not None)
                longest=max(longest,streak);last_site=site
            previous=t
            obs=Observation(t,i,generated,None,None,None,None,tuple(xyz) if g.observe_position else None)
            if generated%64==0 and time.perf_counter()-started>12:
                raise ExperienceError('事前確認の処理時間上限に達しました。確認時間・標本ED数・生成回数上限を減らしてください。')
        n_static=state_counts['stationary'];ordered=sorted(sites.values(),key=lambda x:-x['count'])
        q=residence/o.time_samples;p=tx/used if used else None
        total_variation=float(.5*np.abs(p-q).sum()) if used else None
        row={'id':i,'group':g.name,'weight':weight,'generations':used,'generated_from_start':generated,
             'states':{s:state_counts[s] for s in STATES},'time_state_fractions':{s:time_states[s]/o.time_samples for s in STATES},
             'stationary_site_count':len(sites),'dominant_site_fraction':ordered[0]['count']/used if ordered else (0. if used else None),
             'dominant_stationary_fraction':ordered[0]['count']/n_static if ordered else None,
             'effective_stationary_sites':n_static*n_static/sum(x['count']**2 for x in ordered) if n_static else None,
             'longest_same_site_streak':longest,'spatial_tv':total_variation,
             'dominant_cell_fraction':float(tx[:-1].max()/used) if used else None,
             'effective_spatial_cells':float(used*used/np.square(tx.astype(float)).sum()) if used and not tx[-1] else None,
             'stationary_sites':[dict(x,all_fraction=x['count']/used,stationary_fraction=x['count']/n_static) for x in ordered[:20]],
             'site_table_omitted':max(0,len(sites)-20),'tx_grid':tx.tolist(),'residence_grid':residence.tolist(),
             'timeline':bins.tolist(),'first_generation_times':first_times}
        metric=row['dominant_site_fraction'] if o.concentration_basis=='stationary_site' else row['dominant_cell_fraction']
        row['concentration_state']='insufficient' if used<o.minimum_experiences or (state_counts['unknown'] and o.concentration_basis=='stationary_site') or (tx[-1] and o.concentration_basis=='spatial_cell') else (
            'concentrated' if metric>=o.concentration_threshold else 'below_criterion')
        rows.append(row)
        if not used:warnings.add('確認区間内に送信生成がないEDがあります。開始時刻・間隔・確認区間を見直してください。集計平均からは除外します。')
        elif used<30:warnings.add('送信生成が30回未満のEDがあります。少数の経験だけでは地点割合が大きく変動します。区間を延ばして確認してください。')
        if state_counts['unknown'] or time_states['unknown']:
            warnings.add('移動状態が不明なモデルがあります。不明を静止・移動に置き換えず、地点別の静止経験指標から除外しています。独自モデルはmotion_stateを実装してください。')
        if tx[-1] or residence[-1]:warnings.add('エリア外の位置があり、地図外として別集計しています。地図外の送信があるEDは全送信メッシュの基準判定を保留します。移動先とエリア設定を確認してください。')
    active=[r for r in rows if r['generations']]
    weight_sum=sum(r['weight'] for r in active)
    def mean(fn):return sum(r['weight']*fn(r) for r in active)/weight_sum if weight_sum else None
    aggregate_tx=sum((np.asarray(r['tx_grid'])/r['generations']*r['weight'] for r in active),np.zeros(o.grid_size**2+1))
    aggregate_time=sum((np.asarray(r['residence_grid'])/o.time_samples*r['weight'] for r in active),np.zeros(o.grid_size**2+1))
    if weight_sum:aggregate_tx/=weight_sum;aggregate_time/=weight_sum
    total=sum(g.count for g in profile.groups)
    if len(rows)<total:warnings.add('EDはグループ別の標本です。少数EDへの割当は見落とし得るため、ED番号を指定しても確認してください。')
    if end<profile.duration_s:warnings.add(f'実験全体ではなく0〜{end:g}秒までモデルを進め、指定区間を集計しました。長期の地点訪問率や定常分布を保証しません。')
    result={'schema_version':1,'scope':'generation_opportunities','profile_sha256':hashlib.sha256(profile.model_dump_json().encode()).hexdigest(),
            'profile_seed':profile.seed,'profile_name':profile.name,
            'options':o.model_dump(),'start_s':o.start_s,'end_s':end,'area_m':profile.area_m,'ed_count':total,
            'sampled_devices':len(rows),'active_devices':len(active),'devices':rows,
            'assessed_devices':sum(r['concentration_state']!='insufficient' for r in rows),
            'concentrated_devices':sum(r['concentration_state']=='concentrated' for r in rows),
            'aggregate':{'state_fractions':{s:mean(lambda r:r['states'][s]/r['generations']) for s in STATES},
                         'mean_dominant_site_fraction':mean(lambda r:r['dominant_site_fraction']),
                         'mean_spatial_tv':mean(lambda r:r['spatial_tv']),
                         'pooled_spatial_tv':float(.5*np.abs(aggregate_tx-aggregate_time).sum()) if active else None,
                         'tx_grid':aggregate_tx.tolist(),'residence_grid':aggregate_time.tolist()},
            'warnings':sorted(warnings),'notes':[
                '各EDの送信生成を1経験候補とする事前確認です。実送信・ACK受信・学習更新回数ではありません。MAC待機、Duty Cycle、再送、選択器、受信成否、学習、リプレイバッファは計算しません。',
                '同じ地点の静止経験100回と、移動中に異なる地点で得た経験100回は、静止50%でも地点分布が異なります。最大静止地点の全経験占有率と地点別内訳を確認してください。',
                '集中基準は「1つの静止地点」または「静止・移動を含む1つの地図メッシュ」が全経験候補の指定割合以上です。研究者が設定する実験設計用の基準であり、一般的な統計的有意性・学習性能の基準ではありません。非該当は偏りが存在しないことを意味しません。',
                '静止判定はモデル内部の状態です。送信間に移動して元の場所へ戻る場合も、送信時に移動中なら移動として数えます。状態未公開の独自モデルは不明です。',
                '静止地点は原点基準の指定幅の3次元セルへ集約します。境界を挟む近接点は別地点になります。地図の2次元メッシュとは独立です。',
                '連続回数は同じ集約地点で静止中の生成が続いた最長回数です。送信のない間に別地点へ移動して戻る場合も連続経験です。単一の滞在の長さではありません。',
                '時間分布は層化した一様時刻標本による近似です。TV=1/2 Σ|送信地点割合−滞在時間割合|。有限回数・メッシュ幅に依存し、統計検定でも学習性能の予測でもありません。',
                '集計は送信のあるEDを等重みとし、グループの標本数差を補正します。各EDの偏りは全体の地図で相殺され得るため、ED別指標を優先して確認します。',
                '送信・移動プラグインとED別乱数は本体と共通です。独自送信モデルにはACK等をNoneとして渡すため、通信フィードバック依存のスケジュールは再現できません。',
            ],'wall_s':time.perf_counter()-started}
    return result
