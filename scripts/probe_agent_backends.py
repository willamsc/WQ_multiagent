"""Read-only CLI preflight, not a model/research call. No auth files or raw output exported."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from alpha_platform.agent_interop.optional_cli import probe_cli
from alpha_platform.observability.agent_summary import write_agent_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("examples/agent_interop/backend_probe.json")
    )
    args = parser.parse_args()
    reports = [probe_cli("codex"), probe_cli("claude_code")]
    document = {
        "schema_version": 1,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "backends": reports,
        "dsh": "RESERVED_IMPLEMENTATION_UNCONFIRMED",
        "research_calls": 0,
        "limitation": "CLI installation/auth status is not evidence of enforced research isolation.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_agent_json(args.output, document, "agent_tasks.sqlite3")
    print(json.dumps(document, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
