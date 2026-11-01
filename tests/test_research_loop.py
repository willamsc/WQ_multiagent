import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from alpha_platform.agent_interop.context import ContextBuilder
from alpha_platform.agent_interop.contracts import Role, Subject, canonical
from alpha_platform.agent_interop.ledger import AgentLedger
from alpha_platform.agent_interop.runtime import AgentRuntime
from alpha_platform.observability.diagnostics import references
from alpha_platform.observability.research_summary import research_summary
from alpha_platform.persistence import Repository
from alpha_platform.research_loop.controller import ResearchController
from alpha_platform.research_loop.mock import ResearchMockBackend
from alpha_platform.research_loop.policy import LoopPolicy
from alpha_platform.research_loop.store import ResearchStore


def document():
    return json.loads(
        (Path(__file__).parents[1] / "configs/study-mock.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def loop_system(tmp_path):
    from alpha_platform.agent_interop.backends import BackendRegistry

    path = tmp_path / "agents.sqlite3"
    ledger = AgentLedger(path)
    ledger.initialize()
    registry = BackendRegistry()
    yield ledger, registry, path
    ledger.close()


def run(system, scenario="pass", *, policy=None, builder=None, sources=None):
    ledger, registry, path = system
    index = len(registry.factories)
    g, c = f"g{index}", f"c{index}"
    registry.register(g, lambda: ResearchMockBackend(scenario))
    registry.register(c, lambda: ResearchMockBackend(scenario))
    controller = ResearchController(
        AgentRuntime(ledger, registry, {Role.GENERATOR: g, Role.CRITIC: c}), builder=builder
    )
    doc = document()
    study = asyncio.run(
        controller.run(
            sources or doc["sources"],
            signal_id=str(uuid4()),
            policy=policy or LoopPolicy(timeout_seconds=0.1),
            evidence_catalog=doc["evidence_catalog"],
        )
    )
    return research_summary(path, study)


@pytest.mark.parametrize(
    "scenario,status,reason,versions,calls",
    [
        ("pass", "APPROVED", "ALL_REQUIRED_REVIEWS_PASS", 1, 2),
        ("reject", "REJECTED", "NON_REPAIRABLE_FINDINGS", 1, 2),
        ("uncertain", "NEEDS_EVIDENCE", "CRITIC_UNCERTAIN", 1, 2),
        ("repair_pass", "APPROVED", "ALL_REQUIRED_REVIEWS_PASS", 2, 5),
        ("repair_exhausted", "REJECTED", "REPAIR_LIMIT_EXHAUSTED", 2, 4),
        ("regression_fail", "REJECTED", "REPAIR_LIMIT_EXHAUSTED", 2, 5),
        ("regression_uncertain", "NEEDS_EVIDENCE", "CRITIC_UNCERTAIN", 2, 5),
        ("timeout", "EXECUTION_FAILED", "AGENT_TIMEOUT", 1, 2),
        ("invalid_json", "EXECUTION_FAILED", "AGENT_INVALID_OUTPUT", 1, 2),
        ("wrong_version", "EXECUTION_FAILED", "AGENT_INVALID_OUTPUT", 1, 2),
        ("overreach", "EXECUTION_FAILED", "AGENT_FAILED", 1, 2),
        ("unsupported_syntax", "REJECTED", "DETERMINISTIC_VALIDATION_FAILED", 0, 1),
    ],
)
def test_minimal_loop_matrix(loop_system, scenario, status, reason, versions, calls):
    report = run(loop_system, scenario)
    assert report["status"] == status and report["reason_code"] == reason
    assert len(report["versions"]) == versions
    assert report["budget"]["agent_calls"] == calls
    assert references(report) <= report["evidence"].keys()
    assert report["simulation_budget"] == "NOT_ACCESSED"
    if status == "EXECUTION_FAILED":
        assert not report["reviews"]


def test_v2_is_independent_then_regression_with_immutable_versions(loop_system):
    ledger, _, _ = loop_system
    sources = document()["sources"]
    sources.update(
        generator_private_history="PRIVATE_CANARY",
        drafts="DRAFT_CANARY",
        previous_review="OLD_PRIVATE_REVIEW",
    )
    report = run(loop_system, "repair_pass", sources=sources)
    assert [v["version"] for v in report["versions"]] == [1, 2]
    assert report["versions"][0]["proposal"] != report["versions"][1]["proposal"]
    contexts = {}
    for task in report["tasks"]:
        row = ledger.db.execute(
            "SELECT context_json FROM agent_tasks WHERE task_id=?", (task["task_id"],)
        ).fetchone()
        contexts[task["version"], task["phase"]] = json.loads(row[0])
    assert "workflow" not in contexts[2, "independent"]
    assert contexts[2, "regression-1"]["workflow"]["review"]["signal_version"] == 1
    assert contexts[2, "generate"]["workflow"]["phase"] == "repair"
    all_stored = str(list(ledger.db.iterdump()))
    assert all(
        marker not in all_stored
        for marker in ("PRIVATE_CANARY", "DRAFT_CANARY", "OLD_PRIVATE_REVIEW")
    )
    events = report["events"]
    lock = next(
        e["sequence"]
        for e in events
        if e["event_type"] == "REVIEW_LOCKED"
        and e["payload"]["version"] == 2
        and e["payload"]["phase"] == "independent"
    )
    regression = next(
        e["sequence"]
        for e in events
        if e["event_type"] == "AGENT_AUTHORIZED" and e["payload"]["phase"] == "regression-1"
    )
    assert lock < regression
    with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
        with ledger.transaction():
            ledger.db.execute("UPDATE signal_versions SET proposal_json='{}'")
    with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
        with ledger.transaction():
            ledger.db.execute("DELETE FROM signal_reviews")


@pytest.mark.parametrize(
    "policy,expected_calls,reason",
    [
        (LoopPolicy(max_repairs=0), 2, "REPAIR_LIMIT_EXHAUSTED"),
        (LoopPolicy(max_agent_calls=1), 1, "AGENT_CALL_LIMIT"),
        (LoopPolicy(max_agent_calls=4), 4, "AGENT_CALL_LIMIT"),
    ],
)
def test_budgets_and_repair_caps(loop_system, policy, expected_calls, reason):
    report = run(loop_system, "repair_pass", policy=policy)
    assert report["reason_code"] == reason
    assert report["budget"]["agent_calls"] == expected_calls
    assert (
        loop_system[0].db.execute("SELECT count(*) FROM agent_tasks").fetchone()[0]
        == expected_calls
    )


def test_total_time_limit_does_not_approve(loop_system):
    report = run(
        loop_system, "timeout", policy=LoopPolicy(timeout_seconds=1, total_timeout_seconds=0.02)
    )
    assert report["status"] in {"EXECUTION_FAILED", "LIMIT_REACHED"}
    assert not any(e["event_type"] == "APPROVED" for e in report["events"])


def test_forged_context_is_refused_before_backend_dispatch(loop_system):
    class Forged(ContextBuilder):
        def build(self, *args, **kwargs):
            packet = super().build(*args, **kwargs)
            body = packet.content()
            body["generator_private_history"] = "CANARY"
            return replace(packet, content_json=canonical(body))

    report = run(loop_system, builder=Forged())
    assert report["status"] == "EXECUTION_FAILED"
    assert report["reason_code"] == "CONTEXT_ISOLATION_REJECTED"
    assert report["budget"]["agent_calls"] == 0
    assert not report["tasks"]


def test_context_sensitivity_checks_dataclass_tuple_fields():
    from conftest import approved_sources

    sources = approved_sources()
    sources["proposal"]["risks"] = ["Authorization: Bearer DO-NOT-SHARE"]
    with pytest.raises(ValueError, match="SENSITIVE"):
        ContextBuilder().build(Role.CRITIC, Subject("s", 1), sources)


def test_gateway_is_bounded_and_does_not_auto_approve(loop_system):
    report = run(loop_system, "evidence_request")
    assert report["status"] == "NEEDS_EVIDENCE"
    assert report["budget"]["evidence_requests"] == 1
    assert report["evidence_requests"][0]["response"]["status"] == "AVAILABLE"
    report = run(loop_system, "evidence_request", policy=LoopPolicy(max_evidence_requests=0))
    assert report["status"] == "NEEDS_EVIDENCE" and not report["evidence_requests"]
    assert report["budget"]["evidence_requests"] == 0


def test_terminal_study_cannot_be_rerun_with_new_backend(loop_system):
    ledger, registry, _ = loop_system
    report = run(loop_system, "reject")
    registry.register("new-g", ResearchMockBackend)
    registry.register("new-c", ResearchMockBackend)
    controller = ResearchController(
        AgentRuntime(ledger, registry, {Role.GENERATOR: "new-g", Role.CRITIC: "new-c"})
    )
    before = list(ledger.db.iterdump())
    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(
            controller.run(
                document()["sources"], signal_id=report["signal_id"], study_id=report["study_id"]
            )
        )
    assert list(ledger.db.iterdump()) == before


def test_m1_budget_and_schema_are_untouched(loop_system, tmp_path):
    repo = Repository(tmp_path / "m1.sqlite3")
    repo.initialize()
    before = list(repo.db.iterdump())
    run(loop_system, "repair_pass")
    assert list(repo.db.iterdump()) == before
    repo.close()
    assert loop_system[0].db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_cross_process_queries_and_observatory_alias(loop_system):
    _, _, path = loop_system
    report = run(loop_system, "repair_pass")
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    for prefix in (["study", "show"], ["observe", "study"]):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "alpha_platform.cli",
                *prefix,
                "--ledger",
                str(path),
                "--study-id",
                report["study_id"],
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=10,
        )
        assert result.returncode == 0, result.stdout
        queried = json.loads(result.stdout)
        assert queried["status"] == "APPROVED" and len(queried["versions"]) == 2


def test_late_attempt_does_not_approve_or_overwrite_research(loop_system):
    ledger, registry, path = loop_system

    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()

        class Delayed(ResearchMockBackend):
            async def execute(self, request):
                started.set()
                await release.wait()
                return await super().execute(request)

        registry.register("g", Delayed)
        registry.register("c", ResearchMockBackend)
        runtime = AgentRuntime(ledger, registry, {Role.GENERATOR: "g", Role.CRITIC: "c"})
        controller = ResearchController(runtime)
        pending = asyncio.create_task(
            controller.run(document()["sources"], signal_id="late-study", study_id="late-study")
        )
        await started.wait()
        row = ledger.db.execute(
            "SELECT envelope_json,context_json FROM agent_tasks LIMIT 1"
        ).fetchone()
        from alpha_platform.agent_interop.context import create_task
        from alpha_platform.agent_interop.contracts import ContextPacket, ResourceLimits

        e = json.loads(row[0])
        p = ContextPacket(
            Role.GENERATOR,
            Subject(**e["subject"]),
            e["context_version"],
            row[1],
            tuple(e["allowed_evidence_scopes"]),
        )
        t = create_task(p, task_id=e["task_id"], limits=ResourceLimits(**e["resource_limits"]))
        # This task is limited to one attempt by M2-B; attempts cannot be multiplied by swapping routing.
        with pytest.raises(ValueError, match="ATTEMPT_LIMIT"):
            await runtime.execute(t, p, supersede=True)
        release.set()
        study = await pending
        assert research_summary(path, study)["status"] == "APPROVED"
        assert (
            ledger.db.execute(
                "SELECT count(*) FROM agent_results WHERE task_id=?", (t.task_id,)
            ).fetchone()[0]
            == 1
        )

    asyncio.run(exercise())


def test_late_timeout_result_cannot_become_a_research_review(loop_system):
    ledger, registry, path = loop_system

    async def exercise():
        release = asyncio.Event()

        class Resistant(ResearchMockBackend):
            async def execute(self, request):
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    await release.wait()
                    return await super().execute(request)

        registry.register("g", ResearchMockBackend)
        registry.register("c", Resistant)
        controller = ResearchController(
            AgentRuntime(ledger, registry, {Role.GENERATOR: "g", Role.CRITIC: "c"})
        )
        study = await controller.run(
            document()["sources"], signal_id="late-timeout", policy=LoopPolicy(timeout_seconds=0.02)
        )
        before = research_summary(path, study)
        assert before["status"] == "EXECUTION_FAILED" and not before["reviews"]
        release.set()
        await asyncio.sleep(0.02)
        assert research_summary(path, study) == before
        assert ledger.db.execute("SELECT count(*) FROM agent_results").fetchone()[0] == 1

    asyncio.run(exercise())


def test_all_old_failures_are_regressed_when_more_than_one_repair(loop_system):
    ledger, registry, path = loop_system

    class TwoFix(ResearchMockBackend):
        def __init__(self):
            super().__init__("repair_pass")

        async def execute(self, request):
            response = json.loads(await super().execute(request))
            if request.task.role == Role.CRITIC and request.task.subject.signal_version == 2:
                response["result"]["verdict"] = "FAIL"
                response["result"]["findings"] = [
                    {
                        "id": "second-finding",
                        "category": "mechanism",
                        "severity": "major",
                        "description": "second repair fixture",
                        "evidence_refs": [],
                    }
                ]
            return canonical(response)

    registry.register("g", TwoFix)
    registry.register("c", TwoFix)
    controller = ResearchController(
        AgentRuntime(ledger, registry, {Role.GENERATOR: "g", Role.CRITIC: "c"})
    )
    study = asyncio.run(
        controller.run(
            document()["sources"],
            signal_id="two-fix",
            policy=LoopPolicy(max_repairs=2, max_agent_calls=10),
        )
    )
    report = research_summary(path, study)
    assert report["status"] == "APPROVED" and len(report["versions"]) == 3
    assert {r["phase"] for r in report["reviews"] if r["version"] == 3} == {
        "independent",
        "regression-1",
        "regression-2",
    }
    assert report["budget"]["agent_calls"] == 8


def test_signal_version_and_audit_rollback_together(loop_system, monkeypatch):
    original = ResearchStore.event

    def fail(self, study_id, kind, **payload):
        if kind == "SIGNAL_VERSION_CREATED":
            raise sqlite3.OperationalError("fixture write failure")
        return original(self, study_id, kind, **payload)

    monkeypatch.setattr(ResearchStore, "event", fail)
    with pytest.raises(sqlite3.OperationalError):
        run(loop_system)
    ledger = loop_system[0]
    assert ledger.db.execute("SELECT count(*) FROM signal_versions").fetchone()[0] == 0
    assert ledger.db.execute("SELECT current_version,status FROM signal_studies").fetchone()[:] == (
        0,
        "EXECUTION_FAILED",
    )
    assert (
        ledger.db.execute(
            "SELECT count(*) FROM signal_events WHERE event_type='PROPOSAL_VALIDATED'"
        ).fetchone()[0]
        == 0
    )
    assert ledger.db.execute("SELECT count(*) FROM agent_results").fetchone()[0] == 1


def test_gateway_catalog_cannot_rebind_an_approved_reference(loop_system):
    ledger, registry, _ = loop_system
    registry.register("g", ResearchMockBackend)
    registry.register("c", ResearchMockBackend)
    controller = ResearchController(
        AgentRuntime(ledger, registry, {Role.GENERATOR: "g", Role.CRITIC: "c"})
    )
    original = document()["sources"]
    catalog = [{**original["evidence"][0], "summary": "different content for same ref"}]
    with pytest.raises(ValueError, match="REFERENCE_CONFLICT"):
        asyncio.run(
            controller.run(original, signal_id="catalog-conflict", evidence_catalog=catalog)
        )
    assert ledger.db.execute("SELECT count(*) FROM agent_tasks").fetchone()[0] == 0


def test_input_evidence_cannot_collide_with_internal_observer_namespace(loop_system):
    sources = document()["sources"]
    sources["evidence"][0]["ref"] = "agent_task:fake"
    with pytest.raises(ValueError, match="NAMESPACE"):
        run(loop_system, sources=sources)


def test_research_approval_requires_locked_reviews(loop_system):
    ledger, _, _ = loop_system
    store = ResearchStore(ledger)
    store.initialize()
    store.start(
        "empty-study",
        "empty-signal",
        document()["sources"],
        LoopPolicy(),
        {Role.GENERATOR: "g", Role.CRITIC: "c"},
    )
    with pytest.raises(ValueError, match="LOCKED_REVIEWS"):
        store.finish("empty-study", "APPROVED", "forged")
    assert ledger.db.execute("SELECT status FROM signal_studies").fetchone()[0] == "RUNNING"


def test_known_real_backend_gate_cannot_be_disabled_by_routing(loop_system):
    from alpha_platform.agent_interop.optional_cli import foundation_registry

    ledger, _, path = loop_system
    controller = ResearchController(
        AgentRuntime(
            ledger, foundation_registry(), {Role.GENERATOR: "codex", Role.CRITIC: "claude_code"}
        )
    )
    study = asyncio.run(controller.run(document()["sources"], signal_id="real-blocked"))
    report = research_summary(path, study)
    assert report["status"] == "EXECUTION_FAILED"
    attempt = report["tasks"][0]["attempts"][0]
    assert attempt["execution_status"] == "UNSUPPORTED"
    assert attempt["safe_error_code"] == "HOST_ISOLATION_UNVERIFIED"


def test_gateway_deduplicates_and_bounds_requests(loop_system):
    ledger, registry, path = loop_system

    class Requests(ResearchMockBackend):
        async def execute(self, request):
            response = json.loads(await super().execute(request))
            if request.task.role == Role.CRITIC:
                response["result"]["verdict"] = "UNCERTAIN"
                response["result"]["findings"] = [
                    {
                        "id": f"request-{i}",
                        "category": "evidence_request",
                        "severity": "major",
                        "description": ref,
                        "evidence_refs": [],
                    }
                    for i, ref in enumerate(
                        ["e:additional", "e:additional", "e:unavailable", "e:over-limit"]
                    )
                ]
            return canonical(response)

    registry.register("g", Requests)
    registry.register("c", Requests)
    controller = ResearchController(
        AgentRuntime(ledger, registry, {Role.GENERATOR: "g", Role.CRITIC: "c"})
    )
    doc = document()
    study = asyncio.run(
        controller.run(
            doc["sources"], signal_id="requests", evidence_catalog=doc["evidence_catalog"]
        )
    )
    report = research_summary(path, study)
    assert report["budget"]["evidence_requests"] == 2
    assert [r["response"]["status"] for r in report["evidence_requests"]] == [
        "AVAILABLE",
        "UNAVAILABLE",
    ]
    assert any(e["event_type"] == "EVIDENCE_LIMIT_REACHED" for e in report["events"])
    assert report["status"] == "NEEDS_EVIDENCE"
    assert references(report) <= report["evidence"].keys()


def test_cli_creates_persistent_loop_and_safe_export(tmp_path):
    path = tmp_path / "study.sqlite3"
    output = tmp_path / "study.json"
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alpha_platform.cli",
            "study",
            "run",
            "--input",
            str(Path(__file__).parents[1] / "configs/study-mock.json"),
            "--ledger",
            str(path),
            "--output",
            str(output),
            "--scenario",
            "repair_pass",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "APPROVED" and report["budget"]["agent_calls"] == 5
    assert references(report) <= report["evidence"].keys()
