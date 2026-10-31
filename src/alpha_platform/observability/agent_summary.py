"""A6 read-only Agent identity/evidence summaries; no new Replay/UI workflow nodes."""

import json
from pathlib import Path

from ..agent_interop.ledger import AgentLedgerReader
from .diagnostics import references, write_artifact
from .safety import sanitize


def agent_summary(path, task_id):
    reader = AgentLedgerReader(path)
    try:
        stored = reader.read(task_id)
    finally:
        reader.close()
    envelope = stored["envelope"]
    evidence = {}

    def add(ref, source, value):
        evidence[ref] = {"source_kind": source, "summary": sanitize(value)}
        return ref

    task_ref = add(f"agent_task:{task_id}", "AGENT_TASK", envelope)
    context_ref = add(
        f"agent_context:{envelope['context_digest']}",
        "AGENT_CONTEXT",
        {
            "role": envelope["role"],
            "context_version": envelope["context_version"],
            "digest": envelope["context_digest"],
            "subject": envelope["subject"],
        },
    )
    for item in stored["approved_evidence"]:
        add(item["ref"], "APPROVED_INPUT_NOT_INDEPENDENTLY_VERIFIED", item)
    attempts = []
    for attempt in stored["attempts"]:
        outcome = json.loads(attempt["outcome_json"]) if attempt["outcome_json"] else {}
        result_ref = outcome.get("validated_result_ref")
        ref = add(
            f"agent_attempt:{attempt['attempt_id']}",
            "AGENT_ATTEMPT",
            {
                "status": attempt["status"],
                "backend": attempt["backend"],
                "subject": envelope["subject"],
                "context_digest": envelope["context_digest"],
                "safe_error_code": outcome.get("safe_error_code"),
                "result_ref": result_ref,
            },
        )
        if result_ref:
            add(result_ref, "AGENT_VALIDATED_RESULT", outcome["result"])
        attempts.append(
            {
                "attempt_id": attempt["attempt_id"],
                "backend": attempt["backend"],
                "execution_status": attempt["status"],
                "source_ref": ref,
                "safe_error_code": outcome.get("safe_error_code"),
                "result_ref": result_ref,
                "usage": {
                    "elapsed_ms": outcome.get("elapsed_ms"),
                    "model_tokens": None,
                    "model_cost": None,
                },
            }
        )
    events = []
    for event in stored["events"]:
        payload = json.loads(event["payload_json"])
        ref = add(f"agent_audit:{event['id']}", "AGENT_AUDIT", payload)
        events.append(
            {
                "sequence": event["id"],
                "event_type": event["event_type"],
                "attempt_id": event["attempt_id"],
                "at": event["at"],
                "source_ref": ref,
            }
        )
    report = {
        "schema_version": 1,
        "namespace": "AGENT_INTEROP",
        "task_id": task_id,
        "task_status": stored["status"],
        "role": envelope["role"],
        "subject": envelope["subject"],
        "source_ref": task_ref,
        "context_ref": context_ref,
        "context_version": envelope["context_version"],
        "context_digest": envelope["context_digest"],
        "attempt_budget": {
            "limit": envelope["resource_limits"]["max_attempts"],
            "allocated_attempts": stored["attempt_count"],
        },
        "simulation_budget": "NOT_ACCESSED",
        "attempts": attempts,
        "events": events,
        "evidence": evidence,
        "limitations": [
            "Input evidence references are approved inputs, not verified economic facts",
            "No Agent workflow nodes were added to M1 Replay",
            "Mock context isolation is not an OS sandbox",
        ],
    }
    if references(report) - evidence.keys():
        raise ValueError("AGENT_EVIDENCE_REFERENCE_MISSING")
    return report


def write_agent_json(path, value, ledger):
    target = Path(path)
    if target.suffix.lower() != ".json":
        raise ValueError("AGENT_EXPORT_JSON_REQUIRED")
    if target.exists():
        with target.open("rb") as handle:
            if handle.read(16) == b"SQLite format 3\x00":
                raise ValueError("AGENT_EXPORT_CANNOT_OVERWRITE_SQLITE")
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    if len(encoded.encode("utf-8")) > 262144:
        raise ValueError("AGENT_EXPORT_TOO_LARGE")
    write_artifact(target, encoded, ledger)
