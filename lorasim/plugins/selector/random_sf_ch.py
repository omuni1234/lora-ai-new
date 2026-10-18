from lorasim.interfaces import Policy


class RandomSFCH(Policy):
    uses_action_space = True
    preview_safe = True
    label = '一様ランダム SF×CH'
    description = '実験共通のSF×CH集合から各送信で一様抽選します。'
    def __init__(self,rng,action_space):
        self.rng,self.actions = rng,action_space.radios()
    def select(self,observation):
        return self.actions[int(self.rng.integers(len(self.actions)))].model_copy()


PLUGIN = RandomSFCH
