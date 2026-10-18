"""Compatibility alias for v0.5 profiles; use random + Component.action_space."""
from lorasim.interfaces import Options
from .random import RandomPolicy
class RandomSFCH(RandomPolicy):
    Options=Options
    uses_action_space=True
    label='一様ランダム SF×CH（旧名）'
    description='互換名。randomと同一の実装にシミュレータの行動空間を渡します。'
    def __init__(self,rng,action_space): super().__init__(rng,action_space=action_space)
PLUGIN=RandomSFCH
