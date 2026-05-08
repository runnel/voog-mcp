"""Voog Admin API + Ecommerce v1 API client."""

# PEP 563 lazy annotations — kept for forward compatibility. The package
# requires Python >=3.10 (where ``str | None`` evaluates fine), so this
# import is now belt-and-suspenders rather than a hard requirement.
from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request

logger = logging.getLogger("voog.client")


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
            "User-Agent": "voog-mcp/1.3.0-dev",
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
          - ``urllib.error.HTTPError`` with status code >= 500 (server errors)
          - ``OSError`` (network connectivity — DNS, TCP reset, etc.)

        Does NOT retry on 4xx (caller errors — same payload would fail again).
        Backoff is exponential: ``0.5 * 2^attempt`` seconds between attempts
        (0.5s before the first retry, 1.0s before the second).
        """
        url = f"{base or self.base_url}{path}"
        if params:
            url += f"?{urllib.parse.urlencode(params)}"
        payload = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=payload, headers=self.headers, method=method)
        logger.debug("%s %s", method, url)

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read()
                    return json.loads(body) if body else None
            except urllib.error.HTTPError as e:
                if e.code < 500 or attempt == self.max_retries:
                    raise
                logger.warning(
                    "HTTP %s on %s %s — retrying in %.1fs (attempt %d/%d)",
                    e.code,
                    method,
                    url,
                    0.5 * (2**attempt),
                    attempt + 1,
                    self.max_retries,
                )
                last_exc = e
            except OSError as e:
                if attempt == self.max_retries:
                    raise
                logger.warning(
                    "Network error on %s %s — retrying in %.1fs (attempt %d/%d): %s",
                    method,
                    url,
                    0.5 * (2**attempt),
                    attempt + 1,
                    self.max_retries,
                    e,
                )
                last_exc = e
            time.sleep(0.5 * (2**attempt))
        # Unreachable — the loop either returns or re-raises before exit.
        raise last_exc  # type: ignore[misc]

    def get(self, path: str, *, base: str | None = None, params: dict | None = None):
        return self._request("GET", path, base=base, params=params)

    def put(self, path: str, data=None, *, base: str | None = None):
        return self._request("PUT", path, base=base, data=data)

    def post(self, path: str, data, *, base: str | None = None):
        return self._request("POST", path, base=base, data=data)

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
