from __future__ import annotations
import math
from pathlib import Path
from typing import Literal
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Radio(Model):
    sf: int = Field(7, ge=7, le=12)
    tp_dbm: float = Field(14, ge=-20, le=27)
    cr: int = Field(1, ge=1, le=4)  # 1..4 means 4/5..4/8
    frequency_hz: int = 868100000
    bandwidth_hz: Literal[125000] = 125000
    preamble_symbols: int = Field(8, ge=6, le=65535)
    implicit_header: bool = False
    crc: bool = True


class Component(Model):
    kind: str = "fixed"
    options: dict = Field(default_factory=dict)


class Group(Model):
    name: str
    count: int = Field(1, ge=1, le=1000000)
    tags: list[str] = Field(default_factory=list)
    placement: Literal["uniform", "grid", "explicit"] = "uniform"
    positions: list[tuple[float, float, float]] = Field(default_factory=list)
    policy: Component = Field(default_factory=Component)
    timing: Component = Field(default_factory=lambda: Component(kind="periodic", options={"interval_s": 60}))
    mobility: Component = Field(default_factory=lambda: Component(kind="stationary"))
    payload_bytes: int = Field(20, ge=0, le=222)
    confirmed: bool = True
    max_attempts: int = Field(1, ge=1, le=15)
    observe_position: bool = False

    @model_validator(mode="after")
    def explicit_count(self):
        if self.placement == "explicit" and len(self.positions) != self.count:
            raise ValueError("explicit positions must have count entries")
        return self


class Assignment(Model):
    groups: list[str] = Field(default_factory=list)
    ids: list[int] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    policy: Component | None = None
    timing: Component | None = None
    mobility: Component | None = None


class Gateway(Model):
    id: str
    position: tuple[float, float, float]
    demodulators: int = Field(8, ge=1, le=1024)
    frequencies_hz: list[int] = Field(default_factory=lambda: [868100000, 868300000, 868500000])


class SubBand(Model):
    low_hz: int
    high_hz: int
    duty_cycle: float = Field(gt=0, le=1)
    max_tp_dbm: float


EU_BANDS = [
    SubBand(low_hz=a, high_hz=b, duty_cycle=c, max_tp_dbm=d)
    for a, b, c, d in [(863000000, 865000000, .001, 14),
                       (865000000, 868000000, .01, 14),
                       (868000000, 868600000, .01, 14),
                       (868700000, 869200000, .001, 14),
                       (869400000, 869650000, .1, 27),
                       (869700000, 870000000, .01, 14)]
]


class Channel(Model):
    exponent: float = Field(3.76, gt=0)
    reference_distance_m: float = Field(1, gt=0)
    reference_loss_db: float = Field(7.7, ge=0)
    speed_m_s: float = Field(299792458, gt=0)
    collision_matrix: Literal["goursaud", "aloha"] = "goursaud"


class Mac(Model):
    rx1_delay_s: float = Field(1, gt=0)
    rx2_delay_s: float = Field(2, gt=0)
    rx_symbols: int = Field(8, ge=1, le=1023)
    rx1_sf_offset: int = Field(0, ge=0, le=5)
    rx2_sf: int = Field(12, ge=7, le=12)
    rx2_frequency_hz: int = 869525000
    backhaul_delay_s: float = Field(0, ge=0)
    duty_cycle: bool = True
    @model_validator(mode="after")
    def delays(self):
        if self.rx2_delay_s <= self.rx1_delay_s + self.rx_symbols * 2**12 / 125000:
            raise ValueError("RX2 must follow the longest RX1 listening timeout")
        return self


class Tracking(Model):
    batch_rows: int = Field(4096, ge=1, le=1000000)
    max_air_signals: int = Field(100000, ge=1)
    max_air_bytes: int = Field(536870912, ge=1024)


class Notification(Model):
    url: str
    every_percent: float = Field(10, gt=0, le=100)
    timeout_s: float = Field(5, gt=0, le=60)
    @model_validator(mode="after")
    def http_url(self):
        from urllib.parse import urlsplit
        u = urlsplit(self.url)
        if u.scheme not in {"http", "https"} or not u.hostname or u.username:
            raise ValueError("notification URL must be http(s) without credentials")
        return self


class Profile(Model):
    schema_version: Literal[1] = 1
    name: str = "experiment"
    seed: int = Field(1, ge=0, le=2**32-1)
    duration_s: float = Field(600, gt=0, le=1e9)
    area_m: tuple[float, float] = (2000, 2000)
    channel: Channel = Field(default_factory=Channel)
    mac: Mac = Field(default_factory=Mac)
    sub_bands: list[SubBand] = Field(default_factory=lambda: [x.model_copy() for x in EU_BANDS])
    gateways: list[Gateway] = Field(default_factory=lambda: [Gateway(id="gw0", position=(1000,1000,15))])
    groups: list[Group] = Field(default_factory=lambda: [Group(name="devices", count=10)])
    assignments: list[Assignment] = Field(default_factory=list)
    tracking: Tracking = Field(default_factory=Tracking)
    notifications: list[Notification] = Field(default_factory=list)
    channel_backend: Literal["numpy", "scalar"] = "numpy"

    @model_validator(mode="after")
    def consistent(self):
        if min(self.area_m) <= 0 or not all(math.isfinite(x) for x in self.area_m):
            raise ValueError("area_m must be positive and finite")
        if not self.gateways or not self.groups:
            raise ValueError("at least one gateway and ED group is required")
        if len({g.id for g in self.gateways}) != len(self.gateways):
            raise ValueError("duplicate gateway id")
        if len({g.name for g in self.groups}) != len(self.groups):
            raise ValueError("duplicate group name")
        bands = sorted(self.sub_bands, key=lambda b: b.low_hz)
        if not bands or any(b.low_hz >= b.high_hz for b in bands):
            raise ValueError("invalid sub-band")
        if any(a.high_hz > b.low_hz for a,b in zip(bands,bands[1:])):
            raise ValueError("overlapping sub-bands")
        n = sum(g.count for g in self.groups)
        for a in self.assignments:
            if set(a.groups) - {g.name for g in self.groups} or any(i < 0 or i >= n for i in a.ids):
                raise ValueError("assignment refers to unknown group/device")
        self.band(self.mac.rx2_frequency_hz)
        return self

    def band(self, frequency: int) -> int:
        for i,b in enumerate(self.sub_bands):
            if b.low_hz <= frequency < b.high_hz:
                return i
        raise ValueError(f"frequency {frequency} is outside configured sub-bands")


def load_profile(source: str | Path, *, text: bool = False) -> Profile:
    content = str(source) if text else Path(source).read_text()
    if len(content) > 2_000_000:
        raise ValueError("profile exceeds 2 MB")
    return Profile.model_validate(yaml.safe_load(content))


def dump_profile(profile: Profile) -> str:
    return yaml.safe_dump(profile.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
