"""Regression tests for confirmed M1 correctness defects."""

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from threading import Barrier, Lock

import pytest

from alpha_platform.agents import MockGenerator
from alpha_platform.domain import (
    AlphaCandidate,
    ResearchConfig,
    Signal,
    SimulationRequest,
    SimulationResult,
    SubmissionReceipt,
)
from alpha_platform.persistence import Repository
from alpha_platform.simulation import MockSimulationAdapter, RemoteUnknown
from alpha_platform.validation import ValidationError, fingerprints, validate
from alpha_platform.workflow import Controller


@pytest.fixture
def repo(tmp_path):
    instance = Repository(tmp_path / "review.sqlite3")
    instance.initialize()
    yield instance
    instance.close()


def execute(repo, config=None, adapter=None):
    config = config or ResearchConfig()
    run_id = repo.create_run(config)
    candidate = asyncio.run(MockGenerator().generate(config))
    return asyncio.run(
        Controller(repo, adapter or MockSimulationAdapter(config.mock_scenario)).run_one_candidate(
            run_id, candidate, config
        )
    )


@pytest.mark.parametrize("same_candidate,same_run", [(True, True), (False, True), (True, False)])
def test_concurrent_entry_cannot_overwrite_state_or_spend_twice(tmp_path, same_candidate, same_run):
    path = tmp_path / "race.sqlite3"
    repo = Repository(path)
    repo.initialize()
    config = ResearchConfig(budget_limit=2)
    run_id = repo.create_run(config)
    run_ids = [run_id, run_id if same_run else repo.create_run(config)]
    repo.close()
    barrier, lock = Barrier(2), Lock()
    submissions = []

    class CountingAdapter(MockSimulationAdapter):
        async def submit(self, request):
            with lock:
                submissions.append(request)
            return await super().submit(request)

    def worker(index):
        local = Repository(path)
        transaction = local.transaction
        first = True

        @contextmanager
        def synchronized_transaction():
            nonlocal first
            if first:
                first = False
                barrier.wait(timeout=5)
            with transaction():
                yield

        local.transaction = synchronized_transaction
        expression = "mock_rank(mock_value)"
        if index and not same_candidate:
            expression = "mock_rank(mock_rank(mock_value))"
        candidate = AlphaCandidate(expression, Signal("test", ("mock_value",)))
        try:
            return asyncio.run(
                Controller(local, CountingAdapter()).run_one_candidate(
                    run_ids[index], candidate, config
                )
            )["status"]
        except ValueError:
            return "ENTRY_REJECTED"
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(worker, [0, 1]))
    repo = Repository(path)
    try:
        expected = "ENTRY_REJECTED" if same_run else "REJECTED"
        assert sorted(outcomes) == ["COMPLETED", expected]
        reports = [repo.report(value) for value in set(run_ids)]
        assert sum(len(r["jobs"]) for r in reports) == len(submissions) == 1
        assert sum(r["used_budget"] for r in reports) == 1
        assert sum(r["reserved_budget"] for r in reports) == 0
    finally:
        repo.close()


@pytest.mark.parametrize("legacy", [False, True])
def test_unknown_identity_cannot_be_bypassed_by_switching_mock_scenario(repo, legacy):
    config = ResearchConfig(mock_scenario="timeout")
    original = execute(repo, config)
    job = original["jobs"][0]
    if legacy:
        candidate = asyncio.run(MockGenerator().generate(config))
        canonical, _ = validate(candidate, config)
        with repo.transaction():
            repo.db.execute(
                "UPDATE alphas SET fingerprint=? WHERE id=?",
                (fingerprints(canonical, config)[-1], job["alpha_id"]),
            )
    adapter = MockSimulationAdapter()
    duplicate = execute(repo, ResearchConfig(), adapter)
    assert duplicate["events"][-1]["event_type"] == "DUPLICATE"
    assert job["alpha_id"] in duplicate["events"][-1]["payload_json"]
    assert adapter.submissions == 0
    persisted = repo.report(original["id"])
    assert persisted["reserved_budget"] == 1 and persisted["used_budget"] == 0
    assert persisted["jobs"][0]["status"] == "UNKNOWN"
    assert persisted["attempts"][0]["outcome"] == "REMOTE_UNKNOWN"


def test_failed_run_does_not_accept_late_result_or_release_budget(repo):
    original = repo.save_result

    def fail(*args):
        raise sqlite3.OperationalError("injected write failure")

    repo.save_result = fail
    report = execute(repo)
    repo.save_result = original
    job = report["jobs"][0]
    with pytest.raises(ValueError, match="terminal run"):
        repo.save_result(job["id"], SimulationResult({"sharpe": 1.2}, {}, {}), "PASS")
    unchanged = repo.report(report["id"])
    assert unchanged["status"] == "FAILED" and unchanged["jobs"][0]["status"] == "POLLING"
    assert unchanged["used_budget"] == 1 and unchanged["reserved_budget"] == 0
    assert repo.db.execute("SELECT count(*) FROM sim_results").fetchone()[0] == 0


def test_run_cannot_complete_with_polling_job(repo):
    class InspectAdapter(MockSimulationAdapter):
        async def poll(self, remote_id):
            run_id = repo.db.execute("SELECT run_id FROM sim_jobs").fetchone()[0]
            with pytest.raises(ValueError, match="completed job"):
                repo.finish(run_id, "COMPLETED", "premature")
            return await super().poll(remote_id)

    assert execute(repo, adapter=InspectAdapter())["status"] == "COMPLETED"


@pytest.mark.parametrize("receipt", [None, {}, SubmissionReceipt(""), SubmissionReceipt(42)])
def test_invalid_receipt_is_unknown_without_unhandled_error(repo, receipt):
    class BadAdapter(MockSimulationAdapter):
        async def submit(self, request):
            self.submissions += 1
            return receipt

    adapter = BadAdapter()
    report = execute(repo, adapter=adapter)
    assert report["status"] == "FAILED" and report["jobs"][0]["status"] == "UNKNOWN"
    assert report["reserved_budget"] == 1 and report["used_budget"] == 0
    assert report["attempts"][0]["outcome"] == "INTERNAL"
    assert adapter.submissions == 1


@pytest.mark.parametrize(
    "result",
    [
        None,
        {},
        SimulationResult({}, {}, {}),
        SimulationResult({"sharpe": True}, {}, {}),
        SimulationResult({"sharpe": "1.2"}, {}, {}),
        SimulationResult({"sharpe": float("nan")}, {}, {}),
        SimulationResult({"sharpe": float("inf")}, {}, {}),
        SimulationResult({"sharpe": 1.2}, {}, {"not_json": object()}),
    ],
)
def test_invalid_result_is_audited_without_crash_or_false_gate(repo, result):
    class BadAdapter(MockSimulationAdapter):
        async def fetch_result(self, remote_id):
            return result

    report = execute(repo, adapter=BadAdapter())
    assert report["status"] == "FAILED" and report["jobs"][0]["status"] == "UNKNOWN"
    assert report["jobs"][0]["gate"] is None
    assert report["used_budget"] == 1 and report["reserved_budget"] == 0
    assert report["jobs"][0]["remote_id"]


@pytest.mark.parametrize(
    "scenario,reserved,used",
    [
        ("invalid_receipt", 1, 0),
        ("invalid_status", 0, 1),
        ("invalid_result", 0, 1),
    ],
)
def test_mock_contract_fault_modes(repo, scenario, reserved, used):
    report = execute(repo, ResearchConfig(mock_scenario=scenario))
    assert report["jobs"][0]["status"] == "UNKNOWN"
    assert (report["reserved_budget"], report["used_budget"]) == (reserved, used)


@pytest.mark.parametrize(
    "expression",
    [
        "mock_rank(mock_value) # silently discarded",
        "mock_rank(mock_ｖａｌｕｅ)",
        "mock_pair(mock_value, 1e999)",
        "mock_value; mock_value",
        "x=mock_value",
        "-mock_value",
        "mock_value ? 1 : 0",
    ],
)
def test_unsupported_syntax_is_not_silently_rewritten(expression):
    config = ResearchConfig(operators={"mock_rank": 1, "mock_pair": 2})
    with pytest.raises(ValidationError):
        validate(AlphaCandidate(expression, Signal("test", ("mock_value",))), config)


def test_canonicalization_roundtrip_preserves_supported_ast():
    import ast

    config = ResearchConfig(operators={"mock_pair": 2})
    candidate = AlphaCandidate(" mock_pair( (mock_value), 2.0 ) ", Signal("test", ("mock_value",)))
    canonical, fingerprint = validate(candidate, config)
    roundtrip = replace(candidate, expression=canonical)
    assert validate(roundtrip, config) == (canonical, fingerprint)
    assert ast.dump(ast.parse(candidate.expression.strip(), mode="eval")) == ast.dump(
        ast.parse(canonical, mode="eval")
    )


def test_mock_tracks_delays_by_remote_id_and_rejects_unknown_ids():
    async def check():
        adapter = MockSimulationAdapter("delayed")
        request = SimulationRequest("mock_value", {})
        first, second = await adapter.submit(request), await adapter.submit(request)
        with pytest.raises(RemoteUnknown):
            await adapter.fetch_result(first.remote_id)
        assert await adapter.poll(first.remote_id) == "RUNNING"
        with pytest.raises(RemoteUnknown):
            await adapter.fetch_result(first.remote_id)
        assert await adapter.poll(second.remote_id) == "RUNNING"
        assert await adapter.poll(first.remote_id) == "COMPLETED"
        assert (await adapter.fetch_result(first.remote_id)).metrics["sharpe"] == 1.25
        for method in (adapter.poll, adapter.fetch_result):
            with pytest.raises(RemoteUnknown):
                await method("missing")

    asyncio.run(check())
