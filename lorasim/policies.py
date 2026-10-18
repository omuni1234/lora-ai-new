"""Reusable ED-local policies; no profile parsing or server state."""
import numpy as np
from .config import Radio

class Fixed:
    def __init__(self, rng, **options):
        self.radio = Radio.model_validate(options)
    def select(self, observation):
        return self.radio.model_copy()
    def observe(self, feedback):
        pass


class RandomPolicy:
    def __init__(self, rng, actions=None):
        self.rng = rng
        self.actions = [Radio.model_validate(x) for x in (actions or [{"sf":s} for s in range(7,13)])]
        if not self.actions:
            raise ValueError("actions cannot be empty")
    def select(self, observation):
        return self.actions[int(self.rng.integers(len(self.actions)))].model_copy()
    def observe(self, feedback):
        pass


class Bandit(RandomPolicy):
    """Per-ED epsilon-greedy bandit. Rewards use only ACK and local energy proxy."""
    def __init__(self, rng, actions=None, epsilon=.1, energy_weight=.02):
        super().__init__(rng, actions)
        if not 0 <= epsilon <= 1 or energy_weight < 0:
            raise ValueError("invalid bandit hyperparameters")
        self.epsilon, self.energy_weight = epsilon, energy_weight
        self.q, self.n = np.zeros(len(self.actions)), np.zeros(len(self.actions))
        self.selected = 0
    def select(self, observation):
        self.selected = int(self.rng.integers(len(self.actions))) if self.rng.random() < self.epsilon else int(np.argmax(self.q))
        return self.actions[self.selected].model_copy()
    def observe(self, feedback):
        if feedback.ack is None:
            return
        reward = float(feedback.ack) - self.energy_weight * feedback.airtime_s * 10**(feedback.tx_power_dbm/10)
        i = self.selected
        self.n[i] += 1
        self.q[i] += (reward-self.q[i])/self.n[i]


