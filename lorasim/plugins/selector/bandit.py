import numpy as np
from lorasim.config import Radio
from lorasim.interfaces import Policy
from .random import RandomPolicy
class Bandit(RandomPolicy):
    """Per-ED epsilon-greedy bandit. Rewards use only ACK and local energy proxy."""
    def __init__(self, rng, actions=None, epsilon=.1, energy_weight=.02, action_space=None):
        super().__init__(rng, actions, action_space)
        if not 0 <= epsilon <= 1 or energy_weight < 0:
            raise ValueError("invalid bandit hyperparameters")
        self.epsilon, self.energy_weight = epsilon, energy_weight
        self.q, self.n = np.zeros(len(self.actions)), np.zeros(len(self.actions))
        self.selected = 0
    def select(self, observation):
        self.selected = int(self.rng.integers(len(self.actions))) if self.rng.random() < self.epsilon else int(np.argmax(self.q))
        return self.actions[self.selected].model_copy()
    def observe(self, feedback):
        if feedback.ack is None:
            return
        reward = float(feedback.ack) - self.energy_weight * feedback.airtime_s * 10**(feedback.tx_power_dbm/10)
        i = self.selected
        self.n[i] += 1
        self.q[i] += (reward-self.q[i])/self.n[i]



from pydantic import Field
from typing import Literal
from lorasim.interfaces import Options

from lorasim.config import Radio
class Settings(Options):
    actions: list[Radio] = Field(default_factory=lambda: [Radio(sf=s) for s in range(7,13)], min_length=1, title="送信設定の候補", description="学習で選択するSF・送信電力・周波数等の組み合わせ。")
    epsilon: float = Field(.1, ge=0, le=1, title="探索確率", description="この確率でランダムな候補を選びます。残りは推定報酬が最大の候補です。")
    energy_weight: float = Field(.02, ge=0, title="電力ペナルティの重み", description="報酬 ACK - 重み × airtime × 10^(TP/10) に用います。無線電流モデルとは別の代理値です。")

Bandit.Options = Settings
Bandit.label = 'ACKバンディット'
Bandit.description = 'EDで観測したACKと送信エネルギー代理値から学習するε-greedyモデル。'
Bandit.preview_safe = True
PLUGIN = Bandit
