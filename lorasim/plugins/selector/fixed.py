import numpy as np
from lorasim.config import Radio
from lorasim.interfaces import Policy
class Fixed(Policy):
    accepts_action_space = True
    space_options = ["sf", "frequency_hz", "tp_dbm", "cr"]
    def __init__(self, rng, action_space=None, **options):
        self.radio = Radio.model_validate(options)
        if action_space is not None:
            first = action_space.radios()[0]
            self.radio = self.radio.model_copy(update={k:getattr(first,k) for k in ("sf","frequency_hz","tp_dbm","cr")})
    def select(self, observation):
        return self.radio.model_copy()
    def observe(self, feedback):
        pass



from pydantic import Field
from typing import Literal
from lorasim.interfaces import Options

class Settings(Radio):
    pass

Fixed.Options = Settings
Fixed.label = '固定設定'
Fixed.description = '毎回同じ送信設定を返します。'
Fixed.preview_safe = True
PLUGIN = Fixed
