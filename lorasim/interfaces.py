from dataclasses import dataclass
from typing import Protocol
from .config import Radio

@dataclass(frozen=True, slots=True)
class Observation:
    time_s: float
    device_id: int
    tx_count: int
    last_ack: bool | None
    last_dl_rssi_dbm: float | None
    last_sf: int | None
    last_tp_dbm: float | None
    position: tuple[float,float,float] | None


@dataclass(frozen=True, slots=True)
class Feedback:
    time_s: float
    ack: bool | None
    dl_rssi_dbm: float | None
    airtime_s: float
    tx_power_dbm: float
    attempt: int
    downlink_received: bool = False


class Policy(Protocol):
    def select(self, observation: Observation) -> Radio: ...
    def observe(self, feedback: Feedback) -> None: ...


