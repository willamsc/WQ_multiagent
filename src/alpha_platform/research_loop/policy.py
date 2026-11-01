import math
from dataclasses import dataclass, field

from ..agent_interop.contracts import ContractError, identifier


@dataclass(frozen=True)
class LoopPolicy:
    max_repairs: int = 1
    max_agent_calls: int = 8
    timeout_seconds: float = 30
    total_timeout_seconds: float = 180
    max_evidence_requests: int = 2
    operators: dict = field(default_factory=lambda: {"rank": 1, "ts_mean": 2})

    def __post_init__(self):
        for value, low, high in (
            (self.max_repairs, 0, 3),
            (self.max_agent_calls, 1, 32),
            (self.max_evidence_requests, 0, 4),
        ):
            if type(value) is not int or not low <= value <= high:
                raise ContractError("LOOP_LIMIT_INVALID")
        for value, maximum in ((self.timeout_seconds, 300), (self.total_timeout_seconds, 1800)):
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 < value <= maximum
            ):
                raise ContractError("LOOP_TIME_LIMIT_INVALID")
        if not isinstance(self.operators, dict) or len(self.operators) > 64:
            raise ContractError("LOOP_OPERATORS_INVALID")
        for name, arity in self.operators.items():
            identifier(name)
            if not name.isidentifier() or type(arity) is not int or not 1 <= arity <= 8:
                raise ContractError("LOOP_OPERATORS_INVALID")
