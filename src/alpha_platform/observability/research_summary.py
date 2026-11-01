"""Read-only consistent M2-B facts; shares existing sanitization and safe JSON exports."""

import json
import sqlite3
from pathlib import Path

from .diagnostics import references
from .safety import sanitize


def research_summary(path, study_id):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    evidence = {}

    def add(ref, kind, content):
        evidence[ref] = {"source_kind": kind, "summary": sanitize(content)}
        return ref

    try:
        db.execute("BEGIN")
        study = db.execute("SELECT * FROM signal_studies WHERE study_id=?", (study_id,)).fetchone()
        if study is None:
            raise ValueError("STUDY_NOT_FOUND")
        sources = json.loads(study["sources_json"])
        for item in sources["evidence"]:
            add(item["ref"], "APPROVED_INPUT_NOT_INDEPENDENTLY_VERIFIED", item)
        study_ref = add(
            f"study:{study_id}",
            "RESEARCH_STUDY",
            {
                "signal_id": study["signal_id"],
                "status": study["status"],
                "reason_code": study["reason_code"],
                "policy": json.loads(study["policy_json"]),
                "routing": json.loads(study["routing_json"]),
            },
        )
        versions = []
        for row in db.execute(
            "SELECT * FROM signal_versions WHERE study_id=? ORDER BY version", (study_id,)
        ):
            proposal = json.loads(row["proposal_json"])
            ref = add(f"signal:{study_id}:v{row['version']}", "SIGNAL_VERSION", proposal)
            versions.append(
                {
                    "version": row["version"],
                    "proposal": sanitize(proposal),
                    "source_ref": ref,
                    "task_id": row["task_id"],
                    "attempt_id": row["attempt_id"],
                }
            )
        reviews = []
        for row in db.execute(
            "SELECT * FROM signal_reviews WHERE study_id=? ORDER BY version,phase", (study_id,)
        ):
            review = json.loads(row["review_json"])
            ref = add(
                f"review:{study_id}:v{row['version']}:{row['phase']}", "LOCKED_REVIEW", review
            )
            reviews.append(
                {
                    "version": row["version"],
                    "phase": row["phase"],
                    "review": sanitize(review),
                    "source_ref": ref,
                    "task_id": row["task_id"],
                    "attempt_id": row["attempt_id"],
                }
            )
        tasks = []
        for row in db.execute(
            "SELECT t.*,a.envelope_json,a.status,a.current_attempt_id FROM signal_tasks t "
            "JOIN agent_tasks a ON a.task_id=t.task_id WHERE t.study_id=? ORDER BY t.version,t.phase",
            (study_id,),
        ):
            envelope = json.loads(row["envelope_json"])
            ref = add(f"agent_task:{row['task_id']}", "AGENT_TASK", envelope)
            attempts = []
            for attempt in db.execute(
                "SELECT * FROM agent_attempts WHERE task_id=? ORDER BY created_at,attempt_id",
                (row["task_id"],),
            ):
                outcome = json.loads(attempt["outcome_json"]) if attempt["outcome_json"] else {}
                result_ref = outcome.get("validated_result_ref")
                if result_ref:
                    add(result_ref, "AGENT_VALIDATED_RESULT", outcome["result"])
                attempt_ref = add(
                    f"agent_attempt:{attempt['attempt_id']}",
                    "AGENT_ATTEMPT",
                    {
                        "backend": attempt["backend"],
                        "execution_status": attempt["status"],
                        "safe_error_code": outcome.get("safe_error_code"),
                        "result_ref": result_ref,
                    },
                )
                attempts.append(
                    {
                        "attempt_id": attempt["attempt_id"],
                        "backend": attempt["backend"],
                        "execution_status": attempt["status"],
                        "safe_error_code": outcome.get("safe_error_code"),
                        "source_ref": attempt_ref,
                        "result_ref": result_ref,
                    }
                )
            tasks.append(
                {
                    "task_id": row["task_id"],
                    "version": row["version"],
                    "phase": row["phase"],
                    "status": row["status"],
                    "source_ref": ref,
                    "attempts": attempts,
                }
            )
        events = []
        for row in db.execute(
            "SELECT * FROM signal_events WHERE study_id=? ORDER BY id", (study_id,)
        ):
            payload = json.loads(row["payload_json"])
            ref = add(f"research_audit:{row['id']}", "RESEARCH_AUDIT", payload)
            events.append(
                {
                    "sequence": row["id"],
                    "event_type": row["event_type"],
                    "at": row["at"],
                    "payload": sanitize(payload),
                    "source_ref": ref,
                }
            )
        requests = []
        for row in db.execute(
            "SELECT * FROM signal_evidence_requests WHERE study_id=? ORDER BY id", (study_id,)
        ):
            response = json.loads(row["response_json"])
            item = response.get("evidence")
            if item:
                add(item["ref"], "APPROVED_INPUT_NOT_INDEPENDENTLY_VERIFIED", item)
            ref = add(f"evidence_request:{row['id']}", "EVIDENCE_REQUEST", response)
            requests.append(
                {
                    "source_ref": ref,
                    "task_id": row["task_id"],
                    "version": row["version"],
                    "requested_ref": row["requested_ref"],
                    "response": sanitize(response),
                }
            )
        policy = json.loads(study["policy_json"])
        report = {
            "schema_version": 1,
            "namespace": "M2B_RESEARCH",
            "study_id": study_id,
            "signal_id": study["signal_id"],
            "status": study["status"],
            "reason_code": study["reason_code"],
            "current_version": study["current_version"],
            "source_ref": study_ref,
            "budget": {
                "agent_calls": study["used_calls"],
                "agent_call_limit": policy["max_agent_calls"],
                "evidence_requests": study["used_evidence"],
                "evidence_limit": policy["max_evidence_requests"],
            },
            "simulation_budget": "NOT_ACCESSED",
            "versions": versions,
            "reviews": reviews,
            "tasks": tasks,
            "events": events,
            "evidence_requests": requests,
            "evidence": evidence,
            "limitations": [
                "PASS is not proof of economic validity",
                "Approved input evidence is not independently verified",
                "No automatic study resume or backend switching",
                "No new Replay nodes; no simulation calls",
            ],
        }
        if references(report) - evidence.keys():
            raise ValueError("RESEARCH_EVIDENCE_REFERENCE_MISSING")
        return report
    finally:
        db.rollback()
        db.close()
