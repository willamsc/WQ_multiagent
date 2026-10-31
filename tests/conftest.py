from dataclasses import dataclass
from uuid import uuid4

import pytest

from alpha_platform.agent_interop.backends import BackendRegistry, MockBackend
from alpha_platform.agent_interop.contracts import (
    ContextPacket,
    ResourceLimits,
    Role,
    Subject,
    TaskEnvelope,
    canonical,
)
from alpha_platform.agent_interop.ledger import AgentLedger
from alpha_platform.agent_interop.runtime import AgentRuntime


def approved_sources(version=1):
    return {
        "objective": "验证离线研究契约",
        "settings": {"region": "MOCK", "delay": 1},
        "field_metadata": {"mock_value": {"type": "MATRIX", "description": "mock fixture"}},
        "evidence": [{"ref": "e:1", "scope": "provided_evidence", "summary": "仅用于离线测试"}],
        "proposal": {
            "schema_version": 1,
            "signal_id": "s",
            "signal_version": version,
            "economic_hypothesis": "模拟假设",
            "mechanism": "模拟机制",
            "field_ids": ["mock_value"],
            "expression_suggestions": ["mock_value"],
            "evidence_refs": ["e:1"],
            "risks": ["尚未验证"],
        },
    }


def prepare_task(role=Role.GENERATOR, *, version=1, timeout=1, task_id=None):
    body = approved_sources(version)
    if role == Role.GENERATOR:
        body.pop("proposal")
    context = ContextPacket(
        role,
        Subject("s", version),
        "context-v1",
        canonical(body),
        ("field_metadata", "provided_evidence"),
    )
    task = TaskEnvelope(
        task_id or str(uuid4()),
        role,
        context.subject,
        context.version,
        context.digest,
        resource_limits=ResourceLimits(timeout_seconds=timeout),
    )
    return task, context


@dataclass
class AgentSystem:
    ledger: AgentLedger
    registry: BackendRegistry
    runtime: AgentRuntime
    path: object


@pytest.fixture
def agent_system(tmp_path):
    path = tmp_path / "agents.sqlite3"
    ledger = AgentLedger(path)
    ledger.initialize()
    registry = BackendRegistry()
    registry.register("mock-g", MockBackend)
    registry.register("mock-c", MockBackend)
    runtime = AgentRuntime(ledger, registry, {Role.GENERATOR: "mock-g", Role.CRITIC: "mock-c"})
    yield AgentSystem(ledger, registry, runtime, path)
    ledger.close()
