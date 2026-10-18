from typing import Literal
from pydantic import Field
from lorasim.interfaces import Options
from .base import AnalysisModule, bars
class Settings(Options):
    metric: Literal['jain','gini','min_max','cv']=Field('jain',title='公平性指標',description='全EDがゼロ成功の場合は未定義。PRRと併せて比較してください。')
class Fairness(AnalysisModule):
    requires=frozenset({"fairness"})
    Options=Settings
    label='PRRのフェアネス'
    description='ED別PRRのJain、Gini、最小/最大、変動係数。未送信EDは除外します。'
    sources=['https://arxiv.org/html/1801.00522']
    def compute(self,c,o): return dict(views=[bars(self.label,c.runs,[r['fairness'][o.metric] for r in c.runs],o.metric)])
PLUGIN=Fairness
