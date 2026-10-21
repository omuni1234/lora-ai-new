"""Finite experiment traffic; existing Periodic and scheduler are unchanged."""
from pydantic import Field
from .periodic import Periodic, Settings as PeriodicSettings


class Settings(PeriodicSettings):
    max_messages: int = Field(40000,ge=1,title='EDごとの生成回数',description='この回数を生成後に停止。実送信はMACに従い、再送は回数に含めません。')


class FinitePeriodic(Periodic):
    Options = Settings
    label = '有限回の周期送信（論文実験）'
    description = '既存の周期生成を指定回数で停止。生成上限以外のMACや時間処理は変えません。'

    def __init__(self,rng,**options):
        o=Settings.model_validate(options)
        self.generated=0
        self.limit=o.max_messages
        super().__init__(rng,**o.model_dump(exclude={'max_messages'}))

    def next_time(self,observation,previous_generation_s):
        if self.generated>=self.limit:
            return None
        result=super().next_time(observation,previous_generation_s)
        self.generated+=1
        return result


PLUGIN=FinitePeriodic
