import numpy as np
from lorasim.config import Radio
from lorasim.interfaces import Policy
class Fixed(Policy):
    def __init__(self, rng, **options):
        self.radio = Radio.model_validate(options)
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
