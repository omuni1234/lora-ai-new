import math
import numpy as np
from lorasim.interfaces import Mobility
class Stationary(Mobility):
    def __init__(self, initial, area, rng):
        self.initial = np.array(initial,dtype=float)
    def position(self,t):
        return self.initial
    def motion_state(self,t):
        return 'stationary'



from pydantic import Field
from typing import Literal
from lorasim.interfaces import Options

class Settings(Options):
    pass

Stationary.Options = Settings
Stationary.label = '静止'
Stationary.description = '初期座標に留まります。'
Stationary.preview_safe = True
PLUGIN = Stationary
