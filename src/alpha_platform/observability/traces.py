"""Optional bounded diagnostics. Buffer during business work, flush after it commits."""

import json
import logging
import sqlite3
from collections import deque
from datetime import datetime, timezone

from .graph import NODES
from .safety import sanitize

LOG = logging.getLogger(__name__)
MAX_EVENTS = 256
MAX_PAYLOAD_BYTES = 4096
MAX_STORED_EVENTS = 10000
RETENTION_DAYS = 7
ALLOWED = {
    "duration_ms",
    "reason_code",
    "error_class",
    "expression",
    "status",
    "attempt_number",
    "remote_ref_present",
    "gate",
    "truncated",
    "summary",
}


class TraceSink:
    def __init__(self, path, mode="light"):
        if mode not in {"off", "light", "full"}:
            raise ValueError("trace must be off, light or full")
        self.path, self.mode = path, mode
        self.pending = deque(maxlen=MAX_EVENTS)
        self.dropped = 0
        self.warned = False

    def emit(self, run_id, node, event_type, outcome, payload=None, job_id=None, candidate_id=None):
        if self.mode == "off":
            return
        if node not in NODES or event_type not in {"NODE_STARTED", "NODE_COMPLETED", "NODE_FAILED"}:
            return
        payload = {k: v for k, v in (payload or {}).items() if k in ALLOWED}
        if self.mode == "light":
            payload.pop("expression", None)
        clean = sanitize(payload, limit=2048 if self.mode == "full" else 160)
        clean["truncated"] = any(
            isinstance(v, str) and len(v) > (2048 if self.mode == "full" else 160)
            for v in payload.values()
        )
        encoded = json.dumps(clean, ensure_ascii=True, allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            encoded = json.dumps({"truncated": True, "summary": "payload limit exceeded"})
        if len(self.pending) == MAX_EVENTS:
            self.dropped += 1
        self.pending.append(
            (
                run_id,
                job_id,
                candidate_id,
                node,
                event_type,
                datetime.now(timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
                self.mode.upper(),
                outcome,
                encoded,
            )
        )

    def flush(self):
        if not self.pending:
            return
        db = None
        try:
            if self.dropped:
                last = list(self.pending[-1])
                payload = json.loads(last[-1])
                payload.update(
                    truncated=True, summary=f"{self.dropped} oldest trace events dropped"
                )
                last[-1] = json.dumps(payload)
                self.pending[-1] = tuple(last)
            # A separate connection is intentional; never commit/rollback business work here.
            db = sqlite3.connect(self.path, timeout=0.1)
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                db.execute("""CREATE TABLE IF NOT EXISTS observability_traces (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, schema_version INTEGER NOT NULL DEFAULT 1,
                  run_id TEXT NOT NULL REFERENCES research_runs(id), job_id TEXT,
                  candidate_id TEXT, node TEXT NOT NULL, event_type TEXT NOT NULL,
                  occurred_at TEXT NOT NULL, level TEXT NOT NULL CHECK(level IN ('LIGHT','FULL')),
                  outcome TEXT, payload_json TEXT NOT NULL)""")
                db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_observability_traces_run_id_id "
                    "ON observability_traces(run_id,id)"
                )
                db.executemany(
                    "INSERT INTO observability_traces(run_id,job_id,candidate_id,node,"
                    "event_type,occurred_at,level,outcome,payload_json) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    list(self.pending),
                )
                db.execute(
                    "DELETE FROM observability_traces WHERE occurred_at < "
                    "strftime('%Y-%m-%dT%H:%M:%fZ','now',?)",
                    (f"-{RETENTION_DAYS} days",),
                )
                db.execute(
                    "DELETE FROM observability_traces WHERE id NOT IN "
                    "(SELECT id FROM observability_traces ORDER BY id DESC LIMIT ?)",
                    (MAX_STORED_EVENTS,),
                )
        except Exception:
            if not self.warned:
                LOG.warning("optional_trace_write_failed")
                self.warned = True
        finally:
            self.pending.clear()
            self.dropped = 0
            if db is not None:
                db.close()
