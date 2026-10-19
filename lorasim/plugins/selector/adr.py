"""Source-traceable ADR ED and network implementation. See docs/ALGORITHMS.md."""
from collections import deque
import math
from lorasim.config import Radio
from lorasim.interfaces import NetworkPolicy
from ._adr_space import ADRSpace
from ._channel import ChannelSettings, hop
class ADR(NetworkPolicy):
    accepts_action_space = True
    fixed_space_dimensions = ('coding_rates', 'powers_dbm')
    space_options = ['initial', 'change_tx_power', 'channel_selection', 'frequencies_hz']
    space_description = '比較用拡張：初期SFは候補の最大値。SNRのstep判定後、SFを許可候補へ切り上げます。TP・CRは固定、CHは送信試行ごとに一様選択。backoffも候補内に制限します。原方式のTP適応・既定3CH復帰とは異なります。'
    def __init__(self, rng, initial=None, history_range=20, gateway_combine="max",
                 history_combine="max", change_tx_power=True, margin_db=0.,
                 ack_limit=64, ack_delay=32, backoff=True, action_space=None,
                 channel_selection='legacy', frequencies_hz=None):
        self.rng = rng
        self.channels = ChannelSettings(channel_selection=channel_selection, **({'frequencies_hz':frequencies_hz} if frequencies_hz is not None else {}))
        self.space = ADRSpace(action_space) if action_space is not None else None
        self.radio = (Radio(sf=self.space.sfs[-1], tp_dbm=self.space.tp, cr=self.space.cr,
                            frequency_hz=self.space.channels[0]) if self.space else
                      Radio.model_validate(initial or {"sf":12, "tp_dbm":14}))
        if self.space is None and self.radio.tp_dbm not in range(2,15,2):
            raise ValueError("ADR uses EU TX power steps 2,4,...,14 dBm")
        if type(history_range) is not int or not 1 <= history_range <= 100:
            raise ValueError("ADR history_range must be 1..100")
        if gateway_combine not in {"max","min","avg"} or history_combine not in {"max","min","avg"}:
            raise ValueError("ADR combining method must be max, min or avg")
        if ack_limit < 1 or ack_delay < 1 or not math.isfinite(margin_db):
            raise ValueError("invalid ADR backoff or margin")
        self.server_options = (history_range, gateway_combine, history_combine, change_tx_power, margin_db)
        self.limit,self.delay,self.backoff = ack_limit,ack_delay,backoff
        self.counter,self.last_backoff = 0,-1
        self.answer_pending = False
        self.default_channels = False
        self.channel_index = 0
        self.reset_repetitions = False
    def create_server(self):
        return ADRServer(self.server_options, self.space)
    @property
    def ack_requested(self):
        return self.backoff and self.counter >= self.limit
    def select(self, observation):
        c = self.counter
        if self.backoff and c >= self.limit+self.delay and c != self.last_backoff:
            if c == self.limit+self.delay:
                self.radio.tp_dbm = self.space.tp if self.space else 14
            elif (c-self.limit) % self.delay == 0:
                if self.radio.sf < (self.space.sfs[-1] if self.space else 12):
                    self.radio.sf = self.space.project_sf(self.radio.sf+1) if self.space else self.radio.sf+1
                else:
                    self.default_channels = True
                    self.reset_repetitions = True
            self.last_backoff = c
        if self.space:
            return self.space.hop(self.radio, self.rng)
        if self.channels.channel_selection == 'uniform':
            return hop(self.radio, self.rng, self.channels.frequencies_hz)
        result = self.radio.model_copy()
        if self.default_channels:
            result.frequency_hz = (868100000,868300000,868500000)[self.channel_index % 3]
            self.channel_index += 1
        return result
    def select_attempt(self, observation, new_frame=True):
        if new_frame:
            return self.select(observation)
        if self.space:
            return self.space.hop(self.radio, self.rng)
        return hop(self.radio, self.rng, self.channels.frequencies_hz) if self.channels.channel_selection == 'uniform' else self.radio.model_copy()
    def sent(self, new_frame):
        if new_frame:
            self.counter += 1
    def downlink(self, command):
        self.counter,self.last_backoff = 0,-1
        if command is not None:
            if self.space:
                command = self.space.constrain(command)
            self.radio.sf,self.radio.tp_dbm = command.sf,command.tp_dbm
            self.answer_pending = True
    def observe(self, feedback):
        pass


class ADRServer:
    def __init__(self, options, space=None):
        self.space = space
        self.size,self.gw_method,self.history_method,self.change_power,self.margin = options
        self.history = deque(maxlen=self.size)
        self.last_message = None
    @staticmethod
    def combine(values, method):
        return max(values) if method == "max" else min(values) if method == "min" else sum(values)/len(values)
    def request(self, message_id, radio, powers):
        # Retransmissions of an application frame do not grow the history window.
        if message_id != self.last_message:
            power = self.combine(list(powers),self.gw_method)
            self.history.append(power + 174 - 10*math.log10(125000) - 6)
            self.last_message = message_id
        if len(self.history) < self.size:
            return None
        snr = self.combine(self.history,self.history_method)
        required = {7:-7.5,8:-10.,9:-12.5,10:-15.,11:-17.5,12:-20.}[radio.sf]
        steps = math.floor((snr-required-self.margin)/3)
        if self.space:
            command = self.space.command(radio, steps)
            return command if command.sf != radio.sf else None
        sf,tp = radio.sf,radio.tp_dbm
        while steps > 0 and sf > 7:
            sf -= 1; steps -= 1
        while steps > 0 and tp > 2:
            tp -= 2; steps -= 1
        while steps < 0 and tp < 14:
            tp += 2; steps += 1
        if not self.change_power:
            tp = radio.tp_dbm
        return radio.model_copy(update={"sf":sf,"tp_dbm":tp}) if (sf,tp)!=(radio.sf,radio.tp_dbm) else None

from pydantic import Field
from typing import Literal
from lorasim.interfaces import Options

class Settings(ChannelSettings):
    initial: Radio = Field(default_factory=lambda: Radio(sf=12,tp_dbm=14), title="初期の送信設定", description="送信電力はEUの2,4,…,14 dBm。サーバ指示がDLで届くまで用いる設定です。")
    history_range: int = Field(20, ge=1, le=100, title="履歴に保持するUL数", description="異なるアプリケーションフレームの成功受信をこの件数集めてADRを評価します。再送を重複計上しません。")
    gateway_combine: Literal['max','min','avg'] = Field('max', title="GW間の受信値の集約", description="同じULを受信したGWのRSSIを最大・最小・平均で集約します。")
    history_combine: Literal['max','min','avg'] = Field('max', title="履歴のSNRの集約", description="保持したUL履歴のSNRを最大・最小・平均で集約します。")
    change_tx_power: bool = Field(True, title="送信電力の適応を有効化", description="SF変更に加えて2 dB刻みの送信電力変更を許可します。")
    margin_db: float = Field(0., title="リンク余裕 [dB]", description="必要SNRに追加する設計マージンです。")
    ack_limit: int = Field(64, ge=1, title="ADR_ACK_LIMIT", description="DLを受信しない新規ULがこの回数に達するとADRACKReqを要求します。")
    ack_delay: int = Field(32, ge=1, title="ADR_ACK_DELAY", description="ACK要求後の電力・SF backoffの待ちフレーム数です。")
    backoff: bool = Field(True, title="ADR backoffを有効化", description="DL不達が続くとEDが送信電力・SF・既定チャネルを復旧します。")

ADR.Options = Settings
ADR.label = 'ADR'
ADR.description = '上流AdrComponentに基づくネットワーク制御とEDのADR backoff。'
ADR.preview_safe = True
PLUGIN = ADR
