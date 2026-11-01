import asyncio
import json
import sqlite3
from pathlib import Path

from ..persistence import Repository
from .config import load_live_config
from .multi import WQMultiAdapter
from .runner import credentials, prepare, run_batch
from .transport import HTTPTransport, WQError


def add_wq_commands(sub):
    parser = sub.add_parser("wq", help="M1.5 人工触发的真实回测，不执行正式 Alpha 提交")
    actions = parser.add_subparsers(dest="action", required=True)
    for action in ("probe", "run", "inspect"):
        command = actions.add_parser(action)
        command.add_argument("--credentials-file", default=".env.wq")
        if action == "inspect":
            command.add_argument("--run-id", required=True)
        if action == "run":
            command.add_argument("--config", required=True)
            command.add_argument("--expressions", required=True, help="表达式 JSON 数组文件")
            command.add_argument("--max-simulations", type=int, required=True)
            command.add_argument("--batch-size", type=int, default=1)
            command.add_argument("--concurrent-batches", type=int, default=1)
            command.add_argument("--trace", choices=["off", "light", "full"], default="light")


def run_wq_command(args):
    async def execute():
        prepared = None
        original = None
        if args.action == "inspect":
            db = sqlite3.connect(Path(args.db).resolve().as_uri() + "?mode=ro", uri=True)
            db.row_factory = sqlite3.Row
            try:
                original = db.execute(
                    "SELECT j.remote_id,j.status AS job_status,r.status AS run_status "
                    "FROM sim_jobs j JOIN research_runs r ON r.id=j.run_id "
                    "WHERE r.id=? AND r.phase='M1.5'",
                    (args.run_id,),
                ).fetchone()
                if original is None or not original["remote_id"]:
                    raise WQError("WQ_ORIGINAL_RECEIPT_UNAVAILABLE")
            finally:
                db.close()
        if args.action == "run":
            prepared = prepare(
                load_live_config(args.config),
                json.loads(Path(args.expressions).read_text(encoding="utf-8")),
                args.max_simulations,
            )
            if not 1 <= args.concurrent_batches <= 32:
                raise ValueError("invalid concurrent_batches")
        adapter = WQMultiAdapter(
            HTTPTransport(), getattr(args, "batch_size", 64 if original else 1)
        )
        if original:
            adapter._receipt(original["remote_id"])
        await adapter.authenticate(*credentials(args.credentials_file))
        if args.action == "probe":
            return {"authenticated": True, "simulation_submitted": False}
        if original:
            status = await adapter.poll(original["remote_id"])
            result = (
                await adapter.fetch_result(original["remote_id"]) if status == "COMPLETED" else None
            )
            return {
                "inspection_only": True,
                "run_id": args.run_id,
                "local_run_status": original["run_status"],
                "local_job_status": original["job_status"],
                "remote_status": status,
                "metrics": result.metrics if result else None,
                "evidence_gaps": result.raw["evidence_gaps"] if result else [],
                "simulation_submitted": False,
                "database_modified": False,
            }
        repo = Repository(args.db)
        trace = None
        try:
            repo.initialize()
            if args.trace != "off":
                from ..observability.traces import TraceSink

                trace = TraceSink(args.db, args.trace)
            reports = await run_batch(repo, adapter, prepared, args.concurrent_batches, trace)
            return {
                "source": "wq-live-v1",
                "not_started_count": len(prepared) - len(reports),
                "runs": [
                    {
                        "run_id": r["id"],
                        "status": r["status"],
                        "used_budget": r["used_budget"],
                        "reserved_budget": r["reserved_budget"],
                    }
                    for r in reports
                ],
            }
        finally:
            if trace is not None:
                trace.flush()
            repo.close()

    try:
        result = asyncio.run(execute())
        print(json.dumps(result, ensure_ascii=False))
        return (
            0
            if args.action in {"probe", "inspect"}
            or all(r["status"] == "COMPLETED" for r in result["runs"])
            else 1
        )
    except WQError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    except (ValueError, OSError, TypeError, KeyError, sqlite3.Error):
        # Do not surface exception text, HTTP bodies, environment or credential-file contents.
        print(
            json.dumps(
                {"error": "WQ_COMMAND_FAILED", "hint": "检查配置、认证或已记录任务；禁止盲目重提"},
                ensure_ascii=False,
            )
        )
        return 1
