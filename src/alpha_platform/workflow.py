import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from time import perf_counter
from uuid import uuid4

from .domain import SimulationRequest
from .simulation import RateLimited, RemoteUnknown, validate_receipt, validate_result
from .validation import ValidationError, fingerprints, validate

LOG = logging.getLogger(__name__)


class Controller:
    def __init__(self, repository, adapter, trace=None):
        self.repo = repository
        self.adapter = adapter
        self.trace = trace
        self._trace_warned = False

    def _emit(self, *args, **kwargs):
        if self.trace is not None:
            try:
                self.trace.emit(*args, **kwargs)
            except Exception:
                if not self._trace_warned:
                    LOG.warning("optional_trace_emit_failed")
                    self._trace_warned = True

    @contextmanager
    def trace_span(self, node, run_id, job_id=None, candidate_id=None, **details):
        started = perf_counter()
        self._emit(run_id, node, "NODE_STARTED", "RUNNING", details, job_id, candidate_id)
        try:
            yield details
        except Exception as exc:
            self._emit(
                run_id,
                node,
                "NODE_FAILED",
                "FAILED",
                {
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                    "error_class": type(exc).__name__,
                    "reason_code": "NODE_EXCEPTION",
                },
                job_id,
                candidate_id,
            )
            raise
        else:
            self._emit(
                run_id,
                node,
                "NODE_COMPLETED",
                "COMPLETED",
                {
                    **details,
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                },
                job_id,
                candidate_id,
            )

    async def run_one_candidate(self, run_id, candidate, config):
        repo = self.repo
        job_id, alpha_id = str(uuid4()), str(uuid4())
        with self.trace_span("run_admission", run_id), repo.transaction():
            run = repo.db.execute("SELECT * FROM research_runs WHERE id=?", (run_id,)).fetchone()
            if run is None or run["status"] != "CREATED" or run["config_json"] != config.json():
                raise ValueError("run must be CREATED with matching configuration")
            repo.event(run_id, "GENERATED", asdict(candidate))
            try:
                with self.trace_span("validator", run_id, expression=candidate.expression):
                    canonical, fingerprint = validate(candidate, config)
            except ValidationError as exc:
                repo.set_run_status(run_id, "REJECTED", f"VALIDATION: {exc}")
                return repo.report(run_id)
            repo.event(run_id, "VALIDATED", {"fingerprint": fingerprint})
            identities = fingerprints(canonical, config)
            duplicate = repo.db.execute(
                f"SELECT id FROM alphas WHERE fingerprint IN ({','.join('?' for _ in identities)})",
                identities,
            ).fetchone()
            if duplicate:
                repo.set_run_status(run_id, "REJECTED", "DUPLICATE")
                repo.event(run_id, "DUPLICATE", {"alpha_id": duplicate["id"]})
                return repo.report(run_id)
            available = repo.db.execute(
                "UPDATE research_runs SET reserved_budget=reserved_budget+1,status='RUNNING' "
                "WHERE id=? AND status='CREATED' AND used_budget+reserved_budget<budget_limit",
                (run_id,),
            )
            if available.rowcount != 1:
                repo.set_run_status(run_id, "REJECTED", "BUDGET_EXHAUSTED")
                repo.event(run_id, "BUDGET_EXHAUSTED")
                return repo.report(run_id)
            repo.db.execute(
                "INSERT INTO alphas VALUES(?,?,?,?,?,?)",
                (
                    alpha_id,
                    candidate.expression,
                    canonical,
                    json.dumps(config.settings(), sort_keys=True),
                    fingerprint,
                    json.dumps(asdict(candidate.signal)),
                ),
            )
            repo.db.execute(
                "INSERT INTO sim_jobs(id,run_id,alpha_id,status) VALUES(?,?,?,?)",
                (job_id, run_id, alpha_id, "PENDING"),
            )
            repo.event(run_id, "PENDING", job_id=job_id)
            repo.transition(job_id, "RESERVED")
            repo.transition(job_id, "SUBMITTING")
        receipt = None
        for _ in range(config.max_submit_attempts):
            attempt_id = str(uuid4())
            with repo.transaction():
                repo.db.execute(
                    "INSERT INTO job_attempts(id,job_id,outcome) VALUES(?,?,?)",
                    (attempt_id, job_id, "INTENT"),
                )
                repo.db.execute(
                    "UPDATE sim_jobs SET attempt_count=attempt_count+1 WHERE id=?", (job_id,)
                )
                repo.event(run_id, "SUBMIT_INTENT", job_id=job_id)
            try:
                with self.trace_span(
                    "submit", run_id, job_id, alpha_id, attempt_number=_ + 1, expression=canonical
                ):
                    receipt = await self.adapter.submit(
                        SimulationRequest(canonical, config.settings())
                    )
                    validate_receipt(receipt)
            except RateLimited:
                with repo.transaction():
                    repo.db.execute(
                        "UPDATE job_attempts SET outcome='RATE_LIMIT' WHERE id=?", (attempt_id,)
                    )
                    repo.event(run_id, "RATE_LIMIT", job_id=job_id)
                continue
            except (RemoteUnknown, TimeoutError, OSError):
                with repo.transaction():
                    repo.db.execute(
                        "UPDATE job_attempts SET outcome='REMOTE_UNKNOWN' WHERE id=?", (attempt_id,)
                    )
                    repo.transition(job_id, "UNKNOWN")
                repo.finish(run_id, "FAILED", "REMOTE_UNKNOWN: do not resubmit automatically")
                return repo.report(run_id)
            except Exception:
                # An adapter error after an intent can have an ambiguous side effect.
                with repo.transaction():
                    repo.db.execute(
                        "UPDATE job_attempts SET outcome='INTERNAL' WHERE id=?", (attempt_id,)
                    )
                    repo.transition(job_id, "UNKNOWN")
                LOG.error("adapter_submit_failed", extra={"run_id": run_id})
                repo.finish(run_id, "FAILED", "INTERNAL: submission outcome unknown")
                return repo.report(run_id)
            with repo.transaction():
                repo.db.execute(
                    "UPDATE job_attempts SET outcome='ACCEPTED',remote_ref=? WHERE id=?",
                    (receipt.remote_id, attempt_id),
                )
                repo.db.execute(
                    "UPDATE research_runs SET reserved_budget=reserved_budget-1,"
                    "used_budget=used_budget+1 WHERE id=?",
                    (run_id,),
                )
                repo.transition(job_id, "SUBMITTED", receipt.remote_id)
                repo.transition(job_id, "POLLING")
            break
        if receipt is None:
            with repo.transaction():
                repo.transition(job_id, "FAILED")
                repo.db.execute(
                    "UPDATE research_runs SET reserved_budget=reserved_budget-1 WHERE id=?",
                    (run_id,),
                )
            repo.finish(run_id, "FAILED", "RATE_LIMIT: attempts exhausted")
            return repo.report(run_id)
        try:
            for _ in range(config.max_polls):
                with repo.transaction():
                    repo.event(run_id, "POLL_INTENT", job_id=job_id)
                with self.trace_span(
                    "poll", run_id, job_id, alpha_id, remote_ref_present=True
                ) as detail:
                    status = await self.adapter.poll(receipt.remote_id)
                    detail["status"] = (
                        status if status in {"RUNNING", "COMPLETED", "FAILED"} else "INVALID"
                    )
                if status == "FAILED":
                    with repo.transaction():
                        repo.transition(job_id, "FAILED")
                    repo.finish(run_id, "FAILED", "REMOTE_FAILED")
                    return repo.report(run_id)
                if status == "COMPLETED":
                    with repo.transaction():
                        repo.event(run_id, "FETCH_INTENT", job_id=job_id)
                    with self.trace_span("poll", run_id, job_id, alpha_id, remote_ref_present=True):
                        result = await self.adapter.fetch_result(receipt.remote_id)
                        validate_result(result)
                    break
                if status != "RUNNING":
                    raise RemoteUnknown()
            else:
                raise TimeoutError()
        except Exception:
            with repo.transaction():
                repo.transition(job_id, "UNKNOWN")
            repo.finish(run_id, "FAILED", "REMOTE_UNKNOWN: polling/result unavailable")
            return repo.report(run_id)
        with self.trace_span("metrics_gate", run_id, job_id, alpha_id) as detail:
            gate = "PASS" if result.metrics["sharpe"] >= config.min_sharpe else "FAIL"
            detail["gate"] = gate
        # Retry only local persistence; never repeat submit or charge budget twice.
        for attempt in range(2):
            try:
                with self.trace_span("result_persist", run_id, job_id, alpha_id):
                    repo.save_result(job_id, result, gate)
                break
            except sqlite3.Error:
                LOG.error("result_persistence_failed", extra={"run_id": run_id, "job_id": job_id})
                if attempt == 1:
                    repo.finish(run_id, "FAILED", "INTERNAL: result persistence failed")
                    return repo.report(run_id)
        repo.finish(run_id, "COMPLETED", f"MOCK_GATE_{gate}; not a platform submission check")
        return repo.report(run_id)
