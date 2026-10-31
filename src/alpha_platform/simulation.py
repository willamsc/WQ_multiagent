import json
import math
from typing import Literal, Protocol

from .domain import MOCK_SCENARIOS, SimulationRequest, SimulationResult, SubmissionReceipt

SimulationStatus = Literal["RUNNING", "COMPLETED", "FAILED"]


class AdapterContractError(ValueError):
    pass


def validate_receipt(receipt):
    if (
        not isinstance(receipt, SubmissionReceipt)
        or not isinstance(receipt.remote_id, str)
        or not receipt.remote_id.strip()
    ):
        raise AdapterContractError("invalid submission receipt")


def validate_result(result):
    if (
        not isinstance(result, SimulationResult)
        or not isinstance(result.metrics, dict)
        or not isinstance(result.checks, dict)
        or not isinstance(result.raw, dict)
    ):
        raise AdapterContractError("invalid result shape")
    sharpe = result.metrics.get("sharpe")
    if type(sharpe) not in (int, float) or (
        isinstance(sharpe, float) and not math.isfinite(sharpe)
    ):
        raise AdapterContractError("missing or invalid sharpe")
    try:
        json.dumps([result.metrics, result.checks, result.raw], allow_nan=False)
    except (ValueError, TypeError, OverflowError) as exc:
        raise AdapterContractError("result must be finite JSON data") from exc


class RateLimited(Exception):
    """Mock guarantees request was not accepted; retry is safe."""


class RemoteUnknown(Exception):
    pass


class SimulationAdapter(Protocol):
    """Submit returns a nonempty receipt or raises; ambiguous acceptance is never retried.

    Poll returns RUNNING/COMPLETED/FAILED. Fetch returns finite JSON dictionaries
    with a numeric sharpe. All methods may raise; only RateLimited guarantees
    non-acceptance. These are internal M1 contracts, not a WorldQuant API schema.
    """

    async def submit(self, candidate: SimulationRequest) -> SubmissionReceipt: ...
    async def poll(self, remote_id: str) -> SimulationStatus: ...
    async def fetch_result(self, remote_id: str) -> SimulationResult: ...


class MockSimulationAdapter:
    def __init__(self, scenario="success"):
        if scenario not in MOCK_SCENARIOS:
            raise ValueError("unsupported mock scenario")
        self.scenario = scenario
        self.submissions = 0
        self.polls = 0
        self._remote_polls = {}

    async def submit(self, candidate):
        self.submissions += 1
        if self.scenario == "rate_limit" and self.submissions == 1:
            raise RateLimited()
        if self.scenario == "unknown":
            raise RemoteUnknown()
        if self.scenario == "timeout":
            raise TimeoutError()
        remote_id = f"mock-{self.submissions}"
        self._remote_polls[remote_id] = 0
        if self.scenario == "invalid_receipt":
            return None
        return SubmissionReceipt(remote_id)

    async def poll(self, remote_id):
        if remote_id not in self._remote_polls:
            raise RemoteUnknown("unknown mock remote id")
        self.polls += 1
        self._remote_polls[remote_id] += 1
        if self.scenario == "invalid_status":
            return "INVALID"
        if self.scenario == "failed":
            return "FAILED"
        if self.scenario == "delayed" and self._remote_polls[remote_id] == 1:
            return "RUNNING"
        return "COMPLETED"

    async def fetch_result(self, remote_id):
        if remote_id not in self._remote_polls:
            raise RemoteUnknown("unknown mock remote id")
        required_polls = 2 if self.scenario == "delayed" else 1
        if self._remote_polls[remote_id] < required_polls or self.scenario in {
            "failed",
            "invalid_status",
        }:
            raise RemoteUnknown("mock result is not completed")
        if self.scenario == "invalid_result":
            return SimulationResult({"sharpe": float("nan")}, {}, {})
        return SimulationResult(
            {"sharpe": 1.25, "fitness": 0.8, "turnover": 0.2},
            {"mock_check": "PASS"},
            {"source": "mock", "remote_id": remote_id},
        )
