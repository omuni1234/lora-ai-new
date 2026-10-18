import numpy as np
from lorasim.config import Radio
from lorasim.interfaces import Policy
class RandomPolicy(Policy):
    accepts_action_space = True
    space_options = ["actions"]
    def __init__(self, rng, actions=None, action_space=None):
        self.rng = rng
        self.actions = action_space.radios() if action_space is not None else [Radio.model_validate(x) for x in (actions or [{"sf":s} for s in range(7,13)])]
        if not self.actions:
            raise ValueError("actions cannot be empty")
    def select(self, observation):
        return self.actions[int(self.rng.integers(len(self.actions)))].model_copy()
    def observe(self, feedback):
        pass



from pydantic import Field
from typing import Literal
from lorasim.interfaces import Options

class Settings(Options):
    actions: list[Radio] = Field(default_factory=lambda: [Radio(sf=s) for s in range(7,13)], min_length=1, title="送信設定の候補", description="各候補を等確率で選択します。周波数はUL許可サブバンド内で指定してください。")

RandomPolicy.Options = Settings
RandomPolicy.label = 'ランダム選択'
RandomPolicy.description = '候補から毎回等確率で選びます。'
RandomPolicy.preview_safe = True
PLUGIN = RandomPolicy
