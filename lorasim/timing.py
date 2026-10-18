import math

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


