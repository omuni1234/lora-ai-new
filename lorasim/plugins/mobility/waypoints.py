import math
import numpy as np
from lorasim.interfaces import Mobility
from .stationary import Stationary
class Waypoints(Stationary):
    def __init__(self, initial, area, rng, points):
        super().__init__(initial,area,rng)
        a = np.asarray(points,dtype=float)
        if a.ndim != 2 or a.shape[1] != 4 or len(a) == 0 or not np.isfinite(a).all():
            raise ValueError("waypoints are [time_s,x_m,y_m,z_m]")
        if a[0,0] < 0 or (np.diff(a[:,0]) <= 0).any():
            raise ValueError("waypoint times must strictly increase from >=0")
        if a[0,0] > 0:
            a = np.vstack([[0,*initial],a])
        self.a = a
    def position(self,t):
        return np.array([np.interp(t,self.a[:,0],self.a[:,j]) for j in range(1,4)])



from pydantic import Field
from typing import Literal
from lorasim.interfaces import Options

class Settings(Options):
    points: list[tuple[float,float,float,float]] = Field(default_factory=lambda: [(0,100,100,1.5),(300,1800,1800,1.5)], min_length=1, title="経由地点 [時刻s, x m, y m, 高さm]", description="時刻は0以上の昇順。先頭時刻が0より大きい場合は初期位置を時刻0に挿入します。")

Waypoints.Options = Settings
Waypoints.label = 'ウェイポイント'
Waypoints.description = '指定時刻の座標間を線形補間します。最終地点以降は静止します。'
Waypoints.preview_safe = True
PLUGIN = Waypoints
