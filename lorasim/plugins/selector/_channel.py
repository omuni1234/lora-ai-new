"""Independent uniform channel hopping, driven by the private ED RNG."""
from typing import Literal, Annotated
from pydantic import Field, model_validator
from lorasim.interfaces import Options


class ChannelSettings(Options):
    channel_selection: Literal['legacy','uniform'] = Field('legacy', title='CHの選択方式', description='uniform：送信試行ごとに候補CHから独立に一様抽選。legacy：既存の固定CH／ADR backoff時の既定CH巡回を維持。共通行動空間では常にそのCH候補を一様抽選します。')
    frequencies_hz: list[Annotated[int, Field(gt=0)]] = Field(default_factory=lambda:[868100000,868300000,868500000], min_length=1, max_length=64, title='ランダム選択するCH候補 [Hz]', description='uniformで使用する登録済みUL周波数。SF・TPの制御とは独立に、再送時も抽選します。')

    @model_validator(mode='after')
    def unique_channels(self):
        if len(set(self.frequencies_hz)) != len(self.frequencies_hz):
            raise ValueError('hopping channels must be unique')
        return self


def hop(radio, rng, frequencies):
    return radio.model_copy(update={'frequency_hz':int(rng.choice(frequencies))})
