import json
import math
from dataclasses import asdict, dataclass, field
from enum import StrEnum

MOCK_SCENARIOS = frozenset(
    {
        "success",
        "rate_limit",
        "unknown",
        "failed",
        "delayed",
        "timeout",
        "invalid_receipt",
        "invalid_status",
        "invalid_result",
    }
)


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RESERVED = "RESERVED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    POLLING = "POLLING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class RunStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ResearchConfig:
    region: str = "GBR"
    universe: str = "TOP700"
    delay: int = 1
    instrument: str = "EQUITY"
    budget_limit: int = 1
    min_sharpe: float = 1.0
    max_polls: int = 3
    max_submit_attempts: int = 2
    fields: dict[str, str] = field(default_factory=lambda: {"mock_value": "MATRIX"})
    operators: dict[str, int] = field(default_factory=lambda: {"mock_rank": 1})
    expression: str = "mock_rank(mock_value)"
    mock_scenario: str = "success"

    def __post_init__(self):
        if type(self.budget_limit) is not int or self.budget_limit < 0:
            raise ValueError("budget_limit must be a nonnegative integer")
        if (
            type(self.max_polls) is not int
            or type(self.max_submit_attempts) is not int
            or type(self.delay) is not int
            or self.max_polls < 1
            or self.max_submit_attempts < 1
            or self.delay < 0
        ):
            raise ValueError("invalid execution limits")
        if type(self.min_sharpe) not in (int, float) or not math.isfinite(self.min_sharpe):
            raise ValueError("min_sharpe must be finite")
        if not all(
            isinstance(v, str) and v
            for v in (self.region, self.universe, self.instrument, self.expression)
        ):
            raise ValueError("settings and expression must be nonempty strings")
        if (
            not isinstance(self.fields, dict)
            or not self.fields
            or any(
                not isinstance(k, str) or not k.isidentifier() or not isinstance(v, str)
                for k, v in self.fields.items()
            )
        ):
            raise ValueError("invalid mock field metadata")
        if not isinstance(self.operators, dict) or any(
            not isinstance(k, str) or not k.isidentifier() or type(v) is not int or v < 1
            for k, v in self.operators.items()
        ):
            raise ValueError("invalid mock operator metadata")
        if self.mock_scenario not in MOCK_SCENARIOS:
            raise ValueError("unsupported mock scenario")

    def settings(self):
        return {k: getattr(self, k) for k in ("region", "universe", "delay", "instrument")}

    def json(self):
        return json.dumps(asdict(self), sort_keys=True)


@dataclass(frozen=True)
class Signal:
    hypothesis: str
    field_ids: tuple[str, ...]
    provenance: str = "mock fixture; not empirical evidence"


@dataclass(frozen=True)
class AlphaCandidate:
    expression: str
    signal: Signal


@dataclass(frozen=True)
class SimulationRequest:
    expression: str
    settings: dict


@dataclass(frozen=True)
class SubmissionReceipt:
    remote_id: str


@dataclass(frozen=True)
class SimulationResult:
    metrics: dict
    checks: dict
    raw: dict


@dataclass(frozen=True)
class SimulationJob:
    id: str
    run_id: str
    status: JobStatus
    remote_id: str | None = None


@dataclass(frozen=True)
class ResearchRun:
    id: str
    config: ResearchConfig
    status: RunStatus = RunStatus.CREATED
