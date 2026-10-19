"""Bounded, stateless link-budget preview using the simulator's actual PHY.

No simulation, plugin, RNG, job, or tracking state is constructed here.
Distances are 3D straight-line separation, not ground distance.
"""
import math
import sys
from typing import Literal

import numpy as np
from pydantic import Field

from .config import Model, Channel, Radio
from .phy import links, airtime_ns, GW_SENSITIVITY, ED_SENSITIVITY, NS


class CalculatorRequest(Model):
    channel: Channel = Field(default_factory=Channel)
    radio: Radio = Field(default_factory=Radio)
    direction: Literal['uplink', 'downlink'] = 'uplink'
    margin_db: float = Field(0, ge=0, le=60)
    probe_distance_m: float = Field(1000, ge=0, le=10_000_000)
    max_distance_m: float | None = Field(None, ge=1, le=10_000_000)
    phy_bytes: int = Field(29, ge=1, le=255)


def calculate(request: CalculatorRequest):
    r, c = request.radio, request.channel
    sensitivity = (GW_SENSITIVITY if request.direction == 'uplink' else ED_SENSITIVITY)[r.sf-7]
    required = sensitivity + request.margin_db
    headroom = r.tp_dbm - c.reference_loss_db - required
    if headroom < 0:
        # Even d <= d0 fails. Zero metres would incorrectly imply a usable link.
        limit, state = None, 'below_at_reference'
    else:
        log_limit = math.log(c.reference_distance_m) + (headroom/c.exponent)*(math.log(10)/10)
        if log_limit >= math.log(sys.float_info.max):
            limit, state = None, 'beyond_numeric_range'
        else:
            limit, state = math.exp(log_limit), 'finite'
    xmax = request.max_distance_m
    if xmax is None:
        xmax = min(10_000_000., max(100., request.probe_distance_m*1.1,
                                   (limit*1.25 if limit is not None else 1000.)))
        if state == 'beyond_numeric_range':
            xmax = 10_000_000.
    # Linear plotting, with denser sampling around the d0 knee. At most 453
    # distances irrespective of ED count, simulation length, or plotted range.
    extra = [v for v in (c.reference_distance_m, limit, request.probe_distance_m)
             if v is not None and 0 <= v <= xmax]
    distances = np.unique(np.concatenate((np.linspace(0, xmax, 321),
        np.geomspace(min(c.reference_distance_m, xmax), xmax, 128), extra)))
    positions = np.zeros((len(distances)+1, 3))
    positions[:, 0] = np.append(distances, request.probe_distance_m)
    try:
        with np.errstate(over='raise', invalid='raise', divide='raise'):
            power, loss, _, _ = links(np.zeros(3), positions, r.tp_dbm, c)
        if not np.all(np.isfinite(power)) or not np.all(np.isfinite(loss)):
            raise FloatingPointError('nonfinite received power')
    except (FloatingPointError, OverflowError) as exc:
        raise ValueError('calculator numeric range exceeded') from exc
    return {
        'direction': request.direction, 'radio': r.model_dump(), 'channel': c.model_dump(),
        'sensitivity_dbm': sensitivity, 'required_dbm': required, 'margin_db': request.margin_db,
        'range_m': limit, 'range_state': state, 'max_distance_m': xmax,
        'distances_m': distances.tolist(), 'received_dbm': power[:-1].tolist(),
        'probe': {'distance_m': request.probe_distance_m, 'received_dbm': float(power[-1]),
                  'path_loss_db': float(loss[-1]), 'margin_db': float(power[-1]-sensitivity),
                  'meets_required': bool(power[-1] >= required)},
        'phy_bytes': request.phy_bytes, 'airtime_ms': airtime_ns(request.phy_bytes, r)/NS*1000,
        'notes': [
            '感度条件だけを満たす距離の目安です。干渉・GW送信中・復調器不足・受信窓・ACK往復の成功は保証しません。',
            '距離は送受信点間の直線距離です。基準距離以下では基準損失を使います。',
            '現在のモデルではCRによる感度改善はありません。CRは送信時間に影響し、到達距離は変わりません。',
            '帯域幅は125 kHz。周波数は固定した基準損失に追加の補正を与えません。',
        ],
    }
