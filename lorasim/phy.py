# SPDX-License-Identifier: GPL-2.0-only
# Equations/tables adapted from signetlabdei/lorawan and ns-3. See NOTICE.
from functools import lru_cache
import math
import numpy as np
from .config import Radio, Channel

NS = 1_000_000_000
GW_SENSITIVITY = (-130., -132.5, -135., -137.5, -140., -142.5)
ED_SENSITIVITY = (-124., -127., -130., -133., -135., -137.)
GOURSAUD = np.array([[6,-16,-18,-19,-19,-20],[-24,6,-20,-22,-22,-22],
                    [-27,-27,6,-23,-25,-25],[-30,-30,-30,6,-26,-28],
                    [-33,-33,-33,-33,6,-29],[-36,-36,-36,-36,-36,6]], dtype=float)


def ns(seconds: float) -> int:
    # ns-3 Seconds(double) rounds to the nearest integer time tick.
    return int(math.floor(seconds * NS + 0.5))


@lru_cache(maxsize=8192)
def _airtime(payload: int, sf: int, cr: int, bw: int, preamble: int, ih: bool, crc: bool) -> int:
    ts = ns(2**sf / bw) / NS
    de = sf in (11,12) and bw == 125000
    numerator = 8*payload - 4*sf + 28 + 16*crc - 20*ih
    symbols = 8 + max(math.ceil(numerator/(4*(sf-2*de))) * (cr+4), 0)
    return ns((preamble + 4.25 + symbols) * ts)


def airtime_ns(phy_bytes: int, p: Radio) -> int:
    return _airtime(phy_bytes,p.sf,p.cr,p.bandwidth_hz,p.preamble_symbols,p.implicit_header,p.crc)


def links(tx_xyz, rx_xyz, tp_dbm: float, c: Channel, backend="numpy"):
    if backend == "scalar":
        distance = np.array([math.dist(tx_xyz,x) for x in rx_xyz])
        loss = np.array([c.reference_loss_db + 10*c.exponent*math.log10(max(d/c.reference_distance_m,1)) for d in distance])
    else:
        distance = np.linalg.norm(rx_xyz - tx_xyz, axis=1)
        loss = c.reference_loss_db + 10*c.exponent*np.log10(np.maximum(distance/c.reference_distance_m,1.))
    delays = np.floor(distance / c.speed_m_s * NS + .5).astype(np.int64)
    return tp_dbm - loss, loss, distance, delays


def destroyed_by(sf: int, signal_j: float, energy_j, matrix="goursaud") -> int:
    for i,e in enumerate(energy_j):
        if e <= 0:
            continue
        if matrix == "aloha":
            if sf == i+7:
                return i+7
        elif 10*math.log10(signal_j / e) < GOURSAUD[sf-7,i]:
            return i+7
    return 0
