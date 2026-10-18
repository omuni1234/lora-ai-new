"""Explicit research extension, NOT the LinUCB-SF paper's six-arm algorithm."""
from pydantic import Field, model_validator
from .linucb_sf import LinUCBSF, Settings as SFSettings


class Settings(SFSettings):
    powers_dbm: list[float] = Field(default_factory=lambda:[8.,11.,14.], min_length=1, max_length=48, title='候補送信電力 [dBm]', description='原論文にはないSF×TPの拡張。受信済みULのTPから候補TPとの差分をSNRへ加えて適用可能性を判定します。')
    @model_validator(mode='after')
    def valid_powers(self):
        if len(set(self.powers_dbm)) != len(self.powers_dbm) or any(x < -20 or x > 27 for x in self.powers_dbm):
            raise ValueError('unique powers in -20..27 dBm required')
        if self.initial.tp_dbm not in self.powers_dbm:
            raise ValueError('initial TP must belong to powers_dbm')
        return self


class LinUCBSFTP(LinUCBSF):
    accepts_action_space = False
    Options = Settings
    label = 'LinUCB-SF×TP（研究拡張）'
    description = '原論文のSF選択をSF×TPの腕へ拡張。TP差分によるSNR予測を使用するため、原論文の再現とは区別してください。'
    def __init__(self,rng,**options):
        o = Settings.model_validate(options)
        self._initialize(rng,o,o.powers_dbm)


PLUGIN = LinUCBSFTP
