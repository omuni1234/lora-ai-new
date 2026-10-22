from .base import AnalysisModule, stacked
from ._common import network_loss_notes
class Convergence(AnalysisModule):
    label='送信回数ごとの収束（累積）'
    description='時点tは1〜t回の全履歴。表示開始回数より前の履歴も含みます。'
    requires=frozenset({'series'})
    def compute(self,c,o):
        return dict(views=[{**stacked(f"{r['experiment_name']} / {r['ed_count']} ED",[p['fractions'] for p in r['series']],[p['end'] for p in r['series']],'累積の終点 t（1〜t回）'),'type':'stacked_area'} for r in c.result['by_time']],
                    notes=network_loss_notes(p['fractions'] for r in c.result['by_time'] for p in r['series']))
PLUGIN=Convergence
