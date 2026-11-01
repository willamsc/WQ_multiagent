"""Bounded rolling batches over independent M1 jobs. No new research strategy."""

import asyncio
import os
from dataclasses import replace
from pathlib import Path

from ..domain import AlphaCandidate, Signal
from ..validation import validate
from ..workflow import Controller
from .transport import WQError


def credentials(path=".env.wq"):
    values = {}
    file = Path(path)
    if file.exists():
        for line in file.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if not sep or key.strip() not in {"WQ_USERNAME", "WQ_PASSWORD"}:
                raise WQError("WQ_INVALID_CREDENTIAL_FILE")
            value = value.strip()
            # dotenv-style outer quotes delimit the value, not the Basic credentials.
            # Do not unescape, interpolate variables or strip whitespace inside quotes:
            # those transformations can silently alter a real password.
            if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
                value = value[1:-1]
            values[key.strip()] = value
    username = os.getenv("WQ_USERNAME") or values.get("WQ_USERNAME")
    password = os.getenv("WQ_PASSWORD") or values.get("WQ_PASSWORD")
    if not username or not password:
        raise WQError("WQ_CREDENTIALS_REQUIRED")
    return username, password


def prepare(config, expressions, max_simulations):
    if (
        type(max_simulations) is not int
        or not 1 <= max_simulations <= 10000
        or not isinstance(expressions, list)
        or not expressions
        or len(expressions) > max_simulations
    ):
        raise ValueError("explicit total simulation cap must cover the entire nonempty input")
    prepared = []
    for expression in expressions:
        if not isinstance(expression, str) or len(expression) > 16000:
            raise ValueError("invalid expression")
        item = replace(config, expression=expression)
        # Field declarations are taken from the AST, never guessed from all allowed metadata.
        import ast

        tree = ast.parse(expression, mode="eval")
        fields = tuple(
            sorted(
                {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id in item.fields}
            )
        )
        candidate = AlphaCandidate(
            expression, Signal("人工提供的候选；尚未证明有效", fields, "manual WQ simulation input")
        )
        validate(candidate, item)
        prepared.append((item, candidate))
    return prepared


async def run_batch(repo, adapter, prepared, concurrent_batches=1, trace=None):
    if type(concurrent_batches) is not int or not 1 <= concurrent_batches <= 32:
        raise ValueError("concurrent_batches must be 1..32; verify actual account quota")
    batch_size = adapter.batch_size
    # A window is replenished when a whole group finishes; other groups keep running.
    groups = iter([prepared[i : i + batch_size] for i in range(0, len(prepared), batch_size)])
    reports = []
    halted = False

    async def run_one(config, candidate):
        run_id = repo.create_run(config)
        with repo.transaction():
            repo.db.execute("UPDATE research_runs SET phase='M1.5' WHERE id=?", (run_id,))
        controller = Controller(repo, adapter, trace=trace)
        return await controller.run_one_candidate(run_id, candidate, config)

    async def worker():
        nonlocal halted
        for group in groups:
            if halted:
                return
            # Controller transactions do not span await; one event-loop-owned connection.
            results = await asyncio.gather(
                *(run_one(c, a) for c, a in group), return_exceptions=True
            )
            for result in results:
                if isinstance(result, BaseException):
                    halted = True
                    raise WQError("WQ_WORKFLOW_INTERRUPTED_INSPECT_DATABASE") from result
                reports.append(result)
                if any(job["status"] == "UNKNOWN" for job in result["jobs"]):
                    # Acceptance/pending remote capacity is unresolved. Do not fill that slot.
                    halted = True

    workers = [
        asyncio.create_task(worker())
        for _ in range(min(concurrent_batches, (len(prepared) + batch_size - 1) // batch_size))
    ]
    # Drain every admitted group even when a local worker fails; never abandon peers on first error.
    results = await asyncio.gather(*workers, return_exceptions=True)
    if any(isinstance(r, BaseException) for r in results):
        raise WQError("WQ_WORKFLOW_INTERRUPTED_INSPECT_DATABASE")
    return reports
