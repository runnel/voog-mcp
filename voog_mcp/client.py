"""Voog Admin API + Ecommerce v1 API client."""
import json
import urllib.request
import urllib.parse
import urllib.error


class VoogClient:
    """HTTP client for Voog Admin API and Ecommerce v1 API."""

    def __init__(self, host: str, api_token: str):
        self.host = host
        self.api_token = api_token
        self.base_url = f"https://{host}/admin/api"
        self.ecommerce_url = f"https://{host}/admin/api/ecommerce/v1"
        self.headers = {
            "X-API-Token": api_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "voog-mcp/0.1.0",
        }

    def _request(self, method: str, path: str, *, base: str = None, data=None, params: dict = None):
        url = f"{base or self.base_url}{path}"
        if params:
            query = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
            url += f"?{query}"
        payload = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=payload, headers=self.headers, method=method)
        with urllib.request.urlopen(req) as resp:
            body = resp.read()
            return json.loads(body) if body else None

    def get(self, path: str, *, base: str = None, params: dict = None):
        return self._request("GET", path, base=base, params=params)

    def put(self, path: str, data=None, *, base: str = None):
        return self._request("PUT", path, base=base, data=data)

    def post(self, path: str, data, *, base: str = None):
        return self._request("POST", path, base=base, data=data)

    def delete(self, path: str, *, base: str = None):
        return self._request("DELETE", path, base=base)

    def get_all(self, path: str, *, base: str = None):
        """Pagination through all pages of results."""
        results = []
        page = 1
        while True:
            data = self.get(path, base=base, params={"per_page": 100, "page": page})
            if not data:
                break
            results.extend(data)
            if len(data) < 100:
                break
            page += 1
        return results
