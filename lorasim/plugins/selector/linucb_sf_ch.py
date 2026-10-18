"""Explicit SF×CH extension; each pair has its own linear bandit statistics."""
from pydantic import create_model
from lorasim.interfaces import Options
from .linucb_sf import LinUCBSF, Settings as OriginalSettings


Settings = create_model('LinUCBSFCHSettings', __base__=Options,
    **{k:(v.annotation,v) for k,v in OriginalSettings.model_fields.items() if k not in {'initial','frequencies_hz'}})


class LinUCBSFCH(LinUCBSF):
    Options = Settings
    uses_action_space = True
    label = 'LinUCB-SF×CH（比較用拡張）'
    description = '実験共通のSF×CHを腕として個別に学習します。SNRゲートと6次元Contextは原手法を継承。TP・CRは共通設定で固定します。'
    def __init__(self,rng,action_space,**options):
        values = Settings.model_validate(options).model_dump()
        actions = action_space.radios()
        initial = next(a for a in actions if a.sf == max(action_space.spreading_factors))
        o = OriginalSettings(initial=initial,frequencies_hz=action_space.frequencies_hz,**values)
        self._initialize(rng,o,actions=actions)


PLUGIN = LinUCBSFCH
