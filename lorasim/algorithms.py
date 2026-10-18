"""ED algorithms and the separate network-side ADR controller.

Numerical ADR decisions follow the pinned signetlabdei AdrComponent. LinkADRReq
is delivered through the simulated radio; this module never leaks GW RSSI to EDs.
"""
from collections import deque
import math
from .config import Radio


class BlindADR:
    def __init__(self, rng, sequence=None, random_phase=False, **radio):
        self.sequence = sequence or [12, 7, 10, 7, 10, 7]
        if not self.sequence or any(type(x) is not int or not 7 <= x <= 12 for x in self.sequence):
            raise ValueError("BADR sequence must contain SF7..SF12")
        self.radio = Radio.model_validate(radio)
        self.index = int(rng.integers(len(self.sequence))) if random_phase else 0
    def select(self, observation):
        sf = self.sequence[self.index % len(self.sequence)]
        self.index += 1
        return self.radio.model_copy(update={"sf":sf})
    def observe(self, feedback):
        pass


class ADR:
    def __init__(self, rng, initial=None, history_range=20, gateway_combine="max",
                 history_combine="max", change_tx_power=True, margin_db=0.,
                 ack_limit=64, ack_delay=32, backoff=True):
        self.radio = Radio.model_validate(initial or {"sf":12, "tp_dbm":14})
        if self.radio.tp_dbm not in range(2,15,2):
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
    @property
    def ack_requested(self):
        return self.backoff and self.counter >= self.limit
    def select(self, observation):
        c = self.counter
        if self.backoff and c >= self.limit+self.delay and c != self.last_backoff:
            if c == self.limit+self.delay:
                self.radio.tp_dbm = 14
            elif (c-self.limit) % self.delay == 0:
                if self.radio.sf < 12:
                    self.radio.sf += 1
                else:
                    self.default_channels = True
                    self.reset_repetitions = True
            self.last_backoff = c
        result = self.radio.model_copy()
        if self.default_channels:
            result.frequency_hz = (868100000,868300000,868500000)[self.channel_index % 3]
            self.channel_index += 1
        return result
    def select_attempt(self, observation, new_frame=True):
        return self.select(observation) if new_frame else self.radio.model_copy()
    def sent(self, new_frame):
        if new_frame:
            self.counter += 1
    def downlink(self, command):
        self.counter,self.last_backoff = 0,-1
        if command is not None:
            self.radio.sf,self.radio.tp_dbm = command.sf,command.tp_dbm
            self.answer_pending = True
    def observe(self, feedback):
        pass


class ADRServer:
    def __init__(self, options):
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
