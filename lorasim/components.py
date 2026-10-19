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
    environment = context.pop('environment', None)
    if getattr(cls, 'uses_mobility_environment', False):
        context['environment'] = environment
    space = context.pop('action_space',None)
    bound = category=='policy' and (spec.action_space is not None or getattr(cls,'uses_action_space',False))
    if spec.action_space is not None and category!='policy':
        raise ValueError('action_space is only available for policy plugins')
    if bound:
        if not (getattr(cls,'accepts_action_space',False) or getattr(cls,'uses_action_space',False)):
            raise ValueError('selector does not accept configurable action_space: '+spec.kind)
        if space is None:
            from .config import ActionSpace
            space = ActionSpace()
        space = space.restrict(spec.action_space) if spec.action_space is not None else space.model_copy(deep=True)
        if hasattr(cls, 'validate_action_space'):
            cls.validate_action_space(space)
        context['action_space'] = space
    options = getattr(cls,'Options',None)
    values = options.model_validate(spec.options).model_dump() if options else spec.options
    instance = cls(**context,**values)
    if bound:
        instance.action_keys = frozenset((r.sf,r.frequency_hz,r.tp_dbm,r.cr) for r in space.radios())
        instance.bound_action_space = space
    return instance
