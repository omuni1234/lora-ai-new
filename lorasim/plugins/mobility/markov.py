"""Point-based Markov chain embedded in continuous, constant-speed travel.

The transition matrix applies at arrival-relative decision ticks while at a point.
Self transitions give geometric dwell times; no decisions/teleports during travel.
"""
import math
import numpy as np
from pydantic import Field, model_validator
from lorasim.interfaces import Mobility, Options


class Settings(Options):
    points: list[tuple[float,float,float]] = Field(default_factory=lambda:[(0,0,1.5),(100,0,1.5)], min_length=1, max_length=1000, title='拠点の座標 [x,y,z m]', description='行列の行・列はこの順序です。EDの初期位置から指定の初期拠点まで移動して開始します。')
    random_count: int = Field(0, ge=0, le=1000, title='一様ランダムの拠点数', description='正ならエリア内にこの個数の拠点を生成し、pointsを置き換えます。高さはED初期位置です。')
    random_seed: int | None = Field(None, ge=0, le=2**32-1, title='移動専用の乱数シード', description='未指定ならED別の実験乱数。同一値なら同一拠点集合・乱数列になります。')
    transition_matrix: list[list[float]] = Field(default_factory=list, title='遷移確率行列 P', description='行iから列jへ進む確率。正方、各行合計1、非負。空なら自己滞在確率と他拠点への一様遷移から生成します。')
    stay_probability: float = Field(.8, ge=0, le=1, title='既定行列の自己滞在確率', description='行列が空の場合のPii。残りを他拠点に等分。1なら吸収状態で永続的に静止します。')
    decision_interval_s: float = Field(60., gt=0, title='拠点での遷移判定間隔 [s]', description='到着からこの間隔でマルコフ遷移。自己遷移を含め滞在時間は幾何分布×間隔で、平均は間隔/(1−Pii)。移動中は判定しません。')
    initial_point: int | None = Field(None, ge=0, title='最初に向かう拠点番号', description='0始まり。未指定ならED初期位置から最も近い拠点。位置を瞬間移動させません。')
    speed_m_s: float = Field(1., gt=0, title='拠点間の移動速度 [m/s]', description='拠点間を直線移動する速度です。')
    speed_jitter_m_s: float = Field(0., ge=0, title='区間速度の揺らぎ [±m/s]', description='出発ごとに一様乱数を速度へ加算。速度より小さく指定します。')
    @model_validator(mode='after')
    def valid(self):
        n = self.random_count or len(self.points)
        if self.initial_point is not None and self.initial_point >= n:
            raise ValueError('initial_point outside point set')
        if self.speed_jitter_m_s >= self.speed_m_s:
            raise ValueError('speed jitter must be smaller than speed')
        if self.transition_matrix:
            p = self.transition_matrix
            if len(p)!=n or any(len(row)!=n for row in p):
                raise ValueError('transition_matrix must be point_count × point_count')
            if any(any(v<0 for v in row) or not math.isclose(sum(row),1.,rel_tol=0,abs_tol=1e-12) for row in p):
                raise ValueError('transition probabilities must be nonnegative with row sum 1')
        return self


class Markov(Mobility):
    Options = Settings
    preview_safe = True
    label = 'マルコフ拠点移動'
    description = '拠点間の遷移確率と自己遷移で移動先・滞在時間を生成。拠点間は連続的に移動します。'
    def __init__(self,initial,area,rng,**options):
        self.options = o = Settings.model_validate(options)
        self.rng = np.random.default_rng(o.random_seed) if o.random_seed is not None else rng
        self.points = np.asarray(o.points,dtype=float)
        if o.random_count:
            self.points = np.column_stack((self.rng.uniform([0,0],area,size=(o.random_count,2)),np.full(o.random_count,initial[2])))
        if ((self.points[:,:2]<0)|(self.points[:,:2]>area)).any():
            raise ValueError('Markov points must be inside the area')
        n = len(self.points)
        if o.transition_matrix:
            self.matrix = np.array(o.transition_matrix)
        else:
            self.matrix = np.full((n,n),(1-o.stay_probability)/(n-1)) if n>1 else np.ones((1,1))
            if n>1: np.fill_diagonal(self.matrix,o.stay_probability)
        self.matrix /= self.matrix.sum(axis=1,keepdims=True)
        self.state = o.initial_point if o.initial_point is not None else int(np.argmin(np.linalg.norm(self.points-np.asarray(initial),axis=1)))
        self.last_query = 0.
        self._leg(0.,np.asarray(initial,dtype=float))
    def _leg(self,t,origin):
        o = self.options
        speed = o.speed_m_s+(self.rng.uniform(-o.speed_jitter_m_s,o.speed_jitter_m_s) if o.speed_jitter_m_s else 0.)
        self.origin,self.target,self.depart = origin.copy(),self.points[self.state],t
        self.arrive = t+math.dist(origin,self.target)/speed
        probability = 1-self.matrix[self.state,self.state]
        ticks = self.rng.geometric(probability) if probability>0 else math.inf
        self.leave = self.arrive+ticks*o.decision_interval_s
    def position(self,t):
        if t < self.last_query:
            raise ValueError('Markov mobility queries must be chronological')
        self.last_query = t
        steps = 0
        while t >= self.leave:
            weights = self.matrix[self.state].copy()
            weights[self.state] = 0
            weights /= weights.sum()
            self.state = int(self.rng.choice(len(weights),p=weights))
            self._leg(self.leave,self.target)
            steps += 1
            if steps>100000:
                raise ValueError('Markov mobility exceeds per-query transition budget')
        if t >= self.arrive: return self.target.copy()
        fraction = (t-self.depart)/(self.arrive-self.depart)
        return self.origin+fraction*(self.target-self.origin)
    @staticmethod
    def preview_cost(options,horizon):
        return horizon/options.get('decision_interval_s',60.)


PLUGIN = Markov
