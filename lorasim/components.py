from __future__ import annotations
from dataclasses import dataclass
from importlib import import_module
import math
import os
from typing import Protocol
import numpy as np
from .config import Component, Radio


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


class Policy(Protocol):
    def select(self, observation: Observation) -> Radio: ...
    def observe(self, feedback: Feedback) -> None: ...


class Fixed:
    def __init__(self, rng, **options):
        self.radio = Radio.model_validate(options)
    def select(self, observation):
        return self.radio.model_copy()
    def observe(self, feedback):
        pass


class RandomPolicy:
    def __init__(self, rng, actions=None):
        self.rng = rng
        self.actions = [Radio.model_validate(x) for x in (actions or [{"sf":s} for s in range(7,13)])]
        if not self.actions:
            raise ValueError("actions cannot be empty")
    def select(self, observation):
        return self.actions[int(self.rng.integers(len(self.actions)))].model_copy()
    def observe(self, feedback):
        pass


class Bandit(RandomPolicy):
    """Per-ED epsilon-greedy bandit. Rewards use only ACK and local energy proxy."""
    def __init__(self, rng, actions=None, epsilon=.1, energy_weight=.02):
        super().__init__(rng, actions)
        if not 0 <= epsilon <= 1 or energy_weight < 0:
            raise ValueError("invalid bandit hyperparameters")
        self.epsilon, self.energy_weight = epsilon, energy_weight
        self.q, self.n = np.zeros(len(self.actions)), np.zeros(len(self.actions))
        self.selected = 0
    def select(self, observation):
        self.selected = int(self.rng.integers(len(self.actions))) if self.rng.random() < self.epsilon else int(np.argmax(self.q))
        return self.actions[self.selected].model_copy()
    def observe(self, feedback):
        if feedback.ack is None:
            return
        reward = float(feedback.ack) - self.energy_weight * feedback.airtime_s * 10**(feedback.tx_power_dbm/10)
        i = self.selected
        self.n[i] += 1
        self.q[i] += (reward-self.q[i])/self.n[i]


class Periodic:
    def __init__(self, rng, interval_s=60., first_s=0., random_phase=False, jitter_s=0.):
        if not all(math.isfinite(x) for x in [interval_s, first_s, jitter_s]) or interval_s <= 0 or first_s < 0 or not 0 <= jitter_s < interval_s:
            raise ValueError("invalid periodic timing")
        self.rng, self.interval, self.jitter = rng, interval_s, jitter_s
        self.first = first_s + (rng.uniform(0,interval_s) if random_phase else 0.)
    def next_time(self, observation, previous_generation_s):
        if previous_generation_s is None:
            return self.first
        return max(observation.time_s + 1e-9, previous_generation_s + self.interval + self.rng.uniform(-self.jitter,self.jitter))


class Poisson:
    def __init__(self, rng, mean_s=60.):
        if not math.isfinite(mean_s) or mean_s <= 0:
            raise ValueError("mean_s must be positive")
        self.rng, self.mean = rng, mean_s
    def next_time(self, observation, previous_generation_s):
        return observation.time_s + max(1e-9,float(self.rng.exponential(self.mean)))


class Stationary:
    def __init__(self, initial, area, rng):
        self.initial = np.array(initial,dtype=float)
    def position(self,t):
        return self.initial


class Waypoints(Stationary):
    def __init__(self, initial, area, rng, points):
        super().__init__(initial,area,rng)
        a = np.asarray(points,dtype=float)
        if a.ndim != 2 or a.shape[1] != 4 or len(a) == 0 or not np.isfinite(a).all():
            raise ValueError("waypoints are [time_s,x_m,y_m,z_m]")
        if a[0,0] < 0 or (np.diff(a[:,0]) <= 0).any():
            raise ValueError("waypoint times must strictly increase from >=0")
        if a[0,0] > 0:
            a = np.vstack([[0,*initial],a])
        self.a = a
    def position(self,t):
        return np.array([np.interp(t,self.a[:,0],self.a[:,j]) for j in range(1,4)])


class RandomWalk(Stationary):
    """Time-based, constant-speed 2D random walk with reflecting area boundaries."""
    def __init__(self, initial, area, rng, speed_m_s=1., step_s=10.):
        super().__init__(initial,area,rng)
        if not math.isfinite(speed_m_s) or not math.isfinite(step_s) or speed_m_s < 0 or step_s <= 0:
            raise ValueError("invalid random-walk parameters")
        if any(initial[i]<0 or initial[i]>area[i] for i in range(2)):
            raise ValueError("random-walk initial position outside area")
        self.area, self.rng, self.speed, self.step = np.array(area),rng,speed_m_s,step_s
        self.t = 0.
        self.v = self.velocity()
    def velocity(self):
        theta = self.rng.uniform(0,2*math.pi)
        return self.speed*np.array([math.cos(theta),math.sin(theta)])
    def folded(self,dt):
        p = self.initial.copy()
        q = (p[:2]+self.v*dt) % (2*self.area)
        p[:2] = np.where(q <= self.area,q,2*self.area-q)
        return p
    def position(self,t):
        if t < self.t - 1e-9:
            raise ValueError("mobility queries must be chronological")
        while t >= self.t + self.step:
            self.initial = self.folded(self.step)
            self.t += self.step
            self.v = self.velocity()
        return self.folded(t-self.t)


def make(spec: Component, category: str, **context):
    builtins = {"policy":{"fixed":Fixed,"random":RandomPolicy,"bandit":Bandit},
                "timing":{"periodic":Periodic,"poisson":Poisson},
                "mobility":{"stationary":Stationary,"waypoints":Waypoints,"random_walk":RandomWalk}}
    if spec.kind in builtins[category]:
        constructor = builtins[category][spec.kind]
    elif ":" in spec.kind:
        if os.getenv("LORASIM_ALLOW_PLUGINS") != "1":
            raise ValueError("custom Python code requires LORASIM_ALLOW_PLUGINS=1 on the worker")
        module,name = spec.kind.split(":",1)
        constructor = getattr(import_module(module),name)
    else:
        raise ValueError(f"unknown {category}: {spec.kind}")
    return constructor(**context,**spec.options)
