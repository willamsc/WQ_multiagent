"""Separate Agent task ledger. Never initialize extensions in the M1 business database."""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from .context import approved_text
from .contracts import ExecutionStatus, Subject, canonical, identifier, parse_proposal, parse_review


class AgentLedger:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='research_runs'").fetchone():
            self.db.close()
            raise ValueError("AGENT_LEDGER_MUST_NOT_BE_M1_DATABASE")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        try:
            self.db.execute("BEGIN IMMEDIATE")
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def initialize(self):
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise ValueError("AGENT_LEDGER_VERSION_UNSUPPORTED")
        self.db.executescript("""BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS agent_tasks (
          task_id TEXT PRIMARY KEY, envelope_json TEXT NOT NULL, context_json TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('CREATED','RUNNING','COMPLETED','FAILED','CANCELLED')),
          current_attempt_id TEXT, attempt_count INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS agent_attempts (
          attempt_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES agent_tasks(task_id),
          backend TEXT NOT NULL, status TEXT NOT NULL, outcome_json TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS agent_results (
          attempt_id TEXT PRIMARY KEY REFERENCES agent_attempts(attempt_id),
          task_id TEXT NOT NULL UNIQUE REFERENCES agent_tasks(task_id), result_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS agent_events (
          id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES agent_tasks(task_id),
          attempt_id TEXT REFERENCES agent_attempts(attempt_id), event_type TEXT NOT NULL,
          payload_json TEXT NOT NULL, at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS idx_agent_events_task ON agent_events(task_id,id);
        PRAGMA user_version=1; COMMIT;""")

    def event(self, task_id, attempt_id, kind, payload=None):
        self.db.execute(
            "INSERT INTO agent_events(task_id,attempt_id,event_type,payload_json) VALUES(?,?,?,?)",
            (task_id, attempt_id, kind, canonical(payload or {})),
        )

    def register(self, task, context):
        encoded = canonical(task.document())
        with self.transaction():
            existing = self.db.execute(
                "SELECT * FROM agent_tasks WHERE task_id=?", (task.task_id,)
            ).fetchone()
            if existing:
                if (
                    existing["envelope_json"] != encoded
                    or existing["context_json"] != context.content_json
                ):
                    raise ValueError("IMMUTABLE_TASK_CONFLICT")
                return
            self.db.execute(
                "INSERT INTO agent_tasks(task_id,envelope_json,context_json,status) VALUES(?,?,?,?)",
                (task.task_id, encoded, context.content_json, "CREATED"),
            )
            self.event(
                task.task_id,
                None,
                "TASK_CREATED",
                {"context_digest": task.context_digest, "role": task.role},
            )

    def begin(self, task, backend, supersede=False):
        identifier(backend)
        attempt_id = str(uuid4())
        with self.transaction():
            row = self.db.execute(
                "SELECT * FROM agent_tasks WHERE task_id=?", (task.task_id,)
            ).fetchone()
            if row is None or row["status"] == "COMPLETED":
                raise ValueError("TASK_ALREADY_COMPLETED_OR_MISSING")
            if row["envelope_json"] != canonical(task.document()):
                raise ValueError("IMMUTABLE_TASK_CONFLICT")
            if row["status"] == "RUNNING" and not supersede:
                raise ValueError("TASK_ALREADY_RUNNING")
            if row["attempt_count"] >= task.resource_limits.max_attempts:
                raise ValueError("AGENT_ATTEMPT_LIMIT_REACHED")
            self.db.execute(
                "INSERT INTO agent_attempts(attempt_id,task_id,backend,status) VALUES(?,?,?,?)",
                (attempt_id, task.task_id, backend, "RUNNING"),
            )
            self.db.execute(
                "UPDATE agent_tasks SET current_attempt_id=?,status='RUNNING',"
                "attempt_count=attempt_count+1 WHERE task_id=?",
                (attempt_id, task.task_id),
            )
            self.event(task.task_id, attempt_id, "ATTEMPT_STARTED", {"backend": backend})
        return attempt_id

    def finish(self, outcome):
        with self.transaction():
            row = self.db.execute(
                "SELECT * FROM agent_tasks WHERE task_id=?", (outcome.task_id,)
            ).fetchone()
            attempt = self.db.execute(
                "SELECT * FROM agent_attempts WHERE attempt_id=?", (outcome.attempt_id,)
            ).fetchone()
            if attempt is None or attempt["task_id"] != outcome.task_id:
                raise ValueError("ATTEMPT_NOT_FOUND")
            if attempt["status"] != "RUNNING":
                return False
            if row["current_attempt_id"] != outcome.attempt_id or row["status"] != "RUNNING":
                self.db.execute(
                    "UPDATE agent_attempts SET status='STALE' WHERE attempt_id=?",
                    (outcome.attempt_id,),
                )
                self.event(outcome.task_id, outcome.attempt_id, "LATE_ATTEMPT_DISCARDED")
                return False
            task = json.loads(row["envelope_json"])
            binding = {
                "role": outcome.role,
                "subject": asdict(outcome.subject),
                "context_version": outcome.context_version,
                "context_digest": outcome.context_digest,
                "schema_version": outcome.schema_version,
            }
            if canonical(binding) != canonical({key: task[key] for key in binding}):
                raise ValueError("OUTCOME_TASK_BINDING_MISMATCH")
            if (
                outcome.backend != attempt["backend"]
                or not isinstance(outcome.execution_status, ExecutionStatus)
                or type(outcome.elapsed_ms) not in (int, float)
                or not 0 <= outcome.elapsed_ms <= 10**12
            ):
                raise ValueError("OUTCOME_INVALID")
            if outcome.safe_error_code is not None:
                identifier(outcome.safe_error_code)
                approved_text(outcome.safe_error_code)
            if outcome.execution_status != ExecutionStatus.SUCCEEDED and (
                outcome.result is not None or outcome.validated_result_ref is not None
            ):
                raise ValueError("FAILED_OUTCOME_CANNOT_CONTAIN_RESULT")
            if outcome.execution_status == ExecutionStatus.SUCCEEDED:
                if (
                    outcome.result is None
                    or outcome.safe_error_code is not None
                    or outcome.validated_result_ref != f"agent_result:{outcome.attempt_id}"
                ):
                    raise ValueError("SUCCESS_REQUIRES_VALIDATED_RESULT")
                body = json.loads(row["context_json"])
                payload = json.loads(canonical(asdict(outcome.result)))
                refs = [e["ref"] for e in body["evidence"]]
                subject = Subject(**task["subject"])
                approved_text(payload)
                if task["role"] == "generator":
                    parse_proposal(payload, subject, body["field_metadata"], refs)
                else:
                    parse_review(payload, subject, refs)
                self.db.execute(
                    "INSERT INTO agent_results VALUES(?,?,?)",
                    (outcome.attempt_id, outcome.task_id, canonical(asdict(outcome.result))),
                )
            self.db.execute(
                "UPDATE agent_attempts SET status=?,outcome_json=? WHERE attempt_id=?",
                (outcome.execution_status, canonical(asdict(outcome)), outcome.attempt_id),
            )
            status = (
                "COMPLETED"
                if outcome.execution_status == ExecutionStatus.SUCCEEDED
                else "CANCELLED"
                if outcome.execution_status == ExecutionStatus.CANCELLED
                else "FAILED"
            )
            self.db.execute(
                "UPDATE agent_tasks SET status=? WHERE task_id=?", (status, outcome.task_id)
            )
            self.event(
                outcome.task_id,
                outcome.attempt_id,
                "ATTEMPT_" + outcome.execution_status,
                {
                    "safe_error_code": outcome.safe_error_code,
                    "validated_result_ref": outcome.validated_result_ref,
                },
            )
        return True


class AgentLedgerReader:
    def __init__(self, path):
        self.db = sqlite3.connect(
            Path(path).resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None
        )
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA query_only=ON")
        self.db.execute("PRAGMA busy_timeout=2000")

    def close(self):
        self.db.close()

    def read(self, task_id):
        self.db.execute("BEGIN")
        try:
            row = self.db.execute(
                "SELECT * FROM agent_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                raise ValueError("AGENT_TASK_NOT_FOUND")
            return {
                "task_id": task_id,
                "status": row["status"],
                "envelope": json.loads(row["envelope_json"]),
                "attempt_count": row["attempt_count"],
                "current_attempt_id": row["current_attempt_id"],
                "approved_evidence": json.loads(row["context_json"])["evidence"],
                "attempts": [
                    dict(r)
                    for r in self.db.execute(
                        "SELECT attempt_id,backend,status,outcome_json FROM agent_attempts WHERE task_id=? "
                        "ORDER BY created_at,attempt_id",
                        (task_id,),
                    )
                ],
                "events": [
                    dict(r)
                    for r in self.db.execute(
                        "SELECT * FROM agent_events WHERE task_id=? ORDER BY id", (task_id,)
                    )
                ],
            }
        finally:
            self.db.rollback()
