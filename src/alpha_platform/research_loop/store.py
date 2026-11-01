"""Additive research facts in AgentLedger, never in the simulation database."""

import json
from dataclasses import asdict

from ..agent_interop.contracts import canonical


class ResearchStore:
    def __init__(self, ledger):
        self.ledger, self.db = ledger, ledger.db

    def initialize(self):
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='loop_schema'").fetchone():
            row = self.db.execute("SELECT version FROM loop_schema WHERE id=1").fetchone()
            if row is None or row[0] != 1:
                raise ValueError("RESEARCH_SCHEMA_UNSUPPORTED")
        # AgentLedger schema v1 remains intact; extension migration has its own version.
        self.db.executescript("""BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS loop_schema (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL);
        INSERT OR IGNORE INTO loop_schema VALUES(1,1);
        CREATE TABLE IF NOT EXISTS signal_studies (
          study_id TEXT PRIMARY KEY, signal_id TEXT NOT NULL UNIQUE,
          status TEXT NOT NULL CHECK(status IN ('RUNNING','APPROVED','REJECTED','NEEDS_EVIDENCE',
            'EXECUTION_FAILED','LIMIT_REACHED','CANCELLED')),
          policy_json TEXT NOT NULL, sources_json TEXT NOT NULL, routing_json TEXT NOT NULL,
          current_version INTEGER NOT NULL DEFAULT 0 CHECK(current_version>=0),
          used_calls INTEGER NOT NULL DEFAULT 0 CHECK(used_calls>=0),
          used_evidence INTEGER NOT NULL DEFAULT 0 CHECK(used_evidence>=0),
          reason_code TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS signal_tasks (
          task_id TEXT PRIMARY KEY REFERENCES agent_tasks(task_id),
          study_id TEXT NOT NULL REFERENCES signal_studies(study_id),
          version INTEGER NOT NULL, phase TEXT NOT NULL, UNIQUE(study_id,version,phase));
        CREATE TABLE IF NOT EXISTS signal_versions (
          study_id TEXT NOT NULL REFERENCES signal_studies(study_id), version INTEGER NOT NULL,
          task_id TEXT NOT NULL UNIQUE REFERENCES signal_tasks(task_id),
          attempt_id TEXT NOT NULL UNIQUE REFERENCES agent_results(attempt_id),
          proposal_json TEXT NOT NULL, PRIMARY KEY(study_id,version));
        CREATE TABLE IF NOT EXISTS signal_reviews (
          study_id TEXT NOT NULL, version INTEGER NOT NULL, phase TEXT NOT NULL,
          task_id TEXT NOT NULL UNIQUE REFERENCES signal_tasks(task_id),
          attempt_id TEXT NOT NULL UNIQUE REFERENCES agent_results(attempt_id),
          review_json TEXT NOT NULL, PRIMARY KEY(study_id,version,phase),
          FOREIGN KEY(study_id,version) REFERENCES signal_versions(study_id,version));
        CREATE TABLE IF NOT EXISTS signal_events (
          id INTEGER PRIMARY KEY, study_id TEXT NOT NULL REFERENCES signal_studies(study_id),
          event_type TEXT NOT NULL, payload_json TEXT NOT NULL, at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS signal_evidence_requests (
          id INTEGER PRIMARY KEY, study_id TEXT NOT NULL REFERENCES signal_studies(study_id),
          task_id TEXT NOT NULL REFERENCES signal_tasks(task_id), attempt_id TEXT NOT NULL REFERENCES agent_results(attempt_id),
          version INTEGER NOT NULL, requested_ref TEXT NOT NULL, response_json TEXT NOT NULL,
          UNIQUE(task_id,requested_ref));
        CREATE TRIGGER IF NOT EXISTS signal_version_immutable_update BEFORE UPDATE ON signal_versions
          BEGIN SELECT RAISE(ABORT,'IMMUTABLE_SIGNAL_VERSION'); END;
        CREATE TRIGGER IF NOT EXISTS signal_version_immutable_delete BEFORE DELETE ON signal_versions
          BEGIN SELECT RAISE(ABORT,'IMMUTABLE_SIGNAL_VERSION'); END;
        CREATE TRIGGER IF NOT EXISTS signal_review_immutable_update BEFORE UPDATE ON signal_reviews
          BEGIN SELECT RAISE(ABORT,'IMMUTABLE_SIGNAL_REVIEW'); END;
        CREATE TRIGGER IF NOT EXISTS signal_review_immutable_delete BEFORE DELETE ON signal_reviews
          BEGIN SELECT RAISE(ABORT,'IMMUTABLE_SIGNAL_REVIEW'); END;
        COMMIT;""")
        if self.db.execute("SELECT version FROM loop_schema WHERE id=1").fetchone()[0] != 1:
            raise ValueError("RESEARCH_SCHEMA_UNSUPPORTED")

    def event(self, study_id, kind, **payload):
        self.db.execute(
            "INSERT INTO signal_events(study_id,event_type,payload_json) VALUES(?,?,?)",
            (study_id, kind, canonical(payload)),
        )

    def start(self, study_id, signal_id, sources, policy, routing):
        with self.ledger.transaction():
            self.db.execute(
                "INSERT INTO signal_studies(study_id,signal_id,status,policy_json,sources_json,routing_json) VALUES(?,?,'RUNNING',?,?,?)",
                (
                    study_id,
                    signal_id,
                    canonical(asdict(policy)),
                    canonical(sources),
                    canonical(routing),
                ),
            )
            self.event(study_id, "STUDY_CREATED", signal_id=signal_id)

    def finish(self, study_id, status, reason):
        with self.ledger.transaction():
            if status == "APPROVED":
                current = self.db.execute(
                    "SELECT current_version FROM signal_studies WHERE study_id=?", (study_id,)
                ).fetchone()[0]
                reviews = self.db.execute(
                    "SELECT version,phase,review_json FROM signal_reviews WHERE study_id=?",
                    (study_id,),
                ).fetchall()
                passed = {
                    (r["version"], r["phase"])
                    for r in reviews
                    if json.loads(r["review_json"])["verdict"] == "PASS"
                }
                old_failures = {
                    r["version"]
                    for r in reviews
                    if r["version"] < current and json.loads(r["review_json"])["verdict"] == "FAIL"
                }
                if (
                    (current, "independent") not in passed
                    or any((current, f"regression-{v}") not in passed for v in old_failures)
                    or any(
                        r["version"] == current
                        and json.loads(r["review_json"])["verdict"] != "PASS"
                        for r in reviews
                    )
                ):
                    raise ValueError("APPROVAL_REQUIRES_LOCKED_REVIEWS")
            row = self.db.execute(
                "UPDATE signal_studies SET status=?,reason_code=? WHERE study_id=? AND status='RUNNING'",
                (status, reason, study_id),
            )
            if row.rowcount != 1:
                raise ValueError("STUDY_TERMINAL")
            self.event(study_id, status, reason_code=reason)

    def allocate(self, study_id, task, phase):
        with self.ledger.transaction():
            study = self.db.execute(
                "SELECT * FROM signal_studies WHERE study_id=?", (study_id,)
            ).fetchone()
            policy = json.loads(study["policy_json"])
            expected_role = "generator" if phase == "generate" else "critic"
            expected_version = study["current_version"] + (phase == "generate")
            if (
                task.subject.signal_id != study["signal_id"]
                or task.role != expected_role
                or task.subject.signal_version != expected_version
            ):
                raise ValueError("RESEARCH_TASK_BINDING_MISMATCH")
            if study["status"] != "RUNNING" or study["used_calls"] >= policy["max_agent_calls"]:
                return False
            self.db.execute(
                "INSERT INTO signal_tasks VALUES(?,?,?,?)",
                (task.task_id, study_id, task.subject.signal_version, phase),
            )
            self.db.execute(
                "UPDATE signal_studies SET used_calls=used_calls+1 WHERE study_id=?", (study_id,)
            )
            self.event(
                study_id,
                "AGENT_AUTHORIZED",
                task_id=task.task_id,
                version=task.subject.signal_version,
                phase=phase,
                context_digest=task.context_digest,
            )
        return True

    def _confirmed(self, study_id, outcome, phase):
        row = self.db.execute(
            "SELECT t.*,a.current_attempt_id,a.status AS task_status,r.result_json "
            "FROM signal_tasks t JOIN agent_tasks a ON a.task_id=t.task_id "
            "JOIN agent_results r ON r.task_id=t.task_id AND r.attempt_id=a.current_attempt_id "
            "JOIN signal_studies s ON s.study_id=t.study_id "
            "WHERE t.task_id=? AND t.study_id=? AND s.status='RUNNING'",
            (outcome.task_id, study_id),
        ).fetchone()
        if (
            row is None
            or row["phase"] != phase
            or row["version"] != outcome.subject.signal_version
            or row["current_attempt_id"] != outcome.attempt_id
            or row["task_status"] != "COMPLETED"
            or row["result_json"] != canonical(asdict(outcome.result))
        ):
            raise ValueError("RESEARCH_RESULT_BINDING_MISMATCH")

    def save_proposal(self, study_id, outcome):
        with self.ledger.transaction():
            self._confirmed(study_id, outcome, "generate")
            version = outcome.subject.signal_version
            updated = self.db.execute(
                "UPDATE signal_studies SET current_version=? WHERE study_id=? AND current_version=? AND status='RUNNING'",
                (version, study_id, version - 1),
            )
            if updated.rowcount != 1:
                raise ValueError("SIGNAL_VERSION_CONFLICT")
            self.db.execute(
                "INSERT INTO signal_versions VALUES(?,?,?,?,?)",
                (
                    study_id,
                    version,
                    outcome.task_id,
                    outcome.attempt_id,
                    canonical(asdict(outcome.result)),
                ),
            )
            self.event(study_id, "PROPOSAL_VALIDATED", version=version, task_id=outcome.task_id)
            self.event(
                study_id,
                "SIGNAL_VERSION_CREATED",
                version=version,
                task_id=outcome.task_id,
                attempt_id=outcome.attempt_id,
            )

    def save_review(self, study_id, outcome, phase):
        with self.ledger.transaction():
            self._confirmed(study_id, outcome, phase)
            current = self.db.execute(
                "SELECT current_version FROM signal_studies WHERE study_id=?", (study_id,)
            ).fetchone()[0]
            if current != outcome.subject.signal_version:
                raise ValueError("REVIEW_VERSION_MISMATCH")
            if phase.startswith("regression-"):
                independent = self.db.execute(
                    "SELECT review_json FROM signal_reviews WHERE study_id=? AND version=? AND phase='independent'",
                    (study_id, current),
                ).fetchone()
                if independent is None or json.loads(independent[0])["verdict"] != "PASS":
                    raise ValueError("INDEPENDENT_REVIEW_NOT_LOCKED")
            self.db.execute(
                "INSERT INTO signal_reviews VALUES(?,?,?,?,?,?)",
                (
                    study_id,
                    current,
                    phase,
                    outcome.task_id,
                    outcome.attempt_id,
                    canonical(asdict(outcome.result)),
                ),
            )
            self.event(
                study_id,
                "REVIEW_LOCKED",
                version=current,
                phase=phase,
                verdict=outcome.result.verdict,
                task_id=outcome.task_id,
                attempt_id=outcome.attempt_id,
            )
