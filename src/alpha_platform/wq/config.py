import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from ..domain import ResearchConfig


@dataclass(frozen=True)
class WQConfig(ResearchConfig):
    """Full simulation settings participate in validation identity and audit."""

    adapter_identity = "wq-live-v1"
    gate_label = "LOCAL_RESEARCH_GATE"
    max_polls: int = 120
    simulation_settings: dict = field(default_factory=dict)

    def __post_init__(self):
        super().__post_init__()
        s = self.simulation_settings
        required = {
            "instrumentType",
            "region",
            "universe",
            "delay",
            "decay",
            "neutralization",
            "truncation",
            "pasteurization",
            "testPeriod",
            "unitHandling",
            "nanHandling",
            "language",
            "visualization",
        }
        if not isinstance(s, dict) or set(s) != required:
            raise ValueError("explicit complete WQ simulation_settings required")
        if any(
            s[k] != v
            for k, v in {
                "region": self.region,
                "universe": self.universe,
                "delay": self.delay,
                "instrumentType": self.instrument,
                "language": "FASTEXPR",
                "visualization": False,
            }.items()
        ):
            raise ValueError("inconsistent WQ settings")
        if type(s["delay"]) is not int or type(s["decay"]) is not int or s["decay"] < 0:
            raise ValueError("invalid WQ delay/decay")
        if type(s["visualization"]) is not bool:
            raise ValueError("visualization must be false")
        if type(s["truncation"]) not in (int, float) or not math.isfinite(s["truncation"]):
            raise ValueError("invalid truncation")
        if not 0 <= s["truncation"] <= 1:
            raise ValueError("invalid truncation")
        for k in required - {"delay", "decay", "truncation", "visualization"}:
            if not isinstance(s[k], str) or not s[k]:
                raise ValueError("invalid WQ setting type")

    def settings(self):
        return dict(self.simulation_settings)


def load_live_config(path):
    return WQConfig(**json.loads(Path(path).read_text(encoding="utf-8")))
