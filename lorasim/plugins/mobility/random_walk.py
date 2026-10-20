import math
import numpy as np
from lorasim.interfaces import Mobility
from .stationary import Stationary
class RandomWalk(Stationary):
    """Time-based, constant-speed 2D random walk with reflecting area boundaries."""
    def __init__(self, initial, area, rng, speed_m_s=1., step_s=10.):
        super().__init__(initial,area,rng)
        if not math.isfinite(speed_m_s) or not math.isfinite(step_s) or speed_m_s < 0 or step_s <= 0:
            raise ValueError("invalid random-walk parameters")
        if any(initial[i]<0 or initial[i]>area[i] for i in range(2)):
            raise ValueError("random-walk initial position outside area")
        self.area, self.rng, self.speed, self.step = np.array(area),rng,speed_m_s,step_s
        self.t = 0.
        self.v = self.velocity()
    def velocity(self):
        theta = self.rng.uniform(0,2*math.pi)
        return self.speed*np.array([math.cos(theta),math.sin(theta)])
    def folded(self,dt):
        p = self.initial.copy()
        q = (p[:2]+self.v*dt) % (2*self.area)
        p[:2] = np.where(q <= self.area,q,2*self.area-q)
        return p
    def position(self,t):
        if t < self.t - 1e-9:
            raise ValueError("mobility queries must be chronological")
        while t >= self.t + self.step:
            self.initial = self.folded(self.step)
            self.t += self.step
            self.v = self.velocity()
        return self.folded(t-self.t)
    def motion_state(self,t):
        return 'stationary' if self.speed==0 else 'moving'



from pydantic import Field
from typing import Literal
from lorasim.interfaces import Options

class Settings(Options):
    speed_m_s: float = Field(1., ge=0, title="移動速度 [m/s]", description="EDの移動速度。0なら静止します。")
    step_s: float = Field(10., gt=0, title="方向変更の間隔 [s]", description="このシミュレーション時間ごとに一様乱数で方向を選びます。")

RandomWalk.Options = Settings
RandomWalk.label = 'ランダムウォーク'
RandomWalk.description = '一定時間ごとに方向を選び、エリア境界で反射します。'
RandomWalk.preview_safe = True
PLUGIN = RandomWalk

# Upper bound on direction changes used by the independent configuration preview.
RandomWalk.preview_cost = staticmethod(lambda options,horizon: horizon/options.get('step_s',10.))
