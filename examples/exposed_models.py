"""Reference user plugins: identical interface/loader to built-in models.
Run API and worker with LORASIM_ALLOW_PLUGINS=1 and this directory on PYTHONPATH.
"""
import numpy as np
from pydantic import Field
from lorasim.config import Radio
from lorasim.interfaces import Options, Policy, Mobility, Application


class SelectorOptions(Options):
    sf: int = Field(9,ge=7,le=12,title='初期拡散率',description='ACKを観測する前に使うSFです。')
    tp_dbm: float = Field(14,ge=2,le=14,title='送信電力 [dBm]',description='この例では送信電力を固定します。')
    adapt: bool = Field(True,title='ACK適応を有効化',description='ACK受信時はSFを1下げ、不達時は1上げる簡単な例です。')

class AckSelector(Policy):
    Options = SelectorOptions
    label = 'ACK適応の開発例'
    description = 'EDが観測できるACKだけを使う最小構成の選択モデル。'
    preview_safe = True
    def __init__(self,rng,sf=9,tp_dbm=14,adapt=True):
        self.sf,self.tp,self.adapt=sf,tp_dbm,adapt
    def select(self,observation):
        return Radio(sf=self.sf,tp_dbm=self.tp)
    def observe(self,feedback):
        if self.adapt and feedback.ack is not None:
            self.sf=max(7,self.sf-1) if feedback.ack else min(12,self.sf+1)


class MobilityOptions(Options):
    velocity_m_s: tuple[float,float,float] = Field((1.,0.,0.),title='速度ベクトル [m/s]',description='x,y,z方向の速度。エリア境界では各軸で折り返します。')

class LinearMotion(Mobility):
    Options = MobilityOptions
    label = '一定速度の移動例'
    description = '時間から直接位置を求めるので長期間のプレビューも一定の計算量です。'
    preview_safe = True
    def __init__(self,initial,area,rng,velocity_m_s=(1.,0.,0.)):
        self.initial=np.array(initial);self.area=np.array(area);self.v=np.array(velocity_m_s)
    def position(self,time_s):
        p=self.initial+self.v*time_s;q=p[:2]%(2*self.area)
        p[:2]=np.where(q<=self.area,q,2*self.area-q)
        return p


class ApplicationOptions(Options):
    interval_s: float = Field(120.,gt=0,title='生成間隔 [s]',description='メッセージを生成する基本間隔。MACの待ち時間は含みません。')
    start_max_s: float = Field(300.,ge=0,title='開始時刻の上限 [s]',description='各EDの初回生成を [0, 上限) の一様乱数にします。')

class UniformStart(Application):
    Options = ApplicationOptions
    label = '一様開始の開発例'
    description = '独自のタイミングモデルもOptionsで定数を公開できます。'
    preview_safe = True
    def __init__(self,rng,interval_s=120.,start_max_s=300.):
        self.interval=interval_s;self.first=float(rng.uniform(0,start_max_s))
    def next_time(self,observation,previous_generation_s):
        return self.first if previous_generation_s is None else max(observation.time_s+1e-9,previous_generation_s+self.interval)
