"""Explicit comparison constraints; not part of the original ADR control law."""
from bisect import bisect_left


class ADRSpace:
    def __init__(self, space):
        for key in ('coding_rates', 'powers_dbm'):
            if len(getattr(space, key)) != 1:
                raise ValueError('action_space requires fixed dimension: '+key)
        self.sfs = tuple(sorted(space.spreading_factors))
        self.channels = tuple(space.frequencies_hz)
        self.tp, self.cr = space.powers_dbm[0], space.coding_rates[0]

    def project_sf(self, sf):
        """Ceiling within candidates; saturate at the largest allowed SF."""
        return self.sfs[min(bisect_left(self.sfs, sf), len(self.sfs)-1)]

    def constrain(self, radio):
        return radio.model_copy(update={'sf': self.project_sf(radio.sf),
                                        'tp_dbm': self.tp, 'cr': self.cr})

    def hop(self, radio, rng):
        result = self.constrain(radio)
        result.frequency_hz = self.channels[int(rng.integers(len(self.channels)))] if len(self.channels)>1 else self.channels[0]
        return result

    def command(self, radio, steps):
        # Keep the original one-SF-per-positive-step budget. Gaps in the allowed
        # set cannot buy an extra SF decrease. Negative steps cannot raise fixed TP.
        return self.constrain(radio.model_copy(update={'sf': max(7, radio.sf-max(steps, 0))}))
