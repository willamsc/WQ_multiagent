import json
from pathlib import Path

from .domain import ResearchConfig


def load_config(path):
    if path is None:
        return ResearchConfig()
    return ResearchConfig(**json.loads(Path(path).read_text(encoding="utf-8")))
