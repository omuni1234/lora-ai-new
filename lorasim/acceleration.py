"""Equivalent implementation shortcuts; no interference/sensitivity pruning."""
from collections import OrderedDict
import numpy as np
from .phy import links
from .plugins.mobility.random_walk import RandomWalk


class Geometry:
    def __init__(self, simulation):
        self.s = simulation
        self.cache = OrderedDict()
        self.cache_bytes = 0
        # Exact type check: user subclasses may change position semantics.
        self.walkers = [d for d in simulation.moving if type(d.mobility) is RandomWalk]
        self.generic = [d for d in simulation.moving if type(d.mobility) is not RandomWalk]
        self.ids = np.array([d.id for d in self.walkers], dtype=int)
        self.next_boundary = -1.

    def update(self, t):
        s = self.s
        if self.walkers:
            if t >= self.next_boundary:
                self.origins = np.array([d.mobility.initial for d in self.walkers])
                self.velocities = np.array([d.mobility.v for d in self.walkers])
                self.epochs = np.array([d.mobility.t for d in self.walkers])
                self.areas = np.array([d.mobility.area for d in self.walkers])
                steps = np.array([d.mobility.step for d in self.walkers])
                while True:
                    due = np.flatnonzero(t >= self.epochs+steps)
                    if not len(due):
                        break
                    q = (self.origins[due,:2]+self.velocities[due]*steps[due,None]) % (2*self.areas[due])
                    self.origins[due,:2] = np.where(q <= self.areas[due],q,2*self.areas[due]-q)
                    self.epochs[due] += steps[due]
                    for i in due:
                        m = self.walkers[i].mobility
                        # RNGs are private to each ED; preserve their draw sequence.
                        self.velocities[i] = m.velocity()
                        m.initial = self.origins[i].copy()
                        m.t = float(self.epochs[i])
                        m.v = self.velocities[i].copy()
                self.next_boundary = float((self.epochs+steps).min())
            q = (self.origins[:,:2]+self.velocities*(t-self.epochs)[:,None]) % (2*self.areas)
            s.positions[self.ids,:2] = np.where(q <= self.areas,q,2*self.areas-q)
            s.positions[self.ids,2] = self.origins[:,2]
        for d in self.generic:
            value = np.asarray(d.mobility.position(t),dtype=float)
            if value.shape != (3,) or not np.isfinite(value).all():
                raise ValueError("mobility must return three finite coordinates")
            s.positions[d.id] = value

    def links(self, source, power):
        s = self.s
        limit = s.p.execution.geometry_cache_bytes if not s.moving else 0
        if source in self.cache:
            loss,distance,delay = self.cache[source]
            self.cache.move_to_end(source)
            return power-loss,loss,distance,delay
        result = links(s.positions[source],s.positions,power,s.p.channel,s.p.channel_backend)
        size = sum(a.nbytes for a in result[1:])
        if size <= limit:
            while self.cache_bytes+size > limit:
                _,old = self.cache.popitem(last=False)
                self.cache_bytes -= sum(a.nbytes for a in old)
            self.cache[source] = result[1:]
            self.cache_bytes += size
        return result
