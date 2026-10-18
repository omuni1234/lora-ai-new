from .base import AnalysisModule, stacked
class Outcomes(AnalysisModule):
    label='ED数別の成功率・損失要因'
    description='各EDの判定済みULを分母とし、EDを等しく平均します。'
    def compute(self,c,o):
        rows=c.result['by_ed_count']
        return dict(views=[stacked(self.label,[r['fractions'] for r in rows],[f"{r['ed_count']} / {r['experiment_name']}" for r in rows],'ED数 / Experiment')])
PLUGIN=Outcomes
