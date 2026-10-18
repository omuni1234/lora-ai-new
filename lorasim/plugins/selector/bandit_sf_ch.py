"""Existing ACK/energy epsilon-greedy baseline on the common action space."""
from pydantic import create_model
from lorasim.interfaces import Options
from .bandit import Bandit, Settings as OriginalSettings

Settings = create_model('BanditSFCHSettings',__base__=Options,
    **{k:(v.annotation,v) for k,v in OriginalSettings.model_fields.items() if k!='actions'})


class BanditSFCH(Bandit):
    Options = Settings
    uses_action_space = True
    requires_confirmed = True
    label = 'ACKバンディット SF×CH（比較用拡張）'
    description = '従来のACK・エネルギー代理報酬と標本平均更新を、実験共通のSF×CHに適用します。SEMA内部の累積報酬型ε-greedyとは別モデルです。'
    def __init__(self,rng,action_space,**options):
        super().__init__(rng,action_space=action_space,**Settings.model_validate(options).model_dump())


PLUGIN = BanditSFCH
