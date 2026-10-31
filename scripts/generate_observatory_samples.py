"""Create offline M1-O examples using only deterministic mock research."""

import argparse
import asyncio
import json
import sqlite3
from pathlib import Path

from alpha_platform.cli import execute
from alpha_platform.domain import ResearchConfig
from alpha_platform.observability.diagnostics import encode, write_artifact
from alpha_platform.observability.html_report import render
from alpha_platform.observability.queries import ObservatoryReader
from alpha_platform.observability.replay import build_replay_bundle, encode_replay
from alpha_platform.observability.replay_html import render_replay
from alpha_platform.observability.traces import TraceSink
from alpha_platform.persistence import Repository


def export_replays(root, manifest):
    path = root / "samples.sqlite3"
    reader = ObservatoryReader(path)
    try:
        for name, case in manifest.items():
            bundle = build_replay_bundle(reader.snapshot(case["run_id"], for_replay=True))
            case["replay_html"] = f"{name}-replay.html"
            case["replay_json"] = f"{name}-replay.json"
            write_artifact(root / case["replay_json"], encode_replay(bundle), path)
            write_artifact(root / case["replay_html"], render_replay(bundle), path)
    finally:
        reader.close()
    write_artifact(root / "manifest.json", json.dumps(manifest, indent=2), path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("examples/observatory"))
    parser.add_argument(
        "--replay-existing",
        action="store_true",
        help="Only read existing sample runs and add replay outputs; do not execute Mock",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "samples.sqlite3"
    if args.replay_existing:
        manifest = json.loads((args.output / "manifest.json").read_text(encoding="utf-8"))
        export_replays(args.output, manifest)
        print(json.dumps({"output": str(args.output), "replayed_existing_runs": len(manifest)}))
        return
    if path.exists():
        parser.error("sample database already exists; select another --output directory")
    repo = Repository(path)
    repo.initialize()
    manifest = {}
    cases = {
        "success": ResearchConfig(),
        "validation-rejected": ResearchConfig(expression="wrong(mock_value)"),
        "duplicate": ResearchConfig(),
        "budget-exhausted": ResearchConfig(universe="MOCK_BUDGET", budget_limit=0),
        "unknown": ResearchConfig(delay=2, mock_scenario="timeout"),
        "persistence-failure": ResearchConfig(delay=3),
    }
    original = repo.save_result

    def fail(*args):
        raise sqlite3.OperationalError("injected local persistence failure")

    try:
        for name, config in cases.items():
            repo.save_result = fail if name == "persistence-failure" else original
            run = asyncio.run(execute(repo, config, TraceSink(path, "full")))
            reader = ObservatoryReader(path)
            try:
                encoded = encode(reader.snapshot(run["id"]))
            finally:
                reader.close()
            write_artifact(args.output / f"{name}.json", encoded, path)
            write_artifact(args.output / f"{name}.html", render(json.loads(encoded)), path)
            manifest[name] = {
                "run_id": run["id"],
                "status": run["status"],
                "html": f"{name}.html",
                "json": f"{name}.json",
            }
        write_artifact(args.output / "manifest.json", json.dumps(manifest, indent=2), path)
        export_replays(args.output, manifest)
        print(json.dumps({"output": str(args.output), "runs": manifest}, indent=2))
    finally:
        repo.close()


if __name__ == "__main__":
    main()
