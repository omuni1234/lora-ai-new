"""Small NumPy DQN: replay, MSE Bellman target, Adam, hard target copies.

Thesis pp.21–25,32. Unspecified choices are explicit in Settings and docs/SEMA.md.
No global RNG, tensor runtime, or unbounded per-transmission Python replay objects.
"""
import math
import numpy as np
from pydantic import Field, model_validator
from lorasim.interfaces import Options


class DQNSettings(Options):
    hidden_sizes: list[int] = Field(default_factory=lambda:[64,64], min_length=1, max_length=4, title='DQNの隠れ層サイズ', description='論文表5.4は64,64。各層ReLU、出力は共通SF×CHの行動数。各層1〜512。')
    replay_capacity: int = Field(10000, ge=1, le=1000000, title='経験再生の最大件数', description='各DQNが独立したリングバッファを持ちます。使用分だけ段階的に確保します。')
    batch_size: int = Field(128, ge=1, le=4096, title='ミニバッチ件数', description='経験から重複なしに一様抽出。件数が貯まるまで勾配更新を待ちます。')
    discount: float = Field(.5, ge=0, le=1, title='割引率 γ', description='ベルマン目標 r + γ max Q_target(o′,a′)。')
    learning_rate: float = Field(.01, gt=0, title='学習率', description='Adamで平均二乗誤差を最小化します。論文表5.5の既定値0.01。')
    epsilon_start: float = Field(1., ge=0, le=1, title='DQNの初期探索率', description='ε=end+(start−end) exp(−経験件数/decay)。減衰関数は論文未指定の実装選択です。')
    epsilon_end: float = Field(.01, ge=0, le=1, title='DQNの最終探索率', description='探索時には全SF×CHから一様抽選します。')
    epsilon_decay: float = Field(1000., gt=0, title='DQNの探索率減衰定数', description='経験件数に対する指数減衰の時定数。SEMAでは共有された経験も数えます。')
    target_update: int = Field(16, ge=1, title='ターゲット同期間隔 [勾配更新回]', description='Main NetworkをTarget Networkへ完全コピーする頻度。')
    train_every: int = Field(1, ge=1, title='学習間隔 [経験件数]', description='既定1は毎経験で学習。増やすと計算を減らせますが学習条件が変わるため実験条件として記録されます。')
    adam_beta1: float = Field(.9, ge=0, lt=1, title='Adamの一次モーメント係数', description='勾配移動平均の減衰係数。論文未指定の標準値0.9。')
    adam_beta2: float = Field(.999, ge=0, lt=1, title='Adamの二次モーメント係数', description='勾配二乗移動平均の減衰係数。論文未指定の標準値0.999。')
    adam_epsilon: float = Field(1e-8, gt=0, title='Adamの数値安定化定数', description='分母 sqrt(v_hat)+ε のε。')
    initial_rssi_dbm: float = Field(-120., title='ACK未観測時のRSSI [dBm]', description='論文未指定の初期値。ACK成功時だけ更新し、不達時は最後に観測した値を保持します。')
    rssi_offset_dbm: float = Field(0., title='RSSI入力のオフセット [dBm]', description='入力=(観測RSSI−offset)/scale。既定0は原値入力です。')
    rssi_scale_db: float = Field(1., gt=0, title='RSSI入力の尺度 [dB]', description='既定1は正規化なし。変更時は論文からの実験的変更として記録されます。')
    reward_sf_offset: float = Field(13., title='成功報酬のSF基準', description='ACK成功報酬=(この値−SF)/reward_divisor。論文式4.3は13。')
    reward_divisor: float = Field(6., gt=0, title='成功報酬の除数', description='論文式4.3は6。SF7で1、SF12で1/6。')
    failure_reward: float = Field(-1., title='ACK不達時の報酬', description='論文式4.3は−1。SEMAのメタ報酬はこの設定によらずACKの0/1です。')
    @model_validator(mode='after')
    def valid(self):
        if any(not 1 <= x <= 512 for x in self.hidden_sizes):
            raise ValueError('hidden layer size must be 1..512')
        if self.batch_size > self.replay_capacity:
            raise ValueError('batch_size exceeds replay_capacity')
        if self.epsilon_end > self.epsilon_start:
            raise ValueError('epsilon_end exceeds epsilon_start')
        return self


class Network:
    def __init__(self,rng,sizes):
        self.params = []
        for a,b in zip(sizes,sizes[1:]):
            bound = 1/math.sqrt(a)
            self.params.extend([rng.uniform(-bound,bound,(a,b)),rng.uniform(-bound,bound,b)])
    def forward(self,x,cache=False):
        states = [x]
        for i in range(0,len(self.params),2):
            x = x@self.params[i]+self.params[i+1]
            if i+2 < len(self.params):
                x = np.maximum(x,0.)
            states.append(x)
        return (x,states) if cache else x
    def gradients(self,x,actions,target):
        q,states = self.forward(x,True)
        diff = q[np.arange(len(x)),actions]-target
        grad = np.zeros_like(q)
        grad[np.arange(len(x)),actions] = 2*diff/len(x)
        result = [None]*len(self.params)
        for layer in reversed(range(len(self.params)//2)):
            i = 2*layer
            result[i] = states[layer].T@grad
            result[i+1] = grad.sum(axis=0)
            if layer:
                grad = (grad@self.params[i].T)*(states[layer]>0)
        return float(np.mean(diff**2)),result


class Learner:
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


def reward(o,ack,sf):
    return (o.reward_sf_offset-sf)/o.reward_divisor if ack else o.failure_reward
