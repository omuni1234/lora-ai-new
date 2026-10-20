from collections import Counter
from itertools import combinations
from .base import AnalysisModule
from .channel_distribution import channel_counts, distribution, kl
from ._common import title, table


class ChannelJS(AnalysisModule):
    label='チャネル選択分布のRun間JS Divergence'
    description='選択CH分布がRun間でどれだけ異なるかを対称なJSで比較。ゼロ確率にも定義でき、自然対数で0〜ln(2)。各Run内は送信回数で重み付けします。'
    sources=['https://docs.scipy.org/doc/scipy/reference/generated/scipy.spatial.distance.jensenshannon.html']
    def compute(self,c,o):
        distributions=[]
        for r in c.runs:
            pooled=Counter()
            for row in channel_counts(c,r).values(): pooled.update(row)
            distributions.append(distribution(pooled))
        rows=[]
        # Comparing within ED count prevents accidental cross-load interpretations.
        for (i,a),(j,b) in combinations(enumerate(c.runs),2):
            if a['ed_count']!=b['ed_count']: continue
            p,q=distributions[i],distributions[j]
            m={k:(p.get(k,0)+q.get(k,0))/2 for k in p.keys()|q.keys()}
            js=(kl(p,m)+kl(q,m))/2 if p and q else None
            rows.append([a['ed_count'],title(c,a),title(c,b),js])
            if len(rows)>=2000: break
        return dict(views=[table('同じED数のRun間分布差',['ED数','Run A','Run B','JS [nat]'],rows)],
                    notes=['最大2000組を表示します。JS距離の二乗（divergence）を返します。'])
PLUGIN=ChannelJS
