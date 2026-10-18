from dataclasses import dataclass
from abc import ABC, abstractmethod
from pydantic import BaseModel, ConfigDict
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
    attempt: int = 1
    max_attempts: int = 1


@dataclass(frozen=True, slots=True)
class Feedback:
    time_s: float
    ack: bool | None
    dl_rssi_dbm: float | None
    airtime_s: float
    tx_power_dbm: float
    attempt: int
    downlink_received: bool = False
    ul_rssi_dbm: float | None = None
    ul_snr_db: float | None = None
    ul_tx_power_dbm: float | None = None


class Options(BaseModel):
    """Declare GUI fields with pydantic Field(title=..., description=...)."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Plugin(ABC):
    Options = Options
    label = ""
    description = ""
    # Opt in only for bounded, side-effect-free preflight calls.
    preview_safe = False


class Policy(Plugin):
    @abstractmethod
    def select(self, observation: Observation) -> Radio: ...
    def observe(self, feedback: Feedback) -> None:
        pass


class Mobility(Plugin):
    @abstractmethod
    def position(self, time_s: float): ...


class Application(Plugin):
    @abstractmethod
    def next_time(self, observation: Observation, previous_generation_s: float | None) -> float | None: ...


class NetworkPolicy(Policy):
    """Optional LinkADRReq-compatible network controller contract."""
    answer_pending = False
    ack_requested = False
    reset_repetitions = False
    @abstractmethod
    def create_server(self):
        """Return a per-ED controller with request(message_id, radio, powers)."""
    def select_attempt(self, observation, new_frame=True):
        return self.select(observation)
    def sent(self, new_frame):
        pass
    def downlink(self, command):
        pass


