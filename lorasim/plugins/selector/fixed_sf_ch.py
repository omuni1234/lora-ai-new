from pydantic import Field
from lorasim.interfaces import Policy, Options


class Settings(Options):
    sf: int | None = Field(None,ge=7,le=12,title='使用するSF',description='共通SF集合内の値。未指定なら共通集合の先頭を使用します。')
    frequency_hz: int | None = Field(None,title='使用するCH [Hz]',description='共通CH集合内の値。未指定なら共通集合の先頭を使用します。')


class FixedSFCH(Policy):
    Options = Settings
    uses_action_space = True
    preview_safe = True
    label = '固定設定 SF×CH'
    description = '共通行動空間内の1つのSF・CHを固定使用。TP・CRも共通値です。'
    def __init__(self,rng,action_space,**options):
        o=Settings.model_validate(options)
        self.actions=action_space.radios()
        sf=o.sf if o.sf is not None else action_space.spreading_factors[0]
        freq=o.frequency_hz if o.frequency_hz is not None else action_space.frequencies_hz[0]
        self.radio=next((a for a in self.actions if (a.sf,a.frequency_hz)==(sf,freq)),None)
        if self.radio is None:raise ValueError('fixed SF/CH must belong to the common action space')
    def select(self,observation):
        return self.radio.model_copy()


PLUGIN = FixedSFCH
