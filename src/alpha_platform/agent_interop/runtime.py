"""A2/A4 bounded execution and validation. Backends cannot finalize their own results."""

import asyncio
from dataclasses import replace
from time import perf_counter

from .backends import AttemptRequest, BackendSelectionError
from .context import approved_text, verify_context
from .contracts import (
    BASE_CAPABILITIES,
    ContractError,
    ExecutionOutcome,
    ExecutionStatus,
    Role,
    canonical,
    object_fields,
    parse_proposal,
    parse_review,
    strict_json,
)


def _consume(task):
    try:
        task.result()
    except BaseException:
        pass  # Raw backend errors are never logged/exported.


class AgentRuntime:
    def __init__(self, ledger, registry, role_backends):
        self.ledger, self.registry = ledger, registry
        self.role_backends = dict(role_backends)

    async def execute(self, task, context, *, cancel_event=None, supersede=False):
        verify_context(task, context)
        if (
            context.role != task.role
            or context.subject != task.subject
            or context.version != task.context_version
            or context.digest != task.context_digest
            or context.evidence_scopes != task.allowed_evidence_scopes
        ):
            raise ContractError("CONTEXT_BINDING_MISMATCH")
        self.ledger.register(task, context)
        backend_name = self.role_backends.get(task.role)
        if backend_name is None:
            raise ValueError("ROLE_BACKEND_MISSING")
        attempt_id = self.ledger.begin(task, backend_name, supersede)
        started = perf_counter()
        child = cancel_wait = None

        def outcome(status, code=None, result=None):
            return ExecutionOutcome(
                task.task_id,
                attempt_id,
                backend_name,
                task.role,
                task.subject,
                task.context_version,
                task.context_digest,
                status,
                code,
                round((perf_counter() - started) * 1000, 3),
                f"agent_result:{attempt_id}" if result is not None else None,
                result,
            )

        try:
            try:
                backend = self.registry.create(backend_name)
            except BackendSelectionError as exc:
                result = outcome(ExecutionStatus.UNSUPPORTED, exc.code)
            else:
                if backend.unavailable_code:
                    safe_code = (
                        backend.unavailable_code
                        if backend.unavailable_code
                        in {
                            "HOST_ISOLATION_UNVERIFIED",
                            "BACKEND_NOT_INSTALLED",
                            "AUTH_NOT_VERIFIED",
                        }
                        else "BACKEND_UNAVAILABLE"
                    )
                    result = outcome(ExecutionStatus.UNSUPPORTED, safe_code)
                elif (BASE_CAPABILITIES | set(task.required_capabilities)) - backend.capabilities:
                    result = outcome(ExecutionStatus.UNSUPPORTED, "CAPABILITY_MISSING")
                else:
                    child = asyncio.create_task(
                        backend.execute(AttemptRequest(task, attempt_id, context))
                    )
                    child.add_done_callback(_consume)
                    waits = {child}
                    if cancel_event is not None:
                        cancel_wait = asyncio.create_task(cancel_event.wait())
                        waits.add(cancel_wait)
                    done, _ = await asyncio.wait(
                        waits, timeout=task.resource_limits.timeout_seconds
                    )
                    if cancel_wait in done:
                        child.cancel()
                        result = outcome(ExecutionStatus.CANCELLED, "CANCEL_REQUESTED")
                    elif child not in done:
                        child.cancel()
                        result = outcome(ExecutionStatus.TIMEOUT, "BACKEND_TIMEOUT")
                    elif child.cancelled():
                        result = outcome(ExecutionStatus.CANCELLED, "BACKEND_CANCELLED")
                    else:
                        raw = child.result()
                        try:
                            parsed = self._validate(task, context, attempt_id, raw)
                        except (ValueError, TypeError, RecursionError) as exc:
                            raise ContractError("RESULT_CONTRACT_INVALID") from exc
                        result = outcome(ExecutionStatus.SUCCEEDED, result=parsed)
        except asyncio.CancelledError:
            if child:
                child.cancel()
            self.ledger.finish(outcome(ExecutionStatus.CANCELLED, "CALLER_CANCELLED"))
            raise
        except ContractError:
            result = outcome(ExecutionStatus.INVALID_OUTPUT, "RESULT_CONTRACT_INVALID")
        except PermissionError:
            result = outcome(ExecutionStatus.FAILED, "CONTEXT_ACCESS_DENIED")
        except Exception:
            result = outcome(ExecutionStatus.FAILED, "BACKEND_EXECUTION_FAILED")
        finally:
            if cancel_wait:
                cancel_wait.cancel()
        accepted = self.ledger.finish(result)
        return (
            result
            if accepted
            else replace(
                result,
                execution_status=ExecutionStatus.STALE,
                safe_error_code="LATE_ATTEMPT_DISCARDED",
                result=None,
                validated_result_ref=None,
            )
        )

    def _validate(self, task, context, attempt_id, raw):
        value = strict_json(raw, task.resource_limits.max_output_bytes)
        object_fields(
            value,
            (
                "schema_version",
                "task_id",
                "attempt_id",
                "role",
                "subject",
                "context_version",
                "context_digest",
                "result",
            ),
        )
        expected = {
            "schema_version": 1,
            "task_id": task.task_id,
            "attempt_id": attempt_id,
            "role": task.role,
            "subject": {
                "signal_id": task.subject.signal_id,
                "signal_version": task.subject.signal_version,
            },
            "context_version": task.context_version,
            "context_digest": task.context_digest,
        }
        if canonical({k: value[k] for k in expected}) != canonical(expected):
            raise ContractError("TASK_BINDING_MISMATCH")

        approved_text(value["result"])
        body = context.content()
        refs = [e["ref"] for e in body["evidence"]]
        return (
            parse_proposal(value["result"], task.subject, body["field_metadata"], refs)
            if task.role == Role.GENERATOR
            else parse_review(value["result"], task.subject, refs)
        )
