"""Component factory and backwards-compatible public imports."""
from importlib import import_module
import os
from .config import Component
from .interfaces import Observation, Feedback, Policy
from .policies import Fixed, RandomPolicy, Bandit
from .algorithms import ADR, BlindADR
from .timing import Periodic, Poisson
from .mobility import Stationary, Waypoints, RandomWalk


def make(spec: Component, category: str, **context):
    builtins = {"policy":{"fixed":Fixed,"random":RandomPolicy,"bandit":Bandit,"adr":ADR,"badr":BlindADR},
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
