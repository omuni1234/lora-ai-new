"""Optional vector-state DQN for the explicit dqn_mobility comparison plugin.

Keep this extension separate from the established scalar learner used by DQN
and SEMA. Importing this module does not register or select any simulation model.
"""
import math
import numpy as np
from ._dqn import Network


class VectorLearner:
    def __init__(self,rng,k,options,input_dim=1):
        self.rng,self.k,self.o = rng,k,options
        self.input_dim = input_dim
        self.net = Network(rng,[input_dim,*options.hidden_sizes,k])
        self.target = [p.copy() for p in self.net.params]
        self.m = [np.zeros_like(p) for p in self.net.params]
        self.v = [np.zeros_like(p) for p in self.net.params]
        self.size = self.seen = self.updates = 0
        self.loss = None
        self.data = np.empty((min(128,options.replay_capacity),2*input_dim+2),dtype=float)
    def choose(self,x):
        o = self.o
        epsilon = o.epsilon_end+(o.epsilon_start-o.epsilon_end)*math.exp(-self.seen/o.epsilon_decay)
        if self.rng.random() < epsilon:
            return int(self.rng.integers(self.k))
        return int(np.argmax(self.net.forward(np.asarray(x,dtype=float).reshape(1,self.input_dim))[0]))
    def learn(self,x,a,r,y):
        o = self.o
        if self.size == len(self.data) and self.size < o.replay_capacity:
            new = np.empty((min(o.replay_capacity,2*len(self.data)),2*self.input_dim+2))
            new[:self.size] = self.data
            self.data = new
        if self.input_dim == 1:
            self.data[self.seen % o.replay_capacity] = x,a,r,y
        else:
            self.data[self.seen % o.replay_capacity] = np.r_[x,a,r,y]
        self.seen += 1
        self.size = min(self.seen,o.replay_capacity)
        if self.size < o.batch_size or self.seen % o.train_every:
            return
        b = self.data[self.rng.choice(self.size,o.batch_size,replace=False)]
        n = self.input_dim
        nxt = b[:,n+2:]
        for i in range(0,len(self.target),2):
            nxt = nxt@self.target[i]+self.target[i+1]
            if i+2 < len(self.target): nxt = np.maximum(nxt,0.)
        target = b[:,n+1]+o.discount*nxt.max(axis=1)
        self.loss,grads = self.net.gradients(b[:,:n],b[:,n].astype(int),target)
        if not math.isfinite(self.loss):
            raise FloatingPointError('DQN loss is non-finite; review learning rate and RSSI normalization')
        self.updates += 1
        for p,m,v,g in zip(self.net.params,self.m,self.v,grads):
            m *= o.adam_beta1; m += (1-o.adam_beta1)*g
            v *= o.adam_beta2; v += (1-o.adam_beta2)*g*g
            p -= o.learning_rate*(m/(1-o.adam_beta1**self.updates))/(np.sqrt(v/(1-o.adam_beta2**self.updates))+o.adam_epsilon)
        if self.updates % o.target_update == 0:
            for target,p in zip(self.target,self.net.params): np.copyto(target,p)
    def snapshot(self):
        return dict(experiences=self.seen,replay_size=self.size,gradient_updates=self.updates,last_loss=self.loss)
