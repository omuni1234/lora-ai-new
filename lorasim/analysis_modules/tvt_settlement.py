from collections import defaultdict
import numpy as np
from pydantic import Field
from lorasim.interfaces import Options
from .base import AnalysisModule
from ._common import records, line, title, table


class Settings(Options):
    bins: int=Field(100,ge=2,le=500,title='表示点数',description='送信通番に対する最終記録集合への到達割合を表示します。')


class TVTSettlement(AnalysisModule):
    Options=Settings
    label='TVT：SEMAの戦略定着状況（事後評価）'
    description='各EDが観測期間末のArm集合と一致した時点を事後的に集計します。以後の消去停止・最良Armを保証する指標ではありません。'

    def compute(self,c,o):
        series=[];rows=[];notes=[]
        for r in c.runs:
            h=defaultdict(list);maxima=defaultdict(int)
            for ed,tx,active in records(c,r,'selector_states',['device_id','tx_index','active_arms']):
                if active is not None:h[ed].append((tx,tuple(sorted(active))))
            for ed,tx in records(c,r,'uplinks',['device_id','tx_index']):maxima[ed]=max(maxima[ed],tx)
            if not h:
                notes.append(f"{r['id']}: Arm変更記録なし。");continue
            settled={}
            for ed,events in h.items():
                events.sort();final=events[-1][1]
                settled[ed]=next(tx for tx,active in events if active==final)
            stop=min(c.end or r['maximum_tx_index'],r['maximum_tx_index'])
            xs=np.unique(np.linspace(c.start,stop,min(o.bins,max(1,stop-c.start+1)),dtype=int)) if stop>=c.start else []
            points=[]
            for tx in xs:
                eligible=[ed for ed in settled if maxima[ed]>=tx]
                points.append(dict(x=int(tx),y=sum(settled[ed]<=tx for ed in eligible)/len(eligible) if eligible else None,n=len(eligible)))
            series.append(dict(label=title(c,r),points=points))
            rows.extend([r['id'],ed,settled[ed],len(h[ed][-1][1]),maxima[ed]] for ed in sorted(h))
        return dict(views=[line('最終記録Arm集合への到達割合',series,'EDごとの送信通番','対象ED割合',y_max=1,step=True),
            table('ED別の最終変更時点',['Run','ED','最終集合への到達通番','最終Arm数','観測最終通番'],rows)],
            notes=notes+['その送信通番に到達したEDだけが分母です。最初から消去がないEDは通番0で最終集合と一致します。'])


PLUGIN=TVTSettlement
