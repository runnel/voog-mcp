"""Voog Admin API + Ecommerce v1 API client."""

# PEP 563 lazy annotations — kept for forward compatibility. The package
# requires Python >=3.10 (where ``str | None`` evaluates fine), so this
# import is now belt-and-suspenders rather than a hard requirement.
from __future__ import annotations

import json
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

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
    """HTTP client for Voog Admin API and Ecommerce v1 API."""

    def __init__(self, host: str, api_token: str, *, timeout: int = 60, max_retries: int = 2):
        self.host = host
        self.api_token = api_token
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
            "User-Agent": "voog-mcp/1.3.0",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        base: str | None = None,
        data=None,
        params: dict | None = None,
    ):
        """Execute a single HTTP request, retrying on transient failures.

        Retries up to ``self.max_retries`` times on:
          - ``urllib.error.HTTPError`` with status in ``_RETRYABLE_STATUS``
            (429 rate limit + 5xx server errors)
          - ``OSError`` (network connectivity — DNS, TCP reset, etc.) EXCEPT
            ``socket.timeout`` / ``TimeoutError``, which propagate immediately

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
        if params:
            url += f"?{urllib.parse.urlencode(params)}"
        payload = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=payload, headers=self.headers, method=method)
        logger.debug("%s %s", method, url)

        # POST / PATCH are not safe to retry — see _RETRYABLE_METHODS comment.
        retries = self.max_retries if method in _RETRYABLE_METHODS else 0

        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read()
                    return json.loads(body) if body else None
            except urllib.error.HTTPError as e:
                if e.code not in _RETRYABLE_STATUS or attempt == retries:
                    raise
                backoff = 0.5 * (2**attempt)
                # 429 and 503 may carry Retry-After from Cloudflare/Voog.
                if e.code in (429, 503) and e.headers:
                    retry_after = e.headers.get("Retry-After")
                    if retry_after:
                        backoff = _parse_retry_after(retry_after, backoff)
                logger.warning(
                    "HTTP %s on %s %s — retrying in %.1fs (attempt %d/%d)",
                    e.code,
                    method,
                    url,
                    backoff,
                    attempt + 1,
                    retries,
                )
                time.sleep(backoff)
            except (socket.timeout, TimeoutError):
                # Timeouts are NOT retried — a hung endpoint should surface
                # immediately so the caller knows the request timed out rather
                # than silently burning retries (each retry × timeout seconds).
                raise
            except OSError as e:
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

    def patch(self, path: str, data=None, *, base: str | None = None):
        return self._request("PATCH", path, base=base, data=data)

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
        a correct stop condition on short last pages — pre-1.3.0 hardcoded
        a ``< 100`` check that silently dropped data when the last page
        contained 100-249 items under a caller override.
        """
        results = []
        page = 1
        while True:
            page_params = {"per_page": 200, **(params or {}), "page": page}
            per_page_resolved = page_params["per_page"]
            data = self.get(path, base=base, params=page_params)
            if not data:
                break
            results.extend(data)
            if len(data) < per_page_resolved:
                break
            page += 1
        return results
