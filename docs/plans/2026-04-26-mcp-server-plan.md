# voog-mcp: CLI → MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pordi `voog.py` CLI päris MCP serveriks (`voog-mcp` package) MCP SDK abil, säilitades kogu olemas-oleva funktsionaalsuse + lisades resource'id ja tool annotations.

**Architecture:** Lahuta single-file `voog.py` Python pakettiks `voog_mcp/`. Lisa MCP SDK sõltuvuseks. Iga olemas-olev CLI käsk → MCP tool. Andmekogud (pages, layouts, articles, products) → MCP resources. CLI `voog.py` jääb backward-compat shim'ina.

**Tech Stack:** Python 3, [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) (`mcp>=0.9.0`), urllib stdlib, unittest stdlib, `pyproject.toml` distribution.

**Spec:** [`docs/specs/2026-04-26-mcp-server.md`](../specs/2026-04-26-mcp-server.md)

---

## Parallelization Strategy

**Sequential phases (must finish before next starts):**
- Phase A (Foundation, Tasks 1–5)
- Phase B (Server skeleton, Tasks 6–8)

**Parallel-safe within phase:**
- Phase C (Tools, Tasks 9–14) — different files, no shared state
- Phase D (Resources, Tasks 15–19) — different URI handlers
- Phase E (Polish, Tasks 20–23) — independent concerns

**Parallel sessions can pick:**
- Session 1: Phase A → B → start Phase C
- Session 2 (after Phase B done): pick remaining Phase C task
- Session 3 (after Phase B done): pick Phase D task
- Session N: pick remaining tasks in any phase

**Coordination:** TodoWrite + git status to know who's working on what. Each task = single PR or single commit on `feat/mcp-*` branch. Avoid two sessions modifying same file simultaneously.

---

## Phase A: Foundation (sequential)

### Task 1: Set up package structure

**Eesmärk:** Loo `voog_mcp/` Python paketi struktuur, säilita `voog.py` praegusel kohal (täidame seda Task 4-s).

**Files:**
- Create: `Tööriistad/voog_mcp/__init__.py`
- Create: `Tööriistad/voog_mcp/__main__.py` (placeholder)
- Create: `Tööriistad/voog_mcp/server.py` (placeholder)
- Create: `Tööriistad/voog_mcp/config.py` (placeholder)
- Create: `Tööriistad/voog_mcp/client.py` (placeholder)
- Create: `Tööriistad/voog_mcp/tools/__init__.py`
- Create: `Tööriistad/voog_mcp/resources/__init__.py`

**Branch:** `feat/mcp-foundation`

- [ ] **Step 1.1: Create directories and empty `__init__.py` files**

```bash
cd /Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Tööriistad
mkdir -p voog_mcp/tools voog_mcp/resources
touch voog_mcp/__init__.py voog_mcp/tools/__init__.py voog_mcp/resources/__init__.py
```

- [ ] **Step 1.2: Add version + exports to `voog_mcp/__init__.py`**

```python
"""voog-mcp: MCP server for Voog CMS."""

__version__ = "0.1.0-dev"
```

- [ ] **Step 1.3: Add placeholder `__main__.py`**

```python
"""Entry point: `python3 -m voog_mcp` launches MCP server."""

def main():
    raise NotImplementedError("Server skeleton in Task 6")


if __name__ == "__main__":
    main()
```

- [ ] **Step 1.4: Add placeholder `server.py`, `config.py`, `client.py`**

Each just contains `"""Module docstring."""\n# TODO: Task N\n` for now.

- [ ] **Step 1.5: Verify import works**

```bash
cd /Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Tööriistad
python3 -c "import voog_mcp; print(voog_mcp.__version__)"
```
Expected: `0.1.0-dev`.

- [ ] **Step 1.6: Commit**

```bash
git add voog_mcp/
git commit -m "feat(mcp): scaffold voog_mcp package structure"
```

---

### Task 2: Add `pyproject.toml` and install MCP SDK

**Eesmärk:** Tee voog-mcp pip-installable koos MCP SDK sõltuvusega.

**Files:**
- Create: `Tööriistad/pyproject.toml`

- [ ] **Step 2.1: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "voog-mcp"
version = "0.1.0"
description = "MCP server for Voog CMS — Liquid templates, pages, products, ecommerce"
readme = "README.md"
requires-python = ">=3.10"
license = {text = "MIT"}
authors = [{name = "Tõnu Runnel", email = "runnel@gmail.com"}]
dependencies = [
    "mcp>=0.9.0",
]

[project.optional-dependencies]
dev = [
    "ruff",
]

[project.scripts]
voog-mcp = "voog_mcp.__main__:main"

[tool.setuptools.packages.find]
include = ["voog_mcp*"]
exclude = ["tests*"]
```

NOTE: We don't add a `voog` console script entry yet — `voog.py` (the CLI script in repo root) keeps working as `python3 voog.py ...` invocation. Decision in spec §3.5.

- [ ] **Step 2.2: Install package in editable mode**

```bash
cd /Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Tööriistad
pip install -e ".[dev]" 2>&1 | tail -5
```
Expected: `Successfully installed voog-mcp ...` and `mcp ...`.

- [ ] **Step 2.3: Verify MCP SDK importable**

```bash
python3 -c "import mcp; print(mcp.__version__)"
```
Expected: prints version (≥0.9.0).

- [ ] **Step 2.4: Verify console script registered**

```bash
which voog-mcp
voog-mcp 2>&1 | head -3
```
Expected: shows path; running raises NotImplementedError (placeholder from Task 1).

- [ ] **Step 2.5: Commit**

```bash
git add pyproject.toml
git commit -m "feat(mcp): add pyproject.toml with mcp SDK dependency"
```

---

### Task 3: Extract Voog API client into `voog_mcp/client.py`

**Eesmärk:** Tõmba kogu Voog API loogika (api_get/put/post/delete, api_get_all, init_site, load_env, load_site_config) `voog.py`-st välja `voog_mcp/client.py`-sse.

**Files:**
- Modify: `Tööriistad/voog_mcp/client.py` (replace placeholder)
- Create: `Tööriistad/tests/test_client.py`

- [ ] **Step 3.1: Write the failing test for client class**

Create `tests/test_client.py`:
```python
"""Unit tests for VoogClient."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure package importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voog_mcp.client import VoogClient


class TestVoogClient(unittest.TestCase):
    def test_init_sets_base_urls(self):
        client = VoogClient(host="runnel.ee", api_token="testtoken")
        self.assertEqual(client.base_url, "https://runnel.ee/admin/api")
        self.assertEqual(client.ecommerce_url, "https://runnel.ee/admin/api/ecommerce/v1")

    def test_init_sets_headers(self):
        client = VoogClient(host="runnel.ee", api_token="testtoken")
        self.assertEqual(client.headers["X-API-Token"], "testtoken")
        self.assertEqual(client.headers["Content-Type"], "application/json")
```

- [ ] **Step 3.2: Run test (fails — VoogClient doesn't exist)**

```bash
cd /Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Tööriistad
python3 -m unittest tests.test_client -v
```
Expected: FAIL.

- [ ] **Step 3.3: Implement `VoogClient` in `voog_mcp/client.py`**

```python
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
```

- [ ] **Step 3.4: Run tests — should pass**

```bash
python3 -m unittest tests.test_client -v
```
Expected: PASS.

- [ ] **Step 3.5: Commit**

```bash
git add voog_mcp/client.py tests/test_client.py
git commit -m "feat(mcp): VoogClient class extracts API logic from voog.py"
```

---

### Task 4: Backward-compat shim — `voog.py` imports from `voog_mcp.client`

**Eesmärk:** `voog.py` jätkab tööd, aga delegeerib API loogika `voog_mcp.client`-ile. Kõik 19 olemas-olevat unit testi peavad ikka pass'ima.

**Files:**
- Modify: `Tööriistad/voog.py`
- Modify: `Tööriistad/tests/test_voog.py` (kui mõni test viitab module global'idele mis muutuvad)

- [ ] **Step 4.1: In `voog.py`, replace api_get/put/post/delete/get_all + init_site infrastructure with VoogClient instance**

Strategy:
1. Säilita module-level `api_get`, `api_put` etc. funktsioonidena, AGA delegeeri `_client` instance'ile
2. `init_site()` loob `_client` instance'i ja sätestab module-level globalsd backward-compat'i jaoks (need testid kasutavad `voog.HOST`, `voog.BASE_URL` jne)

```python
# voog.py — refaktoreeritud algus

import sys
import json
import os
from pathlib import Path

from voog_mcp.client import VoogClient

ENV = {}  # täidetakse load_env-is

# Module-level globals — säilita backward-compat olemas-olevatele kasutuskohtadele
SITE_CONFIG = None
HOST = ""
API_KEY = ""
BASE_URL = ""
ECOMMERCE_URL = ""
HEADERS = {}
_client: VoogClient | None = None

_HELP_CMDS = {"help", "-h", "--help"}


def load_env():
    """... (säilita olemas-olev implementatsioon)"""
    ...


def load_site_config():
    """... (säilita olemas-olev implementatsioon)"""
    ...


def init_site():
    """Lazy-init VoogClient + module globals."""
    global SITE_CONFIG, HOST, API_KEY, BASE_URL, ECOMMERCE_URL, _client
    if _client is not None:
        return
    SITE_CONFIG = load_site_config()
    HOST = SITE_CONFIG["host"]
    API_KEY = ENV.get(SITE_CONFIG["api_key_env"], "")
    if not API_KEY:
        sys.stderr.write(f"❌ Env muutuja '{SITE_CONFIG['api_key_env']}' puudub.\n")
        sys.exit(1)
    BASE_URL = f"https://{HOST}/admin/api"
    ECOMMERCE_URL = f"https://{HOST}/admin/api/ecommerce/v1"
    _client = VoogClient(host=HOST, api_token=API_KEY)
    HEADERS.update(_client.headers)


# Backward-compat module-level wrappers — kasutavad _client'i sees
def api_get(path, params=None, base=None):
    return _client.get(path, base=base, params=params)


def api_put(path, data=None, base=None):
    return _client.put(path, data, base=base)


def api_post(path, data, base=None):
    return _client.post(path, data, base=base)


def api_delete(path, base=None):
    return _client.delete(path, base=base)


def api_get_all(path, base=None):
    return _client.get_all(path, base=base)


# Ülejäänud voog.py funktsioonid (pull, push, list_files, pages_list, jne) jäävad muutmatuks —
# nad kasutavad endiselt api_get/put/post/delete/get_all funktsioone.
```

- [ ] **Step 4.2: Käivita kõik unit testid — peavad pass'ima**

```bash
cd /Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Tööriistad
python3 -m unittest discover tests -v 2>&1 | tail -5
```
Expected: 19 + 2 (client) = 21 testid pass'ivad.

- [ ] **Step 4.3: Smoke test CLI ikka töötab**

```bash
cd /Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Isiklik/Runnel/runnel-voog
python3 /Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Tööriistad/voog.py pages | head -3
```
Expected: 85 pages list, no errors.

- [ ] **Step 4.4: Commit**

```bash
git add voog.py
git commit -m "refactor(voog): delegate API calls to VoogClient (backward-compat shim)"
```

---

### Task 5: Add MCP integration test scaffolding

**Eesmärk:** Loo `tests/test_mcp_integration.py` mis käivitab MCP server'i subprocess'is ja teeb baas-handshake'i. Praegu server'it pole — test'id märgitakse `skip`'iks kuni Task 6.

**Files:**
- Create: `Tööriistad/tests/test_mcp_integration.py`

- [ ] **Step 5.1: Write skip-marked integration test scaffold**

```python
"""MCP protocol integration tests — subprocess-based.

Test pattern:
1. Spawn `voog-mcp` server subprocess
2. Send initialize JSON-RPC message via stdin
3. Read response from stdout
4. Send tools/list, expect tool definitions
5. Cleanup subprocess
"""
import json
import os
import subprocess
import sys
import unittest


@unittest.skip("Server skeleton not implemented yet — Task 6")
class TestMCPInitialize(unittest.TestCase):
    def test_server_initialize_handshake(self):
        env = {
            **os.environ,
            "VOOG_HOST": "runnel.ee",
            "VOOG_API_TOKEN": os.environ.get("RUNNEL_VOOG_API_KEY", ""),
        }
        proc = subprocess.Popen(
            ["voog-mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        # Send initialize request
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "0.9.0",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1"},
            },
        }
        proc.stdin.write(json.dumps(init_request) + "\n")
        proc.stdin.flush()
        response_line = proc.stdout.readline()
        response = json.loads(response_line)
        self.assertEqual(response["id"], 1)
        self.assertIn("result", response)
        self.assertIn("capabilities", response["result"])
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 5.2: Run tests — skip'itud**

```bash
python3 -m unittest tests.test_mcp_integration -v
```
Expected: 1 skipped.

- [ ] **Step 5.3: Commit**

```bash
git add tests/test_mcp_integration.py
git commit -m "test(mcp): scaffold MCP protocol integration tests (skipped until Task 6)"
```

---

## Phase B: Server Skeleton (sequential)

### Task 6: Minimal MCP server with initialize handshake

**Eesmärk:** `voog-mcp` käivitab MCP server'i `mcp.server.Server` SDK abil, vastab initialize'le, listib tühja tools list'i.

**Files:**
- Modify: `Tööriistad/voog_mcp/server.py`
- Modify: `Tööriistad/voog_mcp/__main__.py`
- Modify: `Tööriistad/tests/test_mcp_integration.py` (eemalda `@unittest.skip`)

- [ ] **Step 6.1: Write `voog_mcp/server.py`**

```python
"""MCP server setup."""
import logging
from mcp.server import Server
from mcp.server.stdio import stdio_server

from voog_mcp.config import load_config
from voog_mcp.client import VoogClient

logger = logging.getLogger("voog-mcp")


async def run_server():
    config = load_config()
    client = VoogClient(host=config.host, api_token=config.api_token)
    server = Server(name="voog-mcp", version="0.1.0")

    # Tools and resources will be registered in Phase C/D

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
```

- [ ] **Step 6.2: Write `voog_mcp/config.py`**

```python
"""Config loading from MCP server env vars."""
import os
import sys
from dataclasses import dataclass


@dataclass
class Config:
    host: str
    api_token: str


def load_config() -> Config:
    host = os.environ.get("VOOG_HOST")
    token = os.environ.get("VOOG_API_TOKEN")
    if not host:
        sys.stderr.write("❌ VOOG_HOST env muutuja puudub\n")
        sys.exit(1)
    if not token:
        sys.stderr.write("❌ VOOG_API_TOKEN env muutuja puudub\n")
        sys.exit(1)
    return Config(host=host, api_token=token)
```

- [ ] **Step 6.3: Update `voog_mcp/__main__.py`**

```python
"""Entry point: `voog-mcp` console script or `python3 -m voog_mcp`."""
import asyncio
import logging
import sys

from voog_mcp.server import run_server


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
```

- [ ] **Step 6.4: Remove skip from integration test**

In `tests/test_mcp_integration.py`, remove `@unittest.skip(...)` decorator.

- [ ] **Step 6.5: Run integration test**

```bash
cd /Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Tööriistad
RUN_SMOKE=1 python3 -m unittest tests.test_mcp_integration -v
```
Expected: PASS — server initializes, returns capabilities.

- [ ] **Step 6.6: Commit**

```bash
git add voog_mcp/server.py voog_mcp/config.py voog_mcp/__main__.py tests/test_mcp_integration.py
git commit -m "feat(mcp): minimal server with initialize handshake"
```

---

### Task 7: Add config validation tests

**Eesmärk:** Cover Config loading edge cases (missing env vars).

**Files:**
- Create: `Tööriistad/tests/test_config.py`

- [ ] **Step 7.1: Write tests for config loading**

```python
"""Tests for voog_mcp.config."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voog_mcp.config import load_config


class TestLoadConfig(unittest.TestCase):
    def test_missing_host_exits(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("VOOG_HOST", None)
            os.environ.pop("VOOG_API_TOKEN", None)
            with self.assertRaises(SystemExit):
                load_config()

    def test_missing_token_exits(self):
        with patch.dict(os.environ, {"VOOG_HOST": "runnel.ee"}, clear=True):
            with self.assertRaises(SystemExit):
                load_config()

    def test_both_set_returns_config(self):
        env = {"VOOG_HOST": "runnel.ee", "VOOG_API_TOKEN": "abc"}
        with patch.dict(os.environ, env, clear=True):
            cfg = load_config()
            self.assertEqual(cfg.host, "runnel.ee")
            self.assertEqual(cfg.api_token, "abc")
```

- [ ] **Step 7.2: Run, expect 3 tests pass**

```bash
python3 -m unittest tests.test_config -v
```

- [ ] **Step 7.3: Commit**

```bash
git add tests/test_config.py
git commit -m "test(mcp): config loading edge cases"
```

---

### Task 8: Stderr logging + structured error handling

**Eesmärk:** Veendu et **server'i log'id lähevad stderr'isse** (mitte stdout'i, mis on JSON-RPC channel). Lisa basic error wrapping infrastruktuur (kasutame seda Phase C tool'ides).

**Files:**
- Modify: `Tööriistad/voog_mcp/__main__.py` (logging setup)
- Create: `Tööriistad/voog_mcp/errors.py`

- [ ] **Step 8.1: Write `voog_mcp/errors.py`**

```python
"""MCP error response helpers."""
import json
from typing import Any
from mcp.types import TextContent


def error_response(message: str, *, details: dict[str, Any] | None = None) -> list[TextContent]:
    """Return a tool error response as TextContent."""
    payload = {"error": message}
    if details:
        payload["details"] = details
    return [TextContent(type="text", text=json.dumps(payload, indent=2))]


def success_response(data: Any, *, summary: str = "") -> list[TextContent]:
    """Return a tool success response with optional human-readable summary."""
    if summary:
        return [
            TextContent(type="text", text=summary),
            TextContent(type="text", text=json.dumps(data, indent=2, ensure_ascii=False)),
        ]
    return [TextContent(type="text", text=json.dumps(data, indent=2, ensure_ascii=False))]
```

- [ ] **Step 8.2: Verify logging goes to stderr only**

In `voog_mcp/__main__.py`, ensure `logging.basicConfig(stream=sys.stderr)`.

Manual test:
```bash
voog-mcp 2>/dev/null < /dev/null
# Should NOT print anything to stdout (because no input → no response).
```

- [ ] **Step 8.3: Commit**

```bash
git add voog_mcp/errors.py voog_mcp/__main__.py
git commit -m "feat(mcp): error response helpers + verify stderr-only logging"
```

---

## Phase C: Tools (parallel-safe within phase)

Each task = one file in `voog_mcp/tools/` + tests. Multiple sessions can pick different tasks.

### Task 9 (parallel): Pages read tools — `pages_list`, `page_get`, `pages_pull`

**Files:**
- Create: `Tööriistad/voog_mcp/tools/pages.py`
- Create: `Tööriistad/tests/test_tools_pages.py`

**Eesmärk:** 3 read-only MCP tool'i pages andmekogu jaoks.

- [ ] **Step 9.1: Define tool list + schemas in `tools/pages.py`**

```python
"""MCP tools for Voog pages (read-only)."""
import json
from mcp.types import Tool, TextContent
from voog_mcp.client import VoogClient
from voog_mcp.errors import success_response, error_response


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="pages_list",
            description="List all pages on the Voog site (id, path, title, hidden, layout name). Read-only.",
            inputSchema={"type": "object", "properties": {}, "required": []},
            annotations={"readOnlyHint": True},
        ),
        Tool(
            name="page_get",
            description="Get full details of a single page by id (title, path, hidden, layout, language, parent, timestamps, public_url).",
            inputSchema={
                "type": "object",
                "properties": {"page_id": {"type": "integer", "description": "Voog page id"}},
                "required": ["page_id"],
            },
            annotations={"readOnlyHint": True},
        ),
        Tool(
            name="pages_pull",
            description="Save simplified pages.json to disk (structure snapshot, no content bodies). Returns the simplified array.",
            inputSchema={"type": "object", "properties": {}, "required": []},
            annotations={"readOnlyHint": True},
        ),
    ]


async def call_tool(name: str, arguments: dict, client: VoogClient) -> list[TextContent]:
    if name == "pages_list":
        try:
            pages = client.get_all("/pages")
            simplified = _simplify_pages(pages)
            return success_response(simplified, summary=f"📄 {len(simplified)} pages")
        except Exception as e:
            return error_response(f"pages_list ebaõnnestus: {e}")

    if name == "page_get":
        page_id = arguments.get("page_id")
        try:
            p = client.get(f"/pages/{page_id}")
            return success_response(p)
        except Exception as e:
            return error_response(f"page_get id={page_id} ebaõnnestus: {e}")

    if name == "pages_pull":
        try:
            pages = client.get_all("/pages")
            simplified = _simplify_pages(pages)
            return success_response(simplified, summary=f"✓ pages-pull: {len(simplified)} entries")
        except Exception as e:
            return error_response(f"pages_pull ebaõnnestus: {e}")

    return error_response(f"Unknown tool: {name}")


def _simplify_pages(pages: list) -> list:
    """Project pages to simplified structure (matching voog.py pages_pull)."""
    simplified = []
    for p in pages:
        lang = p.get("language") or {}
        layout = p.get("layout") or {}
        simplified.append({
            "id": p.get("id"),
            "path": p.get("path"),
            "title": p.get("title"),
            "hidden": p.get("hidden"),
            "layout_id": p.get("layout_id") or layout.get("id"),
            "layout_name": p.get("layout_name") or layout.get("title"),
            "content_type": p.get("content_type"),
            "parent_id": p.get("parent_id"),
            "language_code": lang.get("code"),
            "public_url": p.get("public_url"),
        })
    return simplified
```

- [ ] **Step 9.2: Wire tools into `server.py`** (in registration code added Task 6, may need to extend)

```python
# voog_mcp/server.py — additions
from voog_mcp.tools import pages as pages_tools

# In run_server(), register tool handlers:

@server.list_tools()
async def handle_list_tools():
    return [
        *pages_tools.get_tools(),
        # Other tool groups added in subsequent tasks
    ]


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict):
    if name in ("pages_list", "page_get", "pages_pull"):
        return await pages_tools.call_tool(name, arguments, client)
    # Other groups
    raise ValueError(f"Unknown tool: {name}")
```

- [ ] **Step 9.3: Write unit tests in `tests/test_tools_pages.py`**

```python
import unittest
from unittest.mock import MagicMock
from voog_mcp.tools import pages as pages_tools


class TestPagesTools(unittest.TestCase):
    def test_get_tools_returns_three(self):
        tools = pages_tools.get_tools()
        names = [t.name for t in tools]
        self.assertEqual(names, ["pages_list", "page_get", "pages_pull"])
        # All marked read-only
        for t in tools:
            self.assertTrue(t.annotations.get("readOnlyHint"))

    def test_pages_list_calls_client(self):
        client = MagicMock()
        client.get_all.return_value = [{"id": 1, "title": "Foo"}]
        import asyncio
        result = asyncio.run(pages_tools.call_tool("pages_list", {}, client))
        client.get_all.assert_called_once_with("/pages")
        self.assertEqual(len(result), 2)  # summary + JSON
```

- [ ] **Step 9.4: Run tests, smoke via MCP inspector or integration test**

```bash
python3 -m unittest tests.test_tools_pages -v
```

- [ ] **Step 9.5: Commit**

```bash
git add voog_mcp/tools/pages.py voog_mcp/server.py tests/test_tools_pages.py
git commit -m "feat(mcp): pages tools — pages_list, page_get, pages_pull"
```

---

### Task 10 (parallel): Pages mutate tools — `page_set_hidden`, `page_set_layout`, `page_delete`

Same pattern as Task 9. Files: `voog_mcp/tools/pages_mutate.py`, `tests/test_tools_pages_mutate.py`. Tools annotated with `destructiveHint: true` for `page_delete`. JSON Schemas:

- `page_set_hidden`: `{ids: array<integer>, hidden: boolean}`
- `page_set_layout`: `{page_id: integer, layout_id: integer}`
- `page_delete`: `{page_id: integer, force: boolean}` — annotation `destructiveHint=true`

Implementation parallels current `voog.py` versions, returning `success_response` with summary text.

Wire into `server.py` `handle_list_tools` and `handle_call_tool` dispatch.

Commit: `feat(mcp): pages mutate tools — set_hidden, set_layout, delete`

---

### Task 11 (parallel): Layout tools — `layout_rename`, `layouts_pull`, `layouts_push`

Files: `voog_mcp/tools/layouts.py`, `tests/test_tools_layouts.py`.

- `layout_rename`: `{layout_id: integer, new_title: string}` — validates new_title (no `/`, `\`, leading `.`)
- `layouts_pull`: `{}` — runs full pull, returns count summary
- `layouts_push`: `{files: array<string> | null}` — push files (or all if null), confirms first via `force: boolean` param

Note: pull/push currently write to LOCAL_DIR (cwd). For MCP, decide: (a) require `target_dir` parameter, (b) operate on a server-config-defined dir. **Recommendation:** add `target_dir` parameter to be explicit.

Commit: `feat(mcp): layout tools — rename, pull, push`

---

### Task 12 (parallel): Snapshot tool — `pages_snapshot`

Files: `voog_mcp/tools/snapshot.py`, `tests/test_tools_snapshot.py`.

- `pages_snapshot`: `{output_dir: string}` — writes pages.json + per-page contents files. v1 sync, v2 progress.

Commit: `feat(mcp): pages_snapshot tool`

---

### Task 13 (parallel): Products tools — `products_list`, `product_get`, `product_update`, `product_set_images`

Files: `voog_mcp/tools/products.py`, `tests/test_tools_products.py`.

All use `client.get(..., base=client.ecommerce_url)`.

- `products_list`: `{}`
- `product_get`: `{product_id: integer}`
- `product_update`: `{product_id: integer, fields: object}` — fields like `{"name-et": "...", "slug-en": "..."}`
- `product_set_images`: `{product_id: integer, files: array<string>}`

Commit: `feat(mcp): products tools — list, get, update, set_images`

---

### Task 14 (parallel): Redirect tools — `redirects_list`, `redirect_add`

Files: `voog_mcp/tools/redirects.py`, `tests/test_tools_redirects.py`.

- `redirects_list`: `{}`
- `redirect_add`: `{source: string, destination: string, redirect_type: integer (default 301)}`

Commit: `feat(mcp): redirect tools — list, add`

---

## Phase D: Resources (parallel-safe within phase)

Each task = one resource handler in `voog_mcp/resources/`.

### Task 15 (parallel): Pages resources

Files: `voog_mcp/resources/pages.py`, `tests/test_resources_pages.py`.

URIs handled:
- `voog://pages` — list all pages
- `voog://pages/{id}` — single page
- `voog://pages/{id}/contents` — contents

Wire into `server.py`:
```python
@server.list_resources()
async def handle_list_resources():
    return [
        Resource(uri="voog://pages", name="All pages", mimeType="application/json"),
    ]


@server.read_resource()
async def handle_read_resource(uri: AnyUrl):
    # Dispatch by URI prefix
    if str(uri).startswith("voog://pages"):
        return await pages_resources.read(uri, client)
    ...
```

Commit: `feat(mcp): pages resources`

---

### Task 16 (parallel): Layouts resources

URIs:
- `voog://layouts` — list
- `voog://layouts/{id}` — body (raw .tpl content)

Returns `mimeType: text/plain` for body.

Commit: `feat(mcp): layouts resources`

---

### Task 17 (parallel): Articles resources

URIs:
- `voog://articles` — list (id, title, page_id, language)
- `voog://articles/{id}` — full article with body HTML

Commit: `feat(mcp): articles resources`

---

### Task 18 (parallel): Products resources

URIs:
- `voog://products` — list (id, name, slug, status, price)
- `voog://products/{id}` — full product with translations + variants

Use `?include=variant_types,translations` query param.

Commit: `feat(mcp): products resources`

---

### Task 19 (parallel): Redirects resource

URI:
- `voog://redirects` — list

Commit: `feat(mcp): redirects resource`

---

## Phase E: Polish + Distribution (parallel-safe)

### Task 20 (parallel): Tool annotations finalization

Walk through all tools, add proper annotations:
- `readOnlyHint: true` for: pages_list, page_get, pages_pull, pages_snapshot, products_list, product_get, redirects_list, layouts_pull
- `destructiveHint: true` for: page_delete, layouts_push (overwrites), product_set_images (replaces images)
- `idempotentHint: true` for: pages_snapshot, pages_pull, layouts_pull (safe retry)

Commit: `feat(mcp): finalize tool annotations`

---

### Task 21 (parallel): MCP integration tests for each tool group

Expand `tests/test_mcp_integration.py` to verify each tool group end-to-end via subprocess + JSON-RPC.

Pattern per tool:
```python
def test_pages_list_via_mcp(self):
    response = self._call_tool("pages_list", {})
    self.assertGreaterEqual(len(response["result"]["content"]), 1)
    pages = json.loads(response["result"]["content"][1]["text"])  # JSON block
    self.assertGreater(len(pages), 50)  # Real Voog has many pages
```

These run only with `RUN_SMOKE=1` env var (real API calls). 1 test per major tool group.

Commit: `test(mcp): MCP protocol integration tests for all tool groups`

---

### Task 22 (parallel): README + claude_desktop_config example

Update `Tööriistad/README.md`:
- Installation (`pip install -e .` until PyPI)
- Configuration example for `claude_desktop_config.json`
- Tool inventory (link to spec)
- Resource URIs (link to spec)
- CLI fallback (voog.py still works)

Commit: `docs(mcp): README installation and Claude config example`

---

### Task 23 (parallel): Skill update — `~/.claude/skills/voog/SKILL.md`

Add new section "voog-mcp server" describing:
- How to register in claude_desktop_config.json
- Tool naming convention (`mcp__voog-runnel__pages_list`, etc.)
- Resource URIs available
- When to prefer MCP tool vs CLI fallback

Commit: `docs(skill): add voog-mcp server section`

---

## Self-Review Notes

**Spec coverage:**
- ✅ Server skeleton + initialize (Task 6)
- ✅ All 17 CLI commands → MCP tools (Tasks 9–14)
- ✅ Resources for pages/layouts/articles/products/redirects (Tasks 15–19)
- ✅ Tool annotations (Task 20)
- ✅ Integration tests (Tasks 5, 21)
- ✅ Distribution config (Task 2)
- ✅ Backward compat (Task 4)
- ⏸ Progress notifications — DEFERRED to v0.3 (spec §8 noted)
- ⏸ Prompts — DEFERRED to v0.3

**Parallelization confidence:**
- Phase A is strictly sequential (foundation)
- Phase B is sequential (server depends on foundation)
- Phase C: 6 tasks independent (different tool files), but ALL register into `server.py` which becomes shared. Mitigation: Task 9 establishes server.py registration pattern; later tasks ADD entries (no conflicts if each session adds tools to a unique elif branch). **If conflict: rebase + retry, easy resolution.**
- Phase D: 5 tasks independent (different resource files). Same shared file concern with `server.py`.
- Phase E: 4 tasks independent.

**Risk areas:**
- `server.py` becomes coordination point — if 6 sessions add tool registrations simultaneously, merge conflicts. **Recommendation:** When parallelizing Phase C, complete Task 9 (which establishes the dispatcher pattern) FIRST, then 10–14 in parallel. Each session edits ONE elif branch in `handle_call_tool` plus appends to `handle_list_tools` list — small surface area, easy resolution.

**Tests strategy:**
- Unit tests per tool/resource file (mocked client)
- Integration tests run only with `RUN_SMOKE=1` (real API)
- All run via `python3 -m unittest discover tests -v`

---

## Execution

Plan complete and saved to `Tööriistad/docs/plans/2026-04-26-mcp-server-plan.md`.

**Recommended approach for parallel sessions:**
- **Session 1:** Phase A (Tasks 1–5) sequentially → Phase B (Tasks 6–8) sequentially → Task 9 (establishes pattern)
- **Sessions 2–6 (after Task 9 done):** Pick remaining Phase C task (10–14)
- **Sessions 7–11:** Pick Phase D task (15–19)
- **Sessions 12–14:** Pick Phase E task (20–23)

**Coordination via TodoWrite:** Each session marks task `in_progress` when starting, `completed` when PR merged. Visible state across sessions.

**Branch convention:** `feat/mcp-task-NN-description` (e.g. `feat/mcp-task-09-pages-tools`). One PR per task.
