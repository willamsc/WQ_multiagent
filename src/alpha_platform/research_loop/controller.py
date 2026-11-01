import asyncio
from dataclasses import asdict
from time import monotonic
from uuid import uuid4

from ..agent_interop.context import ContextBuilder, create_task, verify_context
from ..agent_interop.contracts import (
    ContractError,
    ResourceLimits,
    Role,
    Subject,
    canonical,
    identifier,
)
from ..agent_interop.roles import CriticRole, GeneratorRole
from ..agent_interop.runtime import AgentRuntime
from ..validation import ValidationError
from .evidence import EvidenceGateway
from .policy import LoopPolicy
from .store import ResearchStore
from .validation import repairable, validate_proposal


class ResearchController:
    def __init__(self, runtime, *, builder=None):
        # Freeze routing at construction. No fallback/retry/switch after a verdict.
        self.runtime = AgentRuntime(runtime.ledger, runtime.registry, dict(runtime.role_backends))
        self.store = ResearchStore(runtime.ledger)
        self.builder = builder or ContextBuilder()

    async def run(self, sources, *, signal_id, study_id=None, policy=None, evidence_catalog=()):
        identifier(signal_id)
        study_id = study_id or str(uuid4())
        identifier(study_id)
        # Copy mutable policy dictionaries and sources into an approved immutable wire snapshot.
        import json

        policy = LoopPolicy(**json.loads(canonical(asdict(policy or LoopPolicy()))))
        routing = self.runtime.role_backends
        if (
            set(routing) != {Role.GENERATOR, Role.CRITIC}
            or routing[Role.GENERATOR] == routing[Role.CRITIC]
        ):
            raise ValueError("INDEPENDENT_ROLE_ROUTING_REQUIRED")
        baseline = ContextBuilder().build(Role.GENERATOR, Subject(signal_id, 1), sources).content()
        catalog_sources = {**baseline, "evidence": list(evidence_catalog)}
        catalog = (
            ContextBuilder()
            .build(Role.GENERATOR, Subject(signal_id, 1), catalog_sources)
            .content()["evidence"]
        )
        if any(not item["ref"].startswith("e:") for item in [*baseline["evidence"], *catalog]):
            raise ContractError("RESEARCH_INPUT_EVIDENCE_NAMESPACE_REQUIRED")
        approved = {item["ref"]: item for item in baseline["evidence"]}
        if any(item["ref"] in approved and item != approved[item["ref"]] for item in catalog):
            raise ContractError("EVIDENCE_CATALOG_REFERENCE_CONFLICT")
        if any(item["scope"] != "provided_evidence" for item in catalog):
            raise PermissionError("GATEWAY_SCOPE_DENIED")
        self.store.initialize()
        self.store.start(
            study_id, signal_id, {**baseline, "approved_evidence_catalog": catalog}, policy, routing
        )
        gateway = EvidenceGateway(self.store, catalog)
        deadline = monotonic() + policy.total_timeout_seconds
        failures = []

        def finish(status, reason):
            self.store.finish(study_id, status, reason)
            return study_id

        async def dispatch(role, version, phase, extra):
            used = self.store.db.execute(
                "SELECT used_calls FROM signal_studies WHERE study_id=?", (study_id,)
            ).fetchone()[0]
            if used >= policy.max_agent_calls:
                finish("LIMIT_REACHED", "AGENT_CALL_LIMIT")
                return None
            remaining = deadline - monotonic()
            if remaining <= 0:
                finish("LIMIT_REACHED", "TOTAL_TIME_LIMIT")
                return None
            try:
                context = self.builder.build(
                    role, Subject(signal_id, version), {**baseline, **extra}, version=f"m2b-{phase}"
                )
                task = create_task(
                    context,
                    limits=ResourceLimits(
                        timeout_seconds=min(policy.timeout_seconds, remaining), max_attempts=1
                    ),
                )
                verify_context(task, context)
            except (ValueError, TypeError, PermissionError):
                finish("EXECUTION_FAILED", "CONTEXT_ISOLATION_REJECTED")
                return None
            self.runtime.ledger.register(task, context)
            if not self.store.allocate(study_id, task, phase):
                finish("LIMIT_REACHED", "AGENT_CALL_LIMIT")
                return None
            service = (
                GeneratorRole(self.runtime) if role == Role.GENERATOR else CriticRole(self.runtime)
            )
            outcome = await (
                service.generate(task, context)
                if role == Role.GENERATOR
                else service.review(task, context)
            )
            with self.store.ledger.transaction():
                self.store.event(
                    study_id,
                    "AGENT_EXECUTION",
                    task_id=task.task_id,
                    attempt_id=outcome.attempt_id,
                    execution_status=outcome.execution_status,
                    safe_error_code=outcome.safe_error_code,
                )
            if outcome.execution_status != "SUCCEEDED":
                finish("EXECUTION_FAILED", "AGENT_" + outcome.execution_status)
                return None
            if monotonic() > deadline:
                finish("LIMIT_REACHED", "TOTAL_TIME_LIMIT")
                return None
            return outcome

        def need_evidence(outcome, phase):
            for finding in outcome.result.findings:
                if finding.category == "evidence_request":
                    try:
                        gateway.request(study_id, outcome, phase, finding.description)
                    except ContractError:
                        with self.store.ledger.transaction():
                            self.store.event(
                                study_id, "EVIDENCE_REQUEST_REJECTED", task_id=outcome.task_id
                            )
            return finish("NEEDS_EVIDENCE", "CRITIC_UNCERTAIN")

        try:
            for version in range(1, policy.max_repairs + 2):
                extra = {}
                if failures:
                    previous, review = failures[-1]
                    extra["workflow"] = {
                        "phase": "repair",
                        "previous_proposal": asdict(previous),
                        "review": asdict(review),
                        "changes": [],
                    }
                    with self.store.ledger.transaction():
                        self.store.event(
                            study_id,
                            "REPAIR_AUTHORIZED",
                            from_version=previous.signal_version,
                            to_version=version,
                        )
                generated = await dispatch(Role.GENERATOR, version, "generate", extra)
                if generated is None:
                    return study_id
                proposal = generated.result
                try:
                    validate_proposal(proposal, baseline, policy)
                except (ValueError, TypeError, ValidationError):
                    return finish("REJECTED", "DETERMINISTIC_VALIDATION_FAILED")
                self.store.save_proposal(study_id, generated)
                independent = await dispatch(
                    Role.CRITIC, version, "independent", {"proposal": asdict(proposal)}
                )
                if independent is None:
                    return study_id
                self.store.save_review(study_id, independent, "independent")
                if independent.result.verdict == "UNCERTAIN":
                    return need_evidence(independent, "independent")
                failed = independent.result if independent.result.verdict == "FAIL" else None
                if failed is None:
                    # Independent result is committed before any prior finding/diff is exposed.
                    for old, old_review in failures:
                        phase = f"regression-{old.signal_version}"
                        changes = [
                            key
                            for key in (
                                "economic_hypothesis",
                                "mechanism",
                                "field_ids",
                                "expression_suggestions",
                                "evidence_refs",
                                "risks",
                            )
                            if getattr(old, key) != getattr(proposal, key)
                        ]
                        regression = await dispatch(
                            Role.CRITIC,
                            version,
                            phase,
                            {
                                "proposal": asdict(proposal),
                                "workflow": {
                                    "phase": "regression",
                                    "previous_proposal": asdict(old),
                                    "review": asdict(old_review),
                                    "changes": changes,
                                },
                            },
                        )
                        if regression is None:
                            return study_id
                        self.store.save_review(study_id, regression, phase)
                        if regression.result.verdict == "UNCERTAIN":
                            return need_evidence(regression, phase)
                        if regression.result.verdict == "FAIL":
                            failed = regression.result
                            break
                if failed is None:
                    return finish("APPROVED", "ALL_REQUIRED_REVIEWS_PASS")
                with self.store.ledger.transaction():
                    self.store.event(study_id, "RESEARCH_REVIEW_FAILED", version=version)
                if not repairable(failed):
                    return finish("REJECTED", "NON_REPAIRABLE_FINDINGS")
                if version > policy.max_repairs:
                    return finish("REJECTED", "REPAIR_LIMIT_EXHAUSTED")
                failures.append((proposal, failed))
        except asyncio.CancelledError:
            finish("CANCELLED", "CALLER_CANCELLED")
            raise
        except Exception:
            # Preserve committed Agent outcomes; no raw errors or private traces in business audit.
            finish("EXECUTION_FAILED", "CONTROLLER_INTERRUPTED")
            raise
