"""Voog Admin API + Ecommerce v1 API client."""

# PEP 563 lazy annotations — kept for forward compatibility. The package
# requires Python >=3.10 (where ``str | None`` evaluates fine), so this
# import is now belt-and-suspenders rather than a hard requirement.
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from urllib.parse import urlencode

import httpx

import voog
from voog.errors import RequestBudgetExceeded

logger = logging.getLogger("voog.client")

# Methods safe to retry on transient failures. POST/PATCH are excluded
# because the canonical failure mode (Voog accepts the request, response
# is lost on the read) would silently create duplicate resources on the
# retry — see PR #110 review. GET is read-only; PUT in Voog's API is
# full-replace (sending the same payload twice yields the same end
# state); DELETE on a missing resource returns 404 which the caller can
# tolerate (resource is gone either way).
_RETRYABLE_METHODS = frozenset({"GET", "PUT", "DELETE"})

# HTTP status codes that are safe to retry (server-side transient errors
# and rate-limiting). 429 is added in v1.3 — Cloudflare rate-limits
# return 429 with an optional Retry-After header that we now honor.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Maximum seconds to honor from a Retry-After header. Prevents a
# misbehaving server from pinning the client for a very long time.
_RETRY_AFTER_CAP = 60

# Headers whose values must never appear in DEBUG logs. Lookup is
# case-insensitive — HTTP header names are case-insensitive per RFC 7230
# and Voog accepts mixed-case variants on a few endpoints.
_SENSITIVE_HEADERS = frozenset({"x-api-token", "authorization", "cookie"})


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of *headers* with sensitive values replaced by ``"***"``.

    Defensive helper — current ``_request`` does not log header dicts,
    but any future DEBUG addition (e.g. ``--trace`` envelope dump) is
    protected by routing through this function. Case-insensitive match
    against ``_SENSITIVE_HEADERS``; other headers pass through verbatim.
    The input dict is never mutated.
    """
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in _SENSITIVE_HEADERS:
            out[k] = "***"
        else:
            out[k] = v
    return out


# Query-string values longer than this many characters are replaced with
# a length-marker placeholder in DEBUG log lines. Captures presigned-URL
# signatures (X-Amz-Signature is 256 hex chars) and any accidentally-
# leaked token-bearing query params without dropping short, useful
# values like ``?include=variants,variant_types,translations`` (38 chars)
# or ``?per_page=250``.
#
# Trade-off: 50 is the value-length heuristic, not a security boundary.
# A 32-char hex Voog API token in a ``?api_key=`` query param would NOT
# be redacted at this cap, but Voog tokens travel via ``X-API-Token``
# header (not query string) so the in-the-wild exposure surface is
# narrow. The right principled fix is key-name-based redaction
# (``api_key``, ``token``, ``password``, ``X-Amz-Signature``, …); deferred
# until a real near-miss motivates the complexity.
_QUERY_STRING_VALUE_CAP = 50


def _redact_query_string(qs: str) -> str:
    """Redact long values in a URL query string for DEBUG logging.

    Keys are kept verbatim (they are useful debugging context);
    values longer than ``_QUERY_STRING_VALUE_CAP`` are replaced with
    ``"***N-chars***"``. Empty / short values pass through unchanged.
    Repeated keys are handled — each occurrence is redacted
    independently. The input is the raw ``urllib.parse.urlencode``
    output (already percent-encoded), so this function operates on the
    encoded form and does not need to decode/re-encode.
    """
    if not qs:
        return qs
    parts: list[str] = []
    for pair in qs.split("&"):
        if "=" not in pair:
            parts.append(pair)
            continue
        key, _, val = pair.partition("=")
        if len(val) > _QUERY_STRING_VALUE_CAP:
            parts.append(f"{key}=***{len(val)}-chars***")
        else:
            parts.append(pair)
    return "&".join(parts)


# Per-VoogClient lifetime cap on successful requests. Defaults to 5000;
# override via the VOOG_REQUEST_CAP environment variable. Set to 0 to
# disable the cap entirely (NOT recommended for long-running MCP server
# processes — the cap is the last-line safety against a runaway tool
# loop). Warning threshold is hardcoded at 1000 so operators see the
# log line before they get anywhere near the hard fail.
_REQUEST_BUDGET_WARN_AT = 1000
_DEFAULT_REQUEST_CAP = 5000


def _resolve_request_cap() -> int | None:
    """Resolve ``VOOG_REQUEST_CAP`` to an effective cap value.

    Returns ``None`` when the env var is ``"0"`` (cap disabled). Any
    non-integer or negative value falls back to ``_DEFAULT_REQUEST_CAP``
    with a WARNING log so the operator notices the typo.
    """
    raw = os.environ.get("VOOG_REQUEST_CAP")
    if raw is None:
        return _DEFAULT_REQUEST_CAP
    try:
        val = int(raw)
    except ValueError:
        logger.warning(
            "VOOG_REQUEST_CAP=%r is not an integer — falling back to default %d",
            raw,
            _DEFAULT_REQUEST_CAP,
        )
        return _DEFAULT_REQUEST_CAP
    if val == 0:
        return None
    if val < 0:
        logger.warning(
            "VOOG_REQUEST_CAP=%d is negative — falling back to default %d",
            val,
            _DEFAULT_REQUEST_CAP,
        )
        return _DEFAULT_REQUEST_CAP
    return val


def _parse_retry_after(header_value: str, fallback: float) -> float:
    """Parse a Retry-After header value (integer seconds only).

    Per RFC 7231, Retry-After is either an integer number of seconds or
    an HTTP-date. We implement integer-seconds parsing and fall back to
    ``fallback`` for anything unparseable (including HTTP-dates). The
    result is clamped to [1, _RETRY_AFTER_CAP] so a zero or very large
    value doesn't cause immediate retry or excessive waiting.
    """
    try:
        seconds = int(header_value.strip())
        return float(max(1, min(seconds, _RETRY_AFTER_CAP)))
    except (ValueError, AttributeError):
        return fallback


class VoogClient:
    """HTTP client for Voog Admin API and Ecommerce v1 API.

    v1.4 — transport swapped from ``urllib.request`` to ``httpx.Client``.
    Public surface is unchanged. ``self._http_client`` is the underlying
    pool; one instance is constructed per ``VoogClient`` and reused for
    every request (HTTP/2 keepalive, connection pool — primary win for
    high-fanout snapshots).
    """

    def __init__(
        self,
        host: str,
        api_token: str,
        *,
        timeout: int = 60,
        max_retries: int = 2,
        site_name: str | None = None,
        daily_request_quota: int | None = None,
    ):
        self.host = host
        self.api_token = api_token
        # S-7 / S-8 wiring. ``site_name`` and ``daily_request_quota`` are
        # populated by ``ClientFactory.for_site`` from ``SiteConfig``.
        # Ad-hoc callers (CLI without a registered site, tests) leave both
        # as ``None`` and skip the quota path entirely.
        self.site_name = site_name
        self.daily_request_quota = daily_request_quota
        # Bound on every API call. MCP server is long-running — without a
        # timeout, a hung connection wedges the entire Claude session.
        self.timeout = timeout
        self.max_retries = max_retries
        self.base_url = f"https://{host}/admin/api"
        self.ecommerce_url = f"https://{host}/admin/api/ecommerce/v1"
        self.headers = {
            "X-API-Token": api_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            # N1: single source of truth — voog.__version__ in voog/__init__.py.
            # Phase 5 S9 (per-tool UA suffix) and the v1.4 release tag both
            # rely on this string flowing from one place.
            "User-Agent": f"voog-mcp/{voog.__version__}",
        }
        # httpx.Client owns the connection pool. http2=True opportunistically
        # negotiates HTTP/2 with Voog/Cloudflare; falls back to HTTP/1.1 if
        # the server doesn't advertise h2 in ALPN. Headers are attached to
        # the client so every request inherits them (avoids passing on
        # every call).
        #
        # follow_redirects=True preserves urllib parity: urllib.request.urlopen
        # followed redirects silently (max 10), but httpx defaults to False
        # which would cause 301/302 from Voog/Cloudflare (trailing slash,
        # www↔apex, CDN host) to escalate via raise_for_status as a 3xx
        # HTTPStatusError — a silent regression against pre-1.4 behaviour.
        # httpx's default max_redirects=20 is the new ceiling (vs urllib's 10);
        # TooManyRedirects is caught separately below to avoid wasting retries
        # on a permanent loop.
        self._http_client = httpx.Client(http2=True, headers=self.headers, follow_redirects=True)
        # Per-tool tracking state (S9 / R2 / R3). Thread-local so that
        # ``parallel_map`` workers don't read a sibling thread's tool name.
        # ``_local`` lifetime is the client's lifetime; callers acquire the
        # state via ``with_tool(...)`` which sets ``tool_name`` and
        # ``request_id`` on enter, clears them on exit. ``parallel_map``
        # worker threads need explicit propagation via
        # ``voog._concurrency.propagate_tool_context`` — threading.local()
        # does NOT inherit across thread boundaries. Phase 6 S-8 reuses
        # this carrier for ``last_site`` (set/get via
        # ``set_target_site`` / ``get_target_site``).
        self._local = threading.local()
        # S-6 per-instance request counter (incremented on success only;
        # retries inside _request count as one request per R8). Cap
        # resolved once at construction time so the warning + fail
        # branch read a consistent value across the client's lifetime.
        self._request_count = 0
        self._request_cap = _resolve_request_cap()
        self._warned_at_threshold = False
        # S-8 cross-thread fallback for the target-site carrier. The
        # thread-local slot (``_local.last_site``) wins when set; the
        # instance attribute is the fallback for worker threads (e.g.
        # ``parallel_map``) that haven't set their own slot.
        self._last_site: str | None = None

    def set_target_site(self, site_name: str | None) -> None:
        """Record *site_name* as the most-recent target for this client.

        Both a per-thread (``_local.last_site``) and a per-instance
        (``_last_site``) slot are updated. The per-thread slot is the
        authoritative source for the calling thread; ``_last_site`` is
        the cross-thread fallback (e.g. snapshot's ``parallel_map``
        workers reading what the dispatch thread set).
        """
        self._local.last_site = site_name
        self._last_site = site_name

    def get_target_site(self) -> str | None:
        """Return the most-recent target site for this client.

        Order: thread-local (``_local.last_site``) → instance fallback
        (``_last_site``) → ``None``. Cross-thread reads return the
        instance fallback which may be stale relative to another
        thread's true target — acceptable per spec § Phase 6 S-8.
        """
        tls_site = getattr(self._local, "last_site", None)
        if tls_site is not None:
            return tls_site
        return self._last_site

    @contextmanager
    def with_tool(self, tool_name: str):
        """Tag every HTTP request inside this scope with ``tool_name`` +
        a shared ``X-Request-Id``.

        Headers added per request in this scope:
          - ``X-MCP-Tool: <tool_name>``
          - ``X-Request-Id: <uuid4>`` (one uuid for the entire scope —
            a fan-out of 50 HTTP calls all share the same request id, so
            Voog-side log analysis can recover the burst with GROUP BY).
          - User-Agent suffix ``(tool=<tool_name>)``

        Thread-safety: state lives on ``self._local`` (a
        ``threading.local()``). Two MCP tool calls running concurrently
        on different threads see independent state. **Caveat:**
        ``parallel_map`` worker threads do NOT inherit thread-locals from
        their dispatching thread. Snapshot-style fan-outs that need the
        tool tracking to follow into worker threads must wrap each callable
        with ``voog._concurrency.propagate_tool_context(client, fn)``.

        Re-entry: nesting ``with_tool`` is unsupported and raises
        ``RuntimeError``. The outer caller (tool's ``call_tool`` wrapper)
        is the single point of entry; if a handler ever calls back into a
        sibling tool's ``call_tool`` directly (rather than the public
        ``client.get`` etc.), the explicit guard catches it before silent
        header pollution. The guard is an explicit ``raise`` (not an
        ``assert``) so it survives ``python -O`` / ``PYTHONOPTIMIZE=1``.
        """
        # Safety invariant — NOT an ``assert`` because ``python -O`` /
        # ``PYTHONOPTIMIZE=1`` strips assert statements. Under that flag a
        # silently-overwritten outer scope would lose its tracking state
        # when the inner scope's ``finally`` clears it (the outer scope's
        # subsequent calls would emit untagged requests). Explicit raise
        # keeps the guard load-bearing across all Python optimisation levels.
        if getattr(self._local, "tool_name", None):
            raise RuntimeError(
                f"with_tool nesting is unsupported (current={self._local.tool_name!r}, "
                f"new={tool_name!r}). Tools must not re-enter each other's call_tool; "
                f"go via client.get / client.post / etc."
            )
        self._local.tool_name = tool_name
        self._local.request_id = uuid.uuid4().hex
        try:
            yield
        finally:
            # Clear, don't leave stale state on the thread for a future
            # call that happens to land on the same worker.
            self._local.tool_name = None
            self._local.request_id = None

    def _request(
        self,
        method: str,
        path: str,
        *,
        base: str | None = None,
        data=None,
        params: dict | None = None,
        _force_retryable: bool = False,
    ):
        """Execute a single HTTP request, retrying on transient failures.

        Retries up to ``self.max_retries`` times on:
          - ``httpx.HTTPStatusError`` with status in ``_RETRYABLE_STATUS``
            (429 rate limit + 5xx server errors)
          - ``httpx.NetworkError`` / ``httpx.ConnectError`` / similar
            non-timeout transport errors EXCEPT ``httpx.TimeoutException``
            (and its subclasses ReadTimeout/ConnectTimeout/etc.), which
            propagate immediately re-raised as ``TimeoutError``.

        Does NOT retry on other 4xx (caller errors — same payload would fail
        again) or on timeouts (a hung Voog endpoint should surface, not wedge
        the caller for ~3× the timeout).

        **Retries are restricted to GET / PUT / DELETE** (see
        ``_RETRYABLE_METHODS``). POST and PATCH always run a single
        attempt: under the canonical "Voog accepted but response lost"
        failure mode, retrying a POST would silently create a duplicate
        resource (e.g. a second product, redirect rule). The caller is
        responsible for deciding how to handle a transient POST failure.

        Backoff: on 429/503 with a parseable ``Retry-After`` header, honor
        the header (clamped to ``[1, _RETRY_AFTER_CAP]`` seconds). Otherwise
        exponential: ``0.5 * 2^attempt`` seconds between attempts.
        """
        url = f"{base or self.base_url}{path}"

        # POST / PATCH are not safe to retry by default — see
        # _RETRYABLE_METHODS comment. PATCH can opt in per-call via
        # _force_retryable=True; the page/article PATCH wrappers set it
        # because Voog documents those routes as merge-idempotent.
        if method in _RETRYABLE_METHODS or _force_retryable:
            retries = self.max_retries
        else:
            retries = 0

        # httpx request kwargs — only forward `params` / `json` when set,
        # so test mocks that assert "params kwarg absent" stay clean.
        kwargs: dict = {
            "method": method,
            "url": url,
            "timeout": self.timeout,
        }
        if params:
            kwargs["params"] = params

        # S-9: log the *merged* URL (path + ?params) with long query-string
        # values redacted. The outgoing httpx request still uses ``params``
        # via ``kwargs`` — we synthesise the log string separately so the
        # wire bytes are untouched. Pre-Phase-6a, the log line emitted just
        # ``method url`` (path only), which hid the typed-tool params from
        # operators triaging DEBUG logs and made the redactor a no-op for
        # the common code path. Now redactor + log are load-bearing for both
        # typed tools (via ``params=``) and passthrough (``?`` embedded in
        # ``path`` directly).
        if params:
            qs = urlencode(params, doseq=True)
            log_url = f"{url}?{_redact_query_string(qs)}"
        elif "?" in url:
            base_url, _, query = url.partition("?")
            log_url = f"{base_url}?{_redact_query_string(query)}"
        else:
            log_url = url
        logger.debug("%s %s", method, log_url)

        if data is not None:
            # JSON-encode at the boundary. Going through httpx's `json=`
            # parameter sets Content-Type automatically, but our client
            # already injects "application/json" on the session headers,
            # and we want to keep the body shape identical to the urllib
            # implementation (exact JSON bytes), so we serialise once and
            # pass via `content=`.
            kwargs["content"] = json.dumps(data).encode()

        # S9: per-call tracking headers. Read thread-local state set by
        # ``with_tool``; absent → no extra headers (no User-Agent suffix).
        # httpx merges per-call ``headers=`` on top of client-level
        # ``self.headers``, so this does NOT mutate the shared dict and is
        # safe across concurrent requests on different threads.
        tool_name = getattr(self._local, "tool_name", None)
        request_id = getattr(self._local, "request_id", None)
        if tool_name or request_id:
            per_call_headers: dict = {}
            if tool_name:
                per_call_headers["X-MCP-Tool"] = tool_name
                # Append `(tool=...)` to the canonical UA (N1 sourced from
                # voog.__version__) so Voog-side log greps can attribute
                # requests to the originating MCP tool.
                per_call_headers["User-Agent"] = (
                    f"{self.headers.get('User-Agent', '')} (tool={tool_name})".strip()
                )
            if request_id:
                # R3: one uuid per MCP-tool invocation, reused across the
                # full fan-out so log aggregation can GROUP BY x_request_id.
                per_call_headers["X-Request-Id"] = request_id
            kwargs["headers"] = per_call_headers

        for attempt in range(retries + 1):
            try:
                resp = self._http_client.request(**kwargs)
                # raise_for_status raises HTTPStatusError on 4xx/5xx.
                resp.raise_for_status()
                body = resp.content
                # S-6: count successful requests only (per R8, retries
                # within one logical call count once). Bump AFTER the
                # response is read so a mid-read exception doesn't inflate
                # the counter. Local-counter drift note: this increment
                # is unconditional, but the per-site persisted quota write
                # below may raise (DailyQuotaExceeded) or fail to land
                # (transient disk error). The local counter therefore can
                # be one ahead of the on-disk quota state — this is the
                # "coarse safety rail" trade-off documented in
                # voog/quota.py's module docstring.
                self._request_count += 1
                # ``>=`` (not ``==``) so the warning still fires if two
                # concurrent ``parallel_map`` workers race the boundary
                # and the lost-update window skips over exactly 1000.
                # The duplicate-warn race (two workers both seeing
                # ``not self._warned_at_threshold``) is bounded by the
                # fan-out width (≤8 lines under snapshot's max_workers);
                # the once-only flag is best-effort.
                if self._request_count >= _REQUEST_BUDGET_WARN_AT and not self._warned_at_threshold:
                    logger.warning(
                        "VoogClient request count reached %d (cap=%s) — "
                        "consider whether the calling tool is in a runaway loop",
                        self._request_count,
                        self._request_cap if self._request_cap is not None else "off",
                    )
                    self._warned_at_threshold = True
                if self._request_cap is not None and self._request_count > self._request_cap:
                    raise RequestBudgetExceeded(
                        f"VoogClient exceeded request budget: count={self._request_count}, "
                        f"cap={self._request_cap} (set VOOG_REQUEST_CAP to raise/disable). "
                        f"Site={self.site_name!r}, host={self.host!r}."
                    )
                # S-7: per-site daily quota. Skipped when either
                # ``site_name`` or ``daily_request_quota`` is unset
                # (CLI ad-hoc callers, tests, sites that opted out).
                # ``quota.increment`` raises DailyQuotaExceeded; it
                # propagates to the caller for snapshot's MD4 catch.
                if self.site_name is not None and self.daily_request_quota is not None:
                    # Local import to keep client.py importable in any
                    # environment where ``platformdirs`` isn't installed
                    # (the typical test fixture path uses ``_TmpQuotaPath``
                    # which always sets the env override).
                    from voog import quota as _quota

                    _quota.increment(self.site_name, self.daily_request_quota)
                return json.loads(body) if body else None
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                if code not in _RETRYABLE_STATUS or attempt == retries:
                    raise
                backoff = 0.5 * (2**attempt)
                # 429 and 503 may carry Retry-After from Cloudflare/Voog.
                if code in (429, 503):
                    retry_after = e.response.headers.get("Retry-After")
                    if retry_after:
                        backoff = _parse_retry_after(retry_after, backoff)
                logger.warning(
                    "HTTP %s on %s %s — retrying in %.1fs (attempt %d/%d)",
                    code,
                    method,
                    url,
                    backoff,
                    attempt + 1,
                    retries,
                )
                time.sleep(backoff)
            except httpx.TimeoutException as e:
                # Timeouts are NOT retried — a hung endpoint should surface
                # immediately. Re-raise as TimeoutError so existing callers
                # (and the audit-doc'd contract) keep working unchanged.
                raise TimeoutError(str(e)) from e
            except httpx.TooManyRedirects:
                # Permanent redirect loops are not transient — retrying just
                # wastes the backoff budget. TooManyRedirects inherits from
                # RequestError → HTTPError, so without this branch it would
                # be caught below and retried max_retries times.
                raise
            except httpx.HTTPError as e:
                # httpx.HTTPError covers NetworkError, ConnectError,
                # RemoteProtocolError, etc. These are NOT OSError
                # subclasses (empirically: NetworkError.__mro__ is
                # NetworkError → TransportError → RequestError →
                # HTTPError → Exception). The urllib-era `except OSError`
                # branch is REPLACED by this `except httpx.HTTPError`
                # branch, not extended. Apply the same "retry except on
                # the last attempt" policy.
                if attempt == retries:
                    raise
                backoff = 0.5 * (2**attempt)
                logger.warning(
                    "Network error on %s %s — retrying in %.1fs (attempt %d/%d): %s",
                    method,
                    url,
                    backoff,
                    attempt + 1,
                    retries,
                    e,
                )
                time.sleep(backoff)

    def get(self, path: str, *, base: str | None = None, params: dict | None = None):
        return self._request("GET", path, base=base, params=params)

    def put(self, path: str, data=None, *, base: str | None = None, params: dict | None = None):
        return self._request("PUT", path, base=base, data=data, params=params)

    def post(self, path: str, data, *, base: str | None = None, params: dict | None = None):
        return self._request("POST", path, base=base, data=data, params=params)

    def patch(
        self,
        path: str,
        data=None,
        *,
        base: str | None = None,
        params: dict | None = None,
        _voog_documented_idempotent: bool = False,
    ):
        """PATCH /path with optional retry for Voog-documented-idempotent routes.

        ``_voog_documented_idempotent=True`` opts THIS call into the same
        retry loop as GET/PUT/DELETE. Default ``False`` preserves the v1.3
        no-retry policy on PATCH (because the canonical Voog failure mode
        — "request accepted, response lost" — would silently double-write
        on retry for non-idempotent PATCH routes).

        Set only for routes Voog docs explicitly mark merge-semantics-
        idempotent (page PATCH, article PATCH today). Adding routes here
        requires Voog-docs evidence in the PR description — the global
        _RETRYABLE_METHODS frozen set is NOT modified by this flag.
        """
        return self._request(
            "PATCH",
            path,
            base=base,
            data=data,
            params=params,
            _force_retryable=_voog_documented_idempotent,
        )

    def delete(self, path: str, *, base: str | None = None, params: dict | None = None):
        return self._request("DELETE", path, base=base, params=params)

    def get_all(self, path: str, *, base: str | None = None, params: dict | None = None):
        """Pagination through all pages of results.

        Caller-provided ``params`` (e.g. ``{"include": "translations"}``) are
        merged with pagination params. ``per_page`` may be overridden by the
        caller; ``page`` is **always** controlled by the iteration loop —
        any caller-supplied ``page`` value is ignored, since overriding it
        would silently re-fetch the same page on every iteration and
        infinite-loop on endpoints with ≥1 full page.

        Termination uses the **resolved** ``per_page`` (caller's override
        wins over the default) so callers asking for ``per_page=250`` get
        a correct stop condition on short last pages — pre-1.3 hardcoded
        a ``< 100`` check that silently dropped data when the last page
        contained 100-249 items under a caller override.
        """
        results = []
        page = 1
        while True:
            # MD1 (v1.4): per_page=250 is Voog's documented server-side max
            # (Voog API docs: <https://www.voog.com/developers/api>
            # "Pagination default 50 / max 250"; mirrored in
            # docs/voog-mcp-endpoint-coverage.md). If Voog ever silently caps
            # below 250, the `len(data) < per_page_resolved` termination
            # check below would break the first page and silently truncate
            # lists. The `if not data: break` guard above still terminates
            # correctly on empty pages, so the worst-case failure mode is a
            # missing tail — caught by live-smoke against any endpoint with
            # >250 items (PR-1b checklist).
            # Last verified against Voog API: 2026-05-26.
            page_params = {"per_page": 250, **(params or {}), "page": page}
            per_page_resolved = page_params["per_page"]
            data = self.get(path, base=base, params=page_params)
            if not data:
                break
            results.extend(data)
            if len(data) < per_page_resolved:
                break
            page += 1
        return results
