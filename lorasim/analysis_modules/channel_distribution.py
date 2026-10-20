from collections import Counter, defaultdict
import math
import numpy as np
from pydantic import Field
from lorasim.interfaces import Options
from .base import AnalysisModule, bars
from ._common import profile, title, records, table


def distribution(counts):
    total=sum(counts.values())
    return {k:v/total for k,v in counts.items() if v} if total else {}


def kl(p,q):
    if not p or not q: return None
    if any(q.get(k,0)==0 for k in p): return None  # infinity is reported explicitly
    return max(0.,sum(v*math.log(v/q[k]) for k,v in p.items()))


def channel_counts(c,r):
    def scan():
        per_ed=defaultdict(Counter)
        for direction,ed,index,ch in records(c,r,'transmissions',['direction','device_id','tx_index','frequency_hz']):
            if direction=='UL' and c.selected(index) and ch is not None: per_ed[ed][ch]+=1
        return per_ed
    return c.cached(('channel-counts',r['id']),scan)


class Settings(Options):
    reference_channels_hz: list[int] | None=Field(None,min_length=1,max_length=64,title='基準CH集合 [Hz]（未指定＝各プロファイルの全体行動空間）',description='この集合上の一様分布UとのKL(P||U)。EDごとの制限集合ではなく共通基準です。')


class ChannelDistribution(AnalysisModule):
    Options=Settings
    label='チャネル選択分布のKL Divergence'
    description='一様分布からの偏りをKLで比較します。ED別KLの平均と全送信をまとめたKLを併記し、ED間の偏りの相殺を見分けます。小さいことが高PRRを意味するわけではありません。'
    sources=['https://docs.scipy.org/doc/scipy/reference/generated/scipy.special.rel_entr.html']
    def compute(self,c,o):
        rows=[]; values=[]; details=[]; notes=[]
        for r in c.runs:
            channels=sorted(set(o.reference_channels_hz or profile(c,r).action_space.frequencies_hz))
            q={ch:1/len(channels) for ch in channels}
            counts=channel_counts(c,r); pooled=Counter()
            for row in counts.values(): pooled.update(row)
            kls=[kl(distribution(row),q) for row in counts.values()]
            infinite=sum(v is None for v in kls)
            mean=float(np.mean(kls)) if kls and not infinite else None
            p=distribution(pooled); overall=kl(p,q)
            effective=math.exp(-sum(v*math.log(v) for v in p.values())) if p else None
            rows.append([r['experiment_name'],r['id'],title(c,r),len(counts),sum(pooled.values()),', '.join(f'{ch/1e6:g}' for ch in channels),
                         '∞（基準集合外のCH）' if infinite else mean,
                         '∞（基準集合外のCH）' if p and overall is None else overall,effective])
            values.append(mean)
            for ch in sorted(set(channels)|set(pooled)):
                details.append([r['id'],ch/1e6,pooled[ch],p.get(ch,0),q.get(ch,0)])
            if infinite: notes.append(f"{r['id']}: 基準集合外のCHを選択したEDが {infinite} 台あり、KLは無限大です。グラフ上ではN/Aとして表示。")
        return dict(views=[bars('ED別KLの平均（自然対数）',c.runs,values,'nat'),
                           table('KLとチャネル利用の広がり',['Experiment','Run','選択器・条件','対象ED','UL数','基準CH [MHz]','平均ED KL','全送信KL','全送信の有効CH数 exp(H)'],rows),
                           table('チャネル選択分布',['Run','CH [MHz]','UL数','選択確率 P','基準確率 U'],details)],notes=notes)
PLUGIN=ChannelDistribution
