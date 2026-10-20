from typing import Literal
import numpy as np
from pydantic import Field, model_validator
from lorasim.interfaces import Options
from .base import AnalysisModule
from ._common import profile, title, line, table, uplinks, state_for, endpoints, identities


class Settings(Options):
    distance_edges_m: list[float]=Field(default_factory=lambda:[0,1000,3000,10000],min_length=2,max_length=9,title='距離区間の境界 [m]',description='昇順の境界。例 [0,1000,3000,10000] は近・中・遠の3区間。下端を含み上端を含みません。')
    gateway_id: str | None=Field(None,title='基準GW ID（未指定＝最寄り）',description='3次元の幾何距離を使います。PRR成功はそのGWだけでなく、いずれかのGWで受信したことを意味します。')
    axis: Literal['tx_index','time_s']=Field('tx_index',title='横軸',description='tx_indexはEDごとの実送信通番、time_sはシミュレーション時刻です。')
    cumulative: bool=Field(True,title='累積PRR',description='オンは指定開始回数から各時点まで。オフは各表示区間だけ。通常の収束モジュール（1〜t累積）とは開始回数の扱いが異なります。')
    bins: int=Field(80,ge=2,le=300,title='最大表示点数',description='表示点はED数に応じてさらに制限し、パケット数に比例するRAMを使用しません。')
    @model_validator(mode='after')
    def valid(self):
        if self.distance_edges_m[0]<0 or any(a>=b for a,b in zip(self.distance_edges_m,self.distance_edges_m[1:])):
            raise ValueError('距離境界は0以上で、重複のない昇順にしてください。')
        return self


class StationaryDistance(AnalysisModule):
    Options=Settings
    label='距離を指定した静止時のPRR推移'
    description='送信開始時に静止していたULだけを抽出し、距離区間内で各EDを等重みにしたPRRを比較します。位置差が0だけでは静止と判定しません。'
    def compute(self,c,o):
        series_by_band=[[] for _ in o.distance_edges_m[:-1]]; coverage=[]; notes=[]
        for r in c.runs:
            p=profile(c,r); n=r['ed_count']; bands=len(series_by_band)
            if n*bands>1_000_000:
                raise ValueError('距離別PRRの集計メモリ上限を超えます。距離区間を減らすか、ED数の少ないRunを選択してください。')
            gws=[g.position for g in p.gateways if o.gateway_id is None or g.id==o.gateway_id]
            if not gws: raise ValueError(f"GW {o.gateway_id} がRun {r['id']}に存在しません。")
            stop=min(c.end or r['maximum_tx_index'],r['maximum_tx_index'])
            if o.axis=='tx_index': xs=endpoints(c.start,stop,o.bins,n*bands)
            else:
                duration=c.observed_duration(r)
                if duration is None: duration=p.duration_s; notes.append(f"{r['id']}: 観測終了時刻がないため設定時間を使用。")
                count=max(1,min(o.bins,1_000_000//max(1,n*bands)))
                xs=np.linspace(0,duration,count) if count>1 else np.array([duration])
            totals=np.zeros((len(xs),bands,n),dtype=np.int64); success=np.zeros_like(totals)
            unknown=outside=stationary=moving=0
            if len(xs):
                for ed,index,t,outcome,x,y,z,state in uplinks(c,r):
                    if not c.selected(index) or outcome=='Censored': continue
                    state=state_for(c,r,ed,state)
                    if state=='unknown': unknown+=1; continue
                    if state=='moving': moving+=1; continue
                    stationary+=1
                    if any(v is None for v in (x,y,z)): outside+=1; continue
                    distance=min(float(np.linalg.norm(np.array([x,y,z])-g)) for g in gws)
                    band=int(np.searchsorted(o.distance_edges_m,distance,side='right'))-1
                    if band<0 or band>=bands: outside+=1; continue
                    coord=index if o.axis=='tx_index' else (t or 0)/1e9
                    slot=int(np.searchsorted(xs,coord))
                    if slot>=len(xs): continue
                    totals[slot,band,ed]+=1; success[slot,band,ed]+=outcome=='Received'
            if o.cumulative:
                np.cumsum(totals,axis=0,out=totals); np.cumsum(success,axis=0,out=success)
            for band in range(bands):
                points=[]
                for i,x in enumerate(xs):
                    eligible=totals[i,band]>0
                    points.append(dict(x=float(x),y=float(np.mean(success[i,band,eligible]/totals[i,band,eligible])) if eligible.any() else None,
                                       n=int(eligible.sum()),attempts=int(totals[i,band].sum())))
                series_by_band[band].append(dict(label=title(c,r),color_key=identities(c,r)['algorithm'],points=points))
            coverage.append([r['id'],stationary,moving,unknown,outside])
            if unknown: notes.append(f"{r['id']}: 移動状態が不明な {unknown} 件を除外。新しい実験では送信時の状態を記録します。")
        views=[line(f'静止時PRR · {lo:g}〜{hi:g} m',s,'送信通番' if o.axis=='tx_index' else 'シミュレーション時刻 [s]','PRR',y_max=1)
               for lo,hi,s in zip(o.distance_edges_m,o.distance_edges_m[1:],series_by_band)]
        views.append(table('集計対象の確認',['Run','静止UL','移動UL','状態不明UL','静止ULの範囲外/座標欠損'],coverage))
        return dict(views=views,notes=notes)
PLUGIN=StationaryDistance
