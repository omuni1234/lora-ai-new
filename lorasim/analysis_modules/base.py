"""Public, simulation-independent analysis extension contract."""
from abc import ABC, abstractmethod
from lorasim.interfaces import Options


class AnalysisModule(ABC):
    Options=Options
    label=''
    description=''
    sources=[]
    requires=frozenset()
    @abstractmethod
    def compute(self,context,options):
        """Return {views: [...], ...JSON data...}; see docs/ANALYSIS_PLUGINS.md."""


def bars(title,runs,values,unit=''):
    return dict(type='bars',title=title,unit=unit,items=[dict(label=f"{r['experiment_name']} / {r['ed_count']} ED / {r['id'][:6]}",value=v) for r,v in zip(runs,values)])


def stacked(title,items,labels,unit):
    return dict(type='stacked',title=title,items=items,labels=labels,unit=unit)
