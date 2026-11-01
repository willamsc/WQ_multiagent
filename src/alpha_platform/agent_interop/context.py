"""A3 explicit, bounded role views. No filesystem discovery or autonomous evidence tools."""

from dataclasses import asdict
from uuid import uuid4

from ..observability.safety import safe_text
from .contracts import (
    SCOPES,
    ContextPacket,
    ContractError,
    ResourceLimits,
    Role,
    Subject,
    TaskEnvelope,
    canonical,
    identifier,
    object_fields,
    parse_proposal,
    parse_review,
    strict_json,
    text,
)


def approved_text(value):
    if isinstance(value, str) and safe_text(value, len(value)) != value:
        raise ContractError("SENSITIVE_CONTEXT_OR_RESULT")
    if isinstance(value, dict):
        for key, part in value.items():
            approved_text(key)
            approved_text(part)
    elif isinstance(value, (list, tuple)):
        for part in value:
            approved_text(part)


class ContextBuilder:
    def build(
        self,
        role,
        subject,
        sources,
        *,
        version="context-v1",
        scopes=("field_metadata", "provided_evidence"),
    ):
        if (
            not isinstance(role, Role)
            or not isinstance(subject, Subject)
            or not isinstance(sources, dict)
        ):
            raise ContractError("CONTEXT_SOURCE_INVALID")
        identifier(version)
        if not isinstance(scopes, tuple) or any(not isinstance(s, str) for s in scopes):
            raise ContractError("SCOPES_INVALID")
        if (
            len(set(scopes)) != len(scopes)
            or set(scopes) - SCOPES
            or "field_metadata" not in scopes
        ):
            raise PermissionError("CONTEXT_SCOPE_DENIED")
        settings = sources.get("settings")
        if not isinstance(settings, dict) or set(settings) - {
            "region",
            "universe",
            "delay",
            "instrument",
        }:
            raise ContractError("CONTEXT_SETTINGS_INVALID")
        clean_settings = {}
        for key, value in settings.items():
            if key == "delay":
                if type(value) is not int or not 0 <= value <= 1000000:
                    raise ContractError("CONTEXT_SETTINGS_INVALID")
            else:
                text(value, 128)
            clean_settings[key] = value
        fields = sources.get("field_metadata")
        if not isinstance(fields, dict) or not 1 <= len(fields) <= 32:
            raise ContractError("FIELD_METADATA_REQUIRED")
        clean_fields = {}
        for field_id, data in fields.items():
            text(field_id, 128)
            if (
                not isinstance(data, dict)
                or "type" not in data
                or set(data) - {"type", "description", "source_ref"}
            ):
                raise ContractError("METADATA_FIELDS_INVALID")
            clean_fields[field_id] = {key: text(value, 2000) for key, value in data.items()}
        evidence = sources.get("evidence")
        if not isinstance(evidence, list) or len(evidence) > 32:
            raise ContractError("EVIDENCE_INVALID")
        clean_evidence = []
        for item in evidence:
            if not isinstance(item, dict) or set(item) != {"ref", "scope", "summary"}:
                raise ContractError("EVIDENCE_FIELDS_INVALID")
            if not isinstance(item["scope"], str) or item["scope"] not in scopes:
                raise PermissionError("EVIDENCE_SCOPE_DENIED")
            clean_evidence.append(
                {
                    "ref": identifier(item["ref"]),
                    "scope": item["scope"],
                    "summary": text(item["summary"]),
                }
            )
        refs = [e["ref"] for e in clean_evidence]
        if len(refs) != len(set(refs)):
            raise ContractError("DUPLICATE_EVIDENCE_REF")
        content = {
            "objective": text(sources.get("objective")),
            "settings": clean_settings,
            "field_metadata": clean_fields,
            "evidence": clean_evidence,
        }
        if role == Role.CRITIC:
            # Dataclass tuples and JSON arrays have the same wire representation.
            formal = strict_json(canonical(sources.get("proposal")), 32768)
            proposal = parse_proposal(formal, subject, clean_fields, refs)
            # Only the formal proposal is shared; private drafts/reviews/session histories are not.
            content["proposal"] = asdict(proposal)
        if "workflow" in sources:
            workflow = strict_json(canonical(sources["workflow"]), 32768)
            object_fields(workflow, ("phase", "previous_proposal", "review", "changes"))
            expected = "repair" if role == Role.GENERATOR else "regression"
            if workflow["phase"] != expected or subject.signal_version < 2:
                raise ContractError("WORKFLOW_CONTEXT_PHASE_INVALID")
            old = workflow["previous_proposal"]
            if not isinstance(old, dict) or type(old.get("signal_version")) is not int:
                raise ContractError("WORKFLOW_PREVIOUS_VERSION_INVALID")
            if not 1 <= old["signal_version"] < subject.signal_version:
                raise ContractError("WORKFLOW_PREVIOUS_VERSION_INVALID")
            old_subject = Subject(subject.signal_id, old["signal_version"])
            previous = parse_proposal(old, old_subject, clean_fields, refs)
            review = parse_review(workflow["review"], old_subject, refs)
            if review.verdict != "FAIL":
                raise ContractError("WORKFLOW_REQUIRES_FORMAL_FAILURE")
            from .contracts import strings

            changes = strings(workflow["changes"], 16)
            content["workflow"] = {
                "phase": expected,
                "previous_proposal": asdict(previous),
                "review": asdict(review),
                "changes": list(changes),
            }
        approved_text(content)
        encoded = canonical(content)
        if len(encoded.encode("utf-8")) > 32768:
            raise ContractError("CONTEXT_SIZE_LIMIT")
        return ContextPacket(role, subject, version, encoded, scopes)


def verify_context(task, context):
    if not isinstance(context, ContextPacket):
        raise ContractError("APPROVED_CONTEXT_REQUIRED")
    rebuilt = ContextBuilder().build(
        context.role,
        context.subject,
        context.content(),
        version=context.version,
        scopes=context.evidence_scopes,
    )
    if rebuilt.content_json != context.content_json:
        raise ContractError("UNAPPROVED_CONTEXT_FIELDS")
    if (
        context.role != task.role
        or context.subject != task.subject
        or context.version != task.context_version
        or context.digest != task.context_digest
        or context.evidence_scopes != task.allowed_evidence_scopes
    ):
        raise ContractError("CONTEXT_BINDING_MISMATCH")


def create_task(context, *, task_id=None, limits=None, required_capabilities=None):
    options = (
        {} if required_capabilities is None else {"required_capabilities": required_capabilities}
    )
    return TaskEnvelope(
        task_id or str(uuid4()),
        context.role,
        context.subject,
        context.version,
        context.digest,
        context.evidence_scopes,
        resource_limits=limits or ResourceLimits(),
        **options,
    )
