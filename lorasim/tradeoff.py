"""Bounded planning approximation, deliberately separate from the DES engine.

Compound Poisson overlap sampling uses the actual energy/capture thresholds.
Renewal availability couples successful ULs to RX1/RX2 ACK load. This predicts
stationary operating points under stated action distributions, not learning.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import time
from typing import Literal

import numpy as np
from pydantic import Field
from .config import Model, Radio
from .phy import NS, airtime_ns, links, GW_SENSITIVITY, ED_SENSITIVITY, GOURSAUD
from .registry import bundled
from .visualization import layout

CAUSES = ('Received','Interference','LostBecauseTx','UnderSensitivity','NoMoreReceivers','WrongFrequency','MixedLoss')


class ForecastOptions(Model):
    seed: int = Field(73, ge=0, le=2**32-1)
    device_samples: int = Field(48, ge=8, le=128)
    overlap_trials: int = Field(96, ge=32, le=512)
    snapshot_s: float = Field(0, ge=0, le=600)
    action_mode: Literal['profile','uniform'] = 'profile'
    custom_interval_s: float | None = Field(None, gt=0, le=1e9)
    compare: bool = True


class ForecastError(ValueError):
    pass


def erlang_b(load, servers):
    b = 1.
    for k in range(1, servers+1):
        b = load*b/(k+load*b)
    return b


def alias(category, kind):
    for name, cls in bundled(category).items():
        if kind == cls.__module__+':'+cls.__name__:
            return name
    return kind


class RadioCandidates:
    """Index a Cartesian space without allocating tens of thousands of Radios."""
    def __init__(self, sfs, channels, crs, powers, initial=None):
        self.dimensions=(sfs,channels,crs,powers)
        self.initial=initial or Radio()
    def __len__(self):return math.prod(map(len,self.dimensions))
    def __getitem__(self,index):
        if index<0 or index>=len(self):raise IndexError(index)
        values=[]
        for dimension in reversed(self.dimensions):
            index,j=divmod(int(index),len(dimension));values.append(dimension[j])
        return self.initial.model_copy(update=dict(zip(('tp_dbm','cr','frequency_hz','sf'),values)))
    @classmethod
    def space(cls,s):return cls(s.spreading_factors,s.frequencies_hz,s.coding_rates,s.powers_dbm)


def candidates(p, spec, mode, warnings):
    kind = alias('policy', spec.kind)
    cls = bundled('policy').get(kind)
    if cls is None:
        warnings.add('独自選択器は実行しません。全体／ED候補の一様選択を仮定します。')
    o = cls.Options.model_validate(spec.options).model_dump() if cls and hasattr(cls,'Options') else spec.options
    space = p.action_space.restrict(spec.action_space) if spec.action_space is not None else p.action_space
    uses = spec.action_space is not None or (cls and getattr(cls,'uses_action_space',False))
    if mode == 'uniform':
        return RadioCandidates.space(space)
    if kind=='fixed_sf_ch':
        r=RadioCandidates.space(space)[0]
        for k,allowed in [('sf',space.spreading_factors),('frequency_hz',space.frequencies_hz)]:
            if o.get(k) is not None:
                if o[k] not in allowed:raise ForecastError('固定SF/CHが共通候補に含まれていません。候補集合を確認してください。')
                setattr(r,k,o[k])
        return [r]
    if kind=='fixed':
        r=Radio.model_validate(o)
        if uses:
            first=RadioCandidates.space(space)[0]
            r=r.model_copy(update={k:getattr(first,k) for k in ('sf','frequency_hz','tp_dbm','cr')})
        return [r]
    if kind in {'adr','badr','gadr'}:
        warnings.add('ADR系は初期SF/TPとCH分布を固定して試算します。制御後の収束性能ではありません。ADRコマンドの追加DL・バイトは含みません。')
        r = Radio(sf=max(space.spreading_factors),tp_dbm=space.powers_dbm[0],cr=space.coding_rates[0]) if uses else Radio.model_validate(o.get('initial') or {'sf':12})
        channels = space.frequencies_hz if uses else o.get('frequencies_hz',[r.frequency_hz]) if o.get('channel_selection')=='uniform' else [r.frequency_hz]
        return [r.model_copy(update={'frequency_hz':f}) for f in channels]
    if kind not in {'random','random_sf_ch'}:
        warnings.add('学習器の選択比率は未知です。候補を一様に使う比較条件として推定し、学習や最適化は行いません。')
    if uses or cls is None:
        return RadioCandidates.space(space)
    if 'actions' in o:
        return [Radio.model_validate(r) for r in o['actions']]
    initial = Radio.model_validate(o.get('initial') or {})
    return RadioCandidates(o.get('spreading_factors',list(range(7,13))),o.get('frequencies_hz',[initial.frequency_hz]),
                           [o.get('cr',initial.cr)],o.get('powers_dbm',[initial.tp_dbm]),initial)


def interval(spec, options, warnings):
    kind = alias('timing',spec.kind)
    if kind not in {'periodic','poisson'}:
        if options.custom_interval_s is None:
            raise ForecastError('独自送信タイミングの平均間隔が不明です。「独自モデルの平均間隔」を秒で入力してください。')
        warnings.add('独自送信タイミングを指定された平均間隔の非同期到着として近似します。')
        return options.custom_interval_s, 'periodic'
    o = bundled('timing')[kind].Options.model_validate(spec.options).model_dump()
    if kind == 'poisson':
        return o['mean_s'], kind
    start = o['start']
    spread = (start['max_s']-start['min_s'] if start['mode']=='uniform' else 0) if start else (o['interval_s'] if o['random_phase'] else 0)
    if spread < .8*o['interval_s']:
        warnings.add('初回時刻の分散が周期より狭い設定です。同期・位相固定の衝突をPoisson近似は再現できません。開始時刻を1周期全体でランダム化した比較も確認してください。')
    return o['interval_s'], kind


@dataclass
class Population:
    rows: list
    warnings: set
    total: int
    sampled: int


def population(p, options):
    if len(p.gateways)>8 or len(p.groups)>64:
        raise ForecastError('簡易予測はGW 8台・EDグループ64個まで対応します。条件を絞って試算してください。')
    warnings = set()
    view = layout(p, options.snapshot_s, options.device_samples)
    groups = {g.name:g for g in p.groups}
    counts = {name:sum(d['group']==name for d in view['devices']) for name in groups}
    if any(v==0 for v in counts.values()):
        raise ForecastError('標本に含まれないEDグループがあります。標本ED数を増やすかグループをまとめてください。')
    if view['positions_unavailable']:
        warnings.add('一部の移動モデルは指定時刻の位置を取得できず初期位置を使用しました。')
    if view['ed_count']>len(view['devices']):
        warnings.add('ED配置・割当は標本化しています。少数EDだけへの割当や狭い感度境界は標本数を増やして確認してください。')
    rows=[]
    rng=np.random.default_rng(options.seed)
    for d in view['devices']:
        g=groups[d['group']]
        specs={k:getattr(g,k) for k in ('policy','timing','mobility')}
        for a in p.assignments:
            if (not a.groups or g.name in a.groups) and (not a.ids or d['id'] in a.ids) and (not a.tags or set(a.tags)<=set(g.tags)):
                specs.update({k:getattr(a,k) for k in specs if getattr(a,k) is not None})
        specs={k:p.component(s,k) for k,s in specs.items()}
        radios=candidates(p,specs['policy'],options.action_mode,warnings)
        # Keep all small action sets, otherwise sample uniformly without replacement.
        if len(radios)>8:
            radios=[radios[i] for i in rng.choice(len(radios),8,replace=False)]
            warnings.add('大きな行動空間はED標本ごとに最大8候補を抽出します。seedを変えて標本変動を確認できます。')
        dt,kind=interval(specs['timing'],options,warnings)
        if alias('timing',specs['timing'].kind)=='periodic':
            timing_options=bundled('timing')['periodic'].Options.model_validate(specs['timing'].options)
            first=(timing_options.start.at_s if timing_options.start.mode=='fixed' else timing_options.start.min_s) if timing_options.start else timing_options.first_s
            if first>=p.duration_s:
                raise ForecastError('実験終了より後に初回送信を設定したグループがあります。開始時刻か実験時間を調整してください。')
        if p.duration_s < 10*dt:
            warnings.add('実験時間が生成間隔の10倍未満です。予測は定常状態であり、起動直後・終了端の割合は推定しません。')
        if alias('mobility',specs['mobility'].kind)!='stationary':
            warnings.add('移動は指定時刻の配置スナップショットです。全軌跡の時間平均・位置と方策の相関は含みません。')
        cls=bundled('policy').get(alias('policy',specs['policy'].kind))
        extra=9 if cls and getattr(cls,'link_feedback_bytes',0) else 0
        # LinUCB declares telemetry at instance level via an option.
        if specs['policy'].kind.startswith('linucb'):
            extra=9
        for radio in radios:
            rows.append(dict(id=d['id'],xyz=d['xyz'],weight=g.count/counts[g.name]/len(radios),
                             represented=g.count/counts[g.name],radio=radio,interval=dt,timing=kind,
                             payload=g.payload_bytes,confirmed=g.confirmed,attempts=g.max_attempts,extra=extra))
    if any(p.band(x['radio'].frequency_hz)==p.band(p.mac.rx2_frequency_hz) for x in rows):
        warnings.add('RX1とRX2に同じサブバンドを使う条件があります。2窓の可用性の時間相関を省略するためACK負荷の誤差が増えます。')
    if len(p.gateways)>1:
        warnings.add('複数GWは受信成否の独立近似です。GW間の干渉相関・他GWのDL干渉・応答先再選択を省略し、UL成功率の相関不明時の上下限も表示します。')
    if any(r['attempts']>1 for r in rows):
        warnings.add('再送負荷はACK感度・送信可用性から近似します。DL衝突・受信窓の先着ロックを省略するため再送を過小評価し得ます。')
    if p.mac.backhaul_delay_s:
        warnings.add('バックホール遅延による受信窓逸失はACK負荷近似に含みません。')
    return Population(rows,warnings,view['ed_count'],len(view['devices']))


def overlap_success(radios, times, powers, rates, ids, represented, trials, seed, matrix, budget):
    """Tagged-packet Monte Carlo; no timeline/events or full packet history.

    Starts of mark j in (-Tj, Ti) have count Poisson(lambda_j*(Ti+Tj)).
    Summing overlap energies retains aggregate weak interferers and capture.
    """
    n,ng=powers.shape
    result=np.ones((n,ng))
    rng=np.random.default_rng(seed)
    sf=np.array([r.sf-7 for r in radios]);freq=np.array([r.frequency_hz for r in radios])
    for i,r in enumerate(radios):
        intensity=rates*(times[i]+times)*(freq==r.frequency_hz)
        intensity=intensity.copy()
        intensity[ids==ids[i]]*=max(0.,1-1/represented[i])
        mu=float(intensity.sum())
        if mu==0: continue
        if mu*trials>min(200_000,budget[0]):
            raise ForecastError('通信負荷が簡易計算の上限を超えています。ED数・送信間隔または標本数を調整してください。')
        counts=rng.poisson(mu,trials);number=int(counts.sum());budget[0]-=number
        if budget[0]<0: raise ForecastError('重複計算の上限に達しました。標本数を減らしてください。')
        trial=np.repeat(np.arange(trials),counts)
        j=rng.choice(n,number,p=intensity/mu)
        start=-times[j]+rng.random(number)*(times[i]+times[j])
        overlap=np.maximum(0.,np.minimum(times[i],start+times[j])-np.maximum(0.,start))
        # Normalize by desired energy, avoiding extreme absolute powers.
        energy=np.zeros((trials,6,ng))
        relative=np.power(10.,np.clip((powers[j]-powers[i])/10,-300,300))
        np.add.at(energy,(trial,sf[j]),overlap[:,None]/times[i]*relative)
        if matrix=='aloha': failed=energy[:,sf[i],:]>0
        else: failed=(energy>10**(-GOURSAUD[sf[i]][None,:,None]/10)).any(axis=1)
        result[i]=1-failed.mean(axis=0)
    return result


def scenario(p,pop,options,change,budget):
    rows=pop.rows
    if change.get('channels')=='uniform':
        channels=p.action_space.frequencies_hz
        if len(rows)*len(channels)>4096:
            raise ForecastError('CH比較の組合せ上限です。標本ED数を減らしてください。')
        rows=[{**x,'radio':x['radio'].model_copy(update={'frequency_hz':f}),'weight':x['weight']/len(channels)} for x in rows for f in channels]
    n=len(rows);ng=len(p.gateways)
    radios=[]
    for x in rows:
        r=x['radio'].model_copy()
        for key in ('sf','tp_dbm','cr'):
            if key in change: setattr(r,key,change[key])
        if change.get('channels')=='single':r.frequency_hz=p.action_space.frequencies_hz[0]
        radios.append(r)
    for r,x in zip(radios,rows):
        band=p.direction_band(r.frequency_hz,'ul')
        if r.tp_dbm>p.sub_bands[band].max_tp_dbm: raise ForecastError('候補TPがサブバンドの上限を超えています。')
        if x['payload']>(51 if r.sf>=10 else 115 if r.sf==9 else 222):
            raise ForecastError('候補SFでペイロードがEU制限を超えます。ペイロードを短くしてください。')
    weights=np.array([r['weight'] for r in rows]);weights*=change.get('ed_scale',1.)
    represented=np.array([r['represented'] for r in rows])*change.get('ed_scale',1.)
    times=np.array([airtime_ns(x['payload']+9,r)/NS for x,r in zip(rows,radios)])
    dt=np.array([r['interval'] for r in rows])*change.get('interval_scale',1.)
    confirmed=np.array([r['confirmed'] and not change.get('unconfirmed',False) for r in rows])
    attempts=np.array([r['attempts'] for r in rows])
    xyz=np.array([r['xyz'] for r in rows]);gwxyz=np.array([g.position for g in p.gateways])
    powers=np.vstack([links(x,gwxyz,r.tp_dbm,p.channel)[0] for x,r in zip(xyz,radios)])
    if not np.isfinite(powers).all():raise ForecastError('伝搬パラメータの数値範囲を確認してください。')
    heard=np.array([[r.frequency_hz in g.frequencies_hz for g in p.gateways] for r in radios])
    sensitive=powers>=np.array([GW_SENSITIVITY[r.sf-7] for r in radios])[:,None]
    bands=np.array([p.band(r.frequency_hz) for r in radios]);nb=len(p.sub_bands)
    rx2b=p.direction_band(p.mac.rx2_frequency_hz,'dl')
    duties=np.array([b.duty_cycle if p.mac.duty_cycle else 1. for b in p.sub_bands])
    allowed=np.array([p.dl_sub_bands is None or b.id in p.dl_sub_bands for b in p.sub_bands])
    tdl=np.array([[airtime_ns(9+x['extra'],Radio(sf=min(12,r.sf+p.mac.rx1_sf_offset) if w==0 else p.mac.rx2_sf))/NS for w in range(2)] for x,r in zip(rows,radios)])
    # Pure ACK radios use default CR/preamble, exactly as the simulator does.
    sf_dl=np.array([[min(12,r.sf+p.mac.rx1_sf_offset),p.mac.rx2_sf] for r in radios])
    loss=np.array([r.tp_dbm for r in radios])[:,None]-powers
    dl_ok=np.stack([np.array([p.sub_bands[b].max_tp_dbm for b in bands])[:,None]-loss>=np.array([ED_SENSITIVITY[s-7] for s in sf_dl[:,0]])[:,None],
                   p.sub_bands[rx2b].max_tp_dbm-loss>=ED_SENSITIVITY[p.mac.rx2_sf-7]],axis=-1)
    rho_band=np.zeros((ng,nb));availability=np.ones((ng,nb))*allowed
    own_nu=np.zeros((n,ng));own_rho=np.zeros((n,ng))
    nu=np.zeros(ng);repeat=np.ones(n);rates=np.zeros(n);q=None;old_rates=None;residual=1.;load_limited=False
    for iteration in range(40):
        busy=times+p.mac.rx2_delay_s+p.mac.rx_symbols*2**p.mac.rx2_sf/125000
        duty_gap=times/duties[bands] if p.mac.duty_cycle else times
        # Renewal approximation to the ED's single outstanding message.
        cycle=repeat*busy+np.maximum(0,repeat-1)*2
        generation=np.array([dt[i]+cycle[i] if x['timing']=='poisson' else max(dt[i],cycle[i]) for i,x in enumerate(rows)])
        per_ed=np.minimum(repeat/generation,1/np.maximum(busy,duty_gap))
        load_limited=load_limited or bool(np.any(per_ed<repeat/dt*.95))
        rates=weights*per_ed
        if old_rates is None or np.max(abs(rates-old_rates)/np.maximum(old_rates,1e-9))>.025:
            q=overlap_success(radios,times,powers,rates,np.array([x['id'] for x in rows]),represented,
                              options.overlap_trials,options.seed,p.channel.collision_matrix,budget)
            old_rates=rates.copy()
        rho=np.clip(rho_band.sum(axis=1),0,.999999)
        ids=np.array([x['id'] for x in rows])
        offered_ul=rates[:,None]*times[:,None]*heard*sensitive
        own_ul=np.zeros((n,ng))
        for device in np.unique(ids):
            mask=ids==device;own_ul[mask]=offered_ul[mask].sum(axis=0)/represented[mask][0]
        block=np.array([[erlang_b(max(0,float(offered_ul[:,g].sum()-own_ul[i,g])),gw.demodulators) for g,gw in enumerate(p.gateways)] for i in range(n)])
        # A Class A ED cannot overlap its own outstanding UL/ACK transaction.
        exposed_rho=np.maximum(0,rho[None,:]-own_rho)
        idle=1-exposed_rho
        no_start=np.exp(-times[:,None]*np.maximum(0,nu[None,:]-own_nu)/np.maximum(idle,1e-9))
        can=heard*sensitive*(1-block)
        probs={
            'LostBecauseTx':exposed_rho+idle*can*(1-no_start),
            'WrongFrequency':idle*(~heard), 'UnderSensitivity':idle*heard*(~sensitive),
            'NoMoreReceivers':idle*heard*sensitive*block,
            'Interference':idle*can*no_start*(1-q),'Received':idle*can*no_start*q}
        success=probs['Received']
        # RSSI-ranked first successful gateway, conditional independence approximation.
        order=np.argsort(-powers,axis=1,kind='stable');route=np.zeros((n,ng));remaining=np.ones(n)
        for rank in range(ng):
            gi=order[:,rank];route[np.arange(n),gi]=remaining*success[np.arange(n),gi]
            remaining*=1-success[np.arange(n),gi]
        demand=rates[:,None]*confirmed[:,None]*route
        a1=availability[:,bands].T;a2=availability[:,rx2b][None,:]
        send1=demand*a1;offer2=demand*(1-a1);send2=offer2*a2
        newrho=np.zeros((ng,nb));offered=np.zeros((ng,nb))
        for i,b in enumerate(bands):
            newrho[:,b]+=send1[i]*tdl[i,0];offered[:,b]+=demand[i]*tdl[i,0]/duties[b]
        newrho[:,rx2b]+=(send2*tdl[:,1,None]).sum(axis=0)
        offered[:,rx2b]+=(offer2*tdl[:,1,None]).sum(axis=0)/duties[rx2b]
        other=np.maximum(0,rho_band.sum(axis=1)[:,None]-rho_band)
        newavail=np.clip(1-other,0,1)/(1+offered)*allowed
        newnu=(send1+send2).sum(axis=0)
        new_own_nu=np.zeros((n,ng));new_own_rho=np.zeros((n,ng))
        for device in np.unique(ids):
            mask=ids==device
            new_own_nu[mask]=(send1[mask]+send2[mask]).sum(axis=0)/represented[mask][0]
            new_own_rho[mask]=(send1[mask]*tdl[mask,0,None]+send2[mask]*tdl[mask,1,None]).sum(axis=0)/represented[mask][0]
        ack=((send1*dl_ok[:,:,0]+send2*dl_ok[:,:,1]).sum(axis=1)/np.maximum(rates,1e-30))
        newrepeat=sum(np.where(attempts>k,(1-ack)**k,0) for k in range(int(attempts.max())))
        newrepeat=np.where(confirmed,newrepeat,attempts)
        residual=max(float(np.max(abs(newrho-rho_band))),float(np.max(abs(newavail-availability))),float(np.max(abs(newrepeat-repeat))))
        if residual<1e-5:break
        own_nu=.5*(own_nu+new_own_nu);own_rho=.5*(own_rho+new_own_rho)
        rho_band=.5*(rho_band+newrho);availability=.5*(availability+newavail);nu=.5*(nu+newnu);repeat=.5*(repeat+newrepeat)
    # Same network outcome categories as uplinks: any success wins; heterogeneous failures are MixedLoss.
    outcomes={'Received':1-np.prod(1-probs['Received'],axis=1)}
    for key in CAUSES[1:-1]:outcomes[key]=np.prod(probs[key],axis=1)
    outcomes['MixedLoss']=np.maximum(0,1-sum(outcomes.values()))
    avg=lambda x:float(np.average(x,weights=weights))
    packet=lambda x:float(np.average(x,weights=rates))
    fractions={k:avg(v) for k,v in outcomes.items()}
    eligible=(heard&sensitive).any(axis=1)
    intrinsic_failure=np.prod(1-heard*sensitive*q,axis=1)
    intrinsic_interference=float(np.average(intrinsic_failure[eligible],weights=weights[eligible])) if eligible.any() else None
    return dict(intrinsic_interference=intrinsic_interference,coverage_gap=avg(~eligible),fractions=fractions,packet_fractions={k:packet(v) for k,v in outcomes.items()},
                success_bounds=[avg(probs['Received'].max(axis=1)),avg(np.minimum(1,probs['Received'].sum(axis=1)))],
                uplinks_per_s=float(rates.sum()),downlinks_per_s=float(newnu.sum()),
                mean_airtime_ms=float(np.average(times,weights=rates)*1000),
                gateway_tx_fraction=rho_band.sum(axis=1).tolist(),
                mean_attempts=avg(repeat),load_limited=load_limited,converged=residual<1e-5,
                iterations=iteration+1,residual=residual,
                sampling_error_note=f'重複試行 {options.overlap_trials} 回／候補。配置・行動・近似式の誤差を含む信頼区間ではありません。')


def forecast(p,options=None):
    options=options or ForecastOptions();start=time.perf_counter()
    pop=population(p,options);budget=[5_000_000]
    changes=[('profile','現在の選択分布（仮定）',{})]
    if options.compare:
        changes += [(f'sf{s}',f'全EDをSF{s}に固定',{'sf':s}) for s in p.action_space.spreading_factors]
        changes += [('interval_half','送信間隔 ×0.5',{'interval_scale':.5}),('interval_double','送信間隔 ×2',{'interval_scale':2}),
                    ('unconfirmed','ACKなし（診断用）',{'unconfirmed':True}),('one_channel','1 CHへ集中（診断用）',{'channels':'single'}),
                    ('all_channels','全候補CHを均等利用',{'channels':'uniform'}),
                    ('ed_double','ED数 ×2（同じ配置分布）',{'ed_scale':2.})]
        if len(p.action_space.powers_dbm)>1:
            changes += [(f'tp{t}',f'全EDをTP {t:g} dBm',{'tp_dbm':t}) for t in (min(p.action_space.powers_dbm),max(p.action_space.powers_dbm))]
    results=[]
    for key,label,change in changes:
        try:
            result=scenario(p,pop,options,change,budget)
            results.append(dict(id=key,label=label,changes=change,**result))
        except ForecastError as exc:
            if key=='profile':raise
            results.append(dict(id=key,label=label,changes=change,unavailable=str(exc)))
    valid=[r for r in results if 'fractions' in r]
    if any(not r['converged'] for r in valid):pop.warnings.add('固定点の反復が上限に達した条件があります。数値の安定性が低いため傾向だけを参照してください。')
    if any(r['load_limited'] for r in valid):pop.warnings.add('EDのDuty Cycleまたは処理待ちが生成負荷を制限する条件があります。平均時間での近似のため実送信率との差が大きくなり得ます。')
    base=valid[0]['fractions'];tradeoffs=[]
    for r in valid[1:]:
        delta={k:100*(r['fractions'][k]-base[k]) for k in ('Interference','LostBecauseTx','UnderSensitivity')}
        if min(delta.values())<-.5 and max(delta.values())>.5:tradeoffs.append({'scenario':r['label'],'delta_pp':delta})
    return dict(model='poisson-overlap-renewal-v1',profile_sha256=hashlib.sha256(json.dumps(p.model_dump(mode='json'),sort_keys=True).encode()).hexdigest(),
                assumptions=options.model_dump(),ed_count=pop.total,sampled_devices=pop.sampled,particles=len(pop.rows),
                scenarios=results,tradeoffs=tradeoffs,warnings=sorted(pop.warnings),wall_s=time.perf_counter()-start,
                overlap_draws=5_000_000-budget[0],
                notes=['実験前の定常状態の近似です。実測結果でも学習後の性能予測でもありません。',
                       '主グラフはED等重みのUL成功・損失割合。ACK成功率とは異なります。',
                       '損失原因は競合します。GW送信中が減るだけでも干渉・感度未満の記録割合が増えます。潜在干渉と受信候補GWなしの指標も参照してください。',
                       '比較行は環境の感度診断です。ED固有の候補制限や学習器のACK必須条件を上書きする場合があり、実行プロファイルへ自動適用しません。',
                       '再送・受信窓・Duty Cycleの時刻相関、他GWのDL干渉、ADRコマンドを省略。推定差0.5ポイント未満は強調しません。'])
