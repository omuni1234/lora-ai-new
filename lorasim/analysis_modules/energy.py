from .base import AnalysisModule, bars
class Energy(AnalysisModule):
    label='消費エネルギー'
    description='送信番号区間に帰属するエネルギー。起動時の消費は別計上。MCUは左側で切り替えます。'
    requires=frozenset({'energy'})
    sources=['https://arxiv.org/html/1801.00522']
    def compute(self,c,o):
        return dict(views=[bars('平均消費エネルギー',c.runs,[r['energy']['mean_ed_j'] if r['energy']['measured'] else None for r in c.runs],'J / ED')])
PLUGIN=Energy
