from collections import defaultdict
import numpy as np
from .base import AnalysisModule
from ._common import identities, line, table


class PRRByCount(AnalysisModule):
    label='ED数とアルゴリズム別の平均PRR'
    description='同じExperiment・選択器の設定を線で結びます。各RunのED平均PRRを等重みで平均し、縦線はRun間の最小〜最大です。色は選択器を識別します。'
    def compute(self,c,o):
        groups=defaultdict(lambda:defaultdict(list))
        for r in c.runs:
            if r['mean_ed_prr'] is None: continue
            info=identities(c,r)
            groups[(info['algorithm'],r['experiment_id'],r['experiment_name'],info['signature'])][r['ed_count']].append(r['mean_ed_prr'])
        series=[]; rows=[]
        for (algorithm,eid,name,signature),counts in groups.items():
            points=[]
            for n,values in sorted(counts.items()):
                mean=float(np.mean(values))
                points.append(dict(x=n,y=mean,low=min(values),high=max(values),n=len(values)))
                rows.append([name,algorithm,signature,n,len(values),mean,min(values),max(values)])
            series.append(dict(label=f'{algorithm} · {name} · {signature[:5]}',color_key=algorithm,points=points))
        return dict(views=[line('ED数に対するED平均PRR',series,'ED数','PRR',y_max=1),
                           table('集計値',['Experiment','選択器','設定ID','ED数','Run数','平均PRR','最小','最大'],rows)],
                    notes=['範囲は信頼区間ではありません。移動・送信周期などを揃えたExperiment内で比較してください。'])
PLUGIN=PRRByCount
