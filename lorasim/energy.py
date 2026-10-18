"""State integration from LoraRadioEnergyModel + separate MCU work accounting."""
import math
from .phy import NS


class RadioMeter:
    def __init__(self, config):
        self.config = config
        self.state, self.since, self.ordinal = config.initial_state,0,0
        self.tx_current = config.tx_current_a
        self.total = 0.
    def current(self):
        c = self.config
        return {"SLEEP":c.sleep_current_a,"STANDBY":c.standby_current_a,
                "TX":self.tx_current,"RX_ENABLED":c.rx_current_a,"RX_ACTIVE":c.rx_current_a}[self.state]
    def transition(self, now, state, ordinal, writer, device_id, tx_power=None):
        if now < self.since:
            raise ValueError("energy clock moved backwards")
        if self.config.enabled and now > self.since:
            energy = (now-self.since)/NS*self.current()*self.config.voltage_v
            writer.write("energy",device_id=device_id,tx_index=self.ordinal,component="radio",
                         state=self.state,start_ns=self.since,end_ns=now,duration_s=(now-self.since)/NS,
                         power_w=self.current()*self.config.voltage_v,energy_j=energy,mode="state")
            self.total += energy
        self.since,self.state,self.ordinal = now,state,ordinal
        if tx_power is not None:
            c = self.config
            self.tx_current = c.tx_current_a if c.tx_current_model=="constant" else 10**((tx_power-30)/10)/(c.tx_voltage_v*c.eta)+c.base_current_a


def compute_duration(config, policy, operation, measured_s):
    if config.mode == "measured":
        duration = measured_s
    elif config.mode == "declared":
        name = "inference_time_s" if operation == "inference" else "training_time_s"
        if not hasattr(policy,name):
            raise ValueError(f"MCU declared mode requires policy.{name}")
        duration = getattr(policy,name)
    else:
        duration = config.inference_s if operation=="inference" else config.training_s
    if not isinstance(duration,(int,float)) or not math.isfinite(duration) or duration < 0:
        raise ValueError("MCU duration must be finite and nonnegative")
    return float(duration)
