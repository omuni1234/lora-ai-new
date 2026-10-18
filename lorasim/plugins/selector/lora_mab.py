"""LoRa-MAB, Section IV-A / Algorithm 1 (PDF p.5, printed p.59)."""
from itertools import product
import math
import numpy as np
from pydantic import Field
from lorasim.config import Radio
from lorasim.interfaces import Options, Policy


class Settings(Options):
    spreading_factors: list[int] = Field(default_factory=lambda:list(range(7,13)), min_length=1, max_length=6, title='候補SF', description='論文の腕はSF・チャネル・送信電力の直積です。SF7〜12。')
    powers_dbm: list[float] = Field(default_factory=lambda:[14.], min_length=1, max_length=48, title='候補送信電力 [dBm]', description='論文の実験3は8,11,14 dBm。各サブバンドの最大送信電力以下で指定してください。')
    frequencies_hz: list[int] = Field(default_factory=lambda:[868100000], min_length=1, max_length=64, title='候補チャネル [Hz]', description='論文の実験2は3チャネル。全候補を許可ULサブバンドとGW受信設定に含めてください。')
    cr: int = Field(1, ge=1, le=4, title='符号化率 CR', description='1〜4は4/5〜4/8。論文の学習対象はSF・TP・チャネルです。')
    horizon: int = Field(10000000, ge=1, title='学習ホライズン T', description='γ=min(1,sqrt(K log K/((e−1)T))) のT。論文の実験値は10^7です。')
    gamma: float | None = Field(None, gt=0, le=1, title='探索率 γ の上書き', description='未指定なら論文式から計算。指定値を用いた場合は原論文のホライズン設定からの変更として扱います。')


class LoRaMAB(Policy):
    Options = Settings
    label = 'LoRa-MAB / Exp3'
    description = 'ACKだけで学習する分散型Exp3。SF・周波数・送信電力の直積から選択します。'
    preview_safe = True
    requires_confirmed = True
    def __init__(self, rng, **options):
        self.options = o = Settings.model_validate(options)
        self.rng = rng
        self.actions = [Radio(sf=sf,tp_dbm=tp,frequency_hz=freq,cr=o.cr) for sf,freq,tp in product(o.spreading_factors,o.frequencies_hz,o.powers_dbm)]
        if len({(r.sf,r.frequency_hz,r.tp_dbm) for r in self.actions}) != len(self.actions):
            raise ValueError('LoRa-MAB action candidates must be unique')
        self.k = len(self.actions)
        self.gamma = o.gamma if o.gamma is not None else min(1.,math.sqrt(self.k*math.log(self.k)/((math.e-1)*o.horizon)))
        self.log_weights = np.zeros(self.k)
        self.probabilities = np.full(self.k,1/self.k)
        self.chosen = None
        self.updates = 0
    def select(self, observation):
        self.chosen = int(self.rng.choice(self.k,p=self.probabilities))
        self.selected_probability = float(self.probabilities[self.chosen])
        return self.actions[self.chosen].model_copy()
    def observe(self, feedback):
        if self.chosen is None or feedback.ack is None:
            return
        if feedback.ack:
            self.log_weights[self.chosen] += self.gamma/(self.k*self.selected_probability)
            # Common shift changes neither weights' ratios nor Exp3 probabilities.
            self.log_weights -= self.log_weights.max()
            weights = np.exp(self.log_weights)
            self.probabilities = (1-self.gamma)*weights/weights.sum()+self.gamma/self.k
        self.updates += 1
        self.chosen = None
    def snapshot(self):
        return dict(gamma=self.gamma,updates=self.updates,log_weights=self.log_weights.tolist(),probabilities=self.probabilities.tolist())


PLUGIN = LoRaMAB
