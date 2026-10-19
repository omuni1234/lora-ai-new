"""Run-scoped, immutable geometry shared by mobility instances, never their RNGs."""
import numpy as np


class MobilityPreviewLimit(ValueError):
    """Only request-driven previews impose a shared transition budget."""


class MobilityEnvironment:
    def __init__(self, seed=0, max_transitions=None):
        self.seed = seed
        self._waypoints = {}
        self.remaining = max_transitions

    def transition(self):
        if self.remaining is not None:
            if self.remaining <= 0:
                raise MobilityPreviewLimit('waypoint preview transition budget exhausted')
            self.remaining -= 1

    @property
    def point_sets(self):
        return tuple(self._waypoints.values())

    def waypoints(self, area, count, seed, height):
        key = (tuple(area), count, seed, height)
        if key not in self._waypoints:
            rng = np.random.default_rng(np.random.SeedSequence([self.seed, seed, 920]))
            points = np.column_stack((rng.uniform([0, 0], area, (count, 2)), np.full(count, height)))
            points.flags.writeable = False
            self._waypoints[key] = points
        return self._waypoints[key]
