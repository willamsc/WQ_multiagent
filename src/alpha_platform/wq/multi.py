"""Coalesce submissions into multi-simulations; never infer child identity from order."""

import ast
import asyncio
import json

from ..domain import SubmissionReceipt
from ..simulation import RateLimited, RemoteUnknown
from .adapter import WQSimulationAdapter, retry_seconds
from .transport import BASE, WQError, checked_url


def expression_identity(source):
    try:
        return ast.dump(ast.parse(source, mode="eval"), include_attributes=False)
    except (ValueError, SyntaxError, TypeError, RecursionError):
        raise WQError("WQ_CHILD_EXPRESSION_INVALID") from None


def settings_match(requested, returned):
    """WQ omits testPeriod in both simulation and Alpha details (live verified).

    Missing is not proof of equality; result evidence explicitly reports this gap.
    A returned testPeriod must still match, as must every other requested setting.
    """
    return isinstance(returned, dict) and all(
        (k == "testPeriod" and k not in returned) or (k in returned and returned[k] == v)
        for k, v in requested.items()
    )


class WQMultiAdapter(WQSimulationAdapter):
    """The runner bounds outstanding parents; this adapter batches pending admissions.

    Receipts embed the parent reference and requested expression/settings so a fresh
    adapter can inspect the original task without an in-memory index or re-submit.
    Singleton tails use the single-simulation endpoint shape.
    """

    def __init__(self, transport, batch_size=8, **kwargs):
        super().__init__(transport, **kwargs)
        if type(batch_size) is not int or not 1 <= batch_size <= 64:
            raise ValueError("batch_size must be 1..64; actual account limits require verification")
        self.batch_size = batch_size
        self.pending = []
        self.flush_task = None
        self.parent_locks = {}
        self.resolved = {}
        self.next_poll = {}
        self.child_results = {}

    async def submit(self, candidate):
        if not self.authenticated:
            raise WQError("WQ_AUTH_REQUIRED")
        future = asyncio.get_running_loop().create_future()
        self.pending.append((candidate, future))
        if self.flush_task is None or self.flush_task.done():
            self.flush_task = asyncio.create_task(self._flush())
        return await asyncio.shield(future)

    async def _flush(self):
        # Collect admissions without waiting for a full batch (including duplicate/rejected tails).
        await asyncio.sleep(0.01)
        while self.pending:
            group, self.pending = self.pending[: self.batch_size], self.pending[self.batch_size :]
            try:
                payloads = [
                    {"type": "REGULAR", "regular": r.expression, "settings": r.settings}
                    for r, _ in group
                ]
                response = await self._request(
                    "POST", BASE + "/simulations", payloads if len(group) > 1 else payloads[0]
                )
                if response.status == 429:
                    raise RateLimited()
                if response.status != 201:
                    raise RemoteUnknown("WQ_MULTI_SUBMISSION_NOT_CONFIRMED")
                parent = checked_url(response.headers.get("location", ""), "simulations")
                for request, future in group:
                    receipt = SubmissionReceipt(
                        json.dumps(
                            {
                                "version": 1,
                                "parent": parent,
                                "multi": len(group) > 1,
                                "expression": request.expression,
                                "settings": request.settings,
                            },
                            sort_keys=True,
                        )
                    )
                    if not future.done():
                        future.set_result(receipt)
            except Exception as exc:
                for _, future in group:
                    if not future.done():
                        future.set_exception(exc)

    def _receipt(self, remote_id):
        try:
            data = json.loads(remote_id)
            if (
                not isinstance(data, dict)
                or data.get("version") != 1
                or type(data.get("multi")) is not bool
                or not isinstance(data.get("settings"), dict)
                or not isinstance(data.get("expression"), str)
            ):
                raise ValueError()
            checked_url(data["parent"], "simulations")
            return data
        except (ValueError, KeyError, TypeError):
            raise WQError("WQ_INVALID_MULTI_RECEIPT") from None

    async def _resolve(self, data):
        parent = data["parent"]
        async with self.parent_locks.setdefault(parent, asyncio.Lock()):
            if parent in self.resolved:
                return self.resolved[parent]
            loop = asyncio.get_running_loop()
            if loop.time() < self.next_poll.get(parent, 0):
                await self.sleep(self.next_poll[parent] - loop.time())
                return "RUNNING", []
            response = await self._get(parent)
            if response is None:
                return "RUNNING", []
            status = response.body.get("status")
            if status in {"ERROR", "FAILED"}:
                self.resolved[parent] = ("FAILED", [])
                return self.resolved[parent]
            if status != "COMPLETE":
                if status not in {"PENDING", "RUNNING", "SIMULATING"}:
                    raise WQError("WQ_UNSUPPORTED_MULTI_STATUS")
                self.next_poll[parent] = loop.time() + retry_seconds(response.headers)
                return "RUNNING", []
            children = response.body.get("children")
            if not isinstance(children, list) or not children or len(children) > self.batch_size:
                raise WQError("WQ_INVALID_CHILDREN")
            if any(not isinstance(c, str) for c in children) or len(set(children)) != len(children):
                raise WQError("WQ_INVALID_CHILDREN")
            records = []
            for child in children:
                child_url = checked_url(BASE + "/simulations/" + child, "simulations")
                result = await self._get(child_url)
                if result is None or result.body.get("status") != "COMPLETE":
                    # A failed child without an expression cannot safely be assigned to an Alpha.
                    raise WQError("WQ_CHILD_IDENTITY_UNAVAILABLE")
                alpha_id = result.body.get("alpha")
                if not isinstance(alpha_id, str):
                    raise WQError("WQ_CHILD_IDENTITY_UNAVAILABLE")
                alpha = await self._get(checked_url(BASE + "/alphas/" + alpha_id, "alphas"))
                if (
                    alpha is None
                    or not isinstance(alpha.body.get("regular"), dict)
                    or not isinstance(alpha.body.get("settings"), dict)
                ):
                    raise WQError("WQ_CHILD_IDENTITY_UNAVAILABLE")
                records.append(
                    (
                        expression_identity(alpha.body["regular"].get("code")),
                        alpha.body["settings"],
                        child_url,
                    )
                )
                self.child_results[child_url] = self.result_from_alpha(
                    child_url, alpha_id, alpha.body
                )
            self.resolved[parent] = ("COMPLETED", records)
            return self.resolved[parent]

    async def _child(self, data):
        status, records = await self._resolve(data)
        if status != "COMPLETED":
            return status, None
        matches = [
            url
            for expression, settings, url in records
            if expression == expression_identity(data["expression"])
            and settings_match(data["settings"], settings)
        ]
        if len(matches) != 1:
            raise WQError("WQ_CHILD_MAPPING_AMBIGUOUS")
        return status, matches[0]

    async def poll(self, remote_id):
        data = self._receipt(remote_id)
        if not data["multi"]:
            return await super().poll(data["parent"])
        status, _ = await self._child(data)
        return status

    async def fetch_result(self, remote_id):
        data = self._receipt(remote_id)
        if not data["multi"]:
            response = await self._get(data["parent"])
            if response is None or response.body.get("status") != "COMPLETE":
                raise WQError("WQ_RESULT_NOT_READY")
            alpha_id = response.body.get("alpha")
            if not isinstance(alpha_id, str):
                raise WQError("WQ_ALPHA_REFERENCE_MISSING")
            alpha = await self._get(checked_url(BASE + "/alphas/" + alpha_id, "alphas"))
            if (
                alpha is None
                or not isinstance(alpha.body.get("regular"), dict)
                or not isinstance(alpha.body.get("settings"), dict)
                or expression_identity(alpha.body["regular"].get("code"))
                != expression_identity(data["expression"])
                or not settings_match(data["settings"], alpha.body["settings"])
            ):
                raise WQError("WQ_SINGLE_IDENTITY_MISMATCH")
            return self.result_from_alpha(data["parent"], alpha_id, alpha.body)
        status, child = await self._child(data)
        if status != "COMPLETED":
            raise WQError("WQ_MULTI_RESULT_NOT_READY")
        return self.child_results[child]
