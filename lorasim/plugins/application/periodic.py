"""Periodic application with independently configurable first-generation time."""
import math
from typing import Literal
from pydantic import Field, model_validator
from lorasim.interfaces import Application, Options


class StartTime(Options):
    mode: Literal['fixed','uniform'] = Field('uniform', title='開始時刻の決め方', description='fixedは指定時刻、uniformは下限以上・上限未満の一様乱数です。各EDに独立した乱数を使います。')
    at_s: float = Field(0., ge=0, title='開始時刻 [s]', description='fixedの場合に用いる、シミュレーション開始からの時間です。')
    min_s: float = Field(0., ge=0, title='開始時刻の下限 [s]', description='uniformの場合の一様分布の下限です。')
    max_s: float = Field(60., ge=0, title='開始時刻の上限 [s]', description='uniformの場合の上限です。送信間隔とは独立して設定できます。')
    @model_validator(mode='after')
    def ordered(self):
        if self.mode == 'uniform' and self.max_s < self.min_s:
            raise ValueError('start.max_s must be >= start.min_s')
        return self


class Settings(Options):
    interval_s: float = Field(60., gt=0, title='送信生成間隔 [s]', description='前回生成時刻から次の生成までの基本間隔。MAC処理やDuty cycleによって実送信は遅れる場合があります。')
    start: StartTime | None = Field(None, title='初回送信時刻の分布', description='設定すると旧形式の開始時刻・ランダム位相より優先します。各EDグループで独立した分布を指定できます。')
    first_s: float = Field(0., ge=0, title='開始時刻の基準 [s]（旧形式）', description='初回送信時刻の分布を未設定の場合だけ使用します。')
    random_phase: bool = Field(False, title='開始を1周期内でランダム化（旧形式）', description='分布を未設定の場合、開始時刻の基準に一様乱数 [0, 送信間隔) を加算します。')
    jitter_s: float = Field(0., ge=0, title='周期の揺らぎ [s]', description='初回以降の間隔に [-揺らぎ,+揺らぎ) の一様乱数を加算します。送信間隔未満にしてください。')
    @model_validator(mode='after')
    def jitter(self):
        if self.jitter_s >= self.interval_s:
            raise ValueError('jitter_s must be less than interval_s')
        return self


class Periodic(Application):
    Options = Settings
    label = '周期送信'
    description = '送信間隔と初回の生成時刻を別々に指定できます。'
    preview_safe = True
    def __init__(self, rng, interval_s=60., first_s=0., random_phase=False, jitter_s=0., start=None):
        opts = Settings(interval_s=interval_s,first_s=first_s,random_phase=random_phase,jitter_s=jitter_s,start=start)
        self.rng, self.interval, self.jitter = rng, opts.interval_s, opts.jitter_s
        if opts.start is not None:
            s = opts.start
            self.first = s.at_s if s.mode == 'fixed' else float(rng.uniform(s.min_s,s.max_s))
        else:
            self.first = first_s + (rng.uniform(0,interval_s) if random_phase else 0.)
    def next_time(self, observation, previous_generation_s):
        if previous_generation_s is None:
            return self.first
        return max(observation.time_s + 1e-9, previous_generation_s + self.interval + self.rng.uniform(-self.jitter,self.jitter))

PLUGIN = Periodic
