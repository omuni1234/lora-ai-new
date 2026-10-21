"""Thesis asset mobility as an independent, GUI-discovered plugin."""
import math
from pydantic import Field, model_validator
from lorasim.interfaces import Options
from lorasim.mobility_environment import MobilityEnvironment
from .waypoints import Waypoints
from .stationary import Stationary


class Settings(Options):
    waypoint_count: int = Field(400,ge=1,le=10000,title='共通地点数',description='エリア内に配置する共通の有限地点集合。各EDの移動判断は独立です。')
    waypoint_seed: int = Field(0,ge=0,le=2**32-1,title='地点配置seed',description='実験seedと合わせて地点集合を決めます。')
    waypoint_height_m: float = Field(1.5,title='地点の高さ [m]',description='全ウェイポイントのZ座標。')
    speed_min_m_s: float = Field(1.,gt=0,title='最小移動速度 [m/s]',description='各出発で一様抽出する速度の下限。')
    speed_max_m_s: float = Field(5.,gt=0,title='最大移動速度 [m/s]',description='各出発で一様抽出する速度の上限。')
    pause_min_s: float = Field(3000.,ge=0,title='最小静止時間 [s]',description='初期地点と各到着地点で独立に一様抽出する時間の下限。')
    pause_max_s: float = Field(18000.,ge=0,title='最大静止時間 [s]',description='静止時間の上限。基準環境は50〜300分。')

    @model_validator(mode='after')
    def ordered(self):
        if self.speed_min_m_s>self.speed_max_m_s or self.pause_min_s>self.pause_max_s:
            raise ValueError('minimum must not exceed maximum')
        return self


class AssetWaypoint(Waypoints):
    Options=Settings
    option_visibility={}
    label='資産追跡ウェイポイント（初期静止）'
    description='共通地点で静止→別の地点へ直線移動→静止。初期状態も静止。速度・静止時間を訪問ごとに抽選します。'

    def __init__(self,initial,area,rng,environment=None,**options):
        Stationary.__init__(self,initial,area,rng)
        self.options=o=Settings.model_validate(options)
        self.rng=rng
        self.environment=environment or MobilityEnvironment()
        self.targets=self.environment.waypoints(area,o.waypoint_count,o.waypoint_seed,o.waypoint_height_m)
        self.state=int(rng.integers(len(self.targets)))
        self.last_query=0.
        self.origin=self.target=self.targets[self.state]
        self.depart=self.arrive=0.
        self.resting=True
        self.leave=float(rng.uniform(o.pause_min_s,o.pause_max_s)) if len(self.targets)>1 else math.inf

    def position(self,t):
        return self._random_position(t)

    def motion_state(self,t):
        return 'stationary' if self.resting else 'moving'

    def motion_epoch_start(self):
        """Read-only diagnostic, after position(t); never an ED observation."""
        return self.arrive if self.resting else self.depart

    @staticmethod
    def preview_cost(options,horizon):
        return 1


PLUGIN=AssetWaypoint
