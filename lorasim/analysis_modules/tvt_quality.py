import numpy as np
from pydantic import Field
from lorasim.interfaces import Options
from .base import AnalysisModule, bars
from ._common import line, table, title
from ._tvt import quality, records


class Settings(Options):
    threshold: float=Field(.7,ge=0,le=1,title='PRR達成しきい値',description='この値以上のED割合。アプリケーション共通の保証値ではありません。')


class TVTQuality(AnalysisModule):
    Options=Settings
    requires=frozenset({'prr'})
    label='TVT：PRR・公平性・下位ED品質・CDF'
    description='GUIの共通送信区間でED等重みのPRR/Jain、下位10%平均、しきい値達成率とCDFを表示。ACK成功率を別計数します。'

    def compute(self,c,o):
        rows=[];cdf=[];metrics=[];notes=[]
        for r in c.runs:
            x=np.sort(np.asarray(r['device_prr']));q=quality(x,o.threshold);metrics.append(q)
            ack=[0,0]
            for v in records(c,r,'feedback',['tx_index','ack']):
                if v['tx_index'] is not None and c.selected(v['tx_index']) and v['ack'] is not None:
                    ack[0]+=bool(v['ack']);ack[1]+=1
            rows.append([r['id'],len(x),r['ed_count'],q['prr'],q['jain'],q['bottom10'],q['coverage'],ack[0]/ack[1] if ack[1] else None,ack[1]])
            if not ack[1]:notes.append(f"{r['id']}: ACK通番記録なし、未確認送信または対象フィードバックなし。ACK率はN/A。")
            if len(x):
                values,counts=np.unique(x,return_counts=True);cum=np.cumsum(counts)/len(x)
                ix=np.unique(np.linspace(0,len(values)-1,min(500,len(values)),dtype=int))
                points=[dict(x=0.,y=0.,n=len(x))] if values[0]>0 else []
                points += [dict(x=float(values[i]),y=float(cum[i]),n=len(x)) for i in ix]
                cdf.append(dict(label=title(c,r),points=points))
        return dict(views=[table('通信品質（同一送信区間）',['Run','対象ED','全ED','ED平均PRR','Jain','下位10%平均PRR',f'PRR ≥ {o.threshold:g}','ACK成功率（パケット加重）','ACK標本数'],rows),
            line('ED別PRRの経験CDF',cdf,'PRR','累積ED割合',step=True,y_max=1),
            *[bars(label,c.runs,[q[k] for q in metrics]) for k,label in [('bottom10','下位10% EDの平均PRR'),('coverage','要求PRR達成ED割合')]]],
            notes=notes+['下位10%はceil(0.1×対象ED数)台の平均（10%分位点とは別）。全PRR=0のJainは未定義。CDFは最大500点表示。未判定ULはPRR分母から除外。'])


PLUGIN=TVTQuality
