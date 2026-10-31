"""Versioned role contracts. Execution success is separate from research judgement."""

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from enum import StrEnum


class ContractError(ValueError):
    pass


class Role(StrEnum):
    GENERATOR = "generator"
    CRITIC = "critic"


class ExecutionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    UNSUPPORTED = "UNSUPPORTED"
    STALE = "STALE"


class Verdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNCERTAIN = "UNCERTAIN"


SCOPES = frozenset({"field_metadata", "provided_evidence"})
BASE_CAPABILITIES = frozenset(
    {"structured_result", "bounded_execution", "scoped_context", "fresh_session"}
)
CAPABILITIES = BASE_CAPABILITIES | {"host_isolation"}


def canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def object_fields(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ContractError("OBJECT_FIELDS_INVALID")


def text(value, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ContractError("TEXT_INVALID")
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise ContractError("IDENTIFIER_INVALID")
    return value


def positive_int(value, maximum=1000000):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ContractError("INTEGER_INVALID")
    return value


def strings(value, limit=32):
    if not isinstance(value, list) or len(value) > limit:
        raise ContractError("ARRAY_INVALID")
    items = tuple(text(item) for item in value)
    if len(set(items)) != len(items):
        raise ContractError("DUPLICATE_ITEMS")
    return items


@dataclass(frozen=True)
class Subject:
    signal_id: str
    signal_version: int

    def __post_init__(self):
        identifier(self.signal_id)
        positive_int(self.signal_version)


@dataclass(frozen=True)
class ResourceLimits:
    timeout_seconds: float = 30
    max_attempts: int = 3
    max_output_bytes: int = 65536
    max_evidence_requests: int = 0

    def __post_init__(self):
        if type(self.timeout_seconds) not in (int, float) or not 0 < self.timeout_seconds <= 300:
            raise ContractError("TIMEOUT_LIMIT_INVALID")
        positive_int(self.max_attempts, 10)
        if type(self.max_output_bytes) is not int or not 256 <= self.max_output_bytes <= 262144:
            raise ContractError("OUTPUT_LIMIT_INVALID")
        if type(self.max_evidence_requests) is not int or self.max_evidence_requests != 0:
            raise ContractError("EVIDENCE_GATEWAY_NOT_IMPLEMENTED")


@dataclass(frozen=True)
class TaskEnvelope:
    task_id: str
    role: Role
    subject: Subject
    context_version: str
    context_digest: str
    allowed_evidence_scopes: tuple[str, ...] = ("field_metadata", "provided_evidence")
    required_capabilities: tuple[str, ...] = tuple(sorted(BASE_CAPABILITIES))
    resource_limits: ResourceLimits = ResourceLimits()
    schema_version: int = 1

    def __post_init__(self):
        identifier(self.task_id)
        identifier(self.context_version)
        if not isinstance(self.role, Role) or not isinstance(self.subject, Subject):
            raise ContractError("ROLE_OR_SUBJECT_INVALID")
        if not isinstance(self.resource_limits, ResourceLimits):
            raise ContractError("LIMITS_INVALID")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ContractError("SCHEMA_VERSION_UNSUPPORTED")
        if not isinstance(self.context_digest, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", self.context_digest
        ):
            raise ContractError("CONTEXT_DIGEST_INVALID")
        for values, allowed in (
            (self.allowed_evidence_scopes, SCOPES),
            (self.required_capabilities, CAPABILITIES),
        ):
            if not isinstance(values, tuple) or any(not isinstance(v, str) for v in values):
                raise ContractError("POLICY_INVALID")
            if len(values) != len(set(values)) or set(values) - allowed:
                raise ContractError("POLICY_INVALID")

    @property
    def result_schema(self):
        return "main_signal_proposal_v1" if self.role == Role.GENERATOR else "review_result_v1"

    def document(self):
        return {**asdict(self), "result_schema": self.result_schema}


@dataclass(frozen=True)
class MainSignalProposal:
    signal_id: str
    signal_version: int
    economic_hypothesis: str
    mechanism: str
    field_ids: tuple[str, ...]
    expression_suggestions: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    risks: tuple[str, ...]
    schema_version: int = 1


@dataclass(frozen=True)
class Finding:
    id: str
    category: str
    severity: str
    description: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ReviewResult:
    signal_id: str
    signal_version: int
    verdict: Verdict
    findings: tuple[Finding, ...]
    evidence_refs: tuple[str, ...]
    schema_version: int = 1


@dataclass(frozen=True)
class ExecutionOutcome:
    task_id: str
    attempt_id: str
    backend: str
    role: Role
    subject: Subject
    context_version: str
    context_digest: str
    execution_status: ExecutionStatus
    safe_error_code: str | None
    elapsed_ms: float
    validated_result_ref: str | None = None
    result: MainSignalProposal | ReviewResult | None = None
    schema_version: int = 1


def parse_proposal(value, subject, field_ids, evidence_refs):
    object_fields(value, MainSignalProposal.__dataclass_fields__)
    _subject(value, subject)
    fields = strings(value["field_ids"])
    if not fields or set(fields) - set(field_ids):
        raise ContractError("UNAPPROVED_FIELDS")
    expressions = strings(value["expression_suggestions"], 8)
    if not expressions:
        raise ContractError("EXPRESSION_REQUIRED")
    refs = strings(value["evidence_refs"])
    if set(refs) - set(evidence_refs):
        raise ContractError("UNAPPROVED_EVIDENCE")
    return MainSignalProposal(
        subject.signal_id,
        subject.signal_version,
        text(value["economic_hypothesis"]),
        text(value["mechanism"]),
        fields,
        expressions,
        refs,
        strings(value["risks"]),
    )


def _subject(value, subject):
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or type(value["signal_version"]) is not int
        or value["signal_version"] != subject.signal_version
        or value["signal_id"] != subject.signal_id
    ):
        raise ContractError("SUBJECT_VERSION_MISMATCH")


def parse_review(value, subject, evidence_refs):
    object_fields(value, ReviewResult.__dataclass_fields__)
    _subject(value, subject)
    try:
        verdict = Verdict(value["verdict"])
    except (ValueError, TypeError) as exc:
        raise ContractError("VERDICT_INVALID") from exc
    if not isinstance(value["findings"], list) or len(value["findings"]) > 32:
        raise ContractError("FINDINGS_INVALID")
    findings = []
    for finding in value["findings"]:
        object_fields(finding, Finding.__dataclass_fields__)
        if finding["severity"] not in ("minor", "major", "blocking"):
            raise ContractError("SEVERITY_INVALID")
        refs = strings(finding["evidence_refs"])
        if set(refs) - set(evidence_refs):
            raise ContractError("UNAPPROVED_EVIDENCE")
        findings.append(
            Finding(
                identifier(finding["id"]),
                text(finding["category"], 100),
                finding["severity"],
                text(finding["description"]),
                refs,
            )
        )
    if len({f.id for f in findings}) != len(findings):
        raise ContractError("DUPLICATE_FINDING_ID")
    if verdict == Verdict.PASS and any(f.severity in {"major", "blocking"} for f in findings):
        raise ContractError("PASS_WITH_BLOCKING_FINDING")
    if verdict == Verdict.FAIL and not findings:
        raise ContractError("FAIL_WITHOUT_FINDINGS")
    refs = strings(value["evidence_refs"])
    if set(refs) - set(evidence_refs):
        raise ContractError("UNAPPROVED_EVIDENCE")
    return ReviewResult(subject.signal_id, subject.signal_version, verdict, tuple(findings), refs)


def strict_json(raw, max_bytes):
    if (
        not isinstance(raw, (str, bytes))
        or len(raw.encode("utf-8") if isinstance(raw, str) else raw) > max_bytes
    ):
        raise ContractError("OUTPUT_SIZE_OR_TYPE_INVALID")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ContractError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ContractError("NONFINITE_JSON")

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ContractError("NONFINITE_JSON")
        return number

    try:
        return json.loads(
            raw, object_pairs_hook=pairs, parse_constant=invalid_constant, parse_float=finite_float
        )
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ContractError("INVALID_JSON") from exc


@dataclass(frozen=True)
class ContextPacket:
    role: Role
    subject: Subject
    version: str
    content_json: str
    evidence_scopes: tuple[str, ...]

    @property
    def digest(self):
        return (
            "sha256:"
            + hashlib.sha256(
                canonical(
                    {
                        "role": self.role,
                        "subject": asdict(self.subject),
                        "version": self.version,
                        "content": self.content(),
                        "evidence_scopes": self.evidence_scopes,
                    }
                ).encode()
            ).hexdigest()
        )

    def content(self):
        return strict_json(self.content_json, 32768)

    def evidence(self, scope):
        if scope not in self.evidence_scopes:
            raise PermissionError("EVIDENCE_SCOPE_DENIED")
        content = self.content()
        return content["field_metadata"] if scope == "field_metadata" else content["evidence"]
