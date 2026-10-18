"""LinUCB–SF, DOI 10.1109/JIOT.2026.3651343, Eq.4–15 / Algorithm 1.

See docs/PAPERS.md for underspecified constants, typographical inconsistencies,
telemetry transport, and the explicitly separate SF/TP extension.
"""
import math
import numpy as np
from pydantic import Field
from lorasim.config import Radio
from lorasim.interfaces import Policy, Options


class Settings(Options):
    initial: Radio = Field(default_factory=lambda:Radio(sf=12,tp_dbm=14), title='初期送信設定', description='原論文はSF7〜12の6腕。TPはこの値で固定し、SNR通知が届くまでは初期SFを使用します。')
    frequencies_hz: list[int] = Field(default_factory=lambda:[868100000,868300000,868500000], min_length=1, max_length=64, title='ホッピング周波数 [Hz]', description='選択したSFとは独立に一様にチャネルを選択。原論文のEU868既定3チャネルです。')
    alpha_ucb: float = Field(.8, ge=0, title='UCBの探索係数 α', description='式12の不確実性ボーナス。表IIの既定値0.8。')
    ridge: float = Field(1., gt=0, title='正則化係数 λ', description='式9に従いA=λI、逆行列はI/λで初期化。表IIの既定値1。')
    eta: float = Field(.08, ge=0, title='選択スコアの通信時間係数 η', description='式13のT(a)係数。表IIの既定値0.08。')
    airtime_scale: float = Field(1., gt=0, title='T(a)の比例係数', description='論文で未指定の比例係数。T(a)=この値×2^(SF−7)。報酬の実測通信時間とは別です。')
    link_margin_db: float = Field(2., ge=0, title='適用可能SFのSNR余裕 [dB]', description='式7のδLM。受信済みUL SNRが必要SNR＋この値以上の腕だけを選びます。')
    epsilon: float = Field(.05, ge=0, le=1, title='ランダム探索率 ε', description='表IIの既定値0.05。適用可能な腕の中から一様に探索します。')
    hysteresis: float = Field(.05, ge=0, title='SF変更のヒステリシス', description='表IIの既定値0.05。現在の腕が適用可能なら、スコア改善がこの値未満の場合は維持します。')
    reward_beta: float = Field(.1, ge=0, title='報酬の通信時間係数 β', description='式8。論文に数値指定がないため実装既定値0.1を使用。')
    reward_gamma_max: float = Field(1., ge=0, title='報酬の通信時間上限 γmax', description='式8の通信時間ペナルティをクリップする上限。実装既定値1。')
    reward_c_per_ms: float = Field(.001, ge=0, title='報酬の時間正規化 c [1/ms]', description='実測airtimeをミリ秒へ変換して乗算。実装既定値0.001。')
    reward_delta: float = Field(.05, ge=0, title='低SF報酬 δ', description='式8の低SFボーナス。実装既定値0.05。')
    low_sf: int = Field(8, ge=7, le=12, title='低SFボーナスの上限SF', description='選択SFがこの値以下のときδを加算。論文例はSF7/8。')
    clip_reward: bool = Field(False, title='報酬を0〜1にクリップ', description='オフは式8をそのまま使用。式8は本文の「0〜1」と矛盾し範囲外になり得ます。オンは明示的な追加クリップです。')
    speed_smoothing: float = Field(.5, gt=0, le=1, title='観測速度の平滑化係数', description='前回の位置と時刻から推定した速度を指数平滑化。論文に数値指定がないため実装既定値0.5。')
    maximum_speed_m_s: float = Field(2., gt=0, title='速度の正規化上限 [m/s]', description='平滑化速度をこの値で割って0〜1へ制限。位置観測が無効なEDでは速度特徴量は0。')
    gateway_position: tuple[float,float,float] | None = Field(None, title='EDが既知のGW位置 [m]', description='任意の既知座標。シミュレータ内部の位置を自動参照しません。距離ペナルティ使用時はED位置観測も有効にします。')
    distance_weight: float = Field(0., ge=0, title='距離ペナルティ λd', description='式13。論文に数値指定なし。g(d)は全腕で共通なので腕の順位は変わりません。')
    distance_scale_m: float = Field(5000., gt=0, title='距離の正規化尺度 d0 [m]', description='g(d)=min(d/d0,1)。距離は既知GW座標とED観測位置から計算します。')


class LinUCBSF(Policy):
    Options = Settings
    label = 'LinUCB-SF'
    description = '6次元の端末側ContextとACKで学習。共通空間を有効にするとその全候補を腕として使用。GW情報は9 byteの実験用DL通知を受信したときだけ更新します。'
    preview_safe = True
    requires_confirmed = True
    link_feedback_bytes = 9
    accepts_action_space = True
    space_options = ["initial", "frequencies_hz"]
    def __init__(self,rng,action_space=None,**options):
        o=Settings.model_validate(options)
        if action_space is None:
            self._initialize(rng,o)
        else:
            actions=action_space.radios()
            o.initial=max(actions,key=lambda a:(a.sf,a.tp_dbm))
            self._initialize(rng,o,actions=actions)
            self.uses_action_space=True
            self.joint=len(action_space.powers_dbm)>1
    def _initialize(self,rng,o,powers=None,actions=None):
        self.options,self.rng = o,rng
        self.actions = actions if actions is not None else [o.initial.model_copy(update={'sf':sf,'tp_dbm':tp}) for sf in range(7,13) for tp in (powers or [o.initial.tp_dbm])]
        self.sf = np.array([a.sf for a in self.actions])
        self.tp = np.array([a.tp_dbm for a in self.actions])
        self.k = len(self.actions)
        self.inverse = np.tile(np.eye(6)/o.ridge,(self.k,1,1))
        self.b,self.theta = np.zeros((self.k,6)),np.zeros((self.k,6))
        self.current = next(i for i,a in enumerate(self.actions) if a.sf==o.initial.sf and a.tp_dbm==o.initial.tp_dbm)
        self.snr,self.rssi,self.reported_tp = None,None,None
        self.last_position,self.last_time,self.speed = None,None,0.
        self.pending = None
        self.updates,self.last_reward = 0,None
        self.joint = powers is not None
    def select(self,obs):
        o = self.options
        if obs.position is not None:
            if self.last_position is not None and obs.time_s > self.last_time:
                v = math.dist(obs.position,self.last_position)/(obs.time_s-self.last_time)
                self.speed = o.speed_smoothing*v+(1-o.speed_smoothing)*self.speed
            self.last_position,self.last_time = obs.position,obs.time_s
        maximum_retries = max(0,obs.max_attempts-1)
        rho = max(0.,(maximum_retries-(obs.attempt-1))/maximum_retries) if maximum_retries else 1.
        x = np.array([1.,math.tanh((self.snr if self.snr is not None else 0.)/10),
                      math.tanh((self.rssi if self.rssi is not None else 0.)/20),
                      min(self.speed/o.maximum_speed_m_s,1.) if obs.position is not None else 0.,rho,
                      (obs.last_sf or o.initial.sf)/12])
        if self.snr is None:
            chosen = self.current  # bootstrap is explicit; no inaccessible GW data
        else:
            snr = self.snr + (self.tp-self.reported_tp if self.joint else 0.)
            feasible = np.flatnonzero(snr >= np.array([-7.5,-10.,-12.5,-15.,-17.5,-20.])[self.sf-7]+o.link_margin_db)
            if not len(feasible):
                chosen = max(range(self.k),key=lambda i:(self.sf[i],self.tp[i]))
            else:
                uncertainty = np.sqrt(np.maximum(0.,np.einsum('i,kij,j->k',x,self.inverse,x)))
                scores = self.theta@x+o.alpha_ucb*uncertainty-o.eta*o.airtime_scale*2.**(self.sf-7)
                if obs.position is not None and o.gateway_position is not None:
                    scores -= o.distance_weight*min(math.dist(obs.position,o.gateway_position)/o.distance_scale_m,1.)
                chosen = int(feasible[np.argmax(scores[feasible])])
                if self.current in feasible and scores[chosen]-scores[self.current] < o.hysteresis:
                    chosen = self.current
                if self.rng.random() < o.epsilon:
                    chosen = int(self.rng.choice(feasible))
        self.current = chosen
        self.pending = (chosen,x)
        if self.uses_action_space:
            return self.actions[chosen].model_copy()
        return self.actions[chosen].model_copy(update={'frequency_hz':int(self.rng.choice(o.frequencies_hz))})
    def observe(self,f):
        if f.downlink_received and f.ul_snr_db is not None and f.ul_rssi_dbm is not None:
            self.snr,self.rssi,self.reported_tp = f.ul_snr_db,f.ul_rssi_dbm,f.ul_tx_power_dbm
        if self.pending is None or f.ack is None:
            return
        a,x = self.pending
        o = self.options
        reward = float(f.ack)-o.reward_beta*min(o.reward_gamma_max,o.reward_c_per_ms*f.airtime_s*1000)+o.reward_delta*(self.actions[a].sf<=o.low_sf)
        if o.clip_reward:
            reward = min(1.,max(0.,reward))
        v = self.inverse[a]@x
        self.inverse[a] -= np.outer(v,v)/(1+x@v)
        self.b[a] += reward*x
        self.theta[a] = self.inverse[a]@self.b[a]
        self.updates += 1
        self.last_reward = float(reward)
        self.pending = None
    def snapshot(self):
        return dict(updates=self.updates,last_reward=self.last_reward,last_received_ul_snr_db=self.snr,
                    last_received_ul_rssi_dbm=self.rssi,inverse=self.inverse.tolist(),b=self.b.tolist(),theta=self.theta.tolist())


PLUGIN = LinUCBSF
