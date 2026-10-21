"""Single DQN with ACK power and the locally observed movement bit."""
import math
from pydantic import Field
from .dqn import DQN
from ._dqn import DQNSettings, Learner


class Settings(DQNSettings):
    movement_threshold_m: float = Field(1., ge=0, title='移動判定閾値 [m]', description='SEMAと同じ選択時の2D位置差から移動ビットを生成します。')


class MobilityDQN(DQN):
    Options = Settings
    requires_position = True
    label = 'DQN（RSSI＋移動ビット）'
    description = '単一2入力DQN。移動状態はSEMAと同じ局所位置差で検出。ACK時には最後に検出した状態を保持します。'

    def __init__(self,rng,action_space,**options):
        self.options = Settings.model_validate(options)
        self.actions = action_space.radios()
        self.learner = Learner(rng,len(self.actions),self.options,input_dim=2)
        self.rssi = self.options.initial_rssi_dbm
        self.previous_position = None
        self.moving = False
        self.pending = None

    def initialize(self,obs):
        if obs.position is None:
            raise ValueError('mobility DQN requires position observation')
        self.previous_position = obs.position[:2]

    def detect(self,obs):
        if obs.position is None:
            raise ValueError('mobility DQN requires position observation')
        pos = obs.position[:2]
        self.moving = self.previous_position is not None and math.dist(pos,self.previous_position)>self.options.movement_threshold_m
        self.previous_position = pos

    def state(self):
        return [super().state(),float(self.moving)]

    def select(self,obs):
        self.detect(obs)
        return super().select(obs)

    @classmethod
    def preview_bytes(cls,options,space):
        sizes=[2,*options.get('hidden_sizes',[64,64]),len(space.radios())]
        return 32*sum((a+1)*b for a,b in zip(sizes,sizes[1:]))+48*min(128,options.get('replay_capacity',10000))


PLUGIN = MobilityDQN
