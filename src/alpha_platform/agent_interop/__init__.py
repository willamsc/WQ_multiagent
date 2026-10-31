"""M2-A Agent interoperability; independent of legacy M1 execution."""

from .backends import BackendAdapter, BackendRegistry, MockBackend
from .context import ContextBuilder, create_task
from .roles import CriticRole, GeneratorRole, IndependentCritic, MainSignalGenerator
from .runtime import AgentRuntime

__all__ = [
    "AgentRuntime",
    "BackendAdapter",
    "BackendRegistry",
    "MockBackend",
    "ContextBuilder",
    "create_task",
    "GeneratorRole",
    "CriticRole",
    "MainSignalGenerator",
    "IndependentCritic",
]
