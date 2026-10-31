import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from alpha_platform.agents import MockGenerator
from alpha_platform.cli import execute, main
from alpha_platform.domain import ResearchConfig
from alpha_platform.observability.diagnostics import encode, references, write_artifact
from alpha_platform.observability.html_report import render
from alpha_platform.observability.queries import ObservatoryReader, RunNotFoundError
from alpha_platform.observability.traces import MAX_EVENTS, MAX_PAYLOAD_BYTES, TraceSink
from alpha_platform.persistence import Repository


@pytest.fixture
def env(tmp_path):
    path = tmp_path / "observatory.sqlite3"
    repo = Repository(path)
    repo.initialize()
    yield repo, path
    repo.close()


def snapshot(path, run_id, **kwargs):
    reader = ObservatoryReader(path)
    try:
        return reader.snapshot(run_id, **kwargs)
    finally:
        reader.close()


@pytest.mark.parametrize(
    "config,node,status,code",
    [
        (ResearchConfig(), "result_persist", "COMPLETED", None),
        (
            ResearchConfig(expression="wrong(mock_value)"),
            "validator",
            "REJECTED",
            "VALIDATION_REJECTED",
        ),
        (ResearchConfig(budget_limit=0), "dedup_budget", "REJECTED", "BUDGET_EXHAUSTED"),
        (ResearchConfig(mock_scenario="timeout"), "submit", "UNKNOWN", "MISSING_RECEIPT"),
        (ResearchConfig(mock_scenario="failed"), "poll", "FAILED", "REMOTE_FAILED"),
        (
            ResearchConfig(mock_scenario="rate_limit", max_submit_attempts=1),
            "submit",
            "FAILED",
            "RATE_LIMIT_EXHAUSTED",
        ),
        (
            ResearchConfig(mock_scenario="delayed", max_polls=1),
            "poll",
            "UNKNOWN",
            "REMOTE_EXECUTION_UNKNOWN",
        ),
    ],
)
def test_historical_read_model_real_paths(env, config, node, status, code):
    repo, path = env
    report = asyncio.run(execute(repo, config))
    view = snapshot(path, report["id"])
    assert view["run"]["status"] == report["status"]
    assert view["run"]["budget"] == {
        "total": config.budget_limit,
        "used": report["used_budget"],
        "reserved": report["reserved_budget"],
    }
    assert next(n for n in view["nodes"] if n["id"] == node)["status"] == status
    if code:
        assert code in {a["code"] for a in view["anomalies"]}
    if report["status"] == "REJECTED":
        assert next(n for n in view["nodes"] if n["id"] == "submit")["status"] == "NOT_VISITED"
    assert "NO_OPTIONAL_TRACE" in " ".join(view["evidence_gaps"])
    assert all(e["source_kind"] == "AUDIT" and not e["derived"] for e in view["events"])
    assert len({e["source_ref"] for e in view["events"]}) == len(view["events"])
    assert all(e["occurred_at"].endswith("Z") for e in view["events"])
    encoded = json.loads(encode(view))
    assert (
        references({k: v for k, v in encoded.items() if k != "evidence"})
        <= encoded["evidence"].keys()
    )
    assert not any(n["id"] in {"critic", "func1", "func2"} for n in view["nodes"])


def test_duplicate_keeps_link_to_original_without_invented_job(env):
    repo, path = env
    first = asyncio.run(execute(repo, ResearchConfig()))
    duplicate = asyncio.run(execute(repo, ResearchConfig()))
    view = snapshot(path, duplicate["id"])
    assert view["jobs"] == []
    assert any(a["code"] == "DUPLICATE" for a in view["anomalies"])
    assert first["jobs"][0]["alpha_id"] in json.dumps(view)
    assert next(n for n in view["nodes"] if n["id"] == "queue")["status"] == "NOT_VISITED"


def test_failed_run_polling_job_persistence_failure_is_only_observed(env):
    repo, path = env

    def fail(*args):
        raise sqlite3.OperationalError("secret exception content is never exported")

    repo.save_result = fail
    report = asyncio.run(execute(repo, ResearchConfig(), TraceSink(path, "full")))
    before = list(repo.db.iterdump())
    view = snapshot(path, report["id"])
    assert view["run"]["status"] == "FAILED"
    assert view["jobs"][0]["status"] == "POLLING"
    assert view["run"]["budget"]["used"] == 1
    assert {"PERSISTENCE_FAILURE", "RUN_JOB_STATE_MISMATCH_MANUAL_REVIEW"} <= {
        a["code"] for a in view["anomalies"]
    }
    assert next(n for n in view["nodes"] if n["id"] == "result_persist")["status"] == "FAILED"
    assert "secret exception content" not in encode(view)
    render(json.loads(encode(view)))
    assert list(repo.db.iterdump()) == before


@pytest.mark.parametrize("mode,trace_expected", [("off", False), ("light", True), ("full", True)])
def test_modes_preserve_core_behavior_and_audit(env, mode, trace_expected):
    repo, path = env
    report = asyncio.run(execute(repo, ResearchConfig(), TraceSink(path, mode)))
    assert report["status"] == "COMPLETED"
    assert (report["used_budget"], report["reserved_budget"]) == (1, 0)
    assert len(report["events"]) == 13
    assert repo.db.execute("PRAGMA user_version").fetchone()[0] == 1
    view = snapshot(path, report["id"])
    assert any(e["source_kind"] == "TRACE" for e in view["events"]) == trace_expected
    if mode == "off":
        assert not repo.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='observability_traces'"
        ).fetchone()
    else:
        payloads = [e["payload"] for e in view["events"] if e["source_kind"] == "TRACE"]
        assert any("duration_ms" in p for p in payloads)
        assert any("expression" in p for p in payloads) == (mode == "full")


def test_broken_trace_callback_and_flush_do_not_change_business(env, caplog):
    repo, path = env

    class BrokenSink:
        def emit(self, *args):
            raise RuntimeError("Authorization: must-not-leak")

        def flush(self):
            raise RuntimeError("Cookie: must-not-leak")

    report = asyncio.run(execute(repo, ResearchConfig(), BrokenSink()))
    assert report["status"] == "COMPLETED" and report["used_budget"] == 1
    assert len(report["events"]) == 13
    assert caplog.text.count("optional_trace_emit_failed") == 1
    assert "must-not-leak" not in caplog.text
    assert snapshot(path, report["id"])["jobs"][0]["status"] == "COMPLETED"


def test_trace_sqlite_write_failure_rolls_back_only_optional_data(env, caplog):
    repo, path = env
    run_id = repo.create_run(ResearchConfig())
    sink = TraceSink(path, "light")
    sink.emit(run_id, "generator", "NODE_STARTED", "RUNNING")
    sink.flush()
    repo.db.execute("""CREATE TRIGGER reject_trace BEFORE INSERT ON observability_traces
                       BEGIN SELECT RAISE(ABORT,'trace unavailable'); END""")
    report = asyncio.run(execute(repo, ResearchConfig(), sink))
    assert report["status"] == "COMPLETED" and report["used_budget"] == 1
    assert len(report["events"]) == 13
    assert (
        repo.db.execute(
            "SELECT count(*) FROM observability_traces WHERE run_id=?", (report["id"],)
        ).fetchone()[0]
        == 0
    )
    assert caplog.text.count("optional_trace_write_failed") == 1


def test_required_audit_failure_is_not_swallowed_as_trace_failure(env):
    repo, path = env
    repo.db.execute("""CREATE TRIGGER reject_audit BEFORE INSERT ON research_events
                       WHEN NEW.event_type='CREATED'
                       BEGIN SELECT RAISE(ABORT,'audit unavailable'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(execute(repo, ResearchConfig(), TraceSink(path, "light")))
    assert repo.db.execute("SELECT count(*) FROM research_runs").fetchone()[0] == 0


def test_generator_failure_is_visible_without_exception_secrets(env, monkeypatch):
    repo, path = env

    async def broken(*args):
        raise ValueError("api_key=never-export-this")

    monkeypatch.setattr(MockGenerator, "generate", broken)
    with pytest.raises(ValueError):
        asyncio.run(execute(repo, ResearchConfig(), TraceSink(path, "full")))
    run_id = repo.db.execute("SELECT id FROM research_runs").fetchone()[0]
    view = snapshot(path, run_id)
    assert next(n for n in view["nodes"] if n["id"] == "generator")["status"] == "FAILED"
    assert next(n for n in view["nodes"] if n["id"] == "validator")["status"] == "NOT_VISITED"
    assert "never-export-this" not in encode(view)


def test_security_truncation_and_html_escaping(env, monkeypatch):
    repo, path = env
    monkeypatch.setenv("WQ_TOKEN", "ENV-VALUE-12345")
    run_id = repo.create_run(ResearchConfig())
    sink = TraceSink(path, "full")
    sink.emit(
        run_id,
        "validator",
        "NODE_FAILED",
        "FAILED",
        {
            "expression": '<script>alert("injection")</script>' + "X" * 10000,
            "summary": "ENV-VALUE-12345",
            "Authorization": "Bearer NEVER-EXPOSE",
            "Cookie": "NEVER-COOKIE",
            "error_class": "password=NEVER-PASSWORD",
        },
    )
    sink.flush()
    with repo.transaction():
        repo.event(
            run_id,
            "GENERATED",
            {
                "expression": "<img src=x onerror=alert(1)>",
                "signal": {"field_ids": ["mock_value"]},
                "Authorization": "NEVER-EXPOSE",
            },
        )
        repo.event(run_id, "FAILED", {"reason": "Authorization: Bearer NEVER-EXPOSE"})
    view = json.loads(encode(snapshot(path, run_id)))
    encoded = json.dumps(view)
    document = render(view)
    for secret in ("ENV-VALUE-12345", "NEVER-EXPOSE", "NEVER-COOKIE", "NEVER-PASSWORD"):
        assert secret not in encoded and secret not in document
    assert "<img src=x" not in document and "<script>alert" not in document
    assert "&lt;img" in document and "&lt;script&gt;" in document
    assert any(e["payload"].get("truncated") for e in view["events"] if e["source_kind"] == "TRACE")
    assert (
        max(
            len(row[0].encode())
            for row in repo.db.execute("SELECT payload_json FROM observability_traces")
        )
        <= MAX_PAYLOAD_BYTES
    )
    assert "https://" not in document and 'src="http' not in document


def test_trace_buffer_and_retention_are_bounded_and_never_delete_audit(env):
    repo, path = env
    run_id = repo.create_run(ResearchConfig())
    sink = TraceSink(path, "full")
    for _ in range(MAX_EVENTS + 20):
        sink.emit(run_id, "poll", "NODE_STARTED", "RUNNING")
    assert len(sink.pending) == MAX_EVENTS and sink.dropped == 20
    sink.flush()
    repo.db.execute("UPDATE observability_traces SET occurred_at='2000-01-01T00:00:00.000Z'")
    repo.db.commit()
    sink.emit(run_id, "poll", "NODE_COMPLETED", "COMPLETED")
    sink.flush()
    assert repo.db.execute("SELECT count(*) FROM observability_traces").fetchone()[0] == 1
    assert repo.db.execute("SELECT count(*) FROM research_events").fetchone()[0] == 1


def test_stable_same_timestamp_order_and_resolvable_bounded_export(env):
    repo, path = env
    report = asyncio.run(execute(repo, ResearchConfig()))
    with repo.transaction():
        repo.db.execute("UPDATE research_events SET at='2026-10-30 09:00:00'")
        for _ in range(300):
            repo.event(report["id"], "POLL_INTENT", job_id=report["jobs"][0]["id"])
    first = snapshot(path, report["id"], max_events=2000)
    second = snapshot(path, report["id"], max_events=2000)
    assert first["events"] == second["events"]
    encoded = encode(first, max_bytes=65536)
    assert len(encoded.encode()) <= 65536
    bounded = json.loads(encoded)
    assert bounded["truncated"] is True
    assert (
        references({k: v for k, v in bounded.items() if k != "evidence"})
        <= bounded["evidence"].keys()
    )


def test_reader_snapshot_does_not_see_uncommitted_events(env):
    repo, path = env
    report = asyncio.run(execute(repo, ResearchConfig()))
    with repo.transaction():
        repo.event(report["id"], "UNCOMMITTED")
        view = snapshot(path, report["id"])
        assert all(e["event_type"] != "UNCOMMITTED" for e in view["events"])


def test_readonly_and_missing_run_do_not_modify_or_create_database(env, tmp_path, capsys):
    repo, path = env
    report = asyncio.run(execute(repo, ResearchConfig()))
    before = list(repo.db.iterdump())
    reader = ObservatoryReader(path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            reader.db.execute("UPDATE research_runs SET status='FAILED'")
        with pytest.raises(RunNotFoundError):
            reader.snapshot("not-there")
    finally:
        reader.close()
    assert main(["--db", str(path), "observe", "show", "--run-id", "not-there"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "RUN_NOT_FOUND"
    assert list(repo.db.iterdump()) == before
    missing = tmp_path / "not-created.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        ObservatoryReader(missing)
    assert not missing.exists()
    for output in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        with pytest.raises(ValueError, match="overwrite"):
            write_artifact(output, "bad", path)
    assert report["status"] == "COMPLETED"


def test_observe_cli_cross_process_json_html_and_filters(env, tmp_path):
    repo, path = env
    report = asyncio.run(execute(repo, ResearchConfig(), TraceSink(path, "full")))
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    digest_before = hashlib.sha256(path.read_bytes()).hexdigest()

    def cli(*args):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "alpha_platform.cli",
                "--db",
                str(path),
                "observe",
                *args,
                "--run-id",
                report["id"],
            ],
            env=environment,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    assert cli("show")["run"]["status"] == "COMPLETED"
    assert cli("graph")["nodes"]
    assert all(e["node"] == "submit" for e in cli("timeline", "--node", "submit")["events"])
    assert cli("inspect", "--source-ref", f"run:{report['id']}")["source_kind"] == "BUSINESS"
    destination = tmp_path / "diagnostic.json"
    cli("export", "--output", str(destination))
    bundle = json.loads(destination.read_text(encoding="utf-8"))
    html = tmp_path / "run.html"
    cli("report", "--output", str(html))
    assert bundle["schema_version"] == 1
    assert "Artifact inspector" in html.read_text(encoding="utf-8")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest_before


def test_optional_trace_and_stale_audit_do_not_override_stored_result(env):
    repo, path = env
    report = asyncio.run(execute(repo, ResearchConfig()))
    sink = TraceSink(path, "full")
    sink.emit(
        report["id"], "result_persist", "NODE_FAILED", "FAILED", {"reason_code": "NODE_EXCEPTION"}
    )
    sink.flush()
    with repo.transaction():
        repo.event(report["id"], "FAILED", {"reason": "INTERNAL: result persistence failed"})
    view = snapshot(path, report["id"])
    assert view["run"]["status"] == "COMPLETED"
    assert next(n for n in view["nodes"] if n["id"] == "result_persist")["status"] == "COMPLETED"
    assert any(a["code"] == "PERSISTENCE_FAILURE" for a in view["anomalies"])


def test_missing_core_audit_and_result_are_explicit_evidence_gaps(env):
    repo, path = env
    report = asyncio.run(execute(repo, ResearchConfig()))
    with repo.transaction():
        repo.db.execute("DELETE FROM research_events WHERE run_id=?", (report["id"],))
        repo.db.execute("DELETE FROM sim_results")
    view = snapshot(path, report["id"])
    assert "INCOMPLETE_EVIDENCE" in " ".join(view["evidence_gaps"])
    assert not view["events"]
    assert next(n for n in view["nodes"] if n["id"] == "validator")["status"] == "INFERRED"
    assert all(n["derived"] for n in view["nodes"])


def test_research_run_trace_cli_records_optional_data(env, capsys):
    _, path = env
    assert main(["--db", str(path), "research", "run", "--trace", "light"]) == 0
    report = json.loads(capsys.readouterr().out)
    view = snapshot(path, report["id"])
    assert any(e["source_kind"] == "TRACE" for e in view["events"])
    assert report["status"] == "COMPLETED"
