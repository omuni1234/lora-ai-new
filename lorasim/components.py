"""Validated model factory and backwards-compatible public imports."""
from .config import Component
from .interfaces import Observation, Feedback, Policy, Mobility, Application, NetworkPolicy
from .policies import Fixed, RandomPolicy, Bandit
from .algorithms import ADR, BlindADR
from .timing import Periodic, Poisson
from .mobility import Stationary, Waypoints, RandomWalk
from .registry import constructor


def make(spec: Component, category: str, **context):
    cls = constructor(category,spec.kind)
    options = getattr(cls,'Options',None)
    values = options.model_validate(spec.options).model_dump() if options else spec.options
    return cls(**context,**values)
