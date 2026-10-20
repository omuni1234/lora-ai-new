from bisect import bisect_right
from collections import defaultdict
import json
from typing import Literal
import numpy as np
from pydantic import Field
from lorasim.interfaces import Options
from .base import AnalysisModule
from ._common import title, line, table, records, endpoints


class Settings(Options):
    device_id: int | None=Field(None,ge=0,title='ED ID（未指定＝全SEMA ED）',description='全体は各EDの残存率、個別は0/1の残存状態です。ED間で学習状態を共有するものではありません。')
    axis: Literal['time_s','tx_index']=Field('time_s',title='横軸',description='time_sはシミュレーション時刻、tx_indexはEDごとの実送信通番。消去はACKフィードバック完了時点に反映。')
    bins: int=Field(100,ge=2,le=300,title='表示点数の目安',description='全体表示は等間隔の標本点に消去時点を補います。単一EDはすべての消去を表示します。')


class SEMAPool(AnalysisModule):
    Options=Settings
    label='SEMAのAlgorithm Pool残存・消去の推移'
    description='静止/移動アルゴリズムの組（メタアーム）と、各状態プールのアルゴリズム残存率を表示します。最終状態から過去を推測しません。'
    def compute(self,c,o):
        views=[];notes=[]
        for r in c.runs:
            history=defaultdict(list); arms=None
            for ed,t,tx,experience,pairs,active in records(c,r,'selector_states',['device_id','time_ns','tx_index','experience','arms','active_arms']):
                if o.device_id is not None and ed!=o.device_id: continue
                current=json.loads(pairs)
                if arms is not None and current!=arms: raise ValueError('同じRunでSEMAのメタアーム定義が異なります。')
                arms=current
                history[ed].append((t/1e9 if o.axis=='time_s' else tx,set(active),experience,tx,t/1e9))
            if not history:
                notes.append(f"{r['id']}: SEMAのプール時系列がありません（旧形式、非SEMA、または対象EDなし）。新しい版で実験すると記録されます。")
                continue
            maxima=defaultdict(int)
            for ed,index in records(c,r,'uplinks',['device_id','tx_index']): maxima[ed]=max(maxima[ed],index)
            for ed in history: history[ed].sort(key=lambda x:x[0])
            maximum=max(maxima.values(),default=0)
            start=float(c.start) if o.axis=='tx_index' else 0.
            stop=float(min(c.end or maximum,maximum)) if o.axis=='tx_index' else c.observed_duration(r)
            if stop is None:
                notes.append(f"{r['id']}: 観測終了時刻がないため、時刻軸では最後の記録時点まで表示します。")
                stop=max(h[-1][0] for h in history.values())
            changes={h[0] for hs in history.values() for h in hs if start<=h[0]<=stop}
            xs=list(np.linspace(start,stop,o.bins)) if stop>=start else []
            # The aggregate graph is bounded; all exact changes remain in the table for a selected ED.
            if len(changes)<=300: xs+=list(changes)
            xs=sorted(set(xs))
            pair_names=[f'静止 {a} / 移動 {b}' for a,b in arms]
            members=sorted({(state,name) for pair in arms for state,name in enumerate(pair)})
            member_names=[('静止 ' if state==0 else '移動 ')+name for state,name in members]
            series=[dict(label=name,points=[]) for name in pair_names+member_names]
            counts=[]; converged=[]; event_rows=[]
            coords={ed:[h[0] for h in hs] for ed,hs in history.items()}
            for x in xs:
                present=[]
                for ed,hs in history.items():
                    if o.axis=='tx_index' and maxima[ed]<x: continue
                    j=bisect_right(coords[ed],x)-1
                    if j>=0: present.append(hs[j][1])
                n=len(present)
                for k,s in enumerate(series):
                    values=[k in active for active in present] if k<len(arms) else [
                        any(arms[a][members[k-len(arms)][0]]==members[k-len(arms)][1] for a in active) for active in present]
                    s['points'].append(dict(x=float(x),y=float(np.mean(values)) if n else None,n=n))
                counts.append(dict(x=float(x),y=float(np.mean([len(a) for a in present])) if n else None,n=n))
                converged.append(dict(x=float(x),y=float(np.mean([len(a)==1 for a in present])) if n else None,n=n))
            if o.device_id is not None:
                for ed,hs in history.items():
                    for _,active,exp,tx,t in hs:
                        event_rows.append([ed,t,tx,exp,len(active),', '.join(pair_names[a] for a in sorted(active))])
            axis='シミュレーション時刻 [s]' if o.axis=='time_s' else 'EDごとの送信通番'
            name=title(c,r)+(f' · ED {o.device_id}' if o.device_id is not None else '')
            views.extend([{**line(name+' · メタアーム',series[:len(arms)],axis,'残存ED割合',y_max=1,step=True), 'type':'survival'},
                          {**line(name+' · 状態別アルゴリズム',series[len(arms):],axis,'残存ED割合',y_max=1,step=True), 'type':'survival'},
                          line(name+' · 平均残存アーム数',[dict(label='残存アーム数',points=counts)],axis,'個',y_max=len(arms),step=True),
                          line(name+' · 1アームへ収束したED',[dict(label='1アームのED割合',points=converged)],axis,'割合',y_max=1,step=True)])
            if event_rows: views.append(table('消去時点の全記録',['ED','時刻 [s]','送信通番','ACK経験数','残存数','残存アーム'],event_rows))
        return dict(views=views,notes=notes+['時刻軸では全記録期間を表示（送信回数フィルタは送信通番軸に適用）。送信通番軸の分母はその通番に到達したEDです。残存は選択頻度とは異なります。'])
PLUGIN=SEMAPool
