import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import asdict, replace

import pytest
from conftest import approved_sources, prepare_task

from alpha_platform.agent_interop.backends import AttemptRequest, MockBackend
from alpha_platform.agent_interop.context import ContextBuilder, create_task
from alpha_platform.agent_interop.contracts import (
    ExecutionOutcome,
    ExecutionStatus,
    Role,
    Subject,
    canonical,
)
from alpha_platform.cli import main
from alpha_platform.observability.agent_summary import agent_summary, write_agent_json
from alpha_platform.observability.diagnostics import references


def test_formal_generator_result_can_be_reviewed_without_private_context(agent_system):
    generated = asyncio.run(agent_system.runtime.execute(*prepare_task()))
    sources = approved_sources()
    sources["proposal"] = asdict(generated.result)
    sources["generator_private_history"] = "CANARY_NOT_SHARED"
    context = ContextBuilder().build(Role.CRITIC, Subject("s", 1), sources)
    reviewed = asyncio.run(agent_system.runtime.execute(create_task(context), context))
    assert reviewed.execution_status == ExecutionStatus.SUCCEEDED
    assert reviewed.result.verdict == "UNCERTAIN"
    assert "CANARY_NOT_SHARED" not in context.content_json


def test_summary_is_readonly_evidence_closed_and_has_separate_budgets(agent_system):
    result = asyncio.run(agent_system.runtime.execute(*prepare_task()))
    before = list(agent_system.ledger.db.iterdump())
    report = agent_summary(agent_system.path, result.task_id)
    assert references(report) <= report["evidence"].keys()
    assert report["simulation_budget"] == "NOT_ACCESSED"
    assert report["attempt_budget"]["allocated_attempts"] == 1
    assert report["context_digest"] == result.context_digest
    assert report["attempts"][0]["result_ref"] == result.validated_result_ref
    assert report["attempts"][0]["usage"]["model_tokens"] is None
    assert list(agent_system.ledger.db.iterdump()) == before


def test_failed_result_audit_transaction_can_retry_only_local_finalize(agent_system):
    task, context = prepare_task()
    agent_system.ledger.register(task, context)
    attempt = agent_system.ledger.begin(task, "mock-g")
    raw = asyncio.run(MockBackend().execute(AttemptRequest(task, attempt, context)))
    parsed = agent_system.runtime._validate(task, context, attempt, raw)
    result = ExecutionOutcome(
        task.task_id,
        attempt,
        "mock-g",
        task.role,
        task.subject,
        task.context_version,
        task.context_digest,
        ExecutionStatus.SUCCEEDED,
        None,
        1,
        f"agent_result:{attempt}",
        parsed,
    )
    agent_system.ledger.db.execute("""CREATE TRIGGER reject_agent_audit BEFORE INSERT ON agent_events
      WHEN NEW.event_type='ATTEMPT_SUCCEEDED' BEGIN SELECT RAISE(ABORT,'injected'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        agent_system.ledger.finish(result)
    assert agent_system.ledger.db.execute("SELECT count(*) FROM agent_results").fetchone()[0] == 0
    assert (
        agent_system.ledger.db.execute("SELECT status FROM agent_tasks").fetchone()[0] == "RUNNING"
    )
    agent_system.ledger.db.execute("DROP TRIGGER reject_agent_audit")
    assert agent_system.ledger.finish(result)
    assert agent_system.ledger.finish(result) is False


def test_begin_cannot_bypass_registered_attempt_limits(agent_system):
    task, context = prepare_task()
    agent_system.ledger.register(task, context)
    forged = replace(task, resource_limits=replace(task.resource_limits, max_attempts=10))
    with pytest.raises(ValueError, match="IMMUTABLE_TASK"):
        agent_system.ledger.begin(forged, "mock-g")


def test_public_exports_cannot_overwrite_sqlite_or_write_sidecars(agent_system, tmp_path):
    disguised = tmp_path / "business.json"
    disguised.write_bytes(agent_system.path.read_bytes())
    with pytest.raises(ValueError, match="OVERWRITE_SQLITE"):
        write_agent_json(disguised, {}, agent_system.path)
    with pytest.raises(ValueError, match="JSON_REQUIRED"):
        write_agent_json(str(agent_system.path) + "-wal", {}, agent_system.path)


def test_agent_cli_generates_and_reviews_offline_across_processes(tmp_path):
    path = tmp_path / "cli-agent.sqlite3"

    def cli(*args):
        response = subprocess.run(
            [sys.executable, "-m", "alpha_platform.cli", "agent", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        assert response.returncode == 0, response.stdout + response.stderr
        return json.loads(response.stdout)

    for role, file in (
        ("generator", "agent_generator_mock.json"),
        ("critic", "agent_critic_mock.json"),
    ):
        response = cli(
            "run",
            "--role",
            role,
            "--input",
            "configs/" + file,
            "--ledger",
            str(path),
            "--config",
            "configs/agent_backends_mock.json",
        )
        assert response["outcome"]["execution_status"] == "SUCCEEDED"
        task_id = response["outcome"]["task_id"]
        assert cli("show", "--ledger", str(path), "--task-id", task_id)["role"] == role


def test_cli_rejects_invalid_input_without_raw_error_or_credentials(tmp_path, capsys):
    file = tmp_path / "bad.json"
    file.write_text(canonical({"Authorization": "Bearer NEVER-EXPOSE"}))
    assert (
        main(
            [
                "agent",
                "run",
                "--role",
                "critic",
                "--input",
                str(file),
                "--ledger",
                str(tmp_path / "not-created.sqlite3"),
            ]
        )
        == 1
    )
    assert "NEVER-EXPOSE" not in capsys.readouterr().out
    assert not (tmp_path / "not-created.sqlite3").exists()


def test_execution_success_and_research_fail_are_not_confused(agent_system):
    class RejectingCritic(MockBackend):
        async def execute(self, request):
            response = json.loads(await super().execute(request))
            response["result"].update(
                verdict="FAIL",
                findings=[
                    {
                        "id": "f1",
                        "category": "hypothesis_mismatch",
                        "severity": "major",
                        "description": "离线测试缺陷",
                        "evidence_refs": ["e:1"],
                    }
                ],
            )
            return canonical(response)

    agent_system.registry.register("rejecting", RejectingCritic)
    agent_system.runtime.role_backends[Role.CRITIC] = "rejecting"
    result = asyncio.run(agent_system.runtime.execute(*prepare_task(Role.CRITIC)))
    assert result.execution_status == "SUCCEEDED" and result.result.verdict == "FAIL"
