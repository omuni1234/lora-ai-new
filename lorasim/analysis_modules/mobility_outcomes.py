from collections import defaultdict, Counter
import numpy as np
from .base import AnalysisModule, stacked
from ._common import uplinks, state_for, title, table


class MobilityOutcomes(AnalysisModule):
    label='静止・移動別の経験数とPRR'
    description='静止/移動/状態不明の実送信数と成功率を分け、学習する環境の偏りと通信成績を同時に確認します。判定未了を除外。'
    def compute(self,c,o):
        views=[]; rows=[]
        for r in c.runs:
            states=defaultdict(lambda:defaultdict(Counter))
            for ed,index,t,outcome,x,y,z,state in uplinks(c,r):
                if c.selected(index) and outcome!='Censored': states[state_for(c,r,ed,state)][ed][outcome]+=1
            parts=[];labels=[]
            for state,label in [('stationary','静止'),('moving','移動'),('unknown','状態不明')]:
                data=states[state]; n=len(data); counts=sum(sum(d.values()) for d in data.values())
                causes={k:float(np.mean([v[k]/sum(v.values()) for v in data.values()])) for k in {k for v in data.values() for k in v}} if n else {}
                parts.append(causes);labels.append(label)
                rows.append([r['id'],label,n,counts,causes.get('Received',0) if n else None])
            views.append(stacked(title(c,r),parts,labels,'送信時の移動状態'))
        views.append(table('状態ごとの標本数',['Run','状態','対象ED数','判定済みUL','ED平均PRR'],rows))
        return dict(views=views)
PLUGIN=MobilityOutcomes
