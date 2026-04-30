"""Voog Admin API + Ecommerce v1 API client."""

# PEP 563 lazy annotations — kept for forward compatibility. The package
# requires Python >=3.10 (where ``str | None`` evaluates fine), so this
# import is now belt-and-suspenders rather than a hard requirement.
from __future__ import annotations

import json
import urllib.parse
import urllib.request


class VoogClient:
    """HTTP client for Voog Admin API and Ecommerce v1 API."""

    def __init__(self, host: str, api_token: str, *, timeout: int = 60):
        self.host = host
        self.api_token = api_token
        # Bound on every API call. MCP server is long-running — without a
        # timeout, a hung connection wedges the entire Claude session.
        self.timeout = timeout
        self.base_url = f"https://{host}/admin/api"
        self.ecommerce_url = f"https://{host}/admin/api/ecommerce/v1"
        self.headers = {
            "X-API-Token": api_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "voog-mcp/1.1.1",
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
        url = f"{base or self.base_url}{path}"
        if params:
            url += f"?{urllib.parse.urlencode(params)}"
        payload = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=payload, headers=self.headers, method=method)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = resp.read()
            return json.loads(body) if body else None

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
        caller (escape hatch for endpoints that benefit from a different
        page size); ``page`` is **always** controlled by the iteration loop —
        any caller-supplied ``page`` value is ignored, since overriding it
        would silently re-fetch the same page on every iteration and
        infinite-loop on endpoints with ≥1 full page.
        """
        results = []
        page = 1
        while True:
            page_params = {"per_page": 100, **(params or {}), "page": page}
            data = self.get(path, base=base, params=page_params)
            if not data:
                break
            results.extend(data)
            if len(data) < 100:
                break
            page += 1
        return results
