"""Standard-library HTTPS session. No redirects, automatic POST retry or cookie files."""

import base64
import json
from dataclasses import dataclass
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

BASE = "https://api.worldquantbrain.com"


class WQError(ValueError):
    """Only stable codes; never include server bodies, credentials or cookie values."""


def checked_url(value, collection):
    url = BASE + value if value.startswith("/") and not value.startswith("//") else value
    p = urlsplit(url)
    prefix = "/" + collection + "/"
    identifier = p.path[len(prefix) :] if p.path.startswith(prefix) else ""
    if (
        p.scheme != "https"
        or p.netloc != "api.worldquantbrain.com"
        or p.query
        or p.fragment
        or not identifier
        or any(not (c.isascii() and (c.isalnum() or c in "_-")) for c in identifier)
    ):
        raise WQError("WQ_INVALID_REMOTE_REFERENCE")
    return url


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict
    body: object = None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTPTransport:
    def __init__(self, timeout=30):
        self.timeout = timeout
        self.opener = build_opener(NoRedirect(), HTTPCookieProcessor(CookieJar()))

    def request(self, method, url, payload=None, credentials=None):
        # Called serially by the adapter. Credentials only go to the fixed auth endpoint.
        if not url.startswith(BASE + "/"):
            raise WQError("WQ_INVALID_ORIGIN")
        headers = {"Accept": "application/json"}
        if credentials is not None:
            if method != "POST" or url != BASE + "/authentication":
                raise WQError("WQ_AUTH_SCOPE")
            encoded = base64.b64encode(":".join(credentials).encode()).decode()
            headers["Authorization"] = "Basic " + encoded
        data = None
        if payload is not None:
            data = json.dumps(payload, allow_nan=False).encode()
            headers["Content-Type"] = "application/json"
        req = Request(url, data=data, headers=headers, method=method)
        try:
            try:
                response = self.opener.open(req, timeout=self.timeout)
            except HTTPError as exc:
                response = exc
            with response:
                # Bodies are not logged or stored verbatim.
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise WQError("WQ_RESPONSE_TOO_LARGE")
                try:
                    body = json.loads(raw) if raw else None
                except (ValueError, UnicodeError):
                    body = None
                return Response(
                    response.code, {k.lower(): v for k, v in response.headers.items()}, body
                )
        except (URLError, OSError) as exc:
            raise WQError("WQ_TRANSPORT_UNAVAILABLE") from exc
