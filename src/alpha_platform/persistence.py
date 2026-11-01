"""SQLite unit-of-work boundaries; remote calls never run inside transactions."""

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from uuid import uuid4

from .domain import JobStatus

LOG = logging.getLogger(__name__)
TRANSITIONS = {
    "PENDING": {"RESERVED"},
    "RESERVED": {"SUBMITTING"},
    "SUBMITTING": {"SUBMITTED", "UNKNOWN", "FAILED"},
    "SUBMITTED": {"POLLING"},
    "POLLING": {"COMPLETED", "FAILED", "UNKNOWN"},
}


class Repository:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
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
        except Exception:
            self.db.rollback()
            LOG.error("transaction_failed", extra={"error_class": "PERSISTENCE"})
            raise

    def initialize(self):
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 1:
            raise ValueError("unsupported database schema")
        self.db.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS research_runs (
          id TEXT PRIMARY KEY, config_json TEXT NOT NULL,
          phase TEXT NOT NULL DEFAULT 'M1', status TEXT NOT NULL
          CHECK(status IN ('CREATED','RUNNING','COMPLETED','REJECTED','FAILED')),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          budget_limit INTEGER NOT NULL CHECK(budget_limit>=0),
          used_budget INTEGER NOT NULL DEFAULT 0 CHECK(used_budget>=0),
          reserved_budget INTEGER NOT NULL DEFAULT 0 CHECK(reserved_budget>=0),
          CHECK(used_budget+reserved_budget<=budget_limit));
        CREATE TABLE IF NOT EXISTS alphas (
          id TEXT PRIMARY KEY, expression TEXT NOT NULL, canonical_expression TEXT NOT NULL,
          settings_json TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE, signal_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sim_jobs (
          id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES research_runs(id),
          alpha_id TEXT NOT NULL UNIQUE REFERENCES alphas(id), status TEXT NOT NULL
          CHECK(status IN ('PENDING','RESERVED','SUBMITTING','SUBMITTED','POLLING',
                           'COMPLETED','UNKNOWN','FAILED')),
          remote_id TEXT, attempt_count INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS job_attempts (
          id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES sim_jobs(id),
          outcome TEXT NOT NULL, remote_ref TEXT,
          at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS sim_results (
          job_id TEXT PRIMARY KEY REFERENCES sim_jobs(id), metrics_json TEXT NOT NULL,
          checks_json TEXT NOT NULL, raw_json TEXT NOT NULL, gate TEXT NOT NULL,
          completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS research_events (
          id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES research_runs(id),
          job_id TEXT REFERENCES sim_jobs(id), event_type TEXT NOT NULL, payload_json TEXT NOT NULL,
          at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        PRAGMA user_version=1;
        COMMIT;
        """)

    def event(self, run_id, kind, payload=None, job_id=None):
        self.db.execute(
            "INSERT INTO research_events(run_id,job_id,event_type,payload_json) VALUES(?,?,?,?)",
            (run_id, job_id, kind, json.dumps(payload or {})),
        )

    def create_run(self, config):
        run_id = str(uuid4())
        with self.transaction():
            self.db.execute(
                "INSERT INTO research_runs(id,config_json,status,budget_limit) VALUES(?,?,?,?)",
                (run_id, config.json(), "CREATED", config.budget_limit),
            )
            self.event(run_id, "CREATED")
        return run_id

    def finish(self, run_id, status, reason):
        with self.transaction():
            self.set_run_status(run_id, status, reason)

    def set_run_status(self, run_id, status, reason):
        """Apply a transition within the caller's transaction."""
        if not self.db.in_transaction:
            raise ValueError("run transition requires a transaction")
        row = self.db.execute("SELECT status FROM research_runs WHERE id=?", (run_id,)).fetchone()
        allowed = {"CREATED": {"REJECTED", "FAILED"}, "RUNNING": {"COMPLETED", "FAILED"}}
        if row is None or status not in allowed.get(row["status"], set()):
            raise ValueError("illegal research run transition")
        if status == "COMPLETED":
            jobs = self.db.execute(
                "SELECT status FROM sim_jobs WHERE run_id=?", (run_id,)
            ).fetchall()
            if len(jobs) != 1 or jobs[0]["status"] != "COMPLETED":
                raise ValueError("run completion requires its completed job")
        self.db.execute(
            "UPDATE research_runs SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, run_id),
        )
        self.event(run_id, status, {"reason": reason})

    def transition(self, job_id, target, remote_id=None):
        if not self.db.in_transaction:
            raise ValueError("job transition requires a transaction")
        row = self.db.execute("SELECT * FROM sim_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("job not found")
        run = self.db.execute(
            "SELECT status FROM research_runs WHERE id=?", (row["run_id"],)
        ).fetchone()
        if run["status"] != "RUNNING":
            raise ValueError("terminal run cannot advance its job; manual review required")
        if target not in TRANSITIONS.get(row["status"], set()):
            raise ValueError(f"illegal job transition: {row['status']} -> {target}")
        JobStatus(target)
        self.db.execute(
            "UPDATE sim_jobs SET status=?,remote_id=COALESCE(?,remote_id) WHERE id=?",
            (target, remote_id, job_id),
        )
        self.event(row["run_id"], target, job_id=job_id)

    def save_result(self, job_id, result, gate):
        with self.transaction():
            row = self.db.execute("SELECT status FROM sim_jobs WHERE id=?", (job_id,)).fetchone()
            if row["status"] == "COMPLETED":
                return
            self.db.execute(
                "INSERT INTO sim_results(job_id,metrics_json,checks_json,raw_json,gate) "
                "VALUES(?,?,?,?,?)",
                (
                    job_id,
                    json.dumps(result.metrics),
                    json.dumps(result.checks),
                    json.dumps(result.raw),
                    gate,
                ),
            )
            self.transition(job_id, "COMPLETED")

    def report(self, run_id):
        row = self.db.execute("SELECT * FROM research_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("research run not found")
        report = dict(row)
        report["jobs"] = [
            dict(r)
            for r in self.db.execute(
                "SELECT j.*,a.expression,r.metrics_json,r.checks_json,r.raw_json,r.gate "
                "FROM sim_jobs j JOIN alphas a ON a.id=j.alpha_id "
                "LEFT JOIN sim_results r ON r.job_id=j.id WHERE j.run_id=?",
                (run_id,),
            )
        ]
        report["events"] = [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM research_events WHERE run_id=? ORDER BY id", (run_id,)
            )
        ]
        report["attempts"] = [
            dict(r)
            for r in self.db.execute(
                "SELECT a.* FROM job_attempts a JOIN sim_jobs j ON j.id=a.job_id "
                "WHERE j.run_id=? ORDER BY a.at,a.id",
                (run_id,),
            )
        ]
        kinds = [e["event_type"] for e in report["events"]]
        generated = kinds.count("GENERATED")
        completed = sum(j["status"] == "COMPLETED" for j in report["jobs"])
        submissions = len(report["attempts"])
        report["summary"] = {
            "generated_count": generated,
            "technical_validity_rate": (
                kinds.count("VALIDATED") / generated if generated else None
            ),
            "duplicate_count": kinds.count("DUPLICATE"),
            "duplicate_rate": (kinds.count("DUPLICATE") / generated if generated else None),
            "submit_attempts": submissions,
            "simulation_success_rate": completed / submissions if submissions else None,
            "adapter_calls": submissions + kinds.count("POLL_INTENT") + kinds.count("FETCH_INTENT"),
            "effective_candidates": sum(j["gate"] == "PASS" for j in report["jobs"]),
            "token_usage": None,
            "llm_cost": None,
            "data_version": "wq-live-v1" if report["phase"] == "M1.5" else "mock-v1",
            "elapsed_seconds": (
                datetime.fromisoformat(report["updated_at"])
                - datetime.fromisoformat(report["created_at"])
            ).total_seconds(),
        }
        return report
