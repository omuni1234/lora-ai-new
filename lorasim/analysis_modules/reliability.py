import numpy as np
from pydantic import Field
from lorasim.interfaces import Options
from .base import AnalysisModule, bars
class Settings(Options):
    threshold: float=Field(.9,ge=0,le=1,title='要求PRR',description='PRRがこの閾値未満のED割合を算出します。文献共通の定数ではなく、研究者が決める要件です。')
class Reliability(AnalysisModule):
    Options=Settings
    requires=frozenset({'prr'})
    label='ED別PRR分布・要求達成度'
    description='平均で隠れる不利なEDを確認するため、下位10%点・中央値・要求未達ED割合を表示します。'
    sources=['https://arxiv.org/html/1801.00522']
    def compute(self,c,o):
        rows=[]
        for r in c.runs:
            x=np.asarray(r['device_prr'])
            rows.append(dict(run_id=r['id'],p10=float(np.quantile(x,.1)) if len(x) else None,
                median=float(np.median(x)) if len(x) else None,below_threshold=float(np.mean(x<o.threshold)) if len(x) else None,
                zero_success=float(np.mean(x==0)) if len(x) else None))
        return dict(rows=rows,views=[bars(title,c.runs,[r[key] for r in rows],'割合') for key,title in [('p10','ED別PRRの下位10%点'),('median','ED別PRRの中央値'),('below_threshold',f'要求PRR {o.threshold:g} 未満のED割合'),('zero_success','一度も受信成功しなかったEDの割合')]])
PLUGIN=Reliability
