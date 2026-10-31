import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from alpha_platform.agents import MockGenerator
from alpha_platform.domain import AlphaCandidate, ResearchConfig, Signal
from alpha_platform.persistence import Repository
from alpha_platform.simulation import MockSimulationAdapter
from alpha_platform.validation import ValidationError, validate
from alpha_platform.workflow import Controller


@pytest.fixture
def repo(tmp_path):
    repository = Repository(tmp_path / "research.sqlite3")
    repository.initialize()
    yield repository
    repository.close()


def run(repo, config=None, adapter=None, candidate=None):
    config = config or ResearchConfig()
    adapter = adapter or MockSimulationAdapter(config.mock_scenario)
    candidate = candidate or asyncio.run(MockGenerator().generate(config))
    run_id = repo.create_run(config)
    return asyncio.run(Controller(repo, adapter).run_one_candidate(run_id, candidate, config))


def test_success_and_persistent_report(repo):
    report = run(repo)
    assert report["status"] == "COMPLETED"
    assert report["used_budget"] == 1
    assert report["reserved_budget"] == 0
    assert report["jobs"][0]["gate"] == "PASS"
    events = [e["event_type"] for e in report["events"]]
    assert events == [
        "CREATED",
        "GENERATED",
        "VALIDATED",
        "PENDING",
        "RESERVED",
        "SUBMITTING",
        "SUBMIT_INTENT",
        "SUBMITTED",
        "POLLING",
        "POLL_INTENT",
        "FETCH_INTENT",
        "COMPLETED",
        "COMPLETED",
    ]
    assert report["summary"]["adapter_calls"] == 3
    assert json.loads(report["jobs"][0]["raw_json"])["source"] == "mock"


def test_validation_and_budget_rejected_without_submission(repo):
    adapter = MockSimulationAdapter()
    report = run(
        repo,
        candidate=AlphaCandidate("unknown(mock_value)", Signal("test", ("mock_value",))),
        adapter=adapter,
    )
    assert report["status"] == "REJECTED"
    assert "VALIDATION" in report["events"][-1]["payload_json"]
    report = run(repo, replace(ResearchConfig(), budget_limit=0), adapter)
    assert report["events"][-1]["event_type"] == "BUDGET_EXHAUSTED"
    assert not report["jobs"]
    assert adapter.submissions == 0


def test_dedup_across_runs_and_settings(repo):
    run(repo)
    report = run(repo, replace(ResearchConfig(), expression=" mock_rank( mock_value ) "))
    assert report["events"][-1]["event_type"] == "DUPLICATE"
    assert not report["jobs"]
    assert run(repo, replace(ResearchConfig(), delay=2))["status"] == "COMPLETED"


@pytest.mark.parametrize("scenario", ["unknown", "timeout"])
def test_uncertain_submit_keeps_reservation_and_never_resubmits(repo, scenario):
    adapter = MockSimulationAdapter(scenario)
    report = run(repo, replace(ResearchConfig(), mock_scenario=scenario), adapter)
    assert report["jobs"][0]["status"] == "UNKNOWN"
    assert report["reserved_budget"] == 1
    assert report["used_budget"] == 0
    assert adapter.submissions == 1


@pytest.mark.parametrize(
    "scenario,status,attempts",
    [
        ("rate_limit", "COMPLETED", 2),
        ("delayed", "COMPLETED", 1),
        ("failed", "FAILED", 1),
    ],
)
def test_mock_scenarios(repo, scenario, status, attempts):
    report = run(repo, replace(ResearchConfig(), mock_scenario=scenario))
    assert report["status"] == status
    assert report["jobs"][0]["attempt_count"] == attempts
    assert report["used_budget"] == 1


def test_rate_limit_exhaustion_releases_budget(repo):
    report = run(repo, replace(ResearchConfig(), mock_scenario="rate_limit", max_submit_attempts=1))
    assert report["status"] == "FAILED"
    assert report["used_budget"] == report["reserved_budget"] == 0


def test_polling_limit_does_not_resubmit(repo):
    report = run(repo, replace(ResearchConfig(), mock_scenario="delayed", max_polls=1))
    assert report["jobs"][0]["status"] == "UNKNOWN"
    assert report["jobs"][0]["attempt_count"] == 1


def test_result_transaction_failure_retry_and_idempotence(repo):
    repo.db.execute("""CREATE TRIGGER fail_completion BEFORE UPDATE OF status ON sim_jobs
                       WHEN NEW.status='COMPLETED'
                       BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
    original = repo.save_result
    calls = []

    def flaky(job_id, result, gate):
        calls.append(job_id)
        if len(calls) == 1:
            with pytest.raises(sqlite3.IntegrityError):
                original(job_id, result, gate)
            assert repo.db.execute("SELECT count(*) FROM sim_results").fetchone()[0] == 0
            repo.db.execute("DROP TRIGGER fail_completion")
            raise sqlite3.OperationalError("transient write failure")
        original(job_id, result, gate)
        original(job_id, result, gate)

    repo.save_result = flaky
    adapter = MockSimulationAdapter()
    report = run(repo, adapter=adapter)
    assert report["status"] == "COMPLETED"
    assert len(calls) == 2
    assert adapter.submissions == 1
    assert repo.db.execute("SELECT count(*) FROM sim_results").fetchone()[0] == 1
    assert report["used_budget"] == 1


def test_constraints_and_illegal_transition(repo):
    report = run(repo)
    with pytest.raises(ValueError), repo.transaction():
        repo.transition(report["jobs"][0]["id"], "SUBMITTING")
    with pytest.raises(sqlite3.IntegrityError), repo.transaction():
        repo.db.execute("UPDATE research_runs SET used_budget=2 WHERE id=?", (report["id"],))
    assert repo.report(report["id"])["used_budget"] == 1
    with pytest.raises(sqlite3.IntegrityError), repo.transaction():
        repo.db.execute("UPDATE sim_jobs SET status='INVENTED'")


@pytest.mark.parametrize(
    "expression",
    [
        '__import__("os")',
        "mock_value.x",
        "mock_value + 1",
        "mock_rank()",
        "mock_rank(mock_value, mock_value)",
        "mock_rank(x=mock_value)",
        "[mock_value]",
    ],
)
def test_validator_rejects_unsupported_syntax(expression):
    with pytest.raises(ValidationError):
        validate(AlphaCandidate(expression, Signal("test", ("mock_value",))), ResearchConfig())


def test_gate_is_configurable(repo):
    report = run(repo, replace(ResearchConfig(), min_sharpe=2))
    assert report["status"] == "COMPLETED"
    assert report["jobs"][0]["gate"] == "FAIL"


def test_cli_end_to_end_separate_processes(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    db = str(tmp_path / "cli.sqlite3")

    def cli(*args):
        result = subprocess.run(
            [sys.executable, "-m", "alpha_platform.cli", "--db", db, *args],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    assert cli("init-db")["schema_version"] == 1
    report = cli("research", "run", "--mode", "mock", "--config", "configs/sample-gbr.json")
    persisted = cli("research", "show", report["id"])
    assert persisted == report
    assert persisted["jobs"][0]["status"] == "COMPLETED"
    assert cli("research", "run")["events"][-1]["event_type"] == "DUPLICATE"


def test_queue_failure_rolls_back_alpha_budget_and_job(repo):
    repo.db.execute("""CREATE TRIGGER fail_queue BEFORE INSERT ON sim_jobs
                       BEGIN SELECT RAISE(ABORT, 'injected queue failure'); END""")
    config = ResearchConfig()
    run_id = repo.create_run(config)
    candidate = asyncio.run(MockGenerator().generate(config))
    adapter = MockSimulationAdapter()
    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(Controller(repo, adapter).run_one_candidate(run_id, candidate, config))
    report = repo.report(run_id)
    assert report["status"] == "CREATED"
    assert report["reserved_budget"] == report["used_budget"] == 0
    assert not report["jobs"]
    assert repo.db.execute("SELECT count(*) FROM alphas").fetchone()[0] == 0
    assert adapter.submissions == 0


def test_persistent_result_failure_retains_remote_receipt(repo):
    def fail(*args):
        raise sqlite3.OperationalError("database unavailable")

    repo.save_result = fail
    adapter = MockSimulationAdapter()
    report = run(repo, adapter=adapter)
    assert report["status"] == "FAILED"
    assert report["jobs"][0]["status"] == "POLLING"
    assert report["jobs"][0]["remote_id"]
    assert report["used_budget"] == 1
    assert adapter.submissions == 1
    assert "persistence failed" in report["events"][-1]["payload_json"]


def test_schema_version_foreign_keys_and_init_idempotence(repo):
    report = run(repo)
    repo.initialize()
    assert repo.report(report["id"]) == report
    with pytest.raises(sqlite3.IntegrityError), repo.transaction():
        repo.db.execute("INSERT INTO job_attempts VALUES('a','missing','INTENT',NULL,'now')")
    repo.db.execute("PRAGMA user_version=2")
    with pytest.raises(ValueError, match="schema"):
        repo.initialize()


@pytest.mark.parametrize(
    "changes",
    [
        {"budget_limit": -1},
        {"budget_limit": True},
        {"max_polls": 0},
        {"min_sharpe": float("nan")},
        {"mock_scenario": "live"},
        {"operators": {"mock_rank": "one"}},
    ],
)
def test_invalid_config(changes):
    with pytest.raises(ValueError):
        replace(ResearchConfig(), **changes)
