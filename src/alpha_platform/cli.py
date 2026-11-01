import argparse
import asyncio
import json
import logging
import os
import sqlite3

from .agents import MockGenerator
from .config import load_config
from .persistence import Repository
from .simulation import MockSimulationAdapter
from .workflow import Controller


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {"event": record.getMessage(), "level": record.levelname}
        for key in ("run_id", "job_id", "error_class"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload)


async def execute(repo, config, trace=None):
    run_id = repo.create_run(config)
    controller = Controller(repo, MockSimulationAdapter(config.mock_scenario), trace=trace)
    try:
        with controller.trace_span("generator", run_id):
            candidate = await MockGenerator().generate(config)
        return await controller.run_one_candidate(run_id, candidate, config)
    except Exception:
        repo.finish(run_id, "FAILED", "INTERNAL: workflow interrupted; inspect persisted jobs")
        raise
    finally:
        if trace is not None:
            try:
                trace.flush()
            except Exception:
                logging.getLogger(__name__).warning("optional_trace_flush_failed")


def observe(args):
    from .observability.diagnostics import encode, write_artifact
    from .observability.html_report import render
    from .observability.queries import ObservatoryReader, RunNotFoundError

    reader = ObservatoryReader(args.db)
    try:
        try:
            snapshot = (
                reader.snapshot(args.run_id, args.max_events, for_replay=True)
                if args.action == "replay"
                else reader.snapshot(args.run_id, args.max_events)
            )
        except RunNotFoundError:
            print(json.dumps({"error": "RUN_NOT_FOUND"}))
            return 1
    finally:
        reader.close()
    if args.action == "replay":
        from .observability.replay import build_replay_bundle, encode_replay

        bundle = build_replay_bundle(snapshot, args.max_events, args.max_bytes)
        content = encode_replay(bundle)
        if args.format == "html":
            from .observability.replay_html import render_replay

            content = render_replay(bundle)
        write_artifact(args.output, content, args.db)
        print(json.dumps({"output": args.output, "run_id": args.run_id}))
        return 0
    snapshot["events"] = [
        e
        for e in snapshot["events"]
        if (not args.job_id or e["job_id"] == args.job_id)
        and (not args.candidate_id or e["candidate_id"] == args.candidate_id)
        and (not args.node or e["node"] == args.node)
        and (not args.errors or e["outcome"] in {"FAILED", "UNKNOWN", "REJECTED"})
    ]
    encoded = encode(snapshot, args.max_bytes)
    report = json.loads(encoded)
    if args.action in {"export", "report"}:
        write_artifact(args.output, render(report) if args.action == "report" else encoded, args.db)
        print(json.dumps({"output": args.output, "run_id": args.run_id}))
    elif args.action == "inspect":
        if args.source_ref not in report["evidence"]:
            raise ValueError("evidence reference not available in this bounded snapshot")
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "source_ref": args.source_ref,
                    **report["evidence"][args.source_ref],
                },
                indent=2,
            )
        )
    else:
        fields = {
            "show": ("run", "jobs", "anomalies", "evidence_gaps", "summary"),
            "timeline": ("events", "evidence", "evidence_gaps"),
            "graph": ("nodes", "edges", "evidence", "evidence_gaps"),
        }[args.action]
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "run_id": args.run_id,
                    **{key: report[key] for key in fields},
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="M1 offline alpha research")
    parser.add_argument("--db", default=os.getenv("ALPHA_DB", "research.sqlite3"))
    sub = parser.add_subparsers(dest="command", required=True)
    from .agent_interop.cli import add_agent_commands

    add_agent_commands(sub)
    from .research_loop.cli import add_study_commands, add_study_read_options

    add_study_commands(sub)
    from .wq.cli import add_wq_commands

    add_wq_commands(sub)
    sub.add_parser("init-db")
    research = sub.add_parser("research").add_subparsers(dest="action", required=True)
    run = research.add_parser("run")
    run.add_argument("--mode", choices=["mock"], default=os.getenv("MODE", "mock"))
    run.add_argument("--config")
    run.add_argument(
        "--trace",
        choices=["off", "light", "full"],
        default="off",
        help="Optional diagnostics; business audit is always enabled (default: off)",
    )
    show = research.add_parser("show")
    show.add_argument("run_id")
    observer = sub.add_parser("observe", help="Read-only run diagnostics; never starts a server")
    actions = observer.add_subparsers(dest="action", required=True)
    add_study_read_options(actions.add_parser("study", help="只读 M2-B 研究、版本、审查和证据"))
    for action in ("show", "timeline", "graph", "inspect", "export", "report", "replay"):
        command = actions.add_parser(action)
        command.add_argument("--run-id", required=True)
        command.add_argument("--max-events", type=int, default=200)
        command.add_argument("--max-bytes", type=int, default=262144)
        if action != "replay":
            command.add_argument("--job-id")
            command.add_argument("--candidate-id")
            command.add_argument("--node")
            command.add_argument("--errors", action="store_true")
        command.add_argument(
            "--format",
            choices=["html", "json"] if action == "replay" else ["json"],
            default="html" if action == "replay" else "json",
            help="历史回放输出格式（默认 HTML）"
            if action == "replay"
            else "Diagnostic serialization (report generates HTML)",
        )
        if action in {"export", "report", "replay"}:
            command.add_argument("--output", required=True)
        if action == "inspect":
            command.add_argument("--source-ref", required=True)
    args = parser.parse_args(argv)
    if args.command == "study" or (args.command == "observe" and args.action == "study"):
        from .research_loop.cli import run_study_command

        return run_study_command(args)
    if args.command == "wq":
        from .wq.cli import run_wq_command

        return run_wq_command(args)
    if args.command == "agent":
        from .agent_interop.cli import run_agent_command

        return run_agent_command(args)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(handlers=[handler])
    repo = None
    try:
        if getattr(args, "mode", "mock") != "mock":
            raise ValueError("M1 supports mock mode only")
        if args.command == "observe":
            return observe(args)
        repo = Repository(args.db)
        repo.initialize()
        if args.command == "init-db":
            print(json.dumps({"database": args.db, "schema_version": 1}))
        elif args.action == "show":
            print(json.dumps(repo.report(args.run_id), indent=2))
        else:
            config = load_config(args.config)
            trace = None
            if args.trace != "off":
                from .observability.traces import TraceSink

                trace = TraceSink(args.db, args.trace)
            print(json.dumps(asyncio.run(execute(repo, config, trace)), indent=2))
        return 0
    except (OSError, ValueError, TypeError, sqlite3.Error):
        logging.getLogger(__name__).error("command_failed")
        print(
            json.dumps(
                {
                    "error": "Command failed; check configuration, run ID, output limits and database access."
                }
            )
        )
        return 1
    finally:
        if repo is not None:
            repo.close()


if __name__ == "__main__":
    raise SystemExit(main())
