"""Timed waypoints and constant-speed routes with per-leg/per-visit randomness."""
import math
import numpy as np
from pydantic import Field, model_validator
from typing import Literal
from lorasim.interfaces import Options
from .stationary import Stationary


class Settings(Options):
    mode: Literal['timed','route'] = Field('timed', title='経路の指定方式', description='timedは従来の時刻指定。routeは座標・移動速度・静止時間から到着時刻を計算します。')
    points: list[tuple[float,float,float,float]] = Field(default_factory=lambda: [(0,100,100,1.5),(300,1800,1800,1.5)], min_length=1, max_length=10000, title='時刻指定の経由地点 [s,x,y,z]', description='timed用。時刻は0以上の厳密な昇順。先頭が0より大きい場合は初期位置から移動します。')
    destinations: list[tuple[float,float,float]] = Field(default_factory=lambda: [(100,100,1.5),(1800,1800,1.5)], min_length=1, max_length=10000, title='速度指定の経由地点 [x,y,z m]', description='route用。初期位置から順に訪問します。ランダム地点数が正なら自動生成した地点を使います。')
    random_count: int = Field(0, ge=0, le=10000, title='一様ランダムの地点数', description='route用。正ならエリア内のx,yを一様生成します。高さはED初期位置を引き継ぎます。0なら指定座標を使います。')
    random_seed: int | None = Field(None, ge=0, le=2**32-1, title='経路専用の乱数シード', description='未指定なら実験シードからED別の乱数を生成。同じ値を指定したEDは同じ地点と乱数列を共有します。')
    speed_m_s: float = Field(1., gt=0, title='移動速度 [m/s]', description='route用。各区間をこの速度で移動します。')
    speed_jitter_m_s: float = Field(0., ge=0, title='区間速度の揺らぎ [±m/s]', description='区間の出発ごとに、この範囲の一様乱数を移動速度へ加算します。速度より小さく指定してください。')
    pause_mode: Literal['fixed','uniform'] = Field('fixed', title='地点ごとの静止時間の割当方式', description='固定値、または地点ごとに一度だけ割り当てる一様乱数。')
    pause_s: float = Field(10., ge=0, title='静止時間 [s]', description='pause_mode=fixedで各地点に割り当てる静止時間。')
    pause_min_s: float = Field(0., ge=0, title='静止時間の最小値 [s]', description='pause_mode=uniformで地点へ割り当てる一様乱数の下限。')
    pause_max_s: float = Field(20., ge=0, title='静止時間の最大値 [s]', description='pause_mode=uniformで地点へ割り当てる一様乱数の上限。')
    pause_jitter_s: float = Field(0., ge=0, title='到着ごとの静止時間の揺らぎ [±s]', description='地点に到着するたびに一様乱数を加算。負の静止時間は0に切り上げます。周回時にも再抽選します。')
    repeat: bool = Field(False, title='経路を周回', description='最後の地点から最初の地点へ戻り、同じ地点列を繰り返します。オフでは最後の地点で静止します。')
    @model_validator(mode='after')
    def valid(self):
        if self.speed_jitter_m_s >= self.speed_m_s:
            raise ValueError('speed jitter must be smaller than speed')
        if self.pause_max_s < self.pause_min_s:
            raise ValueError('pause_max_s must be >= pause_min_s')
        a = np.array(self.points)
        if a[0,0] < 0 or (np.diff(a[:,0]) <= 0).any():
            raise ValueError('waypoint times must strictly increase from >=0')
        return self


class Waypoints(Stationary):
    Options = Settings
    label = 'ウェイポイント'
    description = '時刻指定、または速度・ランダム地点・地点ごとの静止時間を指定できる経路モデル。'
    preview_safe = True
    def __init__(self, initial, area, rng, points=None, **options):
        super().__init__(initial,area,rng)
        if points is not None:
            options['points'] = points
        self.options = o = Settings.model_validate(options)
        self.rng = np.random.default_rng(o.random_seed) if o.random_seed is not None else rng
        self.last_query = 0.
        if o.mode == 'timed':
            self.a = np.asarray(o.points,dtype=float)
            if self.a[0,0] > 0:
                self.a = np.vstack([[0,*initial],self.a])
        else:
            self.targets = np.array(o.destinations,dtype=float)
            if o.random_count:
                self.targets = np.column_stack((self.rng.uniform([0,0],area,size=(o.random_count,2)),np.full(o.random_count,initial[2])))
            if ((self.targets[:,:2]<0) | (self.targets[:,:2]>area)).any():
                raise ValueError('route destinations must be inside the area')
            self.pauses = self.rng.uniform(o.pause_min_s,o.pause_max_s,len(self.targets)) if o.pause_mode=='uniform' else np.full(len(self.targets),o.pause_s)
            if o.repeat and np.all(self.targets == self.targets[0]) and not np.any(self.pauses) and not o.pause_jitter_s:
                raise ValueError('a repeated route needs positive travel or pause duration')
            self.a = self._cycle(0.,np.array(initial))
    def _cycle(self, t, origin):
        o = self.options
        points = [(t,*origin)]
        for target,pause in zip(self.targets,self.pauses):
            speed = o.speed_m_s + (self.rng.uniform(-o.speed_jitter_m_s,o.speed_jitter_m_s) if o.speed_jitter_m_s else 0.)
            travel = math.dist(origin,target)/speed
            if travel > 0:
                t += travel
                points.append((t,*target))
            dwell = max(0.,pause + (self.rng.uniform(-o.pause_jitter_s,o.pause_jitter_s) if o.pause_jitter_s else 0.))
            if dwell > 0:
                t += dwell
                points.append((t,*target))
            origin = target
        return np.asarray(points,dtype=float)
    def position(self,t):
        if self.options.mode == 'route':
            if t < self.last_query:
                raise ValueError('route mobility queries must be chronological')
            self.last_query = t
            if self.options.repeat:
                iterations = 0
                while t > self.a[-1,0]:
                    previous = self.a[-1]
                    self.a = self._cycle(previous[0],previous[1:])
                    iterations += 1
                    # Clipped jitter can legitimately make one visit instantaneous.
                    # Draw subsequent visits, bounded against non-progressing routes.
                    if iterations > 100000:
                        raise ValueError('route exceeds per-query cycle budget')
        return np.array([np.interp(t,self.a[:,0],self.a[:,j]) for j in range(1,4)])
    @staticmethod
    def preview_cost(options,horizon):
        n = options.get('random_count',0) or len(options.get('destinations',[(0,0,0)]))
        # Repeated routes can require many random draws; conservatively budget per second.
        return max(n,horizon*n) if options.get('repeat') else n


PLUGIN = Waypoints
