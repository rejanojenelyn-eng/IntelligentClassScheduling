"""
SchedulingPolicy -- read-only interface over the existing scheduler_config
source. This module does not create a second settings store and does not change
current defaults or behavior.

The constraint-to-config/weight mapping is documented centrally in registry.py.
"""

from database import load_scheduler_config


class SchedulingPolicy:
    # Confirmed unused/deprecated keys retained for introspection and backward
    # compatibility only. Do not wire these into behavior from this class.
    DEAD_KEYS = ("hc_program_restrict_enabled",)
    WRITE_ONLY_KEYS = ("sc2_night",)  # deprecated: old concept folded into final SC1

    def __init__(self, config: dict = None):
        self._cfg = dict(config) if config is not None else load_scheduler_config()

    @classmethod
    def load(cls) -> "SchedulingPolicy":
        return cls(load_scheduler_config())

    def get(self, key: str, default=None):
        return self._cfg.get(key, default)

    def is_enabled(self, key: str) -> bool:
        return bool(self._cfg.get(key, 1))

    def as_dict(self) -> dict:
        return dict(self._cfg)
