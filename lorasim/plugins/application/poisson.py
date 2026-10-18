import math
from lorasim.interfaces import Application
class Poisson(Application):
    def __init__(self, rng, mean_s=60.):
        if not math.isfinite(mean_s) or mean_s <= 0:
            raise ValueError("mean_s must be positive")
        self.rng, self.mean = rng, mean_s
    def next_time(self, observation, previous_generation_s):
        return observation.time_s + max(1e-9,float(self.rng.exponential(self.mean)))



from pydantic import Field
from typing import Literal
from lorasim.interfaces import Options

class Settings(Options):
    mean_s: float = Field(60., gt=0, title="平均待ち時間 [s]", description="初回および前回処理完了時点から次の生成までの指数分布の平均です。")

Poisson.Options = Settings
Poisson.label = 'ポアソン送信'
Poisson.description = '指数分布の待ち時間で次の送信を生成します。'
Poisson.preview_safe = True
PLUGIN = Poisson
