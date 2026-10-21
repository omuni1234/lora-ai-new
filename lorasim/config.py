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


class ActionSelection(Model):
    spreading_factors: list[int] | None = Field(None, min_length=1, title="EDの候補SF", description="未指定なら全体の集合を継承。1値だけ選ぶと固定します。")
    frequencies_hz: list[int] | None = Field(None, min_length=1, title="EDの候補CH [Hz]", description="全体の候補CHの部分集合。未指定なら全候補を使います。")
    coding_rates: list[int] | None = Field(None, min_length=1, title="EDの候補符号化率", description="1〜4は4/5〜4/8。例 [1] はCR固定。全体集合の部分集合を指定します。")
    powers_dbm: list[float] | None = Field(None, min_length=1, title="EDの候補送信電力 [dBm]", description="例 [14] は14 dBmに固定。未指定なら全体の集合を継承します。")


class Component(Model):
    kind: str = "fixed"
    ref: str | None = None
    options: dict = Field(default_factory=dict)
    action_space: ActionSelection | None = Field(None, title="EDの行動空間", description="選択器だけで使用。{} は全体集合を継承。各次元の部分集合で選択・固定を指定。未指定は旧プロファイルの動作を維持します。")


class ActionSpace(Model):
    spreading_factors: list[int] = Field(default_factory=lambda:list(range(7,13)), min_length=1, max_length=6, title="利用可能なSF", description="重複のない7〜12。SF×CH×CR×TPの直積を各選択器へ渡します。")
    frequencies_hz: list[int] = Field(default_factory=lambda:[868100000,868300000,868500000], min_length=1, max_length=64, title="利用可能なCH [Hz]", description="許可ULサブバンドに登録された中心周波数の集合。")
    coding_rates: list[int] = Field(default_factory=lambda:[1], min_length=1, max_length=4, title="利用可能な符号化率", description="1〜4は4/5〜4/8。候補が1値なら全EDで固定します。")
    powers_dbm: list[float] = Field(default_factory=lambda:[14.], min_length=1, max_length=48, title="利用可能な送信電力 [dBm]", description="−20〜27 dBmかつ全候補CHのサブバンド上限以下。候補が1値なら固定します。")

    @model_validator(mode="before")
    @classmethod
    def legacy_scalars(cls, value):
        if isinstance(value,dict):
            value=dict(value)
            for old,new in [('tp_dbm','powers_dbm'),('cr','coding_rates')]:
                if old in value:
                    if new in value and value[new]!=[value[old]]:
                        raise ValueError('action_space legacy scalar conflicts with candidates')
                    value[new]=[value.pop(old)]
        return value

    @model_validator(mode="after")
    def valid(self):
        for name,lo,hi in [('spreading_factors',7,12),('coding_rates',1,4),('powers_dbm',-20,27)]:
            values=getattr(self,name)
            if len(set(values))!=len(values) or any(not math.isfinite(x) or not lo<=x<=hi for x in values):
                raise ValueError('action_space candidates must be unique and in range: '+name)
        if len(set(self.frequencies_hz))!=len(self.frequencies_hz):
            raise ValueError('action_space channels must be unique')
        return self

    @property
    def tp_dbm(self):  # compatibility for external v0.5 plugins
        if len(self.powers_dbm)!=1: raise ValueError('legacy plugin requires fixed TP')
        return self.powers_dbm[0]

    @property
    def cr(self):
        if len(self.coding_rates)!=1: raise ValueError('legacy plugin requires fixed CR')
        return self.coding_rates[0]

    @property
    def size(self):
        return math.prod(len(getattr(self,k)) for k in type(self).model_fields)

    def restrict(self, selection):
        values=self.model_dump()
        for key,subset in selection.model_dump(exclude_none=True).items():
            if not set(subset)<=set(values[key]):
                raise ValueError('ED action_space must be a subset of global candidates: '+key)
            values[key]=subset
        return ActionSpace.model_validate(values)

    def radios(self):
        return [Radio(sf=s,frequency_hz=f,cr=c,tp_dbm=t)
                for s in self.spreading_factors for f in self.frequencies_hz
                for c in self.coding_rates for t in self.powers_dbm]


class RadioEnergy(Model):
    enabled: bool = True
    voltage_v: float = Field(3.0, gt=0)
    initial_state: Literal["STANDBY", "SLEEP"] = "STANDBY"
    sleep_current_a: float = Field(0.1e-6, ge=0)
    standby_current_a: float = Field(1.4e-3, ge=0)
    rx_current_a: float = Field(10.8e-3, ge=0)
    tx_current_model: Literal["constant", "linear"] = "constant"
    tx_current_a: float = Field(28e-3, ge=0)
    tx_voltage_v: float = Field(3.3, gt=0)
    eta: float = Field(.452750, gt=0, le=1)
    base_current_a: float = Field(.014646, ge=0)


class MCU(Model):
    enabled: bool = False
    mode: Literal["fixed", "declared", "measured"] = "fixed"
    active_power_w: float = Field(.015, ge=0)
    idle_power_w: float = Field(0, ge=0)
    inference_s: float = Field(.001, ge=0)
    training_s: float = Field(.005, ge=0)

    @model_validator(mode="after")
    def powers(self):
        if self.active_power_w < self.idle_power_w:
            raise ValueError("MCU active_power_w must be >= idle_power_w")
        return self


class Energy(Model):
    radio: RadioEnergy = Field(default_factory=RadioEnergy)
    mcu: MCU = Field(default_factory=MCU)


class Group(Model):
    name: str = ""
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
    mcu: MCU | None = None

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
    id: str = ""
    position: tuple[float, float, float]
    demodulators: int = Field(8, ge=1, le=1024)
    frequencies_hz: list[int] = Field(default_factory=lambda: [868100000, 868300000, 868500000])


class SubBand(Model):
    id: str = ""
    low_hz: int
    high_hz: int
    duty_cycle: float = Field(gt=0, le=1)
    max_tp_dbm: float
    channels_hz: list[int] = Field(default_factory=list)


EU_BANDS = [
    SubBand(id=f"band{i}", low_hz=a, high_hz=b, duty_cycle=c, max_tp_dbm=d,
            channels_hz={2:[868100000,868300000,868500000],4:[869525000]}.get(i,[]))
    for i, (a, b, c, d) in enumerate([(863000000, 865000000, .001, 14),
                       (865000000, 868000000, .01, 14),
                       (868000000, 868600000, .01, 14),
                       (868700000, 869200000, .001, 14),
                       (869400000, 869650000, .1, 27),
                       (869700000, 870000000, .01, 14)])
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


class Execution(Model):
    optimized: bool = Field(True, title="等価な高速処理を有効化", description="DL到着イベントの集約、静止リンクの再利用、組込みランダムウォークの配列計算を使います。オフは比較検証用です。")
    geometry_cache_bytes: int = Field(67108864, ge=0, le=1073741824, title="静止リンクのキャッシュ上限 [byte]", description="全端末が静止している場合の距離・損失・遅延をLRUで再利用します。0で無効。電力や干渉の省略は行いません。")
    writer_processes: int = Field(0, ge=0, le=8, title="試験機能：保存処理の並列プロセス数", description="0は逐次保存。1〜8ではArrow変換とParquet圧縮を別CPUプロセスへ分配します。イベントの因果順序は維持します。小規模実験では起動・転送コストで遅くなる場合があります。")


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
    ul_sub_bands: list[str] | None = None
    dl_sub_bands: list[str] | None = None
    action_space: ActionSpace = Field(default_factory=ActionSpace, title="全体のパラメータ空間", description="SF×CH×CR×TPの候補集合。各EDの選択器には、この部分集合からなる行動空間を渡します。行動ID順はSF→CH→CR→TP。")
    gateways: list[Gateway] = Field(default_factory=lambda: [Gateway(id="gw0", position=(1000,1000,15))])
    groups: list[Group] = Field(default_factory=lambda: [Group(name="devices", count=10)])
    assignments: list[Assignment] = Field(default_factory=list)
    tracking: Tracking = Field(default_factory=Tracking)
    execution: Execution = Field(default_factory=Execution)
    notifications: list[Notification] = Field(default_factory=list)
    channel_backend: Literal["numpy", "scalar"] = "numpy"
    energy: Energy = Field(default_factory=Energy)
    # Catalogs are embedded in run snapshots; files are only a CLI authoring aid.
    models: dict[str, dict[str, Component]] = Field(default_factory=dict)
    model_files: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def auto_identifiers(cls, data):
        if not isinstance(data,dict):
            return data
        data = dict(data)
        for collection,key,prefix in [('gateways','id','gw'),('groups','name','group'),('sub_bands','id','band')]:
            if collection not in data:
                continue
            rows = [x.model_dump() if isinstance(x,BaseModel) else dict(x) for x in data[collection]]
            used = {x.get(key) for x in rows if x.get(key)}
            counter = 0
            for row in rows:
                if not row.get(key):
                    while f'{prefix}{counter}' in used:
                        counter += 1
                    row[key] = f'{prefix}{counter}'
                    used.add(row[key])
            data[collection] = rows
        return data

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
        band_ids = {b.id for b in self.sub_bands}
        if len(band_ids) != len(self.sub_bands):
            raise ValueError("duplicate sub-band id")
        for direction in ('ul','dl'):
            allowed = getattr(self,direction+'_sub_bands')
            if allowed is not None and (not allowed or len(set(allowed)) != len(allowed) or set(allowed)-band_ids):
                raise ValueError(f'{direction}_sub_bands must contain unique, known sub-band IDs')
        for b in self.sub_bands:
            if len(set(b.channels_hz)) != len(b.channels_hz) or any(not b.low_hz <= f < b.high_hz for f in b.channels_hz):
                raise ValueError(f'{b.id}: channels must be unique and inside the sub-band')
        n = sum(g.count for g in self.groups)
        for a in self.assignments:
            if set(a.groups) - {g.name for g in self.groups} or any(i < 0 or i >= n for i in a.ids):
                raise ValueError("assignment refers to unknown group/device")
        self.direction_band(self.mac.rx2_frequency_hz,'dl')
        # Validate only when used: old/custom profiles may use other regions.
        from .registry import constructor
        for spec in [g.policy for g in self.groups]+[a.policy for a in self.assignments if a.policy is not None]:
            resolved = self.component(spec,'policy')
            if resolved.action_space is not None or getattr(constructor('policy',resolved.kind),'uses_action_space',False):
                self.validate_action_space()
                cls=constructor('policy',resolved.kind)
                space = self.action_space
                if resolved.action_space is not None:
                    if not (getattr(cls,'accepts_action_space',False) or getattr(cls,'uses_action_space',False)):
                        raise ValueError('selector does not accept configurable action_space: '+resolved.kind)
                    space = self.action_space.restrict(resolved.action_space)
                if hasattr(cls, 'validate_action_space'):
                    cls.validate_action_space(space)
        if set(self.models) - {"policy", "mobility", "timing"}:
            raise ValueError("model categories must be policy, mobility or timing")
        return self

    def validate_action_space(self):
        for f in self.action_space.frequencies_hz:
            b = self.sub_bands[self.direction_band(f,'ul')]
            if f not in b.channels_hz:
                raise ValueError(f"action_space CH {f} must be a registered UL sub-band center frequency")
            if max(self.action_space.powers_dbm) > b.max_tp_dbm:
                raise ValueError("action_space TP exceeds a candidate sub-band maximum")

    def component(self, spec: Component, category: str) -> Component:
        seen = set()
        def resolve(value):
            if value.ref is None:
                return value.model_copy(deep=True)
            if value.ref in seen:
                raise ValueError(f"cyclic {category} reference: {value.ref}")
            seen.add(value.ref)
            try:
                base = resolve(self.models[category][value.ref])
            except KeyError:
                raise ValueError(f"unknown {category} reference: {value.ref}") from None
            return Component(kind=base.kind, options={**base.options, **value.options},
                             action_space=value.action_space if value.action_space is not None else base.action_space)
        return resolve(spec)

    def band(self, frequency: int) -> int:
        for i,b in enumerate(self.sub_bands):
            if b.low_hz <= frequency < b.high_hz:
                return i
        raise ValueError(f"frequency {frequency} is outside configured sub-bands")

    def direction_band(self, frequency: int, direction: str) -> int:
        index = self.band(frequency)
        allowed = getattr(self,direction+'_sub_bands')
        if allowed is not None and self.sub_bands[index].id not in allowed:
            raise ValueError(f'{direction.upper()} frequency {frequency} is in a disabled sub-band')
        return index


def load_profile(source: str | Path, *, text: bool = False, catalog=None) -> Profile:
    content = str(source) if text else Path(source).read_text()
    if len(content) > 2_000_000:
        raise ValueError("profile exceeds 2 MB")
    data = yaml.safe_load(content)
    if not isinstance(data, dict):
        raise ValueError("profile must be a mapping")
    merged = {k:dict(v) for k,v in (catalog or {}).items()}
    for filename in data.get("model_files", []):
        if text:
            raise ValueError("model_files are CLI-only; use the model catalog or embed models in Web profiles")
        path = (Path(source).resolve().parent / filename).resolve()
        raw = path.read_text()
        if len(raw) > 2_000_000:
            raise ValueError("model file exceeds 2 MB")
        definitions = yaml.safe_load(raw)
        if not isinstance(definitions, dict):
            raise ValueError("model file must map categories to named components")
        for k,v in definitions.items():
            merged.setdefault(k, {}).update(v)
    for k,v in data.get("models", {}).items():
        merged.setdefault(k, {}).update(v)
    data["models"], data["model_files"] = merged, []
    return Profile.model_validate(data)


def dump_profile(profile: Profile) -> str:
    return yaml.safe_dump(profile.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
