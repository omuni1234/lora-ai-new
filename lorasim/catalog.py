"""Compatibility catalog; defaults are defined exclusively by each plugin."""
from .registry import inventory
DEFAULTS = {cat:{kind:item['defaults'] for kind,item in items.items()} for cat,items in inventory().items()}
