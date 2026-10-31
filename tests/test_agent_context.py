import asyncio
from dataclasses import replace

import pytest
from conftest import approved_sources, prepare_task

from alpha_platform.agent_interop.context import ContextBuilder, create_task
from alpha_platform.agent_interop.contracts import ContractError, Role, Subject, canonical


def test_critic_only_receives_formal_proposal_and_original_approved_evidence():
    sources = approved_sources()
    sources.update(
        generator_private_history="GENERATOR_CANARY",
        drafts="DRAFT_CANARY",
        previous_review="OLD_CRITIC_CANARY",
        private_files=["PRIVATE_FILE_CANARY"],
    )
    generator = ContextBuilder().build(Role.GENERATOR, Subject("s", 1), sources)
    critic = ContextBuilder().build(Role.CRITIC, Subject("s", 1), sources)
    assert "proposal" not in generator.content()
    assert critic.content()["proposal"]["mechanism"] == "模拟机制"
    for marker in ("GENERATOR_CANARY", "DRAFT_CANARY", "OLD_CRITIC_CANARY", "PRIVATE_FILE_CANARY"):
        assert marker not in generator.content_json and marker not in critic.content_json
    with pytest.raises(PermissionError):
        critic.evidence("generator_private_history")
    assert "GENERATOR_CANARY" not in canonical(critic.evidence("provided_evidence"))


def test_context_is_frozen_snapshot_and_digest_binds_role_subject_and_version():
    sources = approved_sources()
    packet = ContextBuilder().build(Role.GENERATOR, Subject("s", 1), sources)
    before = packet.digest
    sources["field_metadata"]["mock_value"]["description"] = "changed afterwards"
    copy = packet.content()
    copy["objective"] = "changed in copy"
    assert packet.digest == before and packet.content()["objective"] != copy["objective"]
    assert ContextBuilder().build(Role.GENERATOR, Subject("s", 2), sources).digest != before
    assert (
        ContextBuilder()
        .build(Role.GENERATOR, Subject("s", 1), sources, version="context-v2")
        .digest
        != before
    )


def test_review_cannot_target_foreign_proposal_version():
    with pytest.raises(ContractError, match="VERSION"):
        ContextBuilder().build(Role.CRITIC, Subject("s", 2), approved_sources())


def test_unapproved_scope_is_denied_and_credentials_never_enter_ledger(agent_system):
    sources = approved_sources()
    sources["evidence"][0]["scope"] = "private_history"
    with pytest.raises(PermissionError):
        ContextBuilder().build(Role.CRITIC, Subject("s", 1), sources)
    sources = approved_sources()
    sources["evidence"][0]["summary"] = "Authorization: Bearer NEVER-PERSIST"
    with pytest.raises(ContractError, match="SENSITIVE"):
        ContextBuilder().build(Role.CRITIC, Subject("s", 1), sources)
    assert "NEVER-PERSIST" not in str(list(agent_system.ledger.db.iterdump()))


def test_forged_context_with_self_consistent_digest_still_fails_policy(agent_system):
    task, packet = prepare_task()
    body = packet.content()
    body["generator_private_history"] = "PRIVATE_CANARY"
    forged = replace(packet, content_json=canonical(body))
    task = replace(task, context_digest=forged.digest)
    with pytest.raises(ContractError, match="UNAPPROVED_CONTEXT"):
        asyncio.run(agent_system.runtime.execute(task, forged))
    assert agent_system.ledger.db.execute("SELECT count(*) FROM agent_tasks").fetchone()[0] == 0


def test_role_services_can_use_builder_packets(agent_system):
    packet = ContextBuilder().build(Role.CRITIC, Subject("s", 1), approved_sources())
    outcome = asyncio.run(agent_system.runtime.execute(create_task(packet), packet))
    assert outcome.execution_status == "SUCCEEDED"
    assert outcome.result.verdict == "UNCERTAIN"
