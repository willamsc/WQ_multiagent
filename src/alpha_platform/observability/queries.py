"""Read-only, snapshot-consistent mapping of real M1 facts to a public evidence model."""

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .graph import NODES, build_graph, event_node
from .safety import safe_text, sanitize


class RunNotFoundError(ValueError):
    pass


def utc(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (
            dt.replace(tzinfo=dt.tzinfo or timezone.utc)
            .astimezone(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
    except (TypeError, ValueError):
        return None


def unpack(value):
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return {}


def reason_code(reason):
    for token in (
        "VALIDATION",
        "BUDGET_EXHAUSTED",
        "DUPLICATE",
        "REMOTE_UNKNOWN",
        "REMOTE_FAILED",
        "RATE_LIMIT",
        "MOCK_GATE_PASS",
        "MOCK_GATE_FAIL",
        "LOCAL_RESEARCH_GATE_PASS",
        "LOCAL_RESEARCH_GATE_FAIL",
    ):
        if reason.startswith(token):
            return token
    if "result persistence failed" in reason:
        return "PERSISTENCE_FAILURE"
    return "INTERNAL" if reason else None


def _valid_trace_payload(payload):
    if not isinstance(payload, dict):
        return False
    for key, value in payload.items():
        if key in {"expression", "summary", "reason_code", "error_class"} and not isinstance(
            value, str
        ):
            return False
        if key == "status" and (
            not isinstance(value, str) or value not in {"RUNNING", "COMPLETED", "FAILED", "INVALID"}
        ):
            return False
        if key == "gate" and value not in ("PASS", "FAIL"):
            return False
        if key in {"truncated", "remote_ref_present"} and type(value) is not bool:
            return False
        if key == "attempt_number" and (type(value) is not int or not 1 <= value <= 2**53 - 1):
            return False
        if key == "duration_ms" and (
            type(value) not in (int, float)
            or (isinstance(value, float) and not math.isfinite(value))
            or value < 0
            or value > 10**12
        ):
            return False
    return True


class ObservatoryReader:
    def __init__(self, path):
        self.db = sqlite3.connect(
            Path(path).resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None
        )
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA query_only=ON")
        self.db.execute("PRAGMA busy_timeout=2000")

    def close(self):
        self.db.close()

    def snapshot(self, run_id, max_events=200, *, for_replay=False):
        if not 1 <= max_events <= 2000:
            raise ValueError("max_events must be between 1 and 2000")
        self.db.execute("BEGIN")
        try:
            return self._snapshot(run_id, max_events, for_replay)
        finally:
            self.db.rollback()

    def _snapshot(self, run_id, max_events, for_replay=False):
        row = self.db.execute("SELECT * FROM research_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise RunNotFoundError("research run not found")
        run = dict(row)
        evidence = {}
        replay_warnings = []

        def add(ref, kind, summary):
            evidence[ref] = {"source_kind": kind, "summary": sanitize(summary)}
            return ref

        run_ref = add(
            f"run:{run_id}",
            "BUSINESS",
            {
                "status": run["status"],
                "phase": run["phase"],
                "budget_limit": run["budget_limit"],
                "reserved_budget": run["reserved_budget"],
                "used_budget": run["used_budget"],
                "created_at": utc(run["created_at"]),
            },
        )
        config = unpack(run["config_json"])
        config_summary = {
            key: config.get(key)
            for key in ("region", "universe", "delay", "instrument", "min_sharpe")
        }
        jobs = []
        result_gaps = set()
        for item in self.db.execute(
            "SELECT j.*, a.expression, a.canonical_expression, "
            "a.fingerprint, r.metrics_json, r.checks_json, r.raw_json, r.gate "
            "FROM sim_jobs j JOIN alphas a ON a.id=j.alpha_id "
            "LEFT JOIN sim_results r ON r.job_id=j.id "
            "WHERE j.run_id=? ORDER BY j.id LIMIT 100",
            (run_id,),
        ):
            item = dict(item)
            alpha_ref = add(
                f"alpha:{item['alpha_id']}",
                "BUSINESS",
                {
                    "expression": item["expression"],
                    "canonical_expression": item["canonical_expression"],
                    "fingerprint": item["fingerprint"],
                },
            )
            job = {
                "job_id": item["id"],
                "candidate_id": item["alpha_id"],
                "status": item["status"],
                "remote_id_known": bool(item["remote_id"]),
                "attempt_count": item["attempt_count"],
                "result_present": item["metrics_json"] is not None,
                "candidate_ref": alpha_ref,
                "source_ref": f"job:{item['id']}",
                "result_ref": None,
            }
            add(job["source_ref"], "BUSINESS", {k: v for k, v in job.items() if k != "source_ref"})
            if job["result_present"]:
                raw = unpack(item["raw_json"])
                if isinstance(raw, dict) and isinstance(raw.get("evidence_gaps"), list):
                    if "WQ_TEST_PERIOD_NOT_ECHOED" in raw["evidence_gaps"]:
                        result_gaps.add(
                            "WQ_TEST_PERIOD_NOT_ECHOED: remote test period was not verified"
                        )
                metrics = unpack(item["metrics_json"])
                job["result_ref"] = add(
                    f"result:{item['id']}",
                    "BUSINESS",
                    {
                        "metrics": {
                            k: metrics[k]
                            for k in ("sharpe", "fitness", "turnover", "margin")
                            if k in metrics
                        },
                        "gate": item["gate"],
                        "checks_present": bool(unpack(item["checks_json"])),
                        "raw_response_omitted": True,
                    },
                )
            jobs.append(job)
        job_map = {job["job_id"]: job for job in jobs}
        attempts = []
        for attempt in self.db.execute(
            "SELECT a.* FROM job_attempts a JOIN sim_jobs j "
            "ON j.id=a.job_id WHERE j.run_id=? ORDER BY a.at,a.id "
            "LIMIT 2000",
            (run_id,),
        ):
            ref = add(
                f"attempt:{attempt['id']}",
                "BUSINESS",
                {
                    "job_id": attempt["job_id"],
                    "outcome": attempt["outcome"],
                    "remote_ref_present": bool(attempt["remote_ref"]),
                    "at": utc(attempt["at"]),
                },
            )
            attempts.append(
                {"source_ref": ref, "job_id": attempt["job_id"], "outcome": attempt["outcome"]}
            )
        audit_count = self.db.execute(
            "SELECT count(*) FROM research_events WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        rows = list(
            reversed(
                self.db.execute(
                    "SELECT * FROM research_events WHERE run_id=? ORDER BY id DESC LIMIT 2000",
                    (run_id,),
                ).fetchall()
            )
        )
        events = []
        poll_jobs = set()
        anomalies = []
        last_reason = None

        def anomaly(code, node, refs, status="FAILED", severity="warning"):
            anomalies.append(
                {
                    "code": code,
                    "severity": severity,
                    "node": node,
                    "node_status": status,
                    "derived": True,
                    "evidence_refs": refs,
                }
            )

        for audit in rows:
            payload = unpack(audit["payload_json"])
            if for_replay:
                try:
                    payload = json.loads(audit["payload_json"])
                except (ValueError, TypeError):
                    payload = None
            if for_replay and not isinstance(payload, dict):
                replay_warnings.append(f"MALFORMED_AUDIT_PAYLOAD:audit:{audit['id']}")
                payload = {}
            kind, job_id = audit["event_type"], audit["job_id"]
            reason = str(payload.get("reason", ""))
            code = reason_code(reason)
            if code:
                last_reason = code
            node = event_node(kind, job_id, reason)
            if kind in {"POLLING", "POLL_INTENT", "FETCH_INTENT"}:
                poll_jobs.add(job_id)
            if kind in {"UNKNOWN", "FAILED"} and job_id in poll_jobs and job_id:
                node = "poll"
            summary = {k: payload[k] for k in ("fingerprint", "alpha_id") if k in payload}
            if kind == "GENERATED":
                summary["expression"] = payload.get("expression")
                signal = payload.get("signal", {})
                summary["field_ids"] = signal.get("field_ids") if isinstance(signal, dict) else None
            if reason:
                summary.update(reason_code=code, message=safe_text(reason))
            if kind == "RESERVED":
                summary["budget_delta"] = {"reserved": 1, "used": 0, "derived": True}
            if kind == "SUBMITTED":
                summary["budget_delta"] = {"reserved": -1, "used": 1, "derived": True}
            ref = add(f"audit:{audit['id']}", "AUDIT", summary)
            outcome = (
                "RUNNING"
                if kind in {"SUBMITTING", "SUBMIT_INTENT", "POLLING", "POLL_INTENT"}
                else "REJECTED"
                if kind in {"REJECTED", "DUPLICATE", "BUDGET_EXHAUSTED", "RATE_LIMIT"}
                else kind
                if kind in {"FAILED", "UNKNOWN"}
                else "COMPLETED"
            )
            event = {
                "event_id": ref,
                "schema_version": 1,
                "run_id": run_id,
                "job_id": job_id,
                "candidate_id": job_map.get(job_id, {}).get("candidate_id"),
                "node": node,
                "event_type": kind,
                "occurred_at": utc(audit["at"]),
                "sequence": audit["id"],
                "outcome": outcome,
                "source_kind": "AUDIT",
                "source_ref": ref,
                "payload": sanitize(summary),
                "derived": False,
            }
            events.append(event)
            if kind == "REJECTED" and code == "VALIDATION":
                anomaly("VALIDATION_REJECTED", "validator", [ref], "REJECTED")
            if kind in {"DUPLICATE", "BUDGET_EXHAUSTED"}:
                anomaly(kind, "dedup_budget", [ref], "REJECTED")
            if code == "PERSISTENCE_FAILURE":
                anomaly(code, "result_persist", [ref])
            if code == "RATE_LIMIT":
                anomaly("RATE_LIMIT_EXHAUSTED", "submit", [ref])
            if code == "REMOTE_FAILED":
                anomaly(code, "poll", [ref])
        for job in jobs:
            if job["status"] == "UNKNOWN":
                node = "poll" if job["remote_id_known"] else "submit"
                anomaly("REMOTE_EXECUTION_UNKNOWN", node, [job["source_ref"]], "UNKNOWN")
                if not job["remote_id_known"]:
                    anomaly("MISSING_RECEIPT", "submit", [job["source_ref"]], "UNKNOWN")
            if run["status"] == "FAILED" and job["status"] == "POLLING":
                anomaly(
                    "RUN_JOB_STATE_MISMATCH_MANUAL_REVIEW",
                    "result_persist",
                    [run_ref, job["source_ref"]],
                )
        trace_exists = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='observability_traces'"
        ).fetchone()
        traces = []
        trace_count = 0
        if trace_exists:
            if for_replay:
                trace_count = self.db.execute(
                    "SELECT count(*) FROM observability_traces WHERE run_id=?", (run_id,)
                ).fetchone()[0]
            traces = self.db.execute(
                "SELECT * FROM observability_traces WHERE run_id=? ORDER BY id DESC LIMIT 1000",
                (run_id,),
            ).fetchall()[::-1]
        for trace in traces:
            trace_payload = unpack(trace["payload_json"])
            if for_replay:
                try:
                    trace_payload = json.loads(trace["payload_json"])
                except (TypeError, ValueError):
                    trace_payload = None
                valid_payload = (
                    _valid_trace_payload(trace_payload)
                    and isinstance(trace["payload_json"], str)
                    and len(trace["payload_json"].encode("utf-8")) <= 8192
                )
                if (
                    trace["node"] not in NODES
                    or trace["event_type"] not in {"NODE_STARTED", "NODE_COMPLETED", "NODE_FAILED"}
                    or trace["outcome"] not in {"RUNNING", "COMPLETED", "FAILED"}
                    or not valid_payload
                    or {
                        "NODE_STARTED": "RUNNING",
                        "NODE_COMPLETED": "COMPLETED",
                        "NODE_FAILED": "FAILED",
                    }.get(trace["event_type"])
                    != trace["outcome"]
                ):
                    replay_warnings.append(f"MALFORMED_TRACE_SKIPPED:trace:{trace['id']}")
                    continue
                from .traces import ALLOWED

                trace_payload = {k: v for k, v in trace_payload.items() if k in ALLOWED}
            ref = add(f"trace:{trace['id']}", "TRACE", trace_payload)
            events.append(
                {
                    "event_id": ref,
                    "schema_version": 1,
                    "run_id": run_id,
                    "job_id": trace["job_id"],
                    "candidate_id": trace["candidate_id"],
                    "node": trace["node"],
                    "event_type": trace["event_type"],
                    "occurred_at": utc(trace["occurred_at"]),
                    "sequence": trace["id"],
                    "outcome": trace["outcome"],
                    "source_kind": "TRACE",
                    "source_ref": ref,
                    "payload": evidence[ref]["summary"],
                    "derived": False,
                }
            )
        events.sort(
            key=lambda e: (e["occurred_at"] or "", e["source_kind"] == "TRACE", e["sequence"])
        )
        nodes, edges = build_graph(events, jobs, run["status"], anomalies)
        next(n for n in nodes if n["id"] == "run_terminal")["evidence_refs"].append(run_ref)
        gaps = sorted(result_gaps)
        if not traces:
            gaps.append("NO_OPTIONAL_TRACE: historical durations and poll responses unavailable")
        if not rows or audit_count > len(rows):
            gaps.append("INCOMPLETE_EVIDENCE: audit missing or read limit reached")
        if any(job["status"] == "COMPLETED" and not job["result_present"] for job in jobs):
            gaps.append("INCOMPLETE_EVIDENCE: completed job has no result row")
        if any(e["payload"].get("truncated") for e in events if e["source_kind"] == "TRACE"):
            gaps.append("TRACE_TRUNCATED: payload or trace-buffer limit reached")
        if len(events) > max_events and not for_replay:
            gaps.append("TIMELINE_TRUNCATED: earlier evidence remains available in evidence map")
        for node in nodes:
            if node["status"] == "NOT_VISITED" and run["status"] == "COMPLETED":
                node["status"] = "INFERRED"
                node["evidence_refs"] = [run_ref]
                gaps.append("INCOMPLETE_EVIDENCE: " + node["id"])
        result = {
            "schema_version": 1,
            "run_id": run_id,
            "snapshot_generated_at": datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "run": {
                "source_ref": run_ref,
                "status": run["status"],
                "phase": run["phase"],
                "config_summary": sanitize(config_summary),
                "budget": {
                    "total": run["budget_limit"],
                    "reserved": run["reserved_budget"],
                    "used": run["used_budget"],
                },
                "last_reason_code": last_reason,
            },
            "jobs": jobs,
            "attempts": attempts,
            "nodes": nodes,
            "edges": edges,
            "events": events if for_replay else events[-max_events:],
            "anomalies": anomalies,
            "evidence": evidence,
            "evidence_gaps": gaps,
            "limitations": [
                "M1 has no automatic reconciliation or recovery",
                "Graph and budget deltas are derived; raw adapter data is omitted",
                "Missing optional trace does not indicate execution failure",
                "Pre-admission events may have null job/candidate IDs; no IDs are invented",
                "Audit timestamps have second precision; cross-source timing ties are approximate",
            ],
            "summary": f"Run {run['status']}; {len(jobs)} jobs; {len(anomalies)} anomalies. "
            "Derived observations are not root-cause diagnoses.",
        }
        if for_replay:
            result["replay_input"] = {
                "warnings": replay_warnings,
                "audit_count": audit_count,
                "trace_count": trace_count,
                "read_audit_count": len(rows),
                "read_trace_count": len(traces),
                "read_limited": audit_count > len(rows) or trace_count > len(traces),
            }
        return result
