"""Thesis Algorithm 1, Eq.4.3–4.14 and 5.5–5.6; see docs/SEMA.md."""
from itertools import product
import math
import numpy as np
from pydantic import Field
from ._dqn import DQNSettings, Learner, reward
from .dqn import DQN


class Settings(DQNSettings):
    movement_threshold_m: float = Field(1., ge=0, title='移動判定の距離閾値 [m]', description='前回の選択時との2D位置差がこの値を超えると移動状態。論文未指定の実装既定値1 m。位置観測を有効にしてください。')
    confidence: float = Field(1., gt=0, title='メタエージェントの信頼係数 δconf', description='式4.11のδconf sqrt(2 ln T/n)。論文未指定の実装既定値1。')
    greedy_decay: float = Field(20., gt=0, title='ε-greedyの減衰定数 d', description='式5.6: max(εend,d/(d+t))。移動から静止への遷移でtと累積報酬をリセット。')
    greedy_epsilon_end: float = Field(.1, ge=0, le=1, title='ε-greedyの最終探索率', description='論文の既定値0.1。活用時は式5.5の累積報酬最大のSF×CHを選択。')


class SEMA(DQN):
    Options = Settings
    requires_position = True
    label = 'SEMA（逐次消去メタエージェント）'
    description = '静止[A,B,ε-greedy]×移動[A,C]の6組をACKで逐次消去。同じEDの状態プール内だけで全DQNへ経験を共有します。ED間の共有はありません。'
    def __init__(self,rng,action_space,**options):
        self.options = o = Settings.model_validate(options)
        self.rng,self.actions = rng,action_space.radios()
        self.learners = {name:Learner(np.random.default_rng(int(rng.integers(2**63))),len(self.actions),o) for name in ('A','B','C')}
        self.arms = list(product(('A','B','epsilon'),('A','C')))
        self.active = list(range(len(self.arms)))
        self.counts = np.zeros(6,dtype=np.int64)
        self.rewards = np.zeros(6)
        self.selected_counts = {x:0 for x in ('A','B','C','epsilon')}
        self.eliminated = []
        self.trace_revision = 0
        self.greedy_q = np.zeros(len(self.actions))
        self.greedy_steps = self.greedy_resets = 0
        self.rssi = o.initial_rssi_dbm
        self.previous_position = None
        self.moving = False
        self.pending = None
    def initialize(self,obs):
        if obs.position is None:
            raise ValueError('SEMA requires ED position observation')
        self.previous_position = obs.position[:2]
    def select(self,obs):
        if obs.position is None:
            raise ValueError('SEMA requires ED position observation')
        pos = obs.position[:2]
        moving = self.previous_position is not None and math.dist(pos,self.previous_position)>self.options.movement_threshold_m
        if self.moving and not moving:
            self.greedy_q.fill(0.)
            self.greedy_steps = 0
            self.greedy_resets += 1
        self.previous_position,self.moving = pos,moving
        arm = int(self.rng.choice(self.active))
        name = self.arms[arm][int(moving)]
        x = self.state()
        if name == 'epsilon':
            epsilon = max(self.options.greedy_epsilon_end,self.options.greedy_decay/(self.options.greedy_decay+self.greedy_steps))
            a = int(self.rng.integers(len(self.actions))) if self.rng.random()<epsilon else int(np.argmax(self.greedy_q))
        else:
            a = self.learners[name].choose(x)
        self.selected_counts[name] += 1
        self.pending = x,a,arm,name,moving
        return self.actions[a].model_copy()
    def eliminate(self):
        total = int(self.counts.sum())
        lower,upper = {},{}
        for k in self.active:
            n = self.counts[k]
            if n == 0:
                lower[k],upper[k] = -math.inf,math.inf
            else:
                radius = self.options.confidence*math.sqrt(2*math.log(max(1,total))/n)
                mean = self.rewards[k]/n
                lower[k],upper[k] = mean-radius,mean+radius
        threshold = max(lower.values())
        for k in self.active[:]:
            if upper[k] < threshold:
                self.active.remove(k)
                self.eliminated.append(dict(arm=k,experience=total))
                self.trace_revision += 1
    def observe(self,f):
        if self.pending is None or f.ack is None: return
        x,a,arm,name,moving = self.pending
        if f.ack and f.dl_rssi_dbm is not None: self.rssi = f.dl_rssi_dbm
        r = reward(self.options,f.ack,self.actions[a].sf)
        self.counts[arm] += 1
        self.rewards[arm] += float(f.ack)
        self.eliminate()
        for key in ('A','C' if moving else 'B'):
            self.learners[key].learn(x,a,r,self.state())
        if name == 'epsilon':
            self.greedy_q[a] += r
            self.greedy_steps += 1
        self.pending = None
    def snapshot(self):
        return dict(last_ack_rssi_dbm=self.rssi,arms=self.arms,active_arms=self.active,
            arm_counts=self.counts.tolist(),arm_rewards=self.rewards.tolist(),eliminated=self.eliminated,
            selected_algorithms=self.selected_counts,greedy_resets=self.greedy_resets,
            greedy_q=self.greedy_q.tolist(),learners={k:v.snapshot() for k,v in self.learners.items()})

    def pool_trace(self):
        """Read-only compact state, emitted initially and only on elimination."""
        import json
        return dict(experience=int(self.counts.sum()), arms=json.dumps(self.arms),
                    active_arms=list(self.active))


PLUGIN = SEMA
