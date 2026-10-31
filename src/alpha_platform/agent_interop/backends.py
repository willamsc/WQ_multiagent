"""Adapters get approved immutable data, never a Controller, ledger or simulation handle."""

import asyncio
from dataclasses import asdict, dataclass
from typing import Callable, Protocol
from weakref import WeakValueDictionary

from .contracts import BASE_CAPABILITIES, ContextPacket, Role, TaskEnvelope, canonical, identifier


class BackendError(Exception):
    pass


class BackendSelectionError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AttemptRequest:
    task: TaskEnvelope
    attempt_id: str
    context: ContextPacket


class BackendAdapter(Protocol):
    capabilities: frozenset[str]
    unavailable_code: str | None

    async def execute(self, request: AttemptRequest) -> str: ...


class BackendRegistry:
    def __init__(self):
        self.factories: dict[str, Callable[[], BackendAdapter]] = {}
        self.reserved = {"dsh": "IMPLEMENTATION_UNCONFIRMED"}
        self._instances = WeakValueDictionary()

    def register(self, name, factory):
        identifier(name)
        if name in self.factories or not callable(factory):
            raise ValueError("BACKEND_REGISTRATION_INVALID")
        self.factories[name] = factory
        self.reserved.pop(name, None)

    def create(self, name):
        if name in self.reserved:
            raise BackendSelectionError("BACKEND_RESERVED_UNSUPPORTED")
        if name not in self.factories:
            raise BackendSelectionError("UNKNOWN_BACKEND")
        instance = self.factories[name]()
        if id(instance) in self._instances:
            raise BackendSelectionError("BACKEND_SESSION_REUSE")
        try:
            self._instances[id(instance)] = instance
        except TypeError as exc:
            raise BackendSelectionError("ADAPTER_INSTANCE_INVALID") from exc
        return instance


class MockBackend:
    capabilities = BASE_CAPABILITIES
    unavailable_code = None

    def __init__(self, scenario="success", delay=0):
        self.scenario, self.delay = scenario, delay

    async def execute(self, request):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.scenario == "raise":
            raise BackendError("raw private exception must not be persisted")
        if self.scenario == "invalid_json":
            return "not JSON"
        task, content = request.task, request.context.content()
        subject = asdict(task.subject)
        evidence = [e["ref"] for e in content["evidence"]]
        if task.role == Role.GENERATOR:
            field = next(iter(content["field_metadata"]))
            result = {
                **subject,
                "schema_version": 1,
                "economic_hypothesis": "离线模拟假设，尚未验证预测能力",
                "mechanism": "仅用于契约验证，不是研究结论",
                "field_ids": [field],
                "expression_suggestions": [field],
                "evidence_refs": evidence,
                "risks": ["未经实证验证"],
            }
        else:
            result = {
                **subject,
                "schema_version": 1,
                "verdict": "UNCERTAIN",
                "findings": [],
                "evidence_refs": evidence,
            }
        response = {
            "schema_version": 1,
            "task_id": task.task_id,
            "attempt_id": request.attempt_id,
            "role": task.role,
            "subject": subject,
            "context_version": task.context_version,
            "context_digest": task.context_digest,
            "result": result,
        }
        if self.scenario == "wrong_version":
            response["subject"]["signal_version"] += 1
        if self.scenario == "wrong_role":
            response["role"] = "critic" if task.role == Role.GENERATOR else "generator"
        if self.scenario == "wrong_schema":
            response["result"] = {"verdict": "PASS"}
        if self.scenario == "overreach":
            request.context.evidence("generator_private_history")
        return canonical(response)
