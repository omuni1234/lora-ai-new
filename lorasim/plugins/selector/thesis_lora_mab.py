"""Explicit thesis Eq.5.8–5.11 variant; does not replace original LoRa-MAB."""
import math
import numpy as np
from pydantic import Field
from lorasim.interfaces import Options, Policy


class Settings(Options):
    horizon: int = Field(40000,ge=1,title='総試行数 T',description='γ=min(1,sqrt(K ln(TK)/T))、α=1/T。実験の送信回数に合わせます。')


class ThesisLoRaMAB(Policy):
    Options=Settings
    uses_action_space=True
    requires_confirmed=True
    preview_safe=True
    label='LoRa-MAB（修論式5.8–5.11版）'
    description='成功(13−SF)/6・失敗−1。選択腕の指数更新と、更新前の重み総和に基づく選択腕への加算。既存ACK報酬版とは別の比較条件です。'

    def __init__(self,rng,action_space,**options):
        self.options=o=Settings.model_validate(options)
        self.rng,self.actions=rng,action_space.radios()
        k=len(self.actions)
        self.gamma=min(1.,math.sqrt(k*math.log(o.horizon*k)/o.horizon))
        self.log_weights=np.zeros(k)
        self.pending=None
        self.steps=0

    def probabilities(self):
        w=np.exp(self.log_weights-self.log_weights.max())
        return (1-self.gamma)*w/w.sum()+self.gamma/len(w)

    def select(self,obs):
        probs=self.probabilities()
        a=int(self.rng.choice(len(self.actions),p=probs))
        self.pending=a,float(probs[a])
        return self.actions[a].model_copy()

    def observe(self,f):
        if self.pending is None or f.ack is None:
            return
        a,p=self.pending
        r=(13-self.actions[a].sf)/6 if f.ack else -1.
        k=len(self.actions)
        # Eq.5.11 uses the PRE-update total and adds only to the chosen arm.
        total=float(np.logaddexp.reduce(self.log_weights))
        self.log_weights[a]=np.logaddexp(self.log_weights[a]+self.gamma*r/(k*p),
            1-math.log(self.options.horizon)-math.log(k)+total)
        self.log_weights-=self.log_weights.max()  # common scale leaves probabilities unchanged
        self.steps+=1
        self.pending=None

    def snapshot(self):
        return dict(steps=self.steps,gamma=self.gamma,log_weights=self.log_weights.tolist(),probabilities=self.probabilities().tolist())


PLUGIN=ThesisLoRaMAB
