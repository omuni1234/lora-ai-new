"""Fixed B/C control: no shared A, bandit or meta-agent."""
import numpy as np
from .dqn import DQN
from .dqn_mobility import MobilityDQN, Settings
from ._dqn import Learner


class StateSplitDQN(MobilityDQN):
    label = 'DQN（静止・移動の固定2モデル）'
    description = '静止時B・移動時Cを必ず選択し、選択したモデルだけを更新。同じRSSI、報酬、位置差判定を使用。'

    def __init__(self,rng,action_space,**options):
        self.options = Settings.model_validate(options)
        self.actions = action_space.radios()
        self.learners = {k:Learner(np.random.default_rng(int(rng.integers(2**63))),len(self.actions),self.options) for k in ('B','C')}
        self.learner = self.learners['B']
        self.rssi = self.options.initial_rssi_dbm
        self.previous_position = None
        self.moving = False
        self.pending = None

    def state(self):
        return DQN.state(self)

    def select(self,obs):
        self.detect(obs)
        self.learner = self.learners['C' if self.moving else 'B']
        return DQN.select(self,obs)

    def decision_trace(self):
        return dict(algorithm="C" if self.moving else "B",motion_state="moving" if self.moving else "stationary")

    def snapshot(self):
        return dict(last_ack_rssi_dbm=self.rssi,learners={k:v.snapshot() for k,v in self.learners.items()})

    @classmethod
    def preview_bytes(cls,options,space):
        # DQN's estimate multiplies by three for position-aware subclasses.
        return DQN.preview_bytes(options,space)*2


PLUGIN = StateSplitDQN
