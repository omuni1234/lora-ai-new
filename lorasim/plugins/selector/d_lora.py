"""D-LoRa: DOI 10.1109/JIOT.2026.3676777, Eq.19–24, Algorithm 2.

CF/SF/TP base-arm CUCB. Successful ACK is the observable success signal in
this Class A integration, not an oracle for uplink delivery. See docs/PAPERS_V09.md.
"""
import math
from typing import Literal
import numpy as np
from pydantic import Field
from lorasim.config import Radio
from lorasim.interfaces import Policy, Options


class Settings(Options):
    exploration_c: float = Field(2., ge=0, title='CUCB探索係数 c', description='式23：平均報酬+c×sqrt(log(t)/(2×選択回数))。表IIIの既定値2。')
    sf_weight_xi: float = Field(1., ge=0, title='低SF報酬の重み ξ', description='式20：成功指標+ξ×(SF/2^SF)/Σ(SF候補/2^SF候補)。失敗時も加算。表IIIの既定値1。')
    power_weight_eta: float = Field(1.8, ge=0, title='低電力報酬の重み η', description='式21：成功指標+η×(1−TP/ΣTP候補)。失敗時も加算。表IIIの既定値1.8。')
    power_reward_unit: Literal['dbm','mw'] = Field('dbm', title='TP報酬式の数値単位', description='論文は候補をdBm、エネルギー式は線形電力で記述し、式21の単位を明示していません。dbmは候補の数値を使用（正値のみ）、mwは10^(dBm/10)へ変換する明示的な比較条件です。')


class DLoRa(Policy):
    Options = Settings
    label = 'D-LoRa（分解CUCB）'
    description = '論文のCF・SF・TP別報酬とCUCBを実装。EDごとに独立学習。Class Aでは成功ULの完全な通知は得られないためACK受信を成功指標とし、DL損失も失敗に含まれます。'
    uses_action_space = True
    fixed_space_dimensions = ('coding_rates',)
    space_description = 'CH・SF・TPを各基底腕集合として使用。CRは固定。TPを1候補にすればSF×CH比較になります。候補を変えると式20・21の正規化分母も変わります。'
    requires_confirmed = True
    preview_safe = True

    def __init__(self, rng, action_space, **options):
        self.validate_action_space(action_space)
        self.options = Settings.model_validate(options)
        self.rng = rng
        self.values = [np.array(action_space.frequencies_hz), np.array(action_space.spreading_factors), np.array(action_space.powers_dbm)]
        self.cr = action_space.coding_rates[0]
        self.counts = [np.zeros(len(v), dtype=np.int64) for v in self.values]
        self.means = [np.zeros(len(v)) for v in self.values]
        sf = self.values[1].astype(float)
        sf_score = sf / 2.**sf
        tp = self.values[2].astype(float)
        if self.options.power_reward_unit == 'mw':
            tp = 10.**(tp/10)
        elif np.any(tp <= 0):
            raise ValueError('D-LoRa dBm reward requires positive powers; choose mw for nonpositive dBm')
        self.bonuses = [np.zeros(len(self.values[0])), self.options.sf_weight_xi*sf_score/sf_score.sum(),
                        self.options.power_weight_eta*(1-tp/tp.sum())]
        self.updates = 0
        self.pending = None
        self.last_rewards = None

    def select(self, observation):
        selected = []
        for count, mean in zip(self.counts, self.means):
            unseen = np.flatnonzero(count == 0)
            if len(unseen):
                # Paper requires every base arm tried; order/tie-breaking unspecified.
                index = int(self.rng.choice(unseen))
            else:
                score = mean + self.options.exploration_c*np.sqrt(math.log(max(1, self.updates))/(2*count))
                index = int(self.rng.choice(np.flatnonzero(score == score.max())))
            selected.append(index)
        self.pending = selected
        ch, sf, tp = [values[i].item() for values, i in zip(self.values, selected)]
        return Radio(sf=sf, frequency_hz=ch, tp_dbm=tp, cr=self.cr)

    def observe(self, feedback):
        pending, self.pending = self.pending, None
        if pending is None or feedback.ack is None:
            return
        rewards = []
        for count, mean, bonus, i in zip(self.counts, self.means, self.bonuses, pending):
            reward = float(feedback.ack) + bonus[i]
            # Eq.22 denominator includes the current sample. Algorithm 2 prints
            # the increment after the mean update; using the old zero count is undefined.
            count[i] += 1
            mean[i] += (reward-mean[i])/count[i]
            rewards.append(float(reward))
        self.updates += 1
        self.last_rewards = rewards

    def snapshot(self):
        return {'updates':self.updates, 'counts':[x.tolist() for x in self.counts],
                'means':[x.tolist() for x in self.means], 'last_rewards':self.last_rewards}


PLUGIN = DLoRa
