"""A5 fail-closed CLI compatibility adapters. No unverified research process is launched."""

import re
import shutil
import subprocess
from pathlib import Path

from .backends import BackendError


def _probe_command(executable, arguments):
    try:
        result = subprocess.run(
            [executable, *arguments],
            capture_output=True,
            timeout=5,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return result.returncode, result.stdout[:16384], result.stderr[:16384]
    except (OSError, subprocess.TimeoutExpired):
        return None, "", ""


def probe_cli(family, *, finder=shutil.which, runner=_probe_command):
    if family not in {"codex", "claude_code"}:
        raise ValueError("ONLY_KNOWN_CLI_PREFLIGHT_SUPPORTED")
    executable = finder("codex" if family == "codex" else "claude")
    report = {
        "schema_version": 1,
        "backend": family,
        "installed": bool(executable),
        "executable_name": Path(executable).name if executable else None,
        "cli_version": None,
        "local_auth_status": "NOT_CHECKED",
        "network_credential_validity": "NOT_TESTED",
        "structured_flag_observed": False,
        "host_isolation": "UNVERIFIED",
        "research_task_execution": "NOT_RUN",
        "status": "UNSUPPORTED",
        "safe_error_code": "HOST_ISOLATION_UNVERIFIED",
    }
    if not executable:
        report["safe_error_code"] = "BACKEND_NOT_INSTALLED"
        return report
    code, stdout, stderr = runner(executable, ["--version"])
    version = re.search(r"(?m)^(?:codex-cli\s+)?(\d+\.\d+\.\d+(?:[-+.][A-Za-z0-9_.-]+)?)", stdout)
    if code != 0 or not version:
        report["safe_error_code"] = "CLI_PROBE_FAILED"
        return report
    report["cli_version"] = version.group(1)
    report["cli_initialization_warning"] = bool(stderr)
    if family == "codex":
        code, stdout, _ = runner(executable, ["exec", "--help"])
        report["structured_flag_observed"] = code == 0 and "--output-schema" in stdout
        code, stdout, stderr = runner(executable, ["login", "status"])
        text = (stdout + stderr).lower()
        report["local_auth_status"] = (
            "NOT_AUTHENTICATED"
            if "not logged in" in text
            else "AUTHENTICATED_LOCAL"
            if code == 0 and re.search(r"(?m)^logged in\b", text)
            else "UNKNOWN"
        )
    # Claude's auth flags are not guessed. If later installed, verify its actual help/docs first.
    return report


class CodexBackend:
    capabilities = (
        frozenset()
    )  # No enforced role workspace/credential boundary verified on this host.
    unavailable_code = "HOST_ISOLATION_UNVERIFIED"

    async def execute(self, request):
        raise BackendError("HOST_ISOLATION_UNVERIFIED")

    def probe(self):
        return probe_cli("codex")


class ClaudeCodeBackend:
    capabilities = frozenset()
    unavailable_code = "HOST_ISOLATION_UNVERIFIED"

    async def execute(self, request):
        raise BackendError("HOST_ISOLATION_UNVERIFIED")

    def probe(self):
        return probe_cli("claude_code")


def foundation_registry():
    from .config import mock_registry

    registry = mock_registry()
    registry.register("codex", CodexBackend)
    registry.register("claude_code", ClaudeCodeBackend)
    return registry
