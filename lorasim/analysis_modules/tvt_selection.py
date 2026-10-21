from collections import defaultdict
import math
import numpy as np
from pydantic import Field, model_validator
from lorasim.interfaces import Options
from .base import AnalysisModule
from ._common import profile, title, line, table
from ._tvt import records


class Settings(Options):
    distance_edges_m: list[float]=Field(default_factory=lambda:[0,3000,6000,9000,20000],min_length=2,max_length=20,title='距離区間 [m]',description='送信開始位置から最近傍GWへの3D距離。下端を含み上端を含みません。')
    bins: int=Field(100,ge=1,le=300,title='時系列の区間数',description='共通送信通番範囲を分割し、実際の選択回数割合を表示します。')
    @model_validator(mode='after')
    def ordered(self):
        if self.distance_edges_m[0]<0 or any(b<=a for a,b in zip(self.distance_edges_m,self.distance_edges_m[1:])):
            raise ValueError('distance boundaries must increase from >=0')
        return self


class TVTSelection(AnalysisModule):
    Options=Settings
    label='TVT：距離・状態別の実選択アルゴリズム'
    description='Armの残存率とは別に、送信時に実際に利用した下位アルゴリズムの割合と条件付きPRRを表示します。'

    def compute(self,c,o):
        views=[];rows=[];notes=[];detection_rows=[]
        for r in c.runs:
            p=profile(c,r);gws=np.array([g.position for g in p.gateways]);data=defaultdict(lambda:[0,0,0]);temporal=defaultdict(int);detection=defaultdict(int);missing=outside=0
            stop=min(c.end or r['maximum_tx_index'],r['maximum_tx_index']);width=max(1,math.ceil(max(1,stop-c.start+1)/o.bins))
            for v in records(c,r,'uplinks',['tx_index','outcome','motion_state','detected_motion_state','selected_algorithm','x_m','y_m','z_m']):
                if not c.selected(v['tx_index']):continue
                detection[(v['motion_state'] or 'unknown',v['detected_motion_state'] or 'unknown')]+=1
                if not v['selected_algorithm'] or any(v[k] is None for k in ('x_m','y_m','z_m')):missing+=1;continue
                state=v['motion_state'] or 'unknown';algo=v['selected_algorithm'];xyz=np.array([v[k] for k in ('x_m','y_m','z_m')])
                distance=float(np.linalg.norm(gws-xyz,axis=1).min());d=int(np.searchsorted(o.distance_edges_m,distance,side='right'))-1
                if not 0<=d<len(o.distance_edges_m)-1:outside+=1;continue
                a=data[(state,d,algo)];a[0]+=1;a[1]+=int(v['outcome']!='Censored');a[2]+=int(v['outcome']=='Received')
                temporal[(state,(v['tx_index']-c.start)//width,algo)]+=1
            for (state,d,algo),(selected,eligible,ok) in sorted(data.items()):
                total=sum(v[0] for (st,di,_),v in data.items() if st==state and di==d)
                rows.append([r['id'],state,o.distance_edges_m[d],o.distance_edges_m[d+1],algo,selected,selected/total,eligible,ok/eligible if eligible else None])
            for state in sorted({k[0] for k in temporal}):
                series=[]
                for algo in sorted({k[2] for k in temporal if k[0]==state}):
                    pts=[]
                    for b in range(max(0,math.ceil((stop-c.start+1)/width))):
                        n=sum(v for (st,bi,_),v in temporal.items() if st==state and bi==b)
                        pts.append(dict(x=min(stop,c.start+(b+1)*width-1),y=temporal[(state,b,algo)]/n if n else None,n=n))
                    series.append(dict(label=algo,points=pts))
                views.append(line(title(c,r)+' · '+state,series,'EDごとの送信通番','実選択回数割合',y_max=1))
            if missing or outside:notes.append(f"{r['id']}: 選択/座標記録なし {missing} 件、距離範囲外 {outside} 件。")
            for (actual,detected),n in sorted(detection.items()):
                total=sum(count for (state,_),count in detection.items() if state==actual)
                detection_rows.append([r['id'],actual,detected,n,n/total])
        return dict(views=[table('実選択と条件付きPRR',['Run','物理状態','距離下限 [m]','距離上限 [m]','下位モデル','選択回数','状態・距離内割合','判定済みUL','条件付きPRR'],rows),
            table('物理状態と局所位置差判定',['Run','送信時の物理状態','選択時の判定','件数','同じ物理状態内の割合'],detection_rows),*views],
            notes=notes+['モデルは異なる経験・地点で選択されています。選択後の条件付きPRRは同条件の独立対照実験ではありません。記録にはtracking.policy_diagnosticsが必要。',
                '位置差判定は選択時、物理状態は送信開始時。MAC待機がある条件では時刻差も含みます。unknownは判定不能として別計数。'])


PLUGIN=TVTSelection
