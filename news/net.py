"""HTTP client: timeouts, retries, a plain User-Agent, and conditional requests."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "baif-news-bot/1.0 (+https://github.com/Beneficial-AI-Foundation/ai-formalization-news)"
TIMEOUT_SECONDS = 20
RETRIES = 2


class FetchError(RuntimeError):
    """A source could not be fetched or parsed."""

    def __init__(self, message: str, http_status: int | None = None):
        super().__init__(message)
        self.http_status = http_status


@dataclass
class Response:
    url: str
    status: int
    content: bytes
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def not_modified(self) -> bool:
        return self.status == 304

    def validators(self) -> dict[str, str]:
        out = {}
        if self.headers.get("etag"):
            out["etag"] = self.headers["etag"]
        if self.headers.get("last-modified"):
            out["last_modified"] = self.headers["last-modified"]
        return out


class HttpClient:
    """Thin wrapper over requests.Session.

    `validators` maps a cache key (normally the URL) to the ETag and
    Last-Modified values from the last successful fetch. Sources send them back
    with If-None-Match and If-Modified-Since. New validators are collected in
    `pending` and only stored by the caller after the response parsed cleanly,
    so a parse failure never hides items behind a 304 on the next run.
    """

    def __init__(self, validators: dict[str, dict[str, str]] | None = None, session: requests.Session | None = None):
        self.validators = validators if validators is not None else {}
        self.pending: dict[str, dict[str, str]] = {}
        self.session = session or self._make_session()

    @staticmethod
    def _make_session() -> requests.Session:
        s = requests.Session()
        retry = Retry(
            total=RETRIES,
            connect=RETRIES,
            read=RETRIES,
            status=RETRIES,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            # A server may ask for an hour's wait; that would eat the whole run.
            # Back off 1 s and 2 s instead and let the next hourly run try again.
            respect_retry_after_header=False,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        s.headers["User-Agent"] = USER_AGENT
        return s

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        conditional: bool = True,
        auth: tuple[str, str] | None = None,
        timeout: float | None = None,
    ) -> Response:
        hdrs = dict(headers or {})
        key = url if not params else url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        if conditional:
            cached = self.validators.get(key) or {}
            if cached.get("etag"):
                hdrs["If-None-Match"] = cached["etag"]
            if cached.get("last_modified"):
                hdrs["If-Modified-Since"] = cached["last_modified"]
        try:
            r = self.session.get(url, params=params, headers=hdrs, timeout=timeout or TIMEOUT_SECONDS, auth=auth)
        except requests.RequestException as e:
            raise FetchError(f"request failed: {type(e).__name__}: {e}") from e
        resp = Response(url=r.url, status=r.status_code, content=r.content, headers={k.lower(): v for k, v in r.headers.items()})
        if resp.status >= 400:
            raise FetchError(f"HTTP {resp.status} from {url}", http_status=resp.status)
        if conditional and resp.status == 200 and resp.validators():
            self.pending[key] = resp.validators()
        return resp

    def commit(self) -> None:
        self.validators.update(self.pending)
        self.pending = {}

    def discard(self) -> None:
        self.pending = {}


def github_headers() -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h
