"""Equivalent implementation shortcuts; no interference/sensitivity pruning."""
from collections import OrderedDict
import numpy as np
from .phy import links
from .plugins.mobility.random_walk import RandomWalk
from .plugins.mobility.waypoints import Waypoints
from .plugins.mobility.markov import Markov


class Arrivals:
    """Sorted (time, FIFO id, receiver) view without N Python tuples per DL."""
    def __init__(self,delay,order,base,start):
        self.delay,self.order,self.base,self.start = delay,order,base,start
    def __len__(self):
        return len(self.order)
    def __getitem__(self,index):
        r = int(self.order[index])
        return self.start+int(self.delay[r]),self.base+r,r


def arrival_order(delay,n):
    # Stable sorting keeps the original receiver/FIFO order for equal delays.
    order = np.argsort(delay[:n],kind='stable')
    ranks = np.empty(n,dtype=np.intp)
    ranks[order] = np.arange(n)
    return order,ranks


class Geometry:
    def __init__(self, simulation):
        self.s = simulation
        self.cache = OrderedDict()
        self.cache_bytes = 0
        # Exact type check: user subclasses may change position semantics.
        self.walkers = [d for d in simulation.moving if type(d.mobility) is RandomWalk]
        self.routes = [d for d in simulation.moving if type(d.mobility) in (Waypoints,Markov)]
        self.generic = [d for d in simulation.moving if type(d.mobility) not in (RandomWalk,Waypoints,Markov)]
        self.route_boundaries = np.full(len(self.routes),-1.)
        self.route_next = -1.
        self.route_active = set()
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
        if self.routes:
            due = np.flatnonzero(t >= self.route_boundaries) if t >= self.route_next else ()
            refreshed = set()
            for i in due:
                d = self.routes[i]; m = d.mobility
                value = m.position(t)
                if not np.isfinite(value).all():
                    raise ValueError('mobility must return three finite coordinates')
                s.positions[d.id] = value
                refreshed.add(i)
                if type(m) is Markov or (type(m) is Waypoints and m.options.mode == 'random_waypoint'):
                    moving = t < m.arrive
                    end = m.arrive if moving else m.leave
                else:
                    j = min(len(m.a)-1,int(np.searchsorted(m.a[:,0],t,side='right'))-1)
                    moving = j+1<len(m.a) and bool(np.any(m.a[j,1:] != m.a[j+1,1:]))
                    end = m.a[j+1,0] if j+1<len(m.a) else (t if m.options.mode=='route' and m.options.repeat else np.inf)
                self.route_boundaries[i] = end
                if moving: self.route_active.add(i)
                else: self.route_active.discard(i)
            if len(due): self.route_next = float(self.route_boundaries.min())
            for i in self.route_active-refreshed:
                d = self.routes[i]
                # Preserve np.interp arithmetic exactly; only constant segments skip work.
                value = d.mobility.position(t)
                if not np.isfinite(value).all():
                    raise ValueError('mobility must return three finite coordinates')
                s.positions[d.id] = value
        for d in self.generic:
            value = np.asarray(d.mobility.position(t),dtype=float)
            if value.shape != (3,) or not np.isfinite(value).all():
                raise ValueError("mobility must return three finite coordinates")
            s.positions[d.id] = value

    def links(self, source, power):
        s = self.s
        limit = s.p.execution.geometry_cache_bytes if not s.moving else 0
        if source in self.cache:
            loss,distance,delay = self.cache[source][:3]
            self.cache.move_to_end(source)
            return power-loss,loss,distance,delay
        result = links(s.positions[source],s.positions,power,s.p.channel,s.p.channel_backend)
        cached = result[1:]
        if limit and source >= s.n:
            cached = (*cached,*arrival_order(result[3],s.n))
        size = sum(a.nbytes for a in cached)
        if size <= limit:
            while self.cache_bytes+size > limit:
                _,old = self.cache.popitem(last=False)
                self.cache_bytes -= sum(a.nbytes for a in old)
            self.cache[source] = cached
            self.cache_bytes += size
        return result

    def arrivals(self,source,delay,base,start):
        cached = self.cache.get(source)
        order,ranks = cached[3:] if cached is not None and len(cached)==5 else arrival_order(delay,self.s.n)
        return Arrivals(delay,order,base,start),ranks
