"""Original Exp3 update with the experiment's common SF×CH arms."""
from pydantic import create_model
from lorasim.interfaces import Options
from .lora_mab import LoRaMAB, Settings as OriginalSettings


Settings = create_model('LoRaMABSFCHSettings', __base__=Options,
    **{k:(v.annotation,v) for k,v in OriginalSettings.model_fields.items() if k in {'horizon','gamma'}})


class LoRaMABSFCH(LoRaMAB):
    Options = Settings
    uses_action_space = True
    label = 'LoRa-MAB SF×CH（比較用拡張）'
    description = '原論文のACK報酬・Exp3更新を保ち、実験共通のSF×CH集合を使用します。TP・CRは固定です。'
    def __init__(self,rng,action_space,**options):
        super().__init__(rng,spreading_factors=action_space.spreading_factors,
            frequencies_hz=action_space.frequencies_hz,powers_dbm=[action_space.tp_dbm],
            cr=action_space.cr,**Settings.model_validate(options).model_dump())


PLUGIN = LoRaMABSFCH
