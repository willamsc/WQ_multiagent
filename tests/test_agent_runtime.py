import asyncio
from dataclasses import replace

import pytest
from conftest import prepare_task

from alpha_platform.agent_interop.backends import MockBackend
from alpha_platform.agent_interop.contracts import (
    ExecutionStatus,
    MainSignalProposal,
    ReviewResult,
    Role,
    Verdict,
)
from alpha_platform.agent_interop.roles import CriticRole, GeneratorRole


def test_generator_and_independent_critic_use_unified_runtime(agent_system):
    generator = asyncio.run(GeneratorRole(agent_system.runtime).generate(*prepare_task()))
    critic = asyncio.run(CriticRole(agent_system.runtime).review(*prepare_task(Role.CRITIC)))
    assert generator.execution_status == critic.execution_status == ExecutionStatus.SUCCEEDED
    assert isinstance(generator.result, MainSignalProposal)
    assert isinstance(critic.result, ReviewResult)
    assert critic.result.verdict == Verdict.UNCERTAIN
    assert generator.attempt_id != critic.attempt_id
    assert generator.context_digest != critic.context_digest
    assert agent_system.ledger.db.execute("SELECT count(*) FROM agent_results").fetchone()[0] == 2
    assert not agent_system.ledger.db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='sim_jobs'"
    ).fetchone()


@pytest.mark.parametrize(
    "scenario,status",
    [
        ("invalid_json", ExecutionStatus.INVALID_OUTPUT),
        ("wrong_version", ExecutionStatus.INVALID_OUTPUT),
        ("wrong_role", ExecutionStatus.INVALID_OUTPUT),
        ("wrong_schema", ExecutionStatus.INVALID_OUTPUT),
        ("raise", ExecutionStatus.FAILED),
        ("overreach", ExecutionStatus.FAILED),
    ],
)
def test_backend_failure_never_becomes_research_verdict(agent_system, scenario, status):
    agent_system.registry.register("fault", lambda: MockBackend(scenario))
    agent_system.runtime.role_backends[Role.GENERATOR] = "fault"
    result = asyncio.run(agent_system.runtime.execute(*prepare_task()))
    assert result.execution_status == status and result.result is None
    assert result.validated_result_ref is None
    assert "raw private" not in str(list(agent_system.ledger.db.iterdump()))


def test_context_task_binding_is_verified_before_dispatch(agent_system):
    task, context = prepare_task()
    with pytest.raises(ValueError, match="CONTEXT_BINDING"):
        asyncio.run(agent_system.runtime.execute(replace(task, context_version="foreign"), context))
    assert agent_system.ledger.db.execute("SELECT count(*) FROM agent_attempts").fetchone()[0] == 0


def test_roles_refuse_foreign_task_types(agent_system):
    with pytest.raises(ValueError, match="GENERATOR_ROLE"):
        asyncio.run(GeneratorRole(agent_system.runtime).generate(*prepare_task(Role.CRITIC)))
    with pytest.raises(ValueError, match="CRITIC_ROLE"):
        asyncio.run(CriticRole(agent_system.runtime).review(*prepare_task()))
