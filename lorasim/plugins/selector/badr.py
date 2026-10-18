from lorasim.config import Radio
from lorasim.interfaces import Policy
class BlindADR(Policy):
    def __init__(self, rng, sequence=None, random_phase=False, **radio):
        self.sequence = sequence or [12, 7, 10, 7, 10, 7]
        if not self.sequence or any(type(x) is not int or not 7 <= x <= 12 for x in self.sequence):
            raise ValueError("BADR sequence must contain SF7..SF12")
        self.radio = Radio.model_validate(radio)
        self.index = int(rng.integers(len(self.sequence))) if random_phase else 0
    def select(self, observation):
        sf = self.sequence[self.index % len(self.sequence)]
        self.index += 1
        return self.radio.model_copy(update={"sf":sf})
    def observe(self, feedback):
        pass



from pydantic import Field
from typing import Literal
from lorasim.interfaces import Options

class Settings(Radio):
    sequence: list[int] = Field(default_factory=lambda: [12,7,10,7,10,7], min_length=1, title="SFの巡回順序", description="SF7〜12の列。sf欄よりこの列が優先されます。")
    random_phase: bool = Field(False, title="巡回の開始位置をランダム化", description="各EDが列のどの位置から開始するかを一様乱数で選びます。")

BlindADR.Options = Settings
BlindADR.label = 'BADR（Blind ADR）'
BlindADR.description = 'SF列を繰り返す比較モデル。列と比率は実験条件です。'
BlindADR.preview_safe = True
PLUGIN = BlindADR
