import asyncio
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from ..domain import SimulationResult, SubmissionReceipt
from ..simulation import RateLimited, RemoteUnknown, validate_result
from .transport import BASE, WQError, checked_url


def retry_seconds(headers, default=1):
    value = headers.get("retry-after")
    if value is None:
        return default
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            raise WQError("WQ_INVALID_RETRY_AFTER") from None
    if not math.isfinite(seconds) or seconds < 0:
        raise WQError("WQ_INVALID_RETRY_AFTER")
    if seconds > 300:
        raise WQError("WQ_RETRY_WINDOW_EXCEEDED")
    return max(0.1, seconds)


class WQSimulationAdapter:
    """One receipt per Alpha. In-flight simulations overlap; session I/O is serialized.

    A timeout or malformed submission response NEVER triggers an automatic re-POST.
    Only an explicit HTTP 429 is treated as rejected and safely retryable.
    """

    def __init__(self, transport, sleep=asyncio.sleep):
        self.transport = transport
        self.sleep = sleep
        self.lock = asyncio.Lock()
        self.authenticated = False

    async def _request(self, method, url, payload=None, credentials=None):
        async with self.lock:
            response = await asyncio.to_thread(
                self.transport.request, method, url, payload, credentials
            )
            if response.status == 429:
                await self.sleep(retry_seconds(response.headers))
            return response

    async def authenticate(self, username, password):
        if not username or not password or ":" in username:
            raise WQError("WQ_CREDENTIALS_REQUIRED")
        response = await self._request(
            "POST", BASE + "/authentication", credentials=(username, password)
        )
        if response.status not in {200, 201}:
            if response.headers.get("www-authenticate", "").lower() == "persona":
                raise WQError("WQ_AUTH_INTERACTIVE_REQUIRED")
            raise WQError(f"WQ_AUTH_HTTP_{response.status}")
        self.authenticated = True

    async def submit(self, candidate):
        if not self.authenticated:
            raise WQError("WQ_AUTH_REQUIRED")
        response = await self._request(
            "POST",
            BASE + "/simulations",
            {
                "type": "REGULAR",
                "regular": candidate.expression,
                "settings": candidate.settings,
            },
        )
        if response.status == 429:
            # Gate the entire session, not only the rejected worker.
            raise RateLimited()
        if response.status != 201:
            raise RemoteUnknown("WQ_SUBMISSION_NOT_CONFIRMED")
        try:
            remote = checked_url(response.headers.get("location", ""), "simulations")
        except WQError:
            raise RemoteUnknown("WQ_MISSING_OR_UNSAFE_LOCATION") from None
        return SubmissionReceipt(remote)

    async def _get(self, url):
        for attempt in range(3):
            try:
                response = await self._request("GET", url)
            except WQError as exc:
                if str(exc) != "WQ_TRANSPORT_UNAVAILABLE" or attempt == 2:
                    raise
                await self.sleep(1)
                continue
            if response.status != 429:
                break
        else:
            return None
        if response.status != 200 or not isinstance(response.body, dict):
            raise WQError("WQ_INVALID_GET_RESPONSE")
        return response

    async def poll(self, remote_id):
        response = await self._get(checked_url(remote_id, "simulations"))
        if response is None:
            return "RUNNING"
        status = response.body.get("status")
        if status in {"ERROR", "FAILED"}:
            return "FAILED"
        if status == "COMPLETE":
            if not isinstance(response.body.get("alpha"), str):
                raise WQError("WQ_ALPHA_REFERENCE_MISSING")
            checked_url(BASE + "/alphas/" + response.body["alpha"], "alphas")
            return "COMPLETED"
        if status not in {"PENDING", "RUNNING", "SIMULATING"}:
            raise WQError("WQ_UNSUPPORTED_SIMULATION_STATUS")
        await self.sleep(retry_seconds(response.headers))
        return "RUNNING"

    async def fetch_result(self, remote_id):
        response = await self._get(checked_url(remote_id, "simulations"))
        if response is None or response.body.get("status") != "COMPLETE":
            raise WQError("WQ_RESULT_NOT_READY")
        alpha_id = response.body.get("alpha")
        if not isinstance(alpha_id, str):
            raise WQError("WQ_ALPHA_REFERENCE_MISSING")
        alpha = await self._get(checked_url(BASE + "/alphas/" + alpha_id, "alphas"))
        if alpha is None:
            raise WQError("WQ_METRICS_MISSING")
        return self.result_from_alpha(remote_id, alpha_id, alpha.body)

    @staticmethod
    def result_from_alpha(remote_id, alpha_id, body):
        if not isinstance(body.get("is"), dict):
            raise WQError("WQ_METRICS_MISSING")
        metrics = {
            k: body["is"][k]
            for k in (
                "sharpe",
                "fitness",
                "turnover",
                "returns",
                "drawdown",
                "margin",
                "longCount",
                "shortCount",
            )
            if k in body["is"]
        }
        checks = body["is"].get("checks", [])
        if not isinstance(checks, list) or any(not isinstance(c, dict) for c in checks):
            raise WQError("WQ_INVALID_CHECKS")
        # Whitelist evidence. No raw account details, cookies, server errors or hidden fields.
        safe_checks = [
            {k: c[k] for k in ("name", "result", "value", "limit") if k in c} for c in checks
        ]
        result = SimulationResult(
            metrics,
            {"platform_checks": safe_checks},
            {
                "source": "wq-live-v1",
                "remote_id": remote_id,
                "alpha_id": alpha_id,
                "evidence_gap": "platform checks are returned evidence, not an Alpha submission approval",
                "evidence_gaps": ["WQ_TEST_PERIOD_NOT_ECHOED"]
                if "testPeriod" not in body.get("settings", {})
                else [],
            },
        )
        validate_result(result)
        return result
