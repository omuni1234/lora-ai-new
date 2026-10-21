from collections import defaultdict
from pydantic import Field
from lorasim.interfaces import Options
from .base import AnalysisModule
from ._common import table, title
from ._tvt import quality, comparison_identity, bootstrap


class Settings(Options):
    threshold: float=Field(.7,ge=0,le=1,title='PRR達成しきい値',description='seedごとの要求達成ED割合を計算する基準。')
    reference_kind: str=Field('sema',min_length=1,title='対応差の基準選択器',description='同条件でこのkindだけを使うRunを基準にします。複数の設定がある場合は曖昧として差を出しません。')
    resamples: int=Field(5000,ge=100,le=20000,title='Bootstrap反復数',description='独立seedを単位とする95% percentile区間。解析RNGは2026に固定。')


class TVTSeedComparison(AnalysisModule):
    Options=Settings
    requires=frozenset({'prr'})
    label='TVT：seed反復の区間推定・対応差'
    description='環境・移動・タイミング・実装が同じRunを区別し、seed平均と基準手法との差を集計します。EDやパケットを独立反復として数えません。'

    def compute(self,c,o):
        groups=defaultdict(list);notes=[];summary=[];paired=[]
        for r in c.runs:
            condition,method,kinds,seed=comparison_identity(c,r)
            groups[(condition,method)].append(dict(run=r,kinds=kinds,seed=seed,q=quality(r['device_prr'],o.threshold)))
        usable={}
        for key,rs in groups.items():
            label=title(c,rs[0]['run'])+' · 設定 '+key[1][:6]
            if len({r['seed'] for r in rs})!=len(rs):
                notes.append(label+': 同じseedが重複。独立反復として扱えないため区間と対応差の集計を省略しました。');continue
            usable[key]=rs
            for metric in ('prr','jain','bottom10','coverage'):
                vals=[r['q'][metric] for r in rs if r['q'][metric] is not None]
                mean,low,high=bootstrap(vals,o.resamples)
                summary.append([key[0],label,metric,len(vals),mean,low,high])
        for (condition,method),rs in usable.items():
            references=[v for (cond,_),v in usable.items() if cond==condition and v[0]['kinds']=={o.reference_kind}]
            if rs[0]['kinds']=={o.reference_kind}:continue
            if len(references)!=1:
                notes.append(title(c,rs[0]['run'])+': 同条件の基準設定が一意ではないため対応差を省略しました。');continue
            ref={v['seed']:v for v in references[0]}
            for metric in ('prr','jain','bottom10','coverage'):
                values=[ref[v['seed']]['q'][metric]-v['q'][metric] for v in rs
                    if v['seed'] in ref and v['q'][metric] is not None and ref[v['seed']]['q'][metric] is not None]
                mean,low,high=bootstrap(values,o.resamples)
                paired.append([condition,title(c,rs[0]['run']),metric,len(values),mean,low,high])
        return dict(views=[table('独立seed反復の平均と95%区間',['環境ID','設定','指標','seed数','平均','下限','上限'],summary),
            table('基準手法 − 比較手法（同じseedの対応差）',['環境ID','比較手法','指標','組数','平均差','下限','上限'],paired)],
            notes=notes+['1 seedでは区間を算出しません。多重比較補正なし。未判定・未送信EDの扱いは共通の分析規約に従います。',
                '通信条件と実効移動/送信モデル、保存されている実装hash・依存版を照合。SEMAのサイズが複数なら、比較したい設定だけを選択してください。'])


PLUGIN=TVTSeedComparison
