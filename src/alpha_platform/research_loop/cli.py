import asyncio
import json
import sqlite3
from pathlib import Path

from ..agent_interop.config import load_backend_config
from ..agent_interop.contracts import Role, object_fields, strict_json
from ..agent_interop.ledger import AgentLedger
from ..agent_interop.optional_cli import foundation_registry
from ..agent_interop.runtime import AgentRuntime
from ..observability.agent_summary import write_agent_json
from ..observability.research_summary import research_summary
from .controller import ResearchController
from .mock import SCENARIOS, ResearchMockBackend
from .policy import LoopPolicy


def add_study_commands(sub):
    actions = sub.add_parser(
        "study", help="M2-B 主信号生成、审查及有限修复；不调用回测"
    ).add_subparsers(dest="study_action", required=True)
    run = actions.add_parser("run")
    run.add_argument("--input", required=True)
    run.add_argument("--config", help="固定 Generator/Critic 后端路由，不提供自动切换")
    run.add_argument("--study-id")
    run.add_argument("--scenario", choices=SCENARIOS, default="pass", help="仅作用于 Mock 后端")
    add_study_read_options(actions.add_parser("show"))
    run.add_argument("--ledger", default="agent_tasks.sqlite3")
    run.add_argument("--output")


def add_study_read_options(parser):
    parser.add_argument("--study-id", required=True)
    parser.add_argument("--ledger", default="agent_tasks.sqlite3")
    parser.add_argument("--output")


def run_study_command(args):
    ledger = None
    try:
        if getattr(args, "study_action", "show") == "run":
            with Path(args.input).open("rb") as handle:
                document = strict_json(handle.read(65537), 65536)
            object_fields(
                document, ("schema_version", "signal_id", "sources", "policy", "evidence_catalog")
            )
            if type(document["schema_version"]) is not int or document["schema_version"] != 1:
                raise ValueError("STUDY_INPUT_VERSION_UNSUPPORTED")
            policy = LoopPolicy(**document["policy"])
            registry = foundation_registry()
            registry.register("research_mock_generator", lambda: ResearchMockBackend(args.scenario))
            registry.register("research_mock_critic", lambda: ResearchMockBackend(args.scenario))
            routing = (
                load_backend_config(args.config)
                if args.config
                else {
                    Role.GENERATOR: "research_mock_generator",
                    Role.CRITIC: "research_mock_critic",
                }
            )
            ledger = AgentLedger(args.ledger)
            ledger.initialize()
            controller = ResearchController(AgentRuntime(ledger, registry, routing))
            study_id = asyncio.run(
                controller.run(
                    document["sources"],
                    signal_id=document["signal_id"],
                    study_id=args.study_id,
                    policy=policy,
                    evidence_catalog=document["evidence_catalog"],
                )
            )
        else:
            study_id = args.study_id
        report = research_summary(args.ledger, study_id)
        if args.output:
            write_agent_json(args.output, report, args.ledger)
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        return (
            0
            if getattr(args, "study_action", "show") != "run" or report["status"] == "APPROVED"
            else 2
        )
    except (ValueError, TypeError, PermissionError, OSError, sqlite3.Error):
        print(
            json.dumps(
                {
                    "error": "STUDY_COMMAND_FAILED",
                    "message_zh": "输入、权限、预算或账本不符合约束；请查看已有研究记录，未导出原始错误。",
                },
                ensure_ascii=False,
            )
        )
        return 1
    finally:
        if ledger:
            ledger.close()
