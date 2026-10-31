import asyncio
from dataclasses import replace
from time import perf_counter

import pytest
from conftest import prepare_task

from alpha_platform.agent_interop.backends import MockBackend
from alpha_platform.agent_interop.config import parse_backend_config
from alpha_platform.agent_interop.contracts import (
    ExecutionStatus,
    Role,
)
from alpha_platform.agent_interop.ledger import AgentLedger
from alpha_platform.agent_interop.runtime import AgentRuntime
from alpha_platform.persistence import Repository


@pytest.mark.parametrize(
    "name,code", [("missing", "UNKNOWN_BACKEND"), ("dsh", "BACKEND_RESERVED_UNSUPPORTED")]
)
def test_unavailable_backend_is_explicit_not_research_failure(agent_system, name, code):
    agent_system.runtime.role_backends[Role.CRITIC] = name
    result = asyncio.run(agent_system.runtime.execute(*prepare_task(Role.CRITIC)))
    assert result.execution_status == ExecutionStatus.UNSUPPORTED and result.safe_error_code == code
    assert result.result is None


def test_capability_missing_and_backend_session_reuse_are_blocked(agent_system):
    class Incapable(MockBackend):
        capabilities = frozenset()

        async def execute(self, request):
            raise AssertionError("must not dispatch")

    agent_system.registry.register("incapable", Incapable)
    agent_system.runtime.role_backends[Role.GENERATOR] = "incapable"
    result = asyncio.run(agent_system.runtime.execute(*prepare_task()))
    assert result.safe_error_code == "CAPABILITY_MISSING"
    singleton = MockBackend()
    agent_system.registry.register("singleton", lambda: singleton)
    agent_system.runtime.role_backends = {Role.GENERATOR: "singleton", Role.CRITIC: "singleton"}
    assert (
        asyncio.run(agent_system.runtime.execute(*prepare_task())).execution_status == "SUCCEEDED"
    )
    assert (
        asyncio.run(agent_system.runtime.execute(*prepare_task(Role.CRITIC))).safe_error_code
        == "BACKEND_SESSION_REUSE"
    )


def test_timeout_does_not_wait_for_cancellation_resistant_backend_or_commit_late(agent_system):
    async def check():
        release = asyncio.Event()

        class Resistant(MockBackend):
            async def execute(self, request):
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    await release.wait()
                    return await super().execute(request)

        agent_system.registry.register("resistant", Resistant)
        agent_system.runtime.role_backends[Role.GENERATOR] = "resistant"
        started = perf_counter()
        result = await agent_system.runtime.execute(*prepare_task(timeout=0.02))
        assert perf_counter() - started < 1
        assert result.execution_status == ExecutionStatus.TIMEOUT and result.result is None
        release.set()
        await asyncio.sleep(0.02)
        assert (
            agent_system.ledger.db.execute("SELECT count(*) FROM agent_results").fetchone()[0] == 0
        )

    asyncio.run(check())


def test_cancel_token_and_caller_cancel_are_persisted_without_result(agent_system):
    async def check():
        agent_system.registry.register("slow", lambda: MockBackend(delay=5))
        agent_system.runtime.role_backends[Role.GENERATOR] = "slow"
        token = asyncio.Event()
        pending = asyncio.create_task(
            agent_system.runtime.execute(*prepare_task(), cancel_event=token)
        )
        await asyncio.sleep(0.01)
        token.set()
        assert (await pending).execution_status == ExecutionStatus.CANCELLED
        pending = asyncio.create_task(agent_system.runtime.execute(*prepare_task()))
        await asyncio.sleep(0.01)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert (
            agent_system.ledger.db.execute(
                "SELECT count(*) FROM agent_attempts WHERE status='CANCELLED'"
            ).fetchone()[0]
            == 2
        )

    asyncio.run(check())


def test_late_attempt_cannot_overwrite_new_authorized_attempt(agent_system):
    async def check():
        started, release = asyncio.Event(), asyncio.Event()

        class Delayed(MockBackend):
            async def execute(self, request):
                started.set()
                await release.wait()
                return await super().execute(request)

        task, packet = prepare_task()
        agent_system.registry.register("old", Delayed)
        old_runtime = AgentRuntime(
            agent_system.ledger, agent_system.registry, {Role.GENERATOR: "old"}
        )
        old = asyncio.create_task(old_runtime.execute(task, packet))
        await started.wait()
        newer = await agent_system.runtime.execute(task, packet, supersede=True)
        release.set()
        stale = await old
        assert newer.execution_status == "SUCCEEDED"
        assert stale.execution_status == "STALE" and stale.result is None
        row = agent_system.ledger.db.execute("SELECT * FROM agent_results").fetchone()
        assert row["attempt_id"] == newer.attempt_id
        assert (
            agent_system.ledger.db.execute("SELECT count(*) FROM agent_results").fetchone()[0] == 1
        )
        with pytest.raises(ValueError, match="ALREADY_COMPLETED"):
            await agent_system.runtime.execute(task, packet)

    asyncio.run(check())


def test_duplicate_settlement_is_idempotent_and_subject_binding_is_checked(agent_system):
    task, context = prepare_task()
    result = asyncio.run(agent_system.runtime.execute(task, context))
    before = list(agent_system.ledger.db.iterdump())
    assert agent_system.ledger.finish(result) is False
    assert before == list(agent_system.ledger.db.iterdump())
    other, packet = prepare_task()
    agent_system.ledger.register(other, packet)
    attempt = agent_system.ledger.begin(other, "mock-g")
    forged = replace(result, task_id=other.task_id, attempt_id=attempt, context_version="wrong")
    with pytest.raises(ValueError, match="BINDING"):
        agent_system.ledger.finish(forged)
    assert agent_system.ledger.db.execute("SELECT count(*) FROM agent_results").fetchone()[0] == 1


def test_config_cannot_load_shell_or_untrusted_plugins():
    parsed = parse_backend_config(
        {"schema_version": 1, "roles": {"generator": "mock", "critic": "mock_alt"}}
    )
    assert parsed[Role.CRITIC] == "mock_alt"
    for config in (
        {"schema_version": 1, "roles": {"generator": "mock"}},
        {
            "schema_version": 1,
            "roles": {"generator": "mock", "critic": "mock"},
            "command": "arbitrary",
        },
        {"schema_version": 1, "roles": {"generator": "path/to/tool", "critic": "mock"}},
    ):
        with pytest.raises(ValueError):
            parse_backend_config(config)


def test_m1_database_and_simulation_budget_are_never_reused(tmp_path):
    path = tmp_path / "m1.sqlite3"
    repo = Repository(path)
    repo.initialize()
    before = list(repo.db.iterdump())
    with pytest.raises(ValueError, match="MUST_NOT_BE_M1"):
        AgentLedger(path)
    assert list(repo.db.iterdump()) == before
    repo.close()


def test_agent_attempt_limit_and_task_identity_are_immutable(agent_system):
    task, context = prepare_task()
    task = replace(task, resource_limits=replace(task.resource_limits, max_attempts=1))
    agent_system.runtime.role_backends[Role.GENERATOR] = "missing"
    asyncio.run(agent_system.runtime.execute(task, context))
    with pytest.raises(ValueError, match="ATTEMPT_LIMIT"):
        asyncio.run(agent_system.runtime.execute(task, context))
    with pytest.raises(ValueError, match="IMMUTABLE_TASK"):
        asyncio.run(
            agent_system.runtime.execute(
                replace(task, resource_limits=replace(task.resource_limits, max_attempts=2)),
                context,
            )
        )
