"""Explicit SF-reward controls, separate from legacy ACK/energy bandits."""
import math
from typing import Literal
import numpy as np
from pydantic import Field
from lorasim.interfaces import Policy, Options


class Settings(Options):
    algorithm: Literal['epsilon','ucb1'] = Field('epsilon',title='方式',description='epsilonは累積報酬、ucb1は元の報酬の標本平均と信頼幅。')
    confidence: float = Field(2.,gt=0,title="UCB探索係数",description="修論式5.7の c。標本平均 + c sqrt(log(t)/n)。報酬は正規化しません。")
    decay: float = Field(20.,gt=0,title='探索率減衰',description='epsilon=max(epsilon_end,decay/(decay+t))。')
    epsilon_end: float = Field(.1,ge=0,le=1,title='最終探索率',description='epsilon方式の探索率下限。')


class SFRewardBandit(Policy):
    Options = Settings
    uses_action_space = True
    requires_confirmed = True
    preview_safe = True
    label = 'SF報酬バンディット'
    description = 'SEMAと同じACK成功時(13−SF)/6、失敗時−1を使用。既存LoRa-MABの代替実装ではありません。'

    def __init__(self,rng,action_space,**options):
        self.o = Settings.model_validate(options)
        self.rng,self.actions = rng,action_space.radios()
        self.sums = np.zeros(len(self.actions))
        self.counts = np.zeros(len(self.actions),dtype=int)
        self.selected = None

    def select(self,obs):
        t = int(self.counts.sum())
        if self.o.algorithm == 'epsilon':
            eps = max(self.o.epsilon_end,self.o.decay/(self.o.decay+t))
            a = int(self.rng.integers(len(self.actions))) if self.rng.random()<eps else int(np.argmax(self.sums))
        elif (self.counts==0).any():
            a = int(np.flatnonzero(self.counts==0)[0])
        else:
            a = int(np.argmax(self.sums/self.counts+self.o.confidence*np.sqrt(math.log(t)/self.counts)))
        self.selected = a
        return self.actions[a].model_copy()

    def observe(self,f):
        if self.selected is None or f.ack is None:
            return
        a = self.selected
        self.counts[a] += 1
        self.sums[a] += (13-self.actions[a].sf)/6 if f.ack else -1
        self.selected = None

    def snapshot(self):
        return dict(counts=self.counts.tolist(),reward_sums=self.sums.tolist())


PLUGIN = SFRewardBandit
