"""M2-A two independent Mock tasks, no research approval/repair/simulation loop."""

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from alpha_platform.agent_interop.config import load_backend_config
from alpha_platform.agent_interop.context import ContextBuilder, create_task
from alpha_platform.agent_interop.contracts import Role, Subject
from alpha_platform.agent_interop.ledger import AgentLedger
from alpha_platform.agent_interop.optional_cli import foundation_registry
from alpha_platform.agent_interop.roles import CriticRole, GeneratorRole
from alpha_platform.agent_interop.runtime import AgentRuntime
from alpha_platform.observability.agent_summary import agent_summary, write_agent_json


async def demonstrate(ledger):
    registry = foundation_registry()
    runtime = AgentRuntime(ledger, registry, load_backend_config(None))
    sources = json.loads(Path("configs/agent_generator_mock.json").read_text(encoding="utf-8"))[
        "sources"
    ]
    generator_context = ContextBuilder().build(
        Role.GENERATOR, Subject("MOCK-SIGNAL-001", 1), sources
    )
    generated = await GeneratorRole(runtime).generate(
        create_task(generator_context), generator_context
    )
    # Only the formal result is handed over. No session, tools, drafts or attempt history.
    sources = {**sources, "proposal": asdict(generated.result)}
    critic_context = ContextBuilder().build(Role.CRITIC, Subject("MOCK-SIGNAL-001", 1), sources)
    reviewed = await CriticRole(runtime).review(create_task(critic_context), critic_context)
    return generated, reviewed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("examples/agent_interop"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "demo.sqlite3"
    ledger = AgentLedger(path)
    ledger.initialize()
    try:
        generated, reviewed = asyncio.run(demonstrate(ledger))
        summary = {
            "schema_version": 1,
            "mock_only": True,
            "simulation_calls": 0,
            "generator": agent_summary(path, generated.task_id),
            "critic": agent_summary(path, reviewed.task_id),
        }
        write_agent_json(args.output / "demo-summary.json", summary, path)
        print(
            json.dumps(
                {
                    "generator_status": generated.execution_status,
                    "critic_execution_status": reviewed.execution_status,
                    "critic_verdict": reviewed.result.verdict,
                    "output": str(args.output / "demo-summary.json"),
                },
                ensure_ascii=False,
            )
        )
    finally:
        ledger.close()


if __name__ == "__main__":
    main()
