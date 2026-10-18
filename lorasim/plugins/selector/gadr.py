"""Sensors 2020,20,6466: Section 3.1, equations 1/2/4/5, Algorithm 1."""
from collections import deque
import math
from pydantic import Field
from .adr import ADR, ADRServer, Settings as ADRSettings


class Settings(ADRSettings):
    history_range: int = Field(20, ge=2, le=100, title='ガウスフィルタの履歴数 M', description='論文の既定値は20。標本分散はM−1で割ります。成功した新規ULの履歴を使います。')
    margin_db: float = Field(10., title='リンク余裕 [dB]', description='G-ADR Algorithm 1のdevicemargin。論文値は10 dBです。')
    # ADR's selectable history reducer is replaced by the Gaussian filter.
    history_combine: str = Field('gaussian', pattern='^gaussian$', title='履歴フィルタ', description='平均±標本標準偏差に入るSNRだけを平均します。')


class GADR(ADR):
    Options = Settings
    label = 'G-ADR'
    description = '成功ULのSNRを平均±標準偏差でフィルタし、LinkADRReqでSF・TPを制御します。'
    def __init__(self,rng,**options):
        o = Settings.model_validate(options).model_dump()
        o['history_combine'] = 'avg'
        super().__init__(rng,**o)
    def create_server(self):
        return GADRServer(self.server_options)


class GADRServer(ADRServer):
    def request(self,message_id,radio,powers):
        if message_id != self.last_message:
            power = self.combine(list(powers),self.gw_method)
            self.history.append(power+174-10*math.log10(125000)-6)
            self.last_message = message_id
        if len(self.history) < self.size:
            return None
        mean = sum(self.history)/len(self.history)
        sigma = math.sqrt(sum((x-mean)**2 for x in self.history)/(len(self.history)-1))
        retained = [x for x in self.history if mean-sigma <= x <= mean+sigma]
        snr = sum(retained)/len(retained) if retained else mean
        required = (-7.5,-10.,-12.5,-15.,-17.5,-20.)[radio.sf-7]
        # Paper Eq.5 says int, i.e. truncation toward zero (unlike upstream ADR floor).
        steps = int((snr-required-self.margin)/3)
        sf,tp = radio.sf,radio.tp_dbm
        while steps>0 and sf>7:
            sf -= 1; steps -= 1
        while steps>0 and tp>2:
            tp -= 2; steps -= 1
        while steps<0 and tp<14:
            tp += 2; steps += 1
        if not self.change_power:
            tp = radio.tp_dbm
        # Algorithm 1 ends with NS transmits LinkADRReq, even if settings agree.
        return radio.model_copy(update={'sf':sf,'tp_dbm':tp})


PLUGIN = GADR
