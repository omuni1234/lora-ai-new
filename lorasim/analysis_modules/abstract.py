from .base import AnalysisModule
from ._common import profile, identities, table, CAUSES, CAUSE_LABELS, network_loss_notes


class Abstract(AnalysisModule):
    label='実験結果のアブストラクト（数値一覧）'
    description='ED数ごとにRunを並べ、選択器・seed・PRR・損失・公平性・消費エネルギーを比較します。割合は0〜1。未計測はN/Aです。'
    requires=frozenset({'energy','fairness'})

    def compute(self,c,o):
        views=[]
        columns=['Experiment','Run','選択アルゴリズム','移動モデル','seed','対象ED数','判定済みUL','判定未了',
                 'ED平均PRR','パケット平均PRR',*CAUSE_LABELS[1:],'Jain','Gini','消費 J/ED']
        for n in sorted({r['ed_count'] for r in c.runs}):
            rows=[]
            for r in c.runs:
                if r['ed_count']!=n: continue
                info=identities(c,r)
                rows.append([r['experiment_name'],r['id'],info['algorithm'],info['mobility'],profile(c,r).seed,
                             r['eligible_ed_count'],r['uplinks'],r['censored'],r['mean_ed_prr'],r['pooled_prr'],
                             *[r['fractions'].get(k,0) if r['uplinks'] else None for k in CAUSES[1:]],
                             r['fairness'].get('jain'),r['fairness'].get('gini'),
                             r['energy']['mean_ed_j'] if r['energy']['measured'] else None])
            views.append(table(f'{n} ED · Run別比較',columns,rows))
        return dict(views=views,notes=['同じアルゴリズム名でも設定・移動・負荷が異なる場合があります。Runと保存プロファイルを確認してください。']
                    +network_loss_notes(r['fractions'] for r in c.runs))

PLUGIN=Abstract
