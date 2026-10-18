"""Trusted Python extensions; enable with LORASIM_ALLOW_PLUGINS=1.
Use PYTHONPATH=examples and kind: custom_models:AckAdaptive.
"""
from lorasim.config import Radio


class AckAdaptive:
    def __init__(self,rng,start_sf=7):
        self.sf=start_sf
    def select(self,observation):
        return Radio(sf=self.sf,tp_dbm=14,cr=1)
    def observe(self,feedback):
        if feedback.ack is False:
            self.sf=min(12,self.sf+1)
        elif feedback.ack is True:
            self.sf=max(7,self.sf-1)


class Scheduled:
    def __init__(self,rng,times_s):
        self.times=iter(times_s)
    def next_time(self,observation,previous_generation_s):
        # Deadlines that passed while the MAC was busy are skipped.
        return next((t for t in self.times if t>=observation.time_s+1e-9),None)


class Linear:
    def __init__(self,initial,area,rng,velocity=(1,0,0)):
        self.initial,self.velocity=initial,velocity
    def position(self,t):
        return [p+t*v for p,v in zip(self.initial,self.velocity)]
