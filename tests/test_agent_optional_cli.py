import asyncio

import pytest
from conftest import prepare_task

from alpha_platform.agent_interop.contracts import Role
from alpha_platform.agent_interop.optional_cli import (
    ClaudeCodeBackend,
    CodexBackend,
    foundation_registry,
    probe_cli,
)


def test_missing_cli_is_not_installed_or_called():
    def prohibited(*args):
        raise AssertionError("must not invoke any command")

    result = probe_cli("claude_code", finder=lambda _: None, runner=prohibited)
    assert result["status"] == "UNSUPPORTED"
    assert result["safe_error_code"] == "BACKEND_NOT_INSTALLED"
    assert result["research_task_execution"] == "NOT_RUN"


def test_installed_authenticated_cli_stays_blocked_without_host_isolation():
    calls = []

    def run(path, args):
        calls.append(args)
        if args == ["--version"]:
            return 0, "codex-cli 0.164.0-alpha.11.1", ""
        if args == ["exec", "--help"]:
            return 0, "--output-schema", ""
        return 0, "", "Logged in; Authorization: Bearer DO-NOT-EXPORT"

    result = probe_cli("codex", finder=lambda _: "codex.exe", runner=run)
    assert result["installed"] and result["local_auth_status"] == "AUTHENTICATED_LOCAL"
    assert result["status"] == "UNSUPPORTED" and result["host_isolation"] == "UNVERIFIED"
    assert all(args in (["--version"], ["exec", "--help"], ["login", "status"]) for args in calls)
    assert "DO-NOT-EXPORT" not in str(result)


@pytest.mark.parametrize(
    "response", [(1, "", "private error"), (None, "", ""), (0, "unrecognized", "")]
)
def test_failed_or_nonzero_probe_is_safe_and_not_a_research_verdict(response):
    result = probe_cli("codex", finder=lambda _: "tool", runner=lambda *args: response)
    assert result["status"] == "UNSUPPORTED"
    assert result["safe_error_code"] == "CLI_PROBE_FAILED"
    assert "private error" not in str(result)


def test_real_adapters_cannot_dispatch_or_auto_probe_from_runtime(agent_system, monkeypatch):
    monkeypatch.setattr(
        "subprocess.run", lambda *args, **kwargs: pytest.fail("no subprocess allowed")
    )
    for name, backend in (("codex", CodexBackend), ("claude_code", ClaudeCodeBackend)):
        agent_system.registry.register(name, backend)
        agent_system.runtime.role_backends[Role.GENERATOR] = name
        outcome = asyncio.run(agent_system.runtime.execute(*prepare_task()))
        assert outcome.execution_status == "UNSUPPORTED"
        assert outcome.safe_error_code == "HOST_ISOLATION_UNVERIFIED"
        assert outcome.result is None


def test_dsh_is_only_a_registration_slot():
    registry = foundation_registry()
    assert "dsh" in registry.reserved and "dsh" not in registry.factories
    with pytest.raises(ValueError, match="RESERVED"):
        registry.create("dsh")


@pytest.mark.parametrize(
    "code,message,expected",
    [
        (0, "Not logged in", "NOT_AUTHENTICATED"),
        (1, "Logged in", "UNKNOWN"),
    ],
)
def test_auth_does_not_confuse_negative_or_failed_status(code, message, expected):
    def runner(path, args):
        if args == ["--version"]:
            return 0, "codex-cli 0.164.0", ""
        if args == ["exec", "--help"]:
            return 0, "--output-schema", ""
        return code, message, ""

    result = probe_cli("codex", finder=lambda _: "codex.exe", runner=runner)
    assert result["local_auth_status"] == expected
    assert result["status"] == "UNSUPPORTED"
