"""Optional CUDA-capable model policy. The model outputs one logit per action.
Example YAML kind: torch_policy:TorchPolicy, options: {model_path: /models/policy.pt, device: cuda}
Model code is loaded lazily, only in the simulation child, not in the API process.
"""
import numpy as np
from lorasim.config import Radio


class TorchPolicy:
    _models={}
    def __init__(self,rng,model_path,device='cpu',actions=None):
        self.path,self.device=model_path,device
        self.actions=[Radio.model_validate(p) for p in (actions or [{'sf':7},{'sf':9},{'sf':12}])]
    def select(self,observation):
        import torch
        key=(self.path,self.device)
        if key not in self._models:
            self._models[key]=torch.jit.load(self.path,map_location=self.device).eval()
        features=[float(observation.last_ack) if observation.last_ack is not None else -1,
                  (observation.last_dl_rssi_dbm or -150)/150,
                  (observation.last_sf or 7)/12,(observation.last_tp_dbm or 0)/14]
        with torch.inference_mode():
            logits=self._models[key](torch.tensor([features],dtype=torch.float32,device=self.device))
            if tuple(logits.shape)!=(1,len(self.actions)) or not torch.isfinite(logits).all():
                raise ValueError('model must output finite logits of shape (1, number_of_actions)')
            return self.actions[int(logits.argmax(dim=1).item())].model_copy()
    def observe(self,feedback):
        # Add an online update here if required; built-in bandit demonstrates ACK learning.
        pass
