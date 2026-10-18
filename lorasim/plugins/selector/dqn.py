"""Standalone baseline with exactly the lower SEMA learner and ACK-RSSI state."""
from lorasim.interfaces import Policy
from ._dqn import DQNSettings, Learner, reward


class DQN(Policy):
    Options = DQNSettings
    uses_action_space = True
    requires_confirmed = True
    preview_safe = True
    label = 'DQN（SEMA下位モデル）'
    description = '修士論文の1次元ACK RSSI・学習式を使用。行動候補はシミュレータから受け取り、各EDで独立に学習。GW側の未受信情報は使いません。'
    def __init__(self,rng,action_space,**options):
        self.options = o = DQNSettings.model_validate(options)
        self.actions = action_space.radios()
        self.learner = Learner(rng,len(self.actions),o)
        self.rssi = o.initial_rssi_dbm
        self.pending = None
    def state(self):
        return (self.rssi-self.options.rssi_offset_dbm)/self.options.rssi_scale_db
    def select(self,obs):
        x = self.state()
        a = self.learner.choose(x)
        self.pending = x,a
        return self.actions[a].model_copy()
    def observe(self,f):
        if self.pending is None or f.ack is None: return
        x,a = self.pending
        if f.ack and f.dl_rssi_dbm is not None:
            self.rssi = f.dl_rssi_dbm
        self.learner.learn(x,a,reward(self.options,f.ack,self.actions[a].sf),self.state())
        self.pending = None
    def snapshot(self):
        return dict(last_ack_rssi_dbm=self.rssi,actions=[a.model_dump() for a in self.actions],**self.learner.snapshot())
    @classmethod
    def preview_bytes(cls,options,space):
        sizes=[1,*options.get('hidden_sizes',[64,64]),len(space.radios())]
        # Main, target, Adam first/second moments plus initial replay allocation.
        per_model=32*sum((a+1)*b for a,b in zip(sizes,sizes[1:]))+32*min(128,options.get('replay_capacity',10000))
        return per_model*(3 if cls.requires_position else 1)


PLUGIN = DQN
