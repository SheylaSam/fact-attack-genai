"""Hard per-check cap on real HTTP requests, over both paths the agent uses to reach the paid endpoint.

* The agent LLM (ChatOpenAI -> httpx): `budget_http_client` returns an httpx client whose transport reserves one
  request per `handle_request`, so SDK retries and redirect hops count as well.
* The NLI client (ApertusClient -> HTTPChatClient on http.client): `install_on_apertus` wraps, on the instance only,
  every connection `HTTPChatClient` opens, like `CountedConnection` in scripts/fixed_context_nli_smoke.py. The
  resend after a dropped kept-alive connection is a second `request` and counts as a second request. A spent budget
  is never retried there (see `install_on_apertus`).

A request is reserved (counted, and journaled with fsync when a journal is given) before it is sent. A refused or
unjournaled request never reaches the network. `RequestBudget.exceeded` stays True once a request was refused, because
ApertusClient swallows the exception (translate returns None, infer returns a label-1 NLIOutput with error "api: ...").
"""
import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from src.agent import settings

_ORIGINALS = "_budget_originals"  # (connection factory, get) of an HTTPChatClient before the first install
_ORIGINAL_RETRY_LIMIT = "_budget_original_retry_limit"  # _retry_limit of an ApertusClient before the first install


class BudgetExceeded(RuntimeError):
    """The check has used up its HTTP requests; the request that raised this was not sent."""


def _as_bytes(body: Any) -> bytes:
    if body is None:
        return b""
    return bytes(body) if isinstance(body, (bytes, bytearray)) else str(body).encode("utf-8")


class RequestBudget:
    """Counts the real HTTP requests of one check; thread-safe."""

    def __init__(self, limit: int = settings.MAX_REQUESTS, journal_path: Optional[Path] = None):
        if type(limit) is not int or not 1 <= limit <= settings.MAX_REQUESTS:
            raise ValueError(f"limit must be an integer from 1 to {settings.MAX_REQUESTS}, got {limit!r}")
        self.limit = limit
        self.used = 0
        self.exceeded = False  # True once a request was refused
        self.journal_path = Path(journal_path) if journal_path is not None else None
        self._lock = threading.Lock()

    def reserve(self, body: bytes) -> None:
        """Count one request or raise `BudgetExceeded`. With a journal, the line is on disk before this returns.

        The request counts even when the journal write fails (OSError propagates and nothing is sent): the budget
        errs on the side of too few requests.
        """
        body = _as_bytes(body)
        with self._lock:
            if self.used >= self.limit:
                self.exceeded = True
                raise BudgetExceeded(f"Anfrage-Budget erschöpft: {self.used} von {self.limit} HTTP-Anfragen verbraucht")
            self.used += 1
            if self.journal_path is not None:
                self._journal(self.used, body)

    def _journal(self, sequence: int, body: bytes) -> None:
        line = json.dumps({
            "sequence": sequence,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self.journal_path.open("a", encoding="utf-8") as journal:
            journal.write(line + "\n")
            journal.flush()
            os.fsync(journal.fileno())


class BudgetTransport(httpx.BaseTransport):
    """Reserves one request per `handle_request`, then hands it to the inner transport (retries=0 by default)."""

    def __init__(self, budget: RequestBudget, inner: Optional[httpx.BaseTransport] = None):
        self._budget = budget
        self._inner = inner if inner is not None else httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self._budget.reserve(request.read())
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


def budget_http_client(budget: RequestBudget, timeout: float = 60.0, *,
                       transport: Optional[httpx.BaseTransport] = None) -> httpx.Client:
    """httpx client (for ChatOpenAI's `http_client`) that counts every request on `budget`.

    `transport` replaces the real network transport, for tests. The client is synchronous: use `invoke`, not
    `ainvoke`, or the async path would bypass the budget.
    """
    return httpx.Client(transport=BudgetTransport(budget, transport), timeout=timeout)


class _CountedConnection:
    """Wraps an http.client connection; every `request` is reserved before it goes out."""

    def __init__(self, connection: Any, budget: RequestBudget):
        self._connection = connection
        self._budget = budget

    def request(self, method, url, body=None, headers=None, **kwargs):
        self._budget.reserve(_as_bytes(body))
        return self._connection.request(method, url, body=body, headers=headers or {}, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def install_on_apertus(client: Any, budget: RequestBudget) -> None:
    """Count every HTTP request of `client.client` (HTTPChatClient) on `budget`; a mock client stays unchanged.

    Wraps the instance only (src/http_chat.py and src/apertus_client.py are untouched):
    * `_connection` for the POSTs, `get` for the /models listing that ApertusClient._switch_model may fetch, which
      opens its own connection.
    * `ApertusClient._retry_limit`: `BudgetExceeded` has no status_code, so the original treats it as transient and
      would retry it six times with 2-30 s of backoff each, for a request that can never go out. It now gets 0 retries,
      every other error keeps the original answer.
    Installing again replaces the budget instead of stacking a second counter.
    """
    http_client = getattr(client, "client", None)
    if http_client is None:
        return
    connection, get = getattr(http_client, _ORIGINALS, None) or (http_client._connection, http_client.get)
    setattr(http_client, _ORIGINALS, (connection, get))
    retry_limit = getattr(client, _ORIGINAL_RETRY_LIMIT, None) or client._retry_limit
    setattr(client, _ORIGINAL_RETRY_LIMIT, retry_limit)

    def counted_connection(timeout: float) -> _CountedConnection:
        return _CountedConnection(connection(timeout), budget)

    def counted_get(*args: Any, **kwargs: Any) -> Any:
        budget.reserve(b"")
        return get(*args, **kwargs)

    def retry_limit_without_budget(error: Exception) -> int:
        return 0 if isinstance(error, BudgetExceeded) else retry_limit(error)

    http_client._connection = counted_connection
    http_client.get = counted_get
    client._retry_limit = retry_limit_without_budget
