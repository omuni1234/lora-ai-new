"""Independent movement models, driven only by simulation time and their own RNG."""
import math
import numpy as np

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


