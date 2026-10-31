"""Explicit foundation tasks, independent from research run/simulation orchestration."""

import asyncio
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from .config import load_backend_config
from .context import ContextBuilder, create_task
from .contracts import ResourceLimits, Role, Subject, object_fields, strict_json
from .ledger import AgentLedger
from .optional_cli import foundation_registry, probe_cli
from .runtime import AgentRuntime


def add_agent_commands(subparsers):
    actions = subparsers.add_parser(
        "agent", help="M2-A 独立认知任务，不执行研究闭环"
    ).add_subparsers(dest="agent_action", required=True)
    run = actions.add_parser("run", help="运行一个 Generator 或 Critic 任务")
    run.add_argument("--role", choices=["generator", "critic"], required=True)
    run.add_argument("--input", required=True)
    run.add_argument("--config")
    run.add_argument("--ledger", default="agent_tasks.sqlite3")
    run.add_argument("--output")
    show = actions.add_parser("show", help="只读 Agent 任务审计摘要")
    show.add_argument("--task-id", required=True)
    show.add_argument("--ledger", default="agent_tasks.sqlite3")
    show.add_argument("--output")
    for name in ("probe", "smoke"):
        command = actions.add_parser(name, help="真实 CLI 安全预检；隔离未验证则不运行研究")
        command.add_argument("--backend", choices=["codex", "claude_code", "all"], default="all")
        command.add_argument("--output")


def run_agent_command(args):
    from ..observability.agent_summary import agent_summary, write_agent_json

    ledger = None
    try:
        if args.agent_action in {"probe", "smoke"}:
            backends = ["codex", "claude_code"] if args.backend == "all" else [args.backend]
            report = {
                "schema_version": 1,
                "research_calls": 0,
                "backends": [probe_cli(backend) for backend in backends],
            }
            code = 2 if args.agent_action == "smoke" else 0
        elif args.agent_action == "show":
            report, code = agent_summary(args.ledger, args.task_id), 0
        else:
            with Path(args.input).open("rb") as handle:
                document = strict_json(handle.read(65537), 65536)
            object_fields(
                document,
                (
                    "schema_version",
                    "task_id",
                    "subject",
                    "context_version",
                    "sources",
                    "resource_limits",
                ),
            )
            if type(document["schema_version"]) is not int or document["schema_version"] != 1:
                raise ValueError("TASK_INPUT_VERSION_UNSUPPORTED")
            object_fields(document["subject"], ("signal_id", "signal_version"))
            object_fields(document["resource_limits"], ResourceLimits.__dataclass_fields__)
            context = ContextBuilder().build(
                Role(args.role),
                Subject(**document["subject"]),
                document["sources"],
                version=document["context_version"],
            )
            task = create_task(
                context,
                task_id=document["task_id"],
                limits=ResourceLimits(**document["resource_limits"]),
            )
            routing = load_backend_config(args.config)
            ledger = AgentLedger(args.ledger)
            ledger.initialize()
            outcome = asyncio.run(
                AgentRuntime(ledger, foundation_registry(), routing).execute(task, context)
            )
            report = {
                "schema_version": 1,
                "outcome": asdict(outcome),
                "audit_summary": agent_summary(args.ledger, task.task_id),
            }
            code = 0 if outcome.execution_status == "SUCCEEDED" else 2
        if args.output:
            write_agent_json(args.output, report, getattr(args, "ledger", "agent_tasks.sqlite3"))
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        return code
    except (ValueError, TypeError, PermissionError, OSError, sqlite3.Error):
        print(
            json.dumps(
                {
                    "error": "AGENT_COMMAND_FAILED",
                    "message_zh": "任务输入、版本、权限、配置或账本不符合约束；原始错误未导出。",
                }
            )
        )
        return 1
    finally:
        if ledger is not None:
            ledger.close()
