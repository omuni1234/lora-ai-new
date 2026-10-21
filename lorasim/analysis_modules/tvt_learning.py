from collections import defaultdict
import math
import numpy as np
from pydantic import Field
from lorasim.interfaces import Options
from .base import AnalysisModule
from ._common import line, title
from ._tvt import records


class Settings(Options):
    window: int=Field(1000,ge=1,title='PRR集計幅 [送信回数]',description='非重複区間のED平均PRR。最大1000区間になるよう必要なら幅を増やします。')
    normalize: bool=Field(False,title='終盤PRRによる補助正規化',description='オンで絶対PRRに加え、各Runの最後のtail回のED平均PRRで割った曲線も表示。最適性の証明には使いません。')
    tail: int=Field(2000,ge=1,title='終盤窓 [送信回数]',description='実際に送信された通番の最大値から各EDごとに取る窓幅。打切りは分母から除外。')


class TVTLearning(AnalysisModule):
    Options=Settings
    label='TVT：区間PRRによる学習推移'
    description='累積平均とは別に、非重複送信区間のPRRを表示します。終盤PRRによる正規化は任意の補助表示です。'

    def compute(self,c,o):
        absolute=[];normalized=[];notes=[]
        for r in c.runs:
            stop=min(c.end or r['maximum_tx_index'],r['maximum_tx_index'])
            max_bins=max(1,min(1000,1_000_000//max(1,r['ed_count'])))
            width=max(o.window,math.ceil(max(1,stop-c.start+1)/max_bins))
            counts=defaultdict(lambda:defaultdict(lambda:[0,0]));maxima=defaultdict(int);tail=defaultdict(lambda:[0,0])
            keys=['device_id','tx_index','outcome']
            for v in records(c,r,'uplinks',keys):maxima[v['device_id']]=max(maxima[v['device_id']],v['tx_index'])
            for v in records(c,r,'uplinks',keys):
                ed,tx,out=v['device_id'],v['tx_index'],v['outcome']
                if out=='Censored':continue
                ok=int(out=='Received')
                if tx>maxima[ed]-o.tail:tail[ed][0]+=ok;tail[ed][1]+=1
                if c.selected(tx):
                    n=counts[(tx-c.start)//width][ed];n[0]+=ok;n[1]+=1
            final=float(np.mean([ok/n for ok,n in tail.values()])) if tail else None
            points=[]
            for b in range(max(0,math.ceil((stop-c.start+1)/width))):
                d=counts[b];vals=[ok/n for ok,n in d.values()]
                points.append(dict(x=min(stop,c.start+(b+1)*width-1),y=float(np.mean(vals)) if vals else None,n=len(vals)))
            absolute.append(dict(label=title(c,r),points=points))
            if o.normalize:normalized.append(dict(label=title(c,r),points=[{**v,'y':v['y']/final if final and v['y'] is not None else None} for v in points]))
            notes.append(f"{r['id']}: 区間幅{width}送信、補助正規化の終盤ED平均PRR={final}。")
        views=[line('区間PRRの推移',absolute,'EDごとの送信通番','ED平均PRR',y_max=1)]
        if o.normalize:views.append(line('終盤PRRで正規化した補助曲線',normalized,'EDごとの送信通番','PRR / 終盤PRR'))
        return dict(views=views,notes=notes)


PLUGIN=TVTLearning
