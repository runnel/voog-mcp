# Voog MCP Endpoint Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the "fall back to ad-hoc curl/Python script" pattern. After this plan lands, every Voog Admin API and Ecommerce v1 API operation a Claude session realistically needs is reachable through `voog-mcp` — either via a curated typed tool or via a generic API passthrough.

**Architecture:** Three layers, ordered by reach-per-effort:

1. **Generic API passthrough** — two tools (`voog_admin_api_call`, `voog_ecommerce_api_call`) that take `method`, `path`, optional `body`/`params`, and proxy through the existing `VoogClient`. Single PR makes every endpoint reachable.
2. **Curated typed tools** for the operations the project memory shows are common (full product update, article CRUD with `autosaved_*`, page create/update with `node_id`, text body edit, layout body edit). Each carries explicit MCP annotations and validates inputs.
3. **Multilingual + ergonomic helpers** (`languages_list`, `nodes_list`/`node_get`, redirect update/delete, ecommerce settings, site singleton). Small wrappers that don't add behaviour but make the curated path discoverable and remove "I have to use the raw passthrough" friction for these specific workflows.

Layers compose: typed tools give the best UX where they exist; the generic passthrough catches everything else without forcing sessions to drop down to the shell.

**Tech Stack:** Python ≥3.10, MCP Python SDK, urllib (stdlib only — no deps), `unittest` + `unittest.mock.MagicMock` for tests (existing repo convention), `ruff` for lint.

**Repo:** `/Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Tööriistad` (= [voog-mcp](https://github.com/runnel/voog-mcp), branch `master`).

**Pre-flight before starting any task:** the implementation should happen on a feature branch + worktree. From the repo root:

```bash
git switch -c feat/endpoint-coverage
git worktree add /tmp/voog-mcp-coverage feat/endpoint-coverage
cd /tmp/voog-mcp-coverage
.venv/bin/python -m pip install -e .  # or: uv pip install -e .
.venv/bin/python -m pytest -q          # baseline: must be green
```

---

## Gap analysis snapshot

**Currently exposed via MCP tools (16 tools, v1.1.1):**

| Group | Tools |
|---|---|
| Sites discovery | `voog_list_sites` |
| Pages | `pages_list`, `page_get`, `page_set_hidden`, `page_set_layout`, `page_delete` |
| Layouts | `layout_rename`, `layout_create`, `asset_replace`, `layouts_pull`, `layouts_push` |
| Products | `products_list`, `product_get`, `product_update` (**name+slug only**), `product_set_images` |
| Redirects | `redirects_list`, `redirect_add` |
| Snapshot | `pages_snapshot`, `site_snapshot` |

**Currently exposed via MCP resources (read-only):** `voog://{site}/{pages,pages/{id},pages/{id}/contents,layouts,layouts/{id},articles,articles/{id},products,products/{id},redirects}`.

**Gaps the project memory and the user's complaint highlight:**

| Need | Today's workaround in sessions | Fix tier |
|---|---|---|
| Update product `description` (or `status`, `price`, `sale_price`, `sku`, `stock`, `category_ids`) | Curl to `/products/{id}` directly | Tier 2 (typed) |
| Create / update / delete blog articles (with `autosaved_*` + `publishing:true` semantics) | Custom Python with `urllib.request` | Tier 2 (typed) |
| Create a new page (incl. parallel-translation `node_id` semantics) | CLI has `voog page-create`, MCP doesn't | Tier 2 (typed) |
| Update page title / slug / data fields / image_id / description / keywords | Curl `PUT /pages/{id}` | Tier 2 (typed) |
| Update a page's text content body | Manual `GET /pages/{id}/contents` → `PUT /texts/{id}` | Tier 2 (typed) |
| Add a content area to a fresh page | CLI has `voog page-add-content`, MCP doesn't | Tier 2 (typed) |
| Edit a layout body (Liquid template) without filesystem step | `layouts_pull` + edit + `layouts_push`; or curl `PUT /layouts/{id}` | Tier 2 (typed) |
| Languages / nodes lookup for multilingual workflows | Curl | Tier 3 (helper) |
| Update / delete a redirect rule | Curl `PUT/DELETE /redirect_rules/{id}` | Tier 3 (helper) |
| Ecommerce settings (per-language `products_url_slug`) | Curl `PUT /ecommerce/v1/settings` | Tier 3 (helper) |
| Site singleton (`/site`, `/site/data/{key}`, favicon) | Curl | Tier 3 (helper) |
| Anything else (orders, carts, discounts, shipping_methods, gateways, forms, tickets, elements, element_definitions, tags, media_sets, webhooks, content_partials, code_partials, templates, bulk product update, products imports, …) | Curl / Python script | **Tier 1 (generic passthrough)** |

**Verdict:** Tier 1 alone (~one task) closes the "fall back to curl" pattern for every endpoint. Tiers 2 and 3 raise UX above raw passthrough on the operations Claude touches frequently.

---

## File map

This plan creates / modifies the following files:

```
src/voog/mcp/tools/
  raw.py                  CREATE — generic Admin/Ecommerce passthrough tools
  products.py             MODIFY — extend product_update fields + add product_update_full
  articles.py             CREATE — article CRUD tools
  pages_mutate.py         MODIFY — add page_create, page_update, page_set_data, page_duplicate
  texts.py                CREATE — text_get, text_update, page_add_content tools
  layouts.py              MODIFY — add layout_update, layout_delete, layout_asset_create/update/delete
  multilingual.py         CREATE — languages_list, nodes_list, node_get
  redirects.py            MODIFY — add redirect_update, redirect_delete
  ecommerce_settings.py   CREATE — ecommerce_settings_get, ecommerce_settings_update
  site.py                 CREATE — site_get, site_update, site_set_data
  __init__.py             unchanged (empty)

src/voog/mcp/server.py    MODIFY — register new tool groups in TOOL_GROUPS
src/voog/_payloads.py     MODIFY — add envelope helpers for product, page, article, layout
src/voog/projections.py   MODIFY — simplify_articles already exists; add simplify_languages, simplify_nodes
src/voog/mcp/resources/articles.py    MODIFY — add /articles/{id}/raw resource (full JSON, not just body) — optional
docs/voog-mcp-endpoint-coverage.md    CREATE — gap-analysis reference doc

tests/
  test_tools_raw.py                  CREATE
  test_tools_products.py             MODIFY — add full-field-update tests
  test_tools_articles.py             CREATE
  test_tools_pages_mutate.py         MODIFY — add create/update/data/duplicate tests
  test_tools_texts.py                CREATE
  test_tools_layouts.py              MODIFY — add update/delete/asset tests
  test_tools_multilingual.py         CREATE
  test_tools_redirects.py            MODIFY — add update/delete tests
  test_tools_ecommerce_settings.py   CREATE
  test_tools_site.py                 CREATE
  test_payloads.py                   MODIFY — add envelope tests
  test_projections.py                MODIFY — add simplify_languages/nodes tests

CHANGELOG.md              MODIFY — append unreleased entries per task
README.md                 MODIFY — refresh tool inventory table
pyproject.toml            MODIFY — bump to 1.2.0 in the final task
```

Each module follows the existing pattern in `src/voog/mcp/tools/` — `get_tools()`, `call_tool()`, private `_handler()` per tool, `error_response`/`success_response` from `voog.errors`, `strip_site` from `voog.mcp.tools._helpers`, explicit MCP annotation triple per tool.

---

## Task 1: Write the gap-analysis reference doc

The other tasks reference this doc for which fields go on which envelope. Get it down on paper first so future maintainers (and the engineer running this plan) don't have to re-derive it from the live API.

**Files:**
- Create: `docs/voog-mcp-endpoint-coverage.md`

- [ ] **Step 1: Create the doc**

```bash
mkdir -p docs
```

Write `docs/voog-mcp-endpoint-coverage.md` with the following content:

````markdown
# Voog MCP — endpoint coverage reference

This document maps Voog API endpoints to MCP tools and notes envelope shapes,
mutable fields, and gotchas. Maintained alongside `src/voog/mcp/tools/` —
update this doc when a tool is added or a new endpoint quirk is discovered.

## API surfaces

- **Admin API** — `https://{host}/admin/api/*`
- **Ecommerce v1 API** — `https://{host}/admin/api/ecommerce/v1/*`
- Auth: `X-API-Token: <token>` header (already handled by `VoogClient`)
- Pagination default: 50 / max 250; `voog.client.VoogClient.get_all` defaults to 100 per page
- Filter syntax: `q.<obj>.<attr>.<comp>=value` (`$eq`, `$cont`, `$gt`, …)
- Response shaping: `include=foo,bar`, `language_code=<iso>`

## Coverage matrix

| Resource | Read tools | Write tools | Notes |
|---|---|---|---|
| Pages | `pages_list`, `page_get` | `page_set_hidden`, `page_set_layout`, `page_delete`, `page_create`, `page_update`, `page_set_data`, `page_duplicate` | `parent_id` is a page id, NOT node_id; root pages omit `parent_id`. Parallel translations use `node_id` (see Multilingual). |
| Articles | `articles_list`, `article_get` | `article_create`, `article_update`, `article_publish`, `article_delete` | Use `autosaved_title/excerpt/body` on PUT; `publishing: true` to push autosaved → published. `description` ≠ `excerpt` (see skill memory). |
| Layouts | (resource only) | `layout_rename`, `layout_create`, `layout_update`, `layout_delete`, `asset_replace` | `PUT /layouts/{id}` accepts `body` + `title` only. |
| Layout assets | (resource only) | `layout_asset_create`, `layout_asset_update`, `layout_asset_delete` | PUT `data` only — `filename` is read-only (use `asset_replace`). |
| Texts | (none) | `text_get`, `text_update`, `page_add_content` | Page content bodies live here. Fresh pages return `[]` from `/contents` until edit-mode trigger. |
| Redirects | `redirects_list` | `redirect_add`, `redirect_update`, `redirect_delete` | redirect_type ∈ {301, 302, 307, 410}. |
| Languages | `languages_list` | (none) | Read-only here — language_id resolution helper for page_create. |
| Nodes | `nodes_list`, `node_get` | (none) | Helper for parallel translations: `POST /pages` with `node_id` of existing page. |
| Site | `site_get` | `site_update`, `site_set_data` | `site.code` immutable once set. `data.internal_*` keys read-only. |
| Products | `products_list`, `product_get` | `product_update` (full fields), `product_set_images` | `description`, `status`, `price`, `sale_price`, `sku`, `stock`, `category_ids`, `physical_properties`, `variant_types`, `translations.*` all supported. PUT envelope is `{"product": {...}}`. |
| Ecommerce settings | `ecommerce_settings_get` | `ecommerce_settings_update` | Per-language `products_url_slug` lives in `translations`. |
| **Everything else** | `voog_admin_api_call(method, path, ...)` | `voog_ecommerce_api_call(method, path, ...)` | Generic passthrough — same auth, same timeout, no envelope assumed. Use for orders, carts, discounts, gateways, shipping_methods, forms, tickets, tags, elements, element_definitions, media_sets, webhooks, content_partials, templates, bulk update, imports, search. |

## Envelope conventions

Voog uses different wrapping conventions per endpoint. The `voog._payloads`
module centralises these so CLI and MCP cannot drift.

| Endpoint | Wrapper | Example |
|---|---|---|
| `POST/PUT /pages` | `{"page": {...}}` OR flat (Voog accepts both; flat is what the existing CLI uses) | `{"title": "...", "slug": "..."}` |
| `POST/PUT /articles` | flat | `{"autosaved_title": "...", "publishing": true}` |
| `POST/PUT /layouts` | flat | `{"title": "...", "body": "..."}` |
| `POST/PUT /layout_assets` | flat | `{"filename": "...", "asset_type": "...", "data": "..."}` |
| `PUT /texts/{id}` | flat | `{"body": "<html>..."}` |
| `POST /redirect_rules` | `{"redirect_rule": {...}}` | (already in `voog._payloads`) |
| `PUT /products/{id}` | `{"product": {...}}` | translations / fields nested |
| `PUT /products/{id}` for image_id+asset_ids | **flat, NOT wrapped** | `{"image_id": ..., "asset_ids": [...]}` (empirically confirmed in `product_set_images`) |
| `PUT /ecommerce/v1/settings` | `{"settings": {...}}` | per-lang `products_url_slug` under `translations` |
| `PUT /site` | flat | `{"title": "..."}` |

## Read-only fields (do not send on PUT)

- `article.body`, `article.title`, `article.excerpt` — write to `autosaved_*` instead
- `layout_asset.filename` — DELETE+POST workaround via `asset_replace`
- `page.public_url`, `page.path` (auto-derived from slug+parent), `page.created_at`, `page.updated_at`
- `product.in_stock`, `product.on_sale`, `product.effective_price`, `product.price_min/max`, `product.uses_variants` (computed)
- `site.code` (immutable once set + once site has paid plan)
- Any `data.internal_*` key (server-protected)

## Status enum quick reference

- `product.status`: `"draft"` | `"live"` (NOT `"active"`/`"published"`/`"hidden"`)
- `redirect_rule.redirect_type`: 301, 302, 307, 410
- `layout.content_type`: `page`, `blog`, `blog_article`, `elements`, `element`, `product`, `error_401`, `error_404`, `component`
- `page.content_type`: `page`, `link`, `blog`, etc.

## Cross-references

- Voog official docs index: <https://www.voog.com/developers/api>
- Project memory (skill): `.claude/skills/voog/SKILL.md`
- CHANGELOG entries for each tool: `CHANGELOG.md`
````

- [ ] **Step 2: Commit**

```bash
git add docs/voog-mcp-endpoint-coverage.md
git commit -m "docs: add MCP endpoint coverage reference

Single-source-of-truth mapping API endpoints to MCP tools, envelope
shapes per endpoint, read-only field list, and status enums. Updated
alongside src/voog/mcp/tools/ to prevent drift."
```

---

## Task 2: Generic Admin API + Ecommerce v1 passthrough tools

The single highest-value change in the plan. Two tools that take `method` + `path` + optional `body`/`params` and forward through `VoogClient`. After this lands, no session ever has to fall back to `curl` for an endpoint the typed tools don't cover.

**Why two tools instead of one with a `base` parameter:** Claude reasons about tool scope from the tool name. `voog_admin_api_call` says "you are about to hit the Admin API"; `voog_ecommerce_api_call` says "you are about to hit Ecommerce v1". The split also lets us tune the description per surface (e.g. ecommerce mentions `?include=` and `?language_code=`).

**Files:**
- Create: `src/voog/mcp/tools/raw.py`
- Create: `tests/test_tools_raw.py`
- Modify: `src/voog/mcp/server.py` — add to `TOOL_GROUPS`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tools_raw.py`:

```python
"""Tests for voog.mcp.tools.raw — generic Admin/Ecommerce passthrough."""

import json
import unittest
import urllib.error
from unittest.mock import MagicMock

from voog.mcp.tools import raw as raw_tools


class TestGetTools(unittest.TestCase):
    def test_two_tools_registered(self):
        names = [t.name for t in raw_tools.get_tools()]
        self.assertEqual(
            sorted(names),
            ["voog_admin_api_call", "voog_ecommerce_api_call"],
        )

    def test_admin_call_annotations(self):
        tools = {t.name: t for t in raw_tools.get_tools()}
        ann = tools["voog_admin_api_call"].annotations
        # Generic passthrough — any method possible, so the annotations
        # must be conservative.
        self.assertIs(ann.readOnlyHint, False)
        self.assertIs(ann.destructiveHint, True)
        self.assertIs(ann.idempotentHint, False)


class TestAdminApiCall(unittest.TestCase):
    def test_get_request_passthrough(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.get.return_value = [{"id": 1}, {"id": 2}]
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "GET", "path": "/forms"},
            client,
        )
        client.get.assert_called_once_with(
            "/forms",
            base="https://example.com/admin/api",
            params=None,
        )
        body = json.loads(result[1].text)
        self.assertEqual(body, [{"id": 1}, {"id": 2}])

    def test_get_with_params(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.get.return_value = {"ok": True}
        raw_tools.call_tool(
            "voog_admin_api_call",
            {
                "method": "GET",
                "path": "/articles",
                "params": {"q.article.title.$cont": "kuju"},
            },
            client,
        )
        client.get.assert_called_once_with(
            "/articles",
            base="https://example.com/admin/api",
            params={"q.article.title.$cont": "kuju"},
        )

    def test_put_with_body(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.put.return_value = {"id": 42, "title": "X"}
        raw_tools.call_tool(
            "voog_admin_api_call",
            {
                "method": "PUT",
                "path": "/forms/42",
                "body": {"title": "X"},
            },
            client,
        )
        client.put.assert_called_once_with(
            "/forms/42",
            {"title": "X"},
            base="https://example.com/admin/api",
        )

    def test_post_with_body(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.post.return_value = {"id": 7}
        raw_tools.call_tool(
            "voog_admin_api_call",
            {
                "method": "POST",
                "path": "/articles",
                "body": {"page_id": 1, "autosaved_title": "Draft"},
            },
            client,
        )
        client.post.assert_called_once_with(
            "/articles",
            {"page_id": 1, "autosaved_title": "Draft"},
            base="https://example.com/admin/api",
        )

    def test_delete_request(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.delete.return_value = None
        raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "DELETE", "path": "/redirect_rules/9"},
            client,
        )
        client.delete.assert_called_once_with(
            "/redirect_rules/9",
            base="https://example.com/admin/api",
        )

    def test_rejects_unknown_method(self):
        client = MagicMock()
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "TRACE", "path": "/site"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("method", payload["error"].lower())

    def test_rejects_path_without_leading_slash(self):
        client = MagicMock()
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "GET", "path": "site"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("/", payload["error"])

    def test_rejects_absolute_url(self):
        client = MagicMock()
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "GET", "path": "https://evil.example.com/exfil"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("absolute", payload["error"].lower())

    def test_rejects_path_traversal(self):
        client = MagicMock()
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "GET", "path": "/../../../../etc/passwd"},
            client,
        )
        self.assertTrue(result.isError)

    def test_api_error_propagates(self):
        client = MagicMock()
        client.base_url = "https://example.com/admin/api"
        client.get.side_effect = urllib.error.HTTPError(
            "url", 422, "Unprocessable Entity", {}, None
        )
        result = raw_tools.call_tool(
            "voog_admin_api_call",
            {"method": "GET", "path": "/site"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("422", payload["error"])


class TestEcommerceApiCall(unittest.TestCase):
    def test_uses_ecommerce_base(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.get.return_value = []
        raw_tools.call_tool(
            "voog_ecommerce_api_call",
            {"method": "GET", "path": "/orders"},
            client,
        )
        client.get.assert_called_once_with(
            "/orders",
            base="https://example.com/admin/api/ecommerce/v1",
            params=None,
        )

    def test_put_settings(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.return_value = {"settings": {}}
        raw_tools.call_tool(
            "voog_ecommerce_api_call",
            {
                "method": "PUT",
                "path": "/settings",
                "body": {
                    "settings": {
                        "translations": {
                            "products_url_slug": {"en": "products"}
                        }
                    }
                },
            },
            client,
        )
        client.put.assert_called_once()
        path, body = client.put.call_args.args
        self.assertEqual(path, "/settings")
        self.assertEqual(
            body["settings"]["translations"]["products_url_slug"]["en"],
            "products",
        )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_tools_raw.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'voog.mcp.tools.raw'`.

- [ ] **Step 3: Implement the module**

Create `src/voog/mcp/tools/raw.py`:

```python
"""Generic Admin API + Ecommerce v1 API passthrough tools.

Two tools: ``voog_admin_api_call`` and ``voog_ecommerce_api_call``. Both take
``method`` + ``path`` + optional ``body`` and ``params``, then proxy through
the configured :class:`voog.client.VoogClient`. They cover endpoints the
typed tools haven't gotten to yet — orders, carts, discounts, shipping,
gateways, forms, tickets, elements, tags, media_sets, webhooks, bulk
operations, products imports, search, etc.

Why two tools instead of one with a ``base`` parameter: tool name carries
intent. ``voog_admin_api_call`` advertises "Admin API"; the ecommerce tool
advertises Ecommerce v1 (``?include=``, ``?language_code=``, anonymous-
allowed reads, etc.).

Annotations: both tools are ``destructiveHint=True`` because *any* method
is possible. Claude will surface a confirmation prompt before invoking
them. Callers pick a method explicitly; we don't try to guess intent.

Path validation rejects three obvious foot-guns:
  - Empty path or path without a leading ``/`` (would build an invalid URL).
  - Absolute URL (would let the caller bypass the configured host — a
    secret-exfiltration vector if the response is logged).
  - ``..`` segments (no legitimate Voog endpoint contains them; refusing
    them is cheap defence-in-depth).
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site

ALLOWED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="voog_admin_api_call",
            description=(
                "Generic Admin API passthrough. Forward an HTTP request to "
                "https://<host>/admin/api<path> using the configured site's "
                "API token. method ∈ {GET, POST, PUT, PATCH, DELETE}; body "
                "is JSON-serialised on POST/PUT/PATCH. Use this when no typed "
                "tool covers the endpoint (orders, forms, tickets, elements, "
                "tags, media_sets, webhooks, etc.). Conservative annotations "
                "(destructiveHint=true) — Claude will confirm before calling."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {
                        "type": "string",
                        "description": "Site name from voog_list_sites",
                    },
                    "method": {
                        "type": "string",
                        "enum": list(ALLOWED_METHODS),
                        "description": "HTTP method",
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Endpoint path starting with '/', e.g. "
                            "'/forms', '/articles/42', "
                            "'/redirect_rules/9'. Must NOT be an absolute "
                            "URL — base host comes from the site config."
                        ),
                    },
                    "body": {
                        "type": ["object", "array", "null"],
                        "description": (
                            "Optional JSON body for POST/PUT/PATCH. "
                            "Voog uses different envelope conventions per "
                            "endpoint — see docs/voog-mcp-endpoint-coverage.md."
                        ),
                    },
                    "params": {
                        "type": ["object", "null"],
                        "description": (
                            "Optional query parameters as a flat string-keyed "
                            "object, e.g. {'include': 'translations', "
                            "'q.page.hidden.$eq': 'true'}."
                        ),
                    },
                },
                "required": ["site", "method", "path"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="voog_ecommerce_api_call",
            description=(
                "Generic Ecommerce v1 API passthrough. Forward an HTTP "
                "request to https://<host>/admin/api/ecommerce/v1<path>. "
                "Same shape as voog_admin_api_call, different base URL. "
                "Supports ?include=... and ?language_code=... per Voog "
                "ecommerce conventions. Use for orders, carts, discounts, "
                "shipping_methods, gateways, cart_fields, cart_rules, "
                "delivery_provider_configs, templates, bulk product "
                "actions, products imports, etc."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {
                        "type": "string",
                        "description": "Site name from voog_list_sites",
                    },
                    "method": {
                        "type": "string",
                        "enum": list(ALLOWED_METHODS),
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Endpoint path starting with '/', e.g. "
                            "'/orders', '/products/42', '/settings'."
                        ),
                    },
                    "body": {
                        "type": ["object", "array", "null"],
                    },
                    "params": {
                        "type": ["object", "null"],
                    },
                },
                "required": ["site", "method", "path"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
    ]


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})

    if name == "voog_admin_api_call":
        return _passthrough(arguments, client, base=client.base_url, label="admin")
    if name == "voog_ecommerce_api_call":
        return _passthrough(
            arguments, client, base=client.ecommerce_url, label="ecommerce"
        )

    return error_response(f"Unknown tool: {name}")


def _passthrough(
    arguments: dict, client: VoogClient, *, base: str, label: str
) -> list[TextContent] | CallToolResult:
    method = (arguments.get("method") or "").upper()
    path = arguments.get("path") or ""
    body = arguments.get("body")
    params = arguments.get("params")

    if method not in ALLOWED_METHODS:
        return error_response(
            f"voog_{label}_api_call: method must be one of "
            f"{ALLOWED_METHODS} (got {method!r})"
        )

    err = _validate_path(path)
    if err:
        return error_response(f"voog_{label}_api_call: {err}")

    if params is not None and not isinstance(params, dict):
        return error_response(
            f"voog_{label}_api_call: params must be an object or null"
        )

    try:
        if method == "GET":
            data = client.get(path, base=base, params=params)
        elif method == "DELETE":
            data = client.delete(path, base=base)
        elif method in ("POST", "PUT", "PATCH"):
            # client._request supports POST/PUT; PATCH falls through the
            # generic _request via a direct call.
            if method == "POST":
                data = client.post(path, body, base=base)
            elif method == "PUT":
                data = client.put(path, body, base=base)
            else:
                # PATCH — re-use the private _request; behaves identically
                # to PUT but with the merge semantics Voog applies on the
                # server side.
                data = client._request("PATCH", path, base=base, data=body)
        else:  # pragma: no cover — defended above
            return error_response(f"voog_{label}_api_call: unreachable method {method}")
    except Exception as e:
        return error_response(
            f"voog_{label}_api_call {method} {path} failed: {e}"
        )

    return success_response(
        data,
        summary=f"🔌 {method} {path} ({label} api) → ok",
    )


def _validate_path(path: str) -> str | None:
    if not path:
        return "path must be non-empty"
    if "://" in path or path.startswith("//"):
        return f"path must not be an absolute URL (got {path!r})"
    if not path.startswith("/"):
        return f"path must start with '/' (got {path!r})"
    if ".." in path.split("/"):
        return f"path must not contain '..' segments (got {path!r})"
    return None
```

- [ ] **Step 4: Wire the new module into the server**

Edit `src/voog/mcp/server.py`:

Add import alongside the existing tool imports:

```python
from voog.mcp.tools import raw as raw_tools
```

Add `raw_tools` to `TOOL_GROUPS`:

```python
TOOL_GROUPS = [
    layouts_tools,
    layouts_sync_tools,
    pages_tools,
    pages_mutate_tools,
    products_tools,
    products_images_tools,
    raw_tools,            # NEW
    redirects_tools,
    snapshot_tools,
]
```

- [ ] **Step 5: Run tests to verify pass**

```bash
.venv/bin/python -m pytest tests/test_tools_raw.py -v
.venv/bin/python -m pytest tests/test_main.py tests/test_resource_uri_collisions.py -v
```

Expected: all green. The full-suite check confirms nothing else regressed.

- [ ] **Step 6: Lint**

```bash
.venv/bin/ruff check src/voog/mcp/tools/raw.py tests/test_tools_raw.py src/voog/mcp/server.py
```

Expected: no warnings.

- [ ] **Step 7: Commit**

```bash
git add src/voog/mcp/tools/raw.py tests/test_tools_raw.py src/voog/mcp/server.py
git commit -m "feat(mcp): add voog_admin_api_call + voog_ecommerce_api_call passthrough

Generic passthrough tools that forward (method, path, body, params) to
the configured VoogClient. Closes the 'fall back to curl' gap when no
typed tool exists for an endpoint.

Validation: rejects unknown methods, paths without leading '/', absolute
URLs (host bypass), and '..' segments. Both tools carry destructiveHint=
true so Claude prompts before calling — caller picks the method
explicitly.

Coverage: orders, carts, discounts, shipping_methods, gateways, forms,
tickets, elements, element_definitions, tags, media_sets, webhooks,
content_partials, code_partials, templates, bulk product update,
products imports, search, and any future endpoints."
```

---

## Task 3: Expand `product_update` to all `product` envelope fields

The user's specific complaint: `product_update` only supports `name` and `slug` translations. Description, status, price, sku, stock are unreachable today, forcing sessions to fall back to raw API calls. After this task, the tool covers every field the `{"product": {...}}` envelope accepts.

**Files:**
- Modify: `src/voog/mcp/tools/products.py`
- Modify: `tests/test_tools_products.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools_products.py` after the existing `TestProductUpdate` class:

```python
class TestProductUpdateExpandedFields(unittest.TestCase):
    """v1.2: product_update accepts the full {product: {...}} envelope.

    Backwards-compatible with the v1.1 'fields' translation-only shape:
    if `fields` is present, it still routes to translations. New
    parameters `attributes` and `translations` carry the rest.
    """

    def _put_call(self, client):
        return client.put.call_args.args[1]["product"]

    def test_description_translation_update(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.return_value = {"id": 42}
        products_tools.call_tool(
            "product_update",
            {
                "product_id": 42,
                "translations": {"description": {"et": "Eesti tekst"}},
            },
            client,
        )
        body = self._put_call(client)
        self.assertEqual(
            body["translations"]["description"]["et"], "Eesti tekst"
        )

    def test_status_update(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.return_value = {"id": 42}
        products_tools.call_tool(
            "product_update",
            {"product_id": 42, "attributes": {"status": "live"}},
            client,
        )
        body = self._put_call(client)
        self.assertEqual(body["status"], "live")

    def test_status_invalid_rejected(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        result = products_tools.call_tool(
            "product_update",
            {"product_id": 42, "attributes": {"status": "active"}},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("status", payload["error"].lower())
        client.put.assert_not_called()

    def test_price_and_sku_update(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.return_value = {"id": 42}
        products_tools.call_tool(
            "product_update",
            {
                "product_id": 42,
                "attributes": {
                    "price": "39.00",
                    "sale_price": "29.00",
                    "sku": "BAG-001",
                    "stock": 10,
                },
            },
            client,
        )
        body = self._put_call(client)
        self.assertEqual(body["price"], "39.00")
        self.assertEqual(body["sale_price"], "29.00")
        self.assertEqual(body["sku"], "BAG-001")
        self.assertEqual(body["stock"], 10)

    def test_categories_update(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.return_value = {"id": 42}
        products_tools.call_tool(
            "product_update",
            {"product_id": 42, "attributes": {"category_ids": [1, 7]}},
            client,
        )
        body = self._put_call(client)
        self.assertEqual(body["category_ids"], [1, 7])

    def test_back_compat_fields_param(self):
        # Old shape still works (fields → translations).
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.return_value = {"id": 42}
        products_tools.call_tool(
            "product_update",
            {"product_id": 42, "fields": {"name-et": "Suvekott"}},
            client,
        )
        body = self._put_call(client)
        self.assertEqual(body["translations"]["name"]["et"], "Suvekott")

    def test_combined_attributes_and_translations(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.return_value = {"id": 42}
        products_tools.call_tool(
            "product_update",
            {
                "product_id": 42,
                "attributes": {"status": "draft", "price": "49.00"},
                "translations": {
                    "description": {"et": "ET", "en": "EN"},
                    "name": {"et": "Nimi"},
                },
            },
            client,
        )
        body = self._put_call(client)
        self.assertEqual(body["status"], "draft")
        self.assertEqual(body["price"], "49.00")
        self.assertEqual(body["translations"]["description"]["et"], "ET")
        self.assertEqual(body["translations"]["name"]["et"], "Nimi")

    def test_rejects_empty_call(self):
        # No fields, no attributes, no translations.
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        result = products_tools.call_tool(
            "product_update",
            {"product_id": 42},
            client,
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()

    def test_rejects_unknown_attribute(self):
        # Defensive: catch typos like 'descriptin' before they hit the API.
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        result = products_tools.call_tool(
            "product_update",
            {"product_id": 42, "attributes": {"descriptin": "oops"}},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("descriptin", payload["error"])
        client.put.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_tools_products.py::TestProductUpdateExpandedFields -v
```

Expected: FAIL — current `product_update` rejects anything but `fields`.

- [ ] **Step 3: Rewrite `_product_update` to accept the expanded shape**

Replace the existing `_product_update` body in `src/voog/mcp/tools/products.py` and update the `Tool` definition. Two changes:

(a) Update the `product_update` `Tool` description and schema:

```python
        Tool(
            name="product_update",
            description=(
                "Update a product. Three argument shapes (combinable):\n"
                "  - `attributes`: flat object of root-level product fields "
                "(status, price, sale_price, sku, stock, description, "
                "category_ids, image_id, asset_ids, physical_properties, "
                "uses_variants, variant_types, variants).\n"
                "  - `translations`: nested {field: {lang: value}} for "
                "translatable fields (name, slug, description). Each "
                "field-language pair must be non-empty.\n"
                "  - `fields` (legacy v1.1 shape): flat 'name-et', 'slug-en' "
                "keys — auto-routed to translations. Kept for back-compat.\n"
                "At least one of attributes/translations/fields must be "
                "non-empty. Validates status enum {'draft', 'live'} and "
                "rejects unknown attribute keys (catches typos before they "
                "round-trip to a 422). Reversible by calling with previous "
                "values; idempotent (same input twice = same end state)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "product_id": {"type": "integer"},
                    "attributes": {
                        "type": "object",
                        "description": (
                            "Root-level product fields. Allowed keys: "
                            "status, price, sale_price, sku, stock, "
                            "description, category_ids, image_id, "
                            "asset_ids, physical_properties, uses_variants, "
                            "variant_types, variants."
                        ),
                    },
                    "translations": {
                        "type": "object",
                        "description": (
                            "Nested {field: {lang: value}}. Allowed fields: "
                            "name, slug, description."
                        ),
                    },
                    "fields": {
                        "type": "object",
                        "description": (
                            "Legacy v1.1 shape: flat 'name-et', 'slug-en' "
                            "keys. Auto-routed to translations."
                        ),
                    },
                },
                "required": ["site", "product_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
```

(b) Replace TRANSLATABLE_FIELDS and `_product_update`:

```python
# Voog product PUT envelope: {"product": {...}}. Allowed keys at the root
# of the envelope. Whitelist instead of pass-through so typos surface as
# a clean error rather than a 422 round-trip.
ATTR_KEYS = frozenset(
    [
        "status",
        "price",
        "sale_price",
        "sku",
        "stock",
        "description",
        "category_ids",
        "image_id",
        "asset_ids",
        "physical_properties",
        "uses_variants",
        "variant_types",
        "variants",
    ]
)

# Translatable fields supported by Voog ecommerce. Keep aligned with
# voog/cli/commands/products.py.
TRANSLATABLE_FIELDS = frozenset(["name", "slug", "description"])

# product.status enum per Voog (HTTP 422 otherwise — see project memory).
VALID_STATUS = frozenset(["draft", "live"])


def _product_update(arguments: dict, client: VoogClient) -> list[TextContent] | CallToolResult:
    product_id = arguments.get("product_id")
    attributes = arguments.get("attributes") or {}
    translations = arguments.get("translations") or {}
    legacy_fields = arguments.get("fields") or {}

    if not (attributes or translations or legacy_fields):
        return error_response(
            "product_update: at least one of `attributes`, `translations`, "
            "or `fields` must be a non-empty object"
        )

    # Validate attributes — whitelist + status enum.
    for key in attributes:
        if key not in ATTR_KEYS:
            return error_response(
                f"product_update: attribute {key!r} not supported. "
                f"Allowed: {sorted(ATTR_KEYS)}"
            )
    if "status" in attributes and attributes["status"] not in VALID_STATUS:
        return error_response(
            f"product_update: status must be one of "
            f"{sorted(VALID_STATUS)} (got {attributes['status']!r})"
        )

    # Validate explicit translations.
    merged_translations: dict = {}
    for field, langs in translations.items():
        if field not in TRANSLATABLE_FIELDS:
            return error_response(
                f"product_update: translations field {field!r} not supported. "
                f"Allowed: {sorted(TRANSLATABLE_FIELDS)}"
            )
        if not isinstance(langs, dict) or not langs:
            return error_response(
                f"product_update: translations[{field!r}] must be a "
                "non-empty object {lang: value}"
            )
        for lang, value in langs.items():
            if not lang or lang.startswith("-"):
                return error_response(
                    f"product_update: empty/malformed lang in "
                    f"translations[{field!r}]: {lang!r}"
                )
            if not value:
                return error_response(
                    f"product_update: empty value for translations[{field!r}][{lang!r}]"
                )
            merged_translations.setdefault(field, {})[lang] = value

    # Fold legacy `fields` ('name-et', 'slug-en') into translations.
    for key, value in legacy_fields.items():
        if "-" not in key:
            return error_response(
                f"product_update: legacy field {key!r} must use 'field-lang' "
                "format (e.g. 'name-et', 'slug-en')"
            )
        field, lang = key.split("-", 1)
        if field not in TRANSLATABLE_FIELDS:
            return error_response(
                f"product_update: legacy field {field!r} not supported. "
                f"Allowed: {sorted(TRANSLATABLE_FIELDS)}"
            )
        if not lang or lang.startswith("-"):
            return error_response(
                f"product_update: lang segment in {key!r} is empty or malformed"
            )
        if not value:
            return error_response(
                f"product_update: empty value for {key!r} "
                "(Voog rejects empty translations)"
            )
        merged_translations.setdefault(field, {})[lang] = value

    product_body: dict = dict(attributes)
    if merged_translations:
        product_body["translations"] = merged_translations

    payload = {"product": product_body}

    try:
        result = client.put(
            f"/products/{product_id}",
            payload,
            base=client.ecommerce_url,
        )
        changes = sorted(
            list(attributes.keys())
            + [f"{k}-{l}" for k, langs in merged_translations.items() for l in langs]
        )
        return success_response(
            result,
            summary=f"✓ product {product_id} updated: {', '.join(changes)}",
        )
    except Exception as e:
        return error_response(f"product_update id={product_id} failed: {e}")
```

- [ ] **Step 4: Run tests to verify pass**

```bash
.venv/bin/python -m pytest tests/test_tools_products.py -v
```

Expected: all `TestProductUpdate*` classes green.

- [ ] **Step 5: Lint**

```bash
.venv/bin/ruff check src/voog/mcp/tools/products.py tests/test_tools_products.py
```

- [ ] **Step 6: Commit**

```bash
git add src/voog/mcp/tools/products.py tests/test_tools_products.py
git commit -m "feat(products): expand product_update to full product envelope

Adds two new arguments alongside the legacy 'fields':
  - attributes: flat root-level fields (status, price, sale_price, sku,
    stock, description, category_ids, image_id, asset_ids,
    physical_properties, uses_variants, variant_types, variants)
  - translations: nested {field: {lang: value}} for name/slug/description

Validates status enum {'draft', 'live'} and rejects unknown attribute
keys (catches typos before a 422 round-trip). Backwards-compatible with
v1.1: 'fields' still works and is auto-routed into translations.

Fixes the 'product_update only supports name+slug → fall back to curl
for description' gap that prompted the endpoint coverage rewrite."
```

---

## Task 4: Articles CRUD tools (`articles_list`, `article_get`, `article_create`, `article_update`, `article_publish`, `article_delete`)

Articles are entirely read-only today (resource-only at `voog://{site}/articles`). Sessions writing blog content fall back to direct API calls. The skill memory has detailed `autosaved_*` + `publishing: true` semantics — capture them in the typed tool so they aren't re-derived every session.

**Files:**
- Create: `src/voog/mcp/tools/articles.py`
- Create: `tests/test_tools_articles.py`
- Modify: `src/voog/mcp/server.py` — register `articles_tools` in `TOOL_GROUPS`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tools_articles.py`:

```python
"""Tests for voog.mcp.tools.articles — blog article CRUD."""

import json
import unittest
import urllib.error
from unittest.mock import MagicMock

from voog.mcp.tools import articles as articles_tools


class TestGetTools(unittest.TestCase):
    def test_six_tools_registered(self):
        names = sorted(t.name for t in articles_tools.get_tools())
        self.assertEqual(
            names,
            [
                "article_create",
                "article_delete",
                "article_get",
                "article_publish",
                "article_update",
                "articles_list",
            ],
        )

    def test_read_tools_annotations(self):
        tools = {t.name: t for t in articles_tools.get_tools()}
        for name in ("articles_list", "article_get"):
            ann = tools[name].annotations
            self.assertIs(ann.readOnlyHint, True)
            self.assertIs(ann.destructiveHint, False)
            self.assertIs(ann.idempotentHint, True)

    def test_delete_annotations(self):
        tools = {t.name: t for t in articles_tools.get_tools()}
        ann = tools["article_delete"].annotations
        self.assertIs(ann.readOnlyHint, False)
        self.assertIs(ann.destructiveHint, True)
        self.assertIs(ann.idempotentHint, False)


class TestArticlesList(unittest.TestCase):
    def test_list_returns_simplified(self):
        client = MagicMock()
        client.get_all.return_value = [
            {
                "id": 1,
                "title": "T1",
                "path": "blog/t1",
                "language": {"code": "et"},
                "page": {"id": 5},
            }
        ]
        result = articles_tools.call_tool("articles_list", {}, client)
        client.get_all.assert_called_once_with("/articles")
        items = json.loads(result[1].text)
        self.assertEqual(items[0]["id"], 1)
        self.assertEqual(items[0]["language_code"], "et")
        self.assertEqual(items[0]["page_id"], 5)


class TestArticleGet(unittest.TestCase):
    def test_get_returns_full_article(self):
        client = MagicMock()
        client.get.return_value = {"id": 7, "title": "X", "body": "<p>x</p>"}
        result = articles_tools.call_tool(
            "article_get", {"article_id": 7}, client
        )
        client.get.assert_called_once_with("/articles/7")
        body = json.loads(result[0].text)
        self.assertEqual(body["id"], 7)


class TestArticleCreate(unittest.TestCase):
    def test_create_minimal(self):
        client = MagicMock()
        # Voog: create returns the new article without title set; the
        # follow-up PUT autosaved_title is what makes the title appear.
        client.post.return_value = {"id": 99}
        result = articles_tools.call_tool(
            "article_create",
            {"page_id": 5, "title": "New Post"},
            client,
        )
        client.post.assert_called_once()
        path, body = client.post.call_args.args
        self.assertEqual(path, "/articles")
        self.assertEqual(body["page_id"], 5)
        self.assertEqual(body["autosaved_title"], "New Post")
        # Created articles default to unpublished.
        self.assertNotIn("publishing", body)

    def test_create_with_body_and_publish(self):
        client = MagicMock()
        client.post.return_value = {"id": 99}
        articles_tools.call_tool(
            "article_create",
            {
                "page_id": 5,
                "title": "P",
                "body": "<p>hi</p>",
                "excerpt": "short",
                "publish": True,
            },
            client,
        )
        body = client.post.call_args.args[1]
        self.assertEqual(body["autosaved_body"], "<p>hi</p>")
        self.assertEqual(body["autosaved_excerpt"], "short")
        self.assertIs(body["publishing"], True)

    def test_create_requires_page_id_and_title(self):
        client = MagicMock()
        result = articles_tools.call_tool(
            "article_create", {"page_id": 5}, client
        )
        self.assertTrue(result.isError)
        client.post.assert_not_called()


class TestArticleUpdate(unittest.TestCase):
    def test_update_uses_autosaved_fields(self):
        client = MagicMock()
        client.put.return_value = {"id": 99}
        articles_tools.call_tool(
            "article_update",
            {
                "article_id": 99,
                "title": "Updated",
                "body": "<p>updated</p>",
                "excerpt": "ex",
                "description": "meta",
            },
            client,
        )
        client.put.assert_called_once()
        path, body = client.put.call_args.args
        self.assertEqual(path, "/articles/99")
        self.assertEqual(body["autosaved_title"], "Updated")
        self.assertEqual(body["autosaved_body"], "<p>updated</p>")
        self.assertEqual(body["autosaved_excerpt"], "ex")
        self.assertEqual(body["description"], "meta")  # NOT autosaved
        self.assertNotIn("title", body)
        self.assertNotIn("body", body)

    def test_update_path_and_image_id(self):
        client = MagicMock()
        client.put.return_value = {"id": 99}
        articles_tools.call_tool(
            "article_update",
            {"article_id": 99, "path": "blog/x", "image_id": 1234},
            client,
        )
        body = client.put.call_args.args[1]
        self.assertEqual(body["path"], "blog/x")
        self.assertEqual(body["image_id"], 1234)

    def test_update_data_field(self):
        client = MagicMock()
        client.put.return_value = {"id": 99}
        articles_tools.call_tool(
            "article_update",
            {"article_id": 99, "data": {"item_image": {"original_id": 7}}},
            client,
        )
        body = client.put.call_args.args[1]
        self.assertEqual(body["data"]["item_image"]["original_id"], 7)

    def test_update_rejects_empty(self):
        client = MagicMock()
        result = articles_tools.call_tool(
            "article_update", {"article_id": 99}, client
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()


class TestArticlePublish(unittest.TestCase):
    def test_publish_sends_all_autosaved_and_publishing_true(self):
        client = MagicMock()
        # Per skill memory: publish must include all autosaved_* in the
        # SAME PUT as publishing:true so values are copied to published
        # fields atomically. Implementation reads the article first to
        # get the autosaved values, then replays them.
        client.get.return_value = {
            "id": 99,
            "autosaved_title": "Final Title",
            "autosaved_body": "<p>final</p>",
            "autosaved_excerpt": "final ex",
        }
        client.put.return_value = {"id": 99}
        articles_tools.call_tool(
            "article_publish", {"article_id": 99}, client
        )
        body = client.put.call_args.args[1]
        self.assertEqual(body["autosaved_title"], "Final Title")
        self.assertEqual(body["autosaved_body"], "<p>final</p>")
        self.assertEqual(body["autosaved_excerpt"], "final ex")
        self.assertIs(body["publishing"], True)


class TestArticleDelete(unittest.TestCase):
    def test_requires_force(self):
        client = MagicMock()
        result = articles_tools.call_tool(
            "article_delete", {"article_id": 99}, client
        )
        self.assertTrue(result.isError)
        client.delete.assert_not_called()

    def test_force_true_deletes(self):
        client = MagicMock()
        articles_tools.call_tool(
            "article_delete",
            {"article_id": 99, "force": True},
            client,
        )
        client.delete.assert_called_once_with("/articles/99")
```

- [ ] **Step 2: Run test to fail**

```bash
.venv/bin/python -m pytest tests/test_tools_articles.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the module**

Create `src/voog/mcp/tools/articles.py`:

```python
"""MCP tools for Voog blog articles — list, get, create, update, publish, delete.

Six tools — all hit Admin API ``/articles``:

  - ``articles_list``    — read-only, simplified projection
  - ``article_get``      — read-only, full article object
  - ``article_create``   — POST /articles (idempotent only if you supply
                            a unique title; Voog auto-suffixes path)
  - ``article_update``   — PUT /articles/{id} (uses autosaved_* per skill)
  - ``article_publish``  — convenience: re-PUT autosaved_* + publishing:true
  - ``article_delete``   — DELETE /articles/{id} (requires force=true)

Skill-memory rules captured in code:
  - article.body / article.title / article.excerpt are read-only — write
    to autosaved_body / autosaved_title / autosaved_excerpt.
  - To publish: send ALL autosaved_* + publishing:true in a single PUT
    so values copy to published fields atomically.
  - article.description ≠ article.excerpt; description is meta-description
    and stays as 'description' (not autosaved_description).
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site
from voog.projections import simplify_articles


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="articles_list",
            description=(
                "List all blog articles on the Voog site (simplified: id, "
                "title, path, public_url, published, published_at, "
                "updated_at, created_at, language_code, page_id). Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {"site": {"type": "string"}},
                "required": ["site"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="article_get",
            description=(
                "Get full article details by id (title, path, body, "
                "autosaved_*, published_at, language, page, data, image, "
                "tags). Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "article_id": {"type": "integer"},
                },
                "required": ["site", "article_id"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="article_create",
            description=(
                "Create a new blog article. Required: page_id (the parent "
                "blog page), title. Optional: body (HTML), excerpt, "
                "description (meta), path (auto from title if omitted), "
                "image_id, tag_names (array), data (custom dict), publish "
                "(default false). Title and body go to autosaved_* fields "
                "per Voog convention; if publish=true, publishing:true is "
                "set so values copy to published fields atomically. NOT "
                "idempotent — repeat calls create multiple articles."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "page_id": {
                        "type": "integer",
                        "description": "Parent blog page id",
                    },
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "excerpt": {"type": "string"},
                    "description": {
                        "type": "string",
                        "description": (
                            "Meta description (rendered as og_description in "
                            "Voog Liquid). Distinct from excerpt — excerpt "
                            "goes to listings/RSS, description goes to <meta>."
                        ),
                    },
                    "path": {"type": "string"},
                    "image_id": {
                        "type": "integer",
                        "description": "Asset id (must be image content type)",
                    },
                    "tag_names": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "data": {"type": "object"},
                    "publish": {"type": "boolean", "default": False},
                },
                "required": ["site", "page_id", "title"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="article_update",
            description=(
                "Update an existing article. Title/body/excerpt go to "
                "autosaved_* per Voog convention (the public fields are "
                "read-only — call article_publish to push autosaved → "
                "published). description/path/image_id/tag_names/data are "
                "non-autosaved fields and update directly. At least one "
                "field must be supplied."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "article_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "excerpt": {"type": "string"},
                    "description": {"type": "string"},
                    "path": {"type": "string"},
                    "image_id": {"type": "integer"},
                    "tag_names": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "data": {"type": "object"},
                },
                "required": ["site", "article_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="article_publish",
            description=(
                "Publish an article: GET current autosaved_* values, then "
                "PUT them back together with publishing:true. Voog only "
                "copies autosaved_* → published when publishing:true is "
                "sent in the SAME PUT — that's why this needs a separate "
                "tool rather than just an `publish` flag on article_update."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "article_id": {"type": "integer"},
                },
                "required": ["site", "article_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="article_delete",
            description=(
                "Delete an article. IRREVERSIBLE — Voog does not retain "
                "deleted articles. Requires force=true."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "article_id": {"type": "integer"},
                    "force": {"type": "boolean", "default": False},
                },
                "required": ["site", "article_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
    ]


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})

    if name == "articles_list":
        return _articles_list(client)
    if name == "article_get":
        return _article_get(arguments, client)
    if name == "article_create":
        return _article_create(arguments, client)
    if name == "article_update":
        return _article_update(arguments, client)
    if name == "article_publish":
        return _article_publish(arguments, client)
    if name == "article_delete":
        return _article_delete(arguments, client)

    return error_response(f"Unknown tool: {name}")


def _articles_list(client: VoogClient):
    try:
        articles = client.get_all("/articles")
        simplified = simplify_articles(articles)
        return success_response(simplified, summary=f"📝 {len(simplified)} articles")
    except Exception as e:
        return error_response(f"articles_list failed: {e}")


def _article_get(arguments: dict, client: VoogClient):
    article_id = arguments.get("article_id")
    try:
        article = client.get(f"/articles/{article_id}")
        return success_response(article)
    except Exception as e:
        return error_response(f"article_get id={article_id} failed: {e}")


def _article_create(arguments: dict, client: VoogClient):
    page_id = arguments.get("page_id")
    title = arguments.get("title") or ""
    if not isinstance(page_id, int):
        return error_response("article_create: page_id must be an integer")
    if not title.strip():
        return error_response("article_create: title must be non-empty")

    body = {
        "page_id": page_id,
        "autosaved_title": title,
    }
    if arguments.get("body"):
        body["autosaved_body"] = arguments["body"]
    if arguments.get("excerpt"):
        body["autosaved_excerpt"] = arguments["excerpt"]
    if arguments.get("description"):
        body["description"] = arguments["description"]
    if arguments.get("path"):
        body["path"] = arguments["path"]
    if arguments.get("image_id") is not None:
        body["image_id"] = arguments["image_id"]
    if arguments.get("tag_names"):
        body["tag_names"] = arguments["tag_names"]
    if arguments.get("data"):
        body["data"] = arguments["data"]
    if arguments.get("publish"):
        body["publishing"] = True

    try:
        result = client.post("/articles", body)
        return success_response(
            result,
            summary=f"📝 article {result.get('id')} created (page {page_id})",
        )
    except Exception as e:
        return error_response(f"article_create failed: {e}")


def _article_update(arguments: dict, client: VoogClient):
    article_id = arguments.get("article_id")
    body: dict = {}

    # Map writeable fields → autosaved_* (or pass-through for non-autosaved).
    if arguments.get("title") is not None:
        body["autosaved_title"] = arguments["title"]
    if arguments.get("body") is not None:
        body["autosaved_body"] = arguments["body"]
    if arguments.get("excerpt") is not None:
        body["autosaved_excerpt"] = arguments["excerpt"]
    if arguments.get("description") is not None:
        body["description"] = arguments["description"]
    if arguments.get("path") is not None:
        body["path"] = arguments["path"]
    if arguments.get("image_id") is not None:
        body["image_id"] = arguments["image_id"]
    if arguments.get("tag_names") is not None:
        body["tag_names"] = arguments["tag_names"]
    if arguments.get("data") is not None:
        body["data"] = arguments["data"]

    if not body:
        return error_response(
            "article_update: at least one field (title, body, excerpt, "
            "description, path, image_id, tag_names, data) must be set"
        )

    try:
        result = client.put(f"/articles/{article_id}", body)
        return success_response(
            result,
            summary=f"📝 article {article_id} updated: {sorted(body.keys())}",
        )
    except Exception as e:
        return error_response(f"article_update id={article_id} failed: {e}")


def _article_publish(arguments: dict, client: VoogClient):
    article_id = arguments.get("article_id")
    try:
        article = client.get(f"/articles/{article_id}")
    except Exception as e:
        return error_response(f"article_publish: GET {article_id} failed: {e}")

    body = {"publishing": True}
    for key in ("autosaved_title", "autosaved_body", "autosaved_excerpt"):
        if article.get(key) is not None:
            body[key] = article[key]

    try:
        result = client.put(f"/articles/{article_id}", body)
        return success_response(
            result,
            summary=f"📢 article {article_id} published",
        )
    except Exception as e:
        return error_response(f"article_publish id={article_id} failed: {e}")


def _article_delete(arguments: dict, client: VoogClient):
    article_id = arguments.get("article_id")
    if not arguments.get("force"):
        return error_response(
            f"article_delete: refusing to delete article {article_id} "
            "without force=true. Voog does not retain deleted articles."
        )
    try:
        client.delete(f"/articles/{article_id}")
        return success_response(
            {"deleted": article_id},
            summary=f"🗑️  article {article_id} deleted",
        )
    except Exception as e:
        return error_response(f"article_delete id={article_id} failed: {e}")
```

- [ ] **Step 4: Wire into TOOL_GROUPS**

Edit `src/voog/mcp/server.py` — add `from voog.mcp.tools import articles as articles_tools` and append `articles_tools` to `TOOL_GROUPS` (alphabetised, between `pages_mutate_tools` and `products_tools` for clarity).

- [ ] **Step 5: Run tests pass**

```bash
.venv/bin/python -m pytest tests/test_tools_articles.py tests/test_main.py -v
```

- [ ] **Step 6: Lint**

```bash
.venv/bin/ruff check src/voog/mcp/tools/articles.py tests/test_tools_articles.py src/voog/mcp/server.py
```

- [ ] **Step 7: Commit**

```bash
git add src/voog/mcp/tools/articles.py tests/test_tools_articles.py src/voog/mcp/server.py
git commit -m "feat(articles): add CRUD + publish tools

Six new MCP tools for blog article lifecycle:
- articles_list, article_get (read-only)
- article_create (with optional publish flag)
- article_update (autosaved_* per Voog convention)
- article_publish (atomic autosaved_* + publishing:true PUT)
- article_delete (force=true required)

Captures the skill-memory rules so sessions don't re-derive them:
  - title/body/excerpt are read-only on the public fields; writes go
    to autosaved_*.
  - publishing:true must travel WITH the autosaved_* values to copy
    them to published fields atomically.
  - description ≠ excerpt; description is meta, excerpt is listings."
```

---

## Task 5: Page mutation tools — `page_create`, `page_update`, `page_set_data`, `page_duplicate`

The CLI already has `voog page-create` and `voog page-add-content`; the MCP doesn't. Sessions wanting to programmatically create pages today either shell out to the CLI or use raw curl. This task adds the four canonical page mutations as typed MCP tools.

The `page_create` tool explicitly supports the `node_id` parameter from the skill memory's "Multilingual lehtede paralleel-tõlked" section so the parallel-translation workflow is one tool call, not two.

**Files:**
- Modify: `src/voog/mcp/tools/pages_mutate.py`
- Modify: `tests/test_tools_pages_mutate.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools_pages_mutate.py`:

```python
class TestPageCreate(unittest.TestCase):
    def test_minimal_root_page(self):
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        client.post.return_value = {"id": 100, "path": "uus-leht"}
        pm.call_tool(
            "page_create",
            {
                "title": "Uus leht",
                "slug": "uus-leht",
                "language_id": 627583,
            },
            client,
        )
        path, body = client.post.call_args.args
        self.assertEqual(path, "/pages")
        # Per skill: root pages omit parent_id (Voog attaches to root node)
        self.assertNotIn("parent_id", body)
        self.assertEqual(body["title"], "Uus leht")
        self.assertEqual(body["language_id"], 627583)

    def test_subpage_with_parent_id(self):
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        client.post.return_value = {"id": 100}
        pm.call_tool(
            "page_create",
            {
                "title": "Sub",
                "slug": "sub",
                "language_id": 627583,
                "parent_id": 5,
                "layout_id": 7,
                "hidden": True,
            },
            client,
        )
        body = client.post.call_args.args[1]
        self.assertEqual(body["parent_id"], 5)
        self.assertEqual(body["layout_id"], 7)
        self.assertIs(body["hidden"], True)

    def test_parallel_translation_with_node_id(self):
        # Per skill memory: second-language page must use node_id of the
        # first-language page, NOT parent_id, so the two are parallels.
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        client.post.return_value = {"id": 200}
        pm.call_tool(
            "page_create",
            {
                "title": "Coloured totes",
                "slug": "coloured-totes",
                "language_id": 627582,
                "node_id": 999,
                "layout_id": 7,
            },
            client,
        )
        body = client.post.call_args.args[1]
        self.assertEqual(body["node_id"], 999)
        self.assertNotIn("parent_id", body)

    def test_node_id_and_parent_id_mutually_exclusive(self):
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        result = pm.call_tool(
            "page_create",
            {
                "title": "X",
                "slug": "x",
                "language_id": 1,
                "node_id": 5,
                "parent_id": 9,
            },
            client,
        )
        self.assertTrue(result.isError)
        client.post.assert_not_called()


class TestPageUpdate(unittest.TestCase):
    def test_update_title_and_slug(self):
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        client.put.return_value = {"id": 5}
        pm.call_tool(
            "page_update",
            {"page_id": 5, "title": "Uus", "slug": "uus"},
            client,
        )
        path, body = client.put.call_args.args
        self.assertEqual(path, "/pages/5")
        self.assertEqual(body["title"], "Uus")
        self.assertEqual(body["slug"], "uus")

    def test_update_layout_and_image(self):
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        client.put.return_value = {"id": 5}
        pm.call_tool(
            "page_update",
            {"page_id": 5, "layout_id": 7, "image_id": 1234},
            client,
        )
        body = client.put.call_args.args[1]
        self.assertEqual(body["layout_id"], 7)
        self.assertEqual(body["image_id"], 1234)

    def test_update_keywords_description(self):
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        client.put.return_value = {"id": 5}
        pm.call_tool(
            "page_update",
            {
                "page_id": 5,
                "keywords": "kuju, voog",
                "description": "Meta description",
                "content_type": "page",
            },
            client,
        )
        body = client.put.call_args.args[1]
        self.assertEqual(body["keywords"], "kuju, voog")
        self.assertEqual(body["description"], "Meta description")
        self.assertEqual(body["content_type"], "page")

    def test_update_rejects_empty(self):
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        result = pm.call_tool("page_update", {"page_id": 5}, client)
        self.assertTrue(result.isError)


class TestPageSetData(unittest.TestCase):
    def test_set_single_data_key(self):
        # Voog: PUT /pages/{id}/data/{key}, body {"value": "..."}
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        client.put.return_value = {"key": "foo", "value": "bar"}
        pm.call_tool(
            "page_set_data",
            {"page_id": 5, "key": "foo", "value": "bar"},
            client,
        )
        path, body = client.put.call_args.args
        self.assertEqual(path, "/pages/5/data/foo")
        self.assertEqual(body, {"value": "bar"})

    def test_delete_data_key(self):
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        pm.call_tool(
            "page_set_data",
            {"page_id": 5, "key": "foo", "value": None},
            client,
        )
        client.delete.assert_called_once_with("/pages/5/data/foo")

    def test_rejects_internal_prefix(self):
        # Voog protects keys starting with internal_ — surface this with
        # a clear error rather than letting the API 422.
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        result = pm.call_tool(
            "page_set_data",
            {"page_id": 5, "key": "internal_secret", "value": "x"},
            client,
        )
        self.assertTrue(result.isError)


class TestPageDuplicate(unittest.TestCase):
    def test_duplicate(self):
        from voog.mcp.tools import pages_mutate as pm

        client = MagicMock()
        client.post.return_value = {"id": 200, "path": "copy"}
        pm.call_tool("page_duplicate", {"page_id": 5}, client)
        client.post.assert_called_once_with("/pages/5/duplicate", {})
```

- [ ] **Step 2: Run test to fail**

```bash
.venv/bin/python -m pytest tests/test_tools_pages_mutate.py -v
```

Expected: FAIL — new tools not registered yet.

- [ ] **Step 3: Add the four tools to `pages_mutate.py`**

Edit `src/voog/mcp/tools/pages_mutate.py`. In `get_tools()`, add the following tool definitions to the returned list (alphabetised after the existing three):

```python
        Tool(
            name="page_create",
            description=(
                "Create a new page. Required: title, slug, language_id. "
                "Optional: parent_id (page id, NOT node_id) for subpages, "
                "node_id for parallel-translation pages of an existing "
                "page in another language, layout_id, content_type "
                "('page'|'link'|'blog'|'product'|...), hidden, image_id, "
                "description, keywords, data (custom dict).\n"
                "Multilingual: pass node_id of the first-language page "
                "instead of parent_id when creating its translation in "
                "another language. Voog binds them as parallels (admin "
                "Translate UI works correctly). parent_id and node_id are "
                "mutually exclusive."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "title": {"type": "string"},
                    "slug": {"type": "string"},
                    "language_id": {"type": "integer"},
                    "parent_id": {"type": "integer"},
                    "node_id": {"type": "integer"},
                    "layout_id": {"type": "integer"},
                    "content_type": {"type": "string"},
                    "hidden": {"type": "boolean"},
                    "image_id": {"type": "integer"},
                    "description": {"type": "string"},
                    "keywords": {"type": "string"},
                    "data": {"type": "object"},
                    "publishing": {"type": "boolean"},
                },
                "required": ["site", "title", "slug", "language_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="page_update",
            description=(
                "Update arbitrary fields on a page. At least one of "
                "title, slug, layout_id, image_id, content_type, "
                "parent_id, description, keywords, data must be supplied. "
                "For just hidden / layout id, prefer the dedicated "
                "page_set_hidden / page_set_layout — they're more explicit "
                "in tool listings."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "page_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "slug": {"type": "string"},
                    "layout_id": {"type": "integer"},
                    "image_id": {"type": "integer"},
                    "content_type": {"type": "string"},
                    "parent_id": {"type": "integer"},
                    "description": {"type": "string"},
                    "keywords": {"type": "string"},
                    "data": {"type": "object"},
                },
                "required": ["site", "page_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="page_set_data",
            description=(
                "Set or delete a single page.data.<key> value. value=null "
                "deletes the key (DELETE /pages/{id}/data/{key}). Keys "
                "starting with 'internal_' are server-protected and "
                "rejected client-side."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "page_id": {"type": "integer"},
                    "key": {"type": "string"},
                    "value": {
                        "type": ["string", "number", "boolean", "object", "array", "null"],
                        "description": "null deletes the key",
                    },
                },
                "required": ["site", "page_id", "key"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="page_duplicate",
            description=(
                "POST /pages/{id}/duplicate — create a copy of the page "
                "(including its content). The new page is hidden by "
                "default per Voog convention."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "page_id": {"type": "integer"},
                },
                "required": ["site", "page_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        ),
```

In `call_tool`, add dispatches:

```python
    if name == "page_create":
        return _page_create(arguments, client)
    if name == "page_update":
        return _page_update(arguments, client)
    if name == "page_set_data":
        return _page_set_data(arguments, client)
    if name == "page_duplicate":
        return _page_duplicate(arguments, client)
```

Add the four handlers at the end of the module:

```python
PAGE_UPDATE_FIELDS = (
    "title",
    "slug",
    "layout_id",
    "image_id",
    "content_type",
    "parent_id",
    "description",
    "keywords",
    "data",
)


def _page_create(arguments: dict, client: VoogClient):
    if arguments.get("node_id") is not None and arguments.get("parent_id") is not None:
        return error_response(
            "page_create: node_id and parent_id are mutually exclusive — "
            "use node_id for parallel translations, parent_id for subpages, "
            "or omit both for root pages."
        )
    body: dict = {
        "title": arguments.get("title"),
        "slug": arguments.get("slug"),
        "language_id": arguments.get("language_id"),
    }
    for key in (
        "parent_id",
        "node_id",
        "layout_id",
        "content_type",
        "hidden",
        "image_id",
        "description",
        "keywords",
        "data",
        "publishing",
    ):
        if arguments.get(key) is not None:
            body[key] = arguments[key]
    try:
        result = client.post("/pages", body)
        return success_response(
            result,
            summary=f"📄 page {result.get('id')} created at /{result.get('path', '')}",
        )
    except Exception as e:
        return error_response(f"page_create failed: {e}")


def _page_update(arguments: dict, client: VoogClient):
    page_id = arguments.get("page_id")
    body: dict = {}
    for key in PAGE_UPDATE_FIELDS:
        if arguments.get(key) is not None:
            body[key] = arguments[key]
    if not body:
        return error_response(
            "page_update: at least one of "
            f"{PAGE_UPDATE_FIELDS} must be supplied"
        )
    try:
        result = client.put(f"/pages/{page_id}", body)
        return success_response(
            result,
            summary=f"📄 page {page_id} updated: {sorted(body.keys())}",
        )
    except Exception as e:
        return error_response(f"page_update id={page_id} failed: {e}")


def _page_set_data(arguments: dict, client: VoogClient):
    page_id = arguments.get("page_id")
    key = arguments.get("key") or ""
    value = arguments.get("value")

    if not key.strip():
        return error_response("page_set_data: key must be non-empty")
    if key.startswith("internal_"):
        return error_response(
            f"page_set_data: 'internal_' keys are server-protected "
            f"(got {key!r})"
        )
    try:
        if value is None:
            client.delete(f"/pages/{page_id}/data/{key}")
            return success_response(
                {"deleted": {"page_id": page_id, "key": key}},
                summary=f"🗑️  page {page_id} data.{key} deleted",
            )
        result = client.put(f"/pages/{page_id}/data/{key}", {"value": value})
        return success_response(
            result,
            summary=f"📄 page {page_id} data.{key} set",
        )
    except Exception as e:
        return error_response(f"page_set_data page={page_id} key={key!r} failed: {e}")


def _page_duplicate(arguments: dict, client: VoogClient):
    page_id = arguments.get("page_id")
    try:
        result = client.post(f"/pages/{page_id}/duplicate", {})
        return success_response(
            result,
            summary=f"📑 page {page_id} duplicated → {result.get('id')}",
        )
    except Exception as e:
        return error_response(f"page_duplicate id={page_id} failed: {e}")
```

- [ ] **Step 4: Run tests pass**

```bash
.venv/bin/python -m pytest tests/test_tools_pages_mutate.py -v
```

- [ ] **Step 5: Lint**

```bash
.venv/bin/ruff check src/voog/mcp/tools/pages_mutate.py tests/test_tools_pages_mutate.py
```

- [ ] **Step 6: Commit**

```bash
git add src/voog/mcp/tools/pages_mutate.py tests/test_tools_pages_mutate.py
git commit -m "feat(pages): add page_create, page_update, page_set_data, page_duplicate

page_create handles three creation patterns:
- root page (omit both parent_id and node_id)
- subpage (parent_id = parent page id)
- parallel translation (node_id = existing page's node id)

page_update covers the general PUT /pages/{id} surface (title, slug,
layout_id, image_id, content_type, parent_id, description, keywords,
data). page_set_hidden / page_set_layout remain as ergonomic shortcuts
in tool listings.

page_set_data wraps the per-key data endpoints (PUT /pages/{id}/data/{k},
DELETE /pages/{id}/data/{k} when value is null). Rejects 'internal_*'
keys client-side — Voog protects them server-side, but failing fast
gives a clearer error.

page_duplicate POSTs /pages/{id}/duplicate (Voog returns the new page,
hidden by default per Voog convention)."
```

---

## Task 6: Text content tools — `text_get`, `text_update`, `page_add_content`

Sessions editing page text bodies (FAQ content, About-page copy, etc.) currently walk through the manual flow from the skill memory: `GET /pages/{id}/contents` → find content_id → fetch text_id → `PUT /texts/{id}`. This task collapses that to two tool calls.

`page_add_content` materialises a content area on a fresh page where `/contents` returns `[]` until the admin UI's edit-mode is opened (skill-memory rule). The CLI has it as `voog page-add-content`; the MCP doesn't.

**Files:**
- Create: `src/voog/mcp/tools/texts.py`
- Create: `tests/test_tools_texts.py`
- Modify: `src/voog/mcp/server.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tools_texts.py`:

```python
"""Tests for voog.mcp.tools.texts — text content + page content area tools."""

import json
import unittest
from unittest.mock import MagicMock

from voog.mcp.tools import texts as texts_tools


class TestGetTools(unittest.TestCase):
    def test_three_tools_registered(self):
        names = sorted(t.name for t in texts_tools.get_tools())
        self.assertEqual(
            names,
            ["page_add_content", "text_get", "text_update"],
        )


class TestTextGet(unittest.TestCase):
    def test_get_returns_full_text_object(self):
        client = MagicMock()
        client.get.return_value = {"id": 7, "body": "<p>x</p>"}
        result = texts_tools.call_tool("text_get", {"text_id": 7}, client)
        client.get.assert_called_once_with("/texts/7")
        body = json.loads(result[0].text)
        self.assertEqual(body["body"], "<p>x</p>")


class TestTextUpdate(unittest.TestCase):
    def test_put_text_body(self):
        client = MagicMock()
        client.put.return_value = {"id": 7, "body": "<p>updated</p>"}
        texts_tools.call_tool(
            "text_update",
            {"text_id": 7, "body": "<p>updated</p>"},
            client,
        )
        path, body = client.put.call_args.args
        self.assertEqual(path, "/texts/7")
        self.assertEqual(body, {"body": "<p>updated</p>"})

    def test_rejects_missing_body(self):
        client = MagicMock()
        result = texts_tools.call_tool("text_update", {"text_id": 7}, client)
        self.assertTrue(result.isError)
        client.put.assert_not_called()


class TestPageAddContent(unittest.TestCase):
    def test_default_body_text(self):
        # Per skill: fresh page returns [] from /contents until edit-mode
        # opens it. POST /pages/{id}/contents materialises the area.
        client = MagicMock()
        client.post.return_value = {
            "id": 9999,
            "name": "body",
            "content_type": "text",
            "text": {"id": 88},
        }
        texts_tools.call_tool(
            "page_add_content",
            {"page_id": 5},
            client,
        )
        path, body = client.post.call_args.args
        self.assertEqual(path, "/pages/5/contents")
        self.assertEqual(body["name"], "body")
        self.assertEqual(body["content_type"], "text")

    def test_named_gallery_area(self):
        client = MagicMock()
        client.post.return_value = {
            "id": 9999,
            "name": "gallery_1",
            "content_type": "gallery",
        }
        texts_tools.call_tool(
            "page_add_content",
            {
                "page_id": 5,
                "name": "gallery_1",
                "content_type": "gallery",
            },
            client,
        )
        body = client.post.call_args.args[1]
        self.assertEqual(body["name"], "gallery_1")
        self.assertEqual(body["content_type"], "gallery")
```

- [ ] **Step 2: Run test to fail**

```bash
.venv/bin/python -m pytest tests/test_tools_texts.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `src/voog/mcp/tools/texts.py`:

```python
"""MCP tools for editing page/article text content bodies and content areas.

Three tools — all hit Admin API:

  - ``text_get``         — GET /texts/{id} (read-only)
  - ``text_update``      — PUT /texts/{id} {"body": ...}
  - ``page_add_content`` — POST /pages/{id}/contents to materialise a
                            content area on a fresh page (Voog returns []
                            from /contents until edit-mode opens the page)

Skill-memory rules captured here:
  - Page text bodies are nested in `text` objects; you cannot PUT body
    via /pages/{id}. Walk pages → contents → texts.
  - Default content area name is 'body' (matches an unnamed
    `{% content %}` Liquid tag). Named areas (`{% content name="gallery_1" %}`)
    require name='gallery_1'.
  - content_type defaults to 'text'; 'gallery', 'form', 'content_partial',
    'buy_button', 'code' are also valid (Voog Contents API).
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site

VALID_CONTENT_TYPES = (
    "text",
    "gallery",
    "form",
    "content_partial",
    "buy_button",
    "code",
)


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="text_get",
            description=(
                "Get a text resource by id (GET /texts/{id}). Texts hold "
                "the body of `text`-type content areas. Find the text_id "
                "via voog://{site}/pages/{page_id}/contents → text.id. "
                "Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "text_id": {"type": "integer"},
                },
                "required": ["site", "text_id"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="text_update",
            description=(
                "Update a text body (PUT /texts/{id} {body}). body is the "
                "raw HTML rendered into the page where the matching "
                "`{% content %}` Liquid tag lives. Reversible by calling "
                "again with the previous body."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "text_id": {"type": "integer"},
                    "body": {
                        "type": "string",
                        "description": "Raw HTML for the content area",
                    },
                },
                "required": ["site", "text_id", "body"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="page_add_content",
            description=(
                "Create a content area + linked text on a page "
                "(POST /pages/{id}/contents). Use this on freshly-created "
                "pages where /contents returns [] until the admin UI's "
                "edit-mode opens the page. name must match the layout's "
                "{% content %} tag — default 'body' for unnamed, "
                "'gallery_1' for named. content_type defaults to 'text'; "
                "valid values: text, gallery, form, content_partial, "
                "buy_button, code."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "page_id": {"type": "integer"},
                    "name": {
                        "type": "string",
                        "description": (
                            "Content area name (default 'body'; named areas "
                            "match {% content name=\"...\" %})"
                        ),
                        "default": "body",
                    },
                    "content_type": {
                        "type": "string",
                        "enum": list(VALID_CONTENT_TYPES),
                        "default": "text",
                    },
                },
                "required": ["site", "page_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        ),
    ]


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})

    if name == "text_get":
        text_id = arguments.get("text_id")
        try:
            return success_response(client.get(f"/texts/{text_id}"))
        except Exception as e:
            return error_response(f"text_get id={text_id} failed: {e}")

    if name == "text_update":
        text_id = arguments.get("text_id")
        body = arguments.get("body")
        if body is None:
            return error_response("text_update: body is required")
        try:
            result = client.put(f"/texts/{text_id}", {"body": body})
            return success_response(
                result,
                summary=f"📝 text {text_id} body updated ({len(body)} chars)",
            )
        except Exception as e:
            return error_response(f"text_update id={text_id} failed: {e}")

    if name == "page_add_content":
        page_id = arguments.get("page_id")
        area_name = arguments.get("name") or "body"
        content_type = arguments.get("content_type") or "text"
        if content_type not in VALID_CONTENT_TYPES:
            return error_response(
                f"page_add_content: content_type must be one of "
                f"{VALID_CONTENT_TYPES} (got {content_type!r})"
            )
        try:
            result = client.post(
                f"/pages/{page_id}/contents",
                {"name": area_name, "content_type": content_type},
            )
            return success_response(
                result,
                summary=(
                    f"➕ page {page_id} content area "
                    f"{area_name!r} ({content_type}) added → "
                    f"id={result.get('id')}"
                ),
            )
        except Exception as e:
            return error_response(
                f"page_add_content page={page_id} failed: {e}"
            )

    return error_response(f"Unknown tool: {name}")
```

- [ ] **Step 4: Wire into TOOL_GROUPS**

Edit `src/voog/mcp/server.py` — add `from voog.mcp.tools import texts as texts_tools` and append `texts_tools` to `TOOL_GROUPS`.

- [ ] **Step 5: Run tests pass**

```bash
.venv/bin/python -m pytest tests/test_tools_texts.py tests/test_main.py -v
```

- [ ] **Step 6: Lint**

```bash
.venv/bin/ruff check src/voog/mcp/tools/texts.py tests/test_tools_texts.py src/voog/mcp/server.py
```

- [ ] **Step 7: Commit**

```bash
git add src/voog/mcp/tools/texts.py tests/test_tools_texts.py src/voog/mcp/server.py
git commit -m "feat(texts): add text_get, text_update, page_add_content tools

Collapses the manual three-step flow (GET pages/{id}/contents → find
content → PUT texts/{id}) into typed MCP tools. Captures the skill rule
that fresh pages return [] from /contents until the admin UI opens
edit-mode — page_add_content materialises the area without that step.

content_type whitelist (text, gallery, form, content_partial,
buy_button, code) per Voog Contents API."
```

---

## Task 7: Layout body update + asset CRUD — `layout_update`, `layout_delete`, `layout_asset_create`, `layout_asset_update`, `layout_asset_delete`

Pure-API Liquid template editing without the layouts_pull/layouts_push filesystem detour. Useful for one-off touch-ups, plus full CRUD for layout_assets (CSS/JS/images) which currently has only `asset_replace` (filename rename workaround).

**Files:**
- Modify: `src/voog/mcp/tools/layouts.py`
- Modify: `tests/test_tools_layouts.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools_layouts.py`:

```python
class TestLayoutUpdate(unittest.TestCase):
    def test_update_body(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        client.put.return_value = {"id": 5}
        layouts_tools.call_tool(
            "layout_update",
            {"layout_id": 5, "body": "<h1>{{ page.title }}</h1>"},
            client,
        )
        path, body = client.put.call_args.args
        self.assertEqual(path, "/layouts/5")
        self.assertEqual(body["body"], "<h1>{{ page.title }}</h1>")
        self.assertNotIn("title", body)

    def test_update_title_and_body(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        client.put.return_value = {"id": 5}
        layouts_tools.call_tool(
            "layout_update",
            {"layout_id": 5, "title": "Renamed", "body": "x"},
            client,
        )
        body = client.put.call_args.args[1]
        self.assertEqual(body["title"], "Renamed")
        self.assertEqual(body["body"], "x")

    def test_rejects_unsafe_title(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        result = layouts_tools.call_tool(
            "layout_update",
            {"layout_id": 5, "title": "../escape"},
            client,
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()

    def test_rejects_empty_call(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        result = layouts_tools.call_tool(
            "layout_update", {"layout_id": 5}, client
        )
        self.assertTrue(result.isError)


class TestLayoutDelete(unittest.TestCase):
    def test_requires_force(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        result = layouts_tools.call_tool(
            "layout_delete", {"layout_id": 5}, client
        )
        self.assertTrue(result.isError)
        client.delete.assert_not_called()

    def test_force_true_deletes(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        layouts_tools.call_tool(
            "layout_delete",
            {"layout_id": 5, "force": True},
            client,
        )
        client.delete.assert_called_once_with("/layouts/5")


class TestLayoutAssetCreate(unittest.TestCase):
    def test_create_text_asset(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        client.post.return_value = {"id": 99, "filename": "main.css"}
        layouts_tools.call_tool(
            "layout_asset_create",
            {
                "filename": "main.css",
                "asset_type": "stylesheet",
                "data": "body{margin:0}",
            },
            client,
        )
        path, body = client.post.call_args.args
        self.assertEqual(path, "/layout_assets")
        self.assertEqual(body["filename"], "main.css")
        self.assertEqual(body["data"], "body{margin:0}")

    def test_rejects_unsafe_filename(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        result = layouts_tools.call_tool(
            "layout_asset_create",
            {
                "filename": "../etc/passwd",
                "asset_type": "stylesheet",
                "data": "x",
            },
            client,
        )
        self.assertTrue(result.isError)
        client.post.assert_not_called()


class TestLayoutAssetUpdate(unittest.TestCase):
    def test_put_data(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        client.put.return_value = {"id": 99}
        layouts_tools.call_tool(
            "layout_asset_update",
            {"asset_id": 99, "data": "body{margin:0;padding:0}"},
            client,
        )
        path, body = client.put.call_args.args
        self.assertEqual(path, "/layout_assets/99")
        self.assertEqual(body, {"data": "body{margin:0;padding:0}"})

    def test_rejects_filename_change(self):
        # Skill memory: PUT /layout_assets/{id} with filename returns 500.
        # Refuse client-side and point at asset_replace.
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        result = layouts_tools.call_tool(
            "layout_asset_update",
            {"asset_id": 99, "data": "x", "filename": "new.css"},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("asset_replace", payload["error"])


class TestLayoutAssetDelete(unittest.TestCase):
    def test_requires_force(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        result = layouts_tools.call_tool(
            "layout_asset_delete", {"asset_id": 99}, client
        )
        self.assertTrue(result.isError)
        client.delete.assert_not_called()

    def test_force_deletes(self):
        from voog.mcp.tools import layouts as layouts_tools

        client = MagicMock()
        layouts_tools.call_tool(
            "layout_asset_delete",
            {"asset_id": 99, "force": True},
            client,
        )
        client.delete.assert_called_once_with("/layout_assets/99")
```

Add `import json` near the top of the file if not already present.

- [ ] **Step 2: Run test to fail**

```bash
.venv/bin/python -m pytest tests/test_tools_layouts.py -v
```

- [ ] **Step 3: Add the five tools to `layouts.py`**

In `src/voog/mcp/tools/layouts.py`, append to the `get_tools()` returned list:

```python
        Tool(
            name="layout_update",
            description=(
                "Update a layout — body (Liquid template source), title, "
                "or both. At least one must be supplied. Reversible by "
                "calling again with the previous values; idempotent."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "layout_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "body": {
                        "type": "string",
                        "description": "Liquid template source",
                    },
                },
                "required": ["site", "layout_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="layout_delete",
            description=(
                "Delete a layout. IRREVERSIBLE — Voog does not retain "
                "deleted layouts. Refuses without force=true. Pages "
                "currently using the layout will 500 on render until "
                "reassigned via page_set_layout — back up with "
                "site_snapshot first."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "layout_id": {"type": "integer"},
                    "force": {"type": "boolean", "default": False},
                },
                "required": ["site", "layout_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="layout_asset_create",
            description=(
                "Create a layout_asset (CSS/JS/image). filename + asset_type "
                "+ data required. asset_type ∈ {stylesheet, javascript, "
                "image, plain_text, video, pdf, ...}. For image uploads, "
                "use POST /assets + 3-step protocol via product_set_images "
                "instead — this tool is for text assets (CSS/JS/HTML "
                "fragments)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "filename": {"type": "string"},
                    "asset_type": {"type": "string"},
                    "data": {
                        "type": "string",
                        "description": "Asset content (text)",
                    },
                },
                "required": ["site", "filename", "asset_type", "data"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
            },
        ),
        Tool(
            name="layout_asset_update",
            description=(
                "Update a layout_asset's content (PUT /layout_assets/{id} "
                "{data}). filename is read-only — Voog returns 500 if "
                "filename is sent on PUT. Use asset_replace to rename."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "asset_id": {"type": "integer"},
                    "data": {"type": "string"},
                    "filename": {
                        "type": "string",
                        "description": "REJECTED — use asset_replace to rename",
                    },
                },
                "required": ["site", "asset_id", "data"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="layout_asset_delete",
            description=(
                "Delete a layout_asset. IRREVERSIBLE. Refuses without "
                "force=true. Templates referencing the deleted file will "
                "render with empty content."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "asset_id": {"type": "integer"},
                    "force": {"type": "boolean", "default": False},
                },
                "required": ["site", "asset_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
```

In `call_tool`, dispatch new names:

```python
    if name == "layout_update":
        return _layout_update(arguments, client)
    if name == "layout_delete":
        return _layout_delete(arguments, client)
    if name == "layout_asset_create":
        return _layout_asset_create(arguments, client)
    if name == "layout_asset_update":
        return _layout_asset_update(arguments, client)
    if name == "layout_asset_delete":
        return _layout_asset_delete(arguments, client)
```

Append handlers:

```python
def _layout_update(arguments: dict, client: VoogClient):
    layout_id = arguments.get("layout_id")
    body: dict = {}
    if arguments.get("title") is not None:
        title = arguments["title"]
        err = _validate_voog_name(title, "title")
        if err:
            return error_response(f"layout_update: {err}")
        body["title"] = title
    if arguments.get("body") is not None:
        body["body"] = arguments["body"]
    if not body:
        return error_response("layout_update: at least one of title/body required")
    try:
        result = client.put(f"/layouts/{layout_id}", body)
        return success_response(
            result,
            summary=f"✏️  layout {layout_id} updated ({sorted(body.keys())})",
        )
    except Exception as e:
        return error_response(f"layout_update id={layout_id} failed: {e}")


def _layout_delete(arguments: dict, client: VoogClient):
    layout_id = arguments.get("layout_id")
    if not arguments.get("force"):
        return error_response(
            f"layout_delete: refusing to delete layout {layout_id} without force=true. "
            "Pages using this layout will fail to render — reassign via "
            "page_set_layout first, and back up with site_snapshot."
        )
    try:
        client.delete(f"/layouts/{layout_id}")
        return success_response(
            {"deleted": layout_id},
            summary=f"🗑️  layout {layout_id} deleted",
        )
    except Exception as e:
        return error_response(f"layout_delete id={layout_id} failed: {e}")


def _layout_asset_create(arguments: dict, client: VoogClient):
    filename = arguments.get("filename") or ""
    asset_type = arguments.get("asset_type") or ""
    data = arguments.get("data")
    err = _validate_voog_name(filename, "filename")
    if err:
        return error_response(f"layout_asset_create: {err}")
    if not asset_type:
        return error_response("layout_asset_create: asset_type is required")
    if data is None:
        return error_response("layout_asset_create: data is required")
    try:
        result = client.post(
            "/layout_assets",
            {"filename": filename, "asset_type": asset_type, "data": data},
        )
        return success_response(
            result,
            summary=f"📁 layout_asset {result.get('id')} created: {filename}",
        )
    except Exception as e:
        return error_response(f"layout_asset_create failed: {e}")


def _layout_asset_update(arguments: dict, client: VoogClient):
    asset_id = arguments.get("asset_id")
    if "filename" in arguments and arguments["filename"]:
        return error_response(
            "layout_asset_update: filename is read-only on PUT (Voog "
            "returns 500). Use asset_replace to rename via DELETE+POST."
        )
    if arguments.get("data") is None:
        return error_response("layout_asset_update: data is required")
    try:
        result = client.put(
            f"/layout_assets/{asset_id}",
            {"data": arguments["data"]},
        )
        return success_response(
            result,
            summary=f"📁 layout_asset {asset_id} content updated",
        )
    except Exception as e:
        return error_response(f"layout_asset_update id={asset_id} failed: {e}")


def _layout_asset_delete(arguments: dict, client: VoogClient):
    asset_id = arguments.get("asset_id")
    if not arguments.get("force"):
        return error_response(
            f"layout_asset_delete: refusing to delete asset {asset_id} "
            "without force=true. Templates referencing it will break."
        )
    try:
        client.delete(f"/layout_assets/{asset_id}")
        return success_response(
            {"deleted": asset_id},
            summary=f"🗑️  layout_asset {asset_id} deleted",
        )
    except Exception as e:
        return error_response(f"layout_asset_delete id={asset_id} failed: {e}")
```

- [ ] **Step 4: Run tests pass**

```bash
.venv/bin/python -m pytest tests/test_tools_layouts.py -v
```

- [ ] **Step 5: Lint**

```bash
.venv/bin/ruff check src/voog/mcp/tools/layouts.py tests/test_tools_layouts.py
```

- [ ] **Step 6: Commit**

```bash
git add src/voog/mcp/tools/layouts.py tests/test_tools_layouts.py
git commit -m "feat(layouts): add update, delete, asset CRUD tools

- layout_update: PUT /layouts/{id} body/title (pure-API Liquid edit)
- layout_delete: DELETE /layouts/{id} (force=true required)
- layout_asset_create: POST /layout_assets (text content, CSS/JS)
- layout_asset_update: PUT /layout_assets/{id}/data
  (rejects filename — Voog returns 500; pointer at asset_replace)
- layout_asset_delete: DELETE /layout_assets/{id} (force=true required)

Pure-API editing without the layouts_pull/layouts_push filesystem
detour. layouts_sync remains the recommended path for batch git-tracked
edits; the new tools are for one-off touch-ups."
```

---

## Task 8: Multilingual + ergonomic helpers — `languages_list`, `nodes_list`, `node_get`

These are read-only convenience tools for the multilingual workflows the skill memory describes. `languages_list` lets `page_create` / `article_create` callers look up the right `language_id`. `node_get` is the missing helper for the parallel-translation pattern (caller does `page_get` to fetch first-language page, reads `node.id`, passes to `page_create(node_id=...)`).

**Files:**
- Create: `src/voog/mcp/tools/multilingual.py`
- Create: `tests/test_tools_multilingual.py`
- Modify: `src/voog/projections.py` — add `simplify_languages`, `simplify_nodes`
- Modify: `src/voog/mcp/server.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tools_multilingual.py`:

```python
"""Tests for voog.mcp.tools.multilingual — languages, nodes."""

import json
import unittest
from unittest.mock import MagicMock

from voog.mcp.tools import multilingual as mt


class TestGetTools(unittest.TestCase):
    def test_three_tools_registered(self):
        names = sorted(t.name for t in mt.get_tools())
        self.assertEqual(names, ["languages_list", "node_get", "nodes_list"])


class TestLanguagesList(unittest.TestCase):
    def test_returns_simplified_list(self):
        client = MagicMock()
        client.get_all.return_value = [
            {
                "id": 627583,
                "code": "et",
                "title": "Eesti",
                "default_language": True,
                "published": True,
                "position": 1,
            },
            {
                "id": 627582,
                "code": "en",
                "title": "English",
                "default_language": False,
                "published": True,
                "position": 2,
            },
        ]
        result = mt.call_tool("languages_list", {}, client)
        client.get_all.assert_called_once_with("/languages")
        items = json.loads(result[1].text)
        self.assertEqual(items[0]["code"], "et")
        self.assertIs(items[0]["default_language"], True)


class TestNodesList(unittest.TestCase):
    def test_returns_simplified_list(self):
        client = MagicMock()
        client.get_all.return_value = [
            {"id": 1, "title": "Home", "parent_id": None, "position": 1},
            {"id": 2, "title": "Sub", "parent_id": 1, "position": 1},
        ]
        result = mt.call_tool("nodes_list", {}, client)
        client.get_all.assert_called_once_with("/nodes")
        items = json.loads(result[1].text)
        self.assertEqual(len(items), 2)


class TestNodeGet(unittest.TestCase):
    def test_returns_node(self):
        client = MagicMock()
        client.get.return_value = {
            "id": 5,
            "title": "Pood",
            "pages": [
                {"id": 100, "language_id": 627583},
                {"id": 101, "language_id": 627582},
            ],
        }
        result = mt.call_tool("node_get", {"node_id": 5}, client)
        client.get.assert_called_once_with("/nodes/5")
        body = json.loads(result[0].text)
        self.assertEqual(body["id"], 5)
        self.assertEqual(len(body["pages"]), 2)
```

- [ ] **Step 2: Run test to fail**

```bash
.venv/bin/python -m pytest tests/test_tools_multilingual.py -v
```

- [ ] **Step 3: Add projection helpers**

Append to `src/voog/projections.py`:

```python
def simplify_languages(languages: list) -> list:
    """Project languages list to {id, code, title, default, published, position}."""
    return [
        {
            "id": lang.get("id"),
            "code": lang.get("code"),
            "title": lang.get("title"),
            "default_language": lang.get("default_language"),
            "published": lang.get("published"),
            "position": lang.get("position"),
        }
        for lang in languages
    ]


def simplify_nodes(nodes: list) -> list:
    """Project nodes list to {id, title, parent_id, position}."""
    return [
        {
            "id": n.get("id"),
            "title": n.get("title"),
            "parent_id": n.get("parent_id"),
            "position": n.get("position"),
        }
        for n in nodes
    ]
```

- [ ] **Step 4: Implement the tools**

Create `src/voog/mcp/tools/multilingual.py`:

```python
"""MCP tools for Voog multilingual primitives — languages and nodes.

Three read-only tools:

  - ``languages_list``  — GET /languages, simplified projection. Use
                           the returned ids for page_create.language_id
                           / article_update etc.
  - ``nodes_list``      — GET /nodes, simplified projection.
  - ``node_get``        — GET /nodes/{id}, full object including the
                           parallel-translation pages array. Use when
                           preparing page_create(node_id=...) for a
                           parallel translation.
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site
from voog.projections import simplify_languages, simplify_nodes


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="languages_list",
            description=(
                "List all languages on the Voog site (id, code, title, "
                "default_language, published, position). Use the returned "
                "ids for page_create.language_id / article fields. "
                "Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {"site": {"type": "string"}},
                "required": ["site"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="nodes_list",
            description=(
                "List all page nodes (id, title, parent_id, position). "
                "Each node represents a language-agnostic page identity; "
                "its parallel translations are pages sharing the same "
                "node.id. Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {"site": {"type": "string"}},
                "required": ["site"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="node_get",
            description=(
                "Get a single node by id, with its full pages array — one "
                "entry per language. Use this when preparing a parallel "
                "translation: read the node id from one page, then pass "
                "node_id to page_create with the second-language details."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "node_id": {"type": "integer"},
                },
                "required": ["site", "node_id"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
    ]


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})

    if name == "languages_list":
        try:
            langs = client.get_all("/languages")
            simplified = simplify_languages(langs)
            return success_response(simplified, summary=f"🌐 {len(simplified)} languages")
        except Exception as e:
            return error_response(f"languages_list failed: {e}")

    if name == "nodes_list":
        try:
            nodes = client.get_all("/nodes")
            simplified = simplify_nodes(nodes)
            return success_response(simplified, summary=f"🌳 {len(simplified)} nodes")
        except Exception as e:
            return error_response(f"nodes_list failed: {e}")

    if name == "node_get":
        node_id = arguments.get("node_id")
        try:
            node = client.get(f"/nodes/{node_id}")
            return success_response(node)
        except Exception as e:
            return error_response(f"node_get id={node_id} failed: {e}")

    return error_response(f"Unknown tool: {name}")
```

- [ ] **Step 5: Wire into TOOL_GROUPS**

Edit `src/voog/mcp/server.py` to import and register `multilingual_tools`.

- [ ] **Step 6: Run tests pass**

```bash
.venv/bin/python -m pytest tests/test_tools_multilingual.py tests/test_projections.py tests/test_main.py -v
```

- [ ] **Step 7: Lint**

```bash
.venv/bin/ruff check src/voog/mcp/tools/multilingual.py tests/test_tools_multilingual.py src/voog/projections.py src/voog/mcp/server.py
```

- [ ] **Step 8: Commit**

```bash
git add src/voog/mcp/tools/multilingual.py tests/test_tools_multilingual.py src/voog/projections.py src/voog/mcp/server.py
git commit -m "feat(multilingual): add languages_list, nodes_list, node_get

Three read-only helpers for the parallel-translation workflow:
- languages_list — language_id lookup for page/article tools
- nodes_list — see all page nodes at a glance
- node_get — fetch a node + its pages array (one per language) when
  preparing a parallel-translation page_create call

Captures the multilingual decomposition described in the skill memory
('Multilingual lehtede paralleel-tõlked')."
```

---

## Task 9: Redirects update + delete

Two small additions to round out redirect rule lifecycle. The current MCP can list and add but can't update or delete — sessions wanting to fix a typo or remove a stale rule fall back to curl.

**Files:**
- Modify: `src/voog/mcp/tools/redirects.py`
- Modify: `tests/test_tools_redirects.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools_redirects.py`:

```python
class TestRedirectUpdate(unittest.TestCase):
    def test_update_destination(self):
        from voog.mcp.tools import redirects as redirects_tools

        client = MagicMock()
        client.put.return_value = {"id": 9}
        redirects_tools.call_tool(
            "redirect_update",
            {"redirect_id": 9, "destination": "/uus"},
            client,
        )
        path, body = client.put.call_args.args
        self.assertEqual(path, "/redirect_rules/9")
        # Voog accepts flat or wrapped — flat is what existing add code uses
        # via build_redirect_payload. Use the same envelope shape on PUT for
        # consistency.
        self.assertEqual(body["redirect_rule"]["destination"], "/uus")

    def test_update_redirect_type_validated(self):
        from voog.mcp.tools import redirects as redirects_tools

        client = MagicMock()
        result = redirects_tools.call_tool(
            "redirect_update",
            {"redirect_id": 9, "redirect_type": 999},
            client,
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()

    def test_update_active_flag(self):
        from voog.mcp.tools import redirects as redirects_tools

        client = MagicMock()
        client.put.return_value = {"id": 9}
        redirects_tools.call_tool(
            "redirect_update",
            {"redirect_id": 9, "active": False},
            client,
        )
        body = client.put.call_args.args[1]
        self.assertIs(body["redirect_rule"]["active"], False)


class TestRedirectDelete(unittest.TestCase):
    def test_requires_force(self):
        from voog.mcp.tools import redirects as redirects_tools

        client = MagicMock()
        result = redirects_tools.call_tool(
            "redirect_delete", {"redirect_id": 9}, client
        )
        self.assertTrue(result.isError)
        client.delete.assert_not_called()

    def test_force_deletes(self):
        from voog.mcp.tools import redirects as redirects_tools

        client = MagicMock()
        redirects_tools.call_tool(
            "redirect_delete",
            {"redirect_id": 9, "force": True},
            client,
        )
        client.delete.assert_called_once_with("/redirect_rules/9")
```

- [ ] **Step 2: Run test to fail**

```bash
.venv/bin/python -m pytest tests/test_tools_redirects.py -v
```

- [ ] **Step 3: Add the two tools**

In `src/voog/mcp/tools/redirects.py`, append to `get_tools()`:

```python
        Tool(
            name="redirect_update",
            description=(
                "Update an existing redirect rule. At least one of source, "
                "destination, redirect_type, active must be supplied. "
                "redirect_type ∈ {301, 302, 307, 410}. Reversible by "
                "calling again with previous values."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "redirect_id": {"type": "integer"},
                    "source": {"type": "string"},
                    "destination": {"type": "string"},
                    "redirect_type": {
                        "type": "integer",
                        "enum": VALID_REDIRECT_TYPES,
                    },
                    "active": {"type": "boolean"},
                },
                "required": ["site", "redirect_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="redirect_delete",
            description=(
                "Delete a redirect rule. Refuses without force=true. "
                "Reversible only by re-creating the rule via redirect_add."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "redirect_id": {"type": "integer"},
                    "force": {"type": "boolean", "default": False},
                },
                "required": ["site", "redirect_id"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
            },
        ),
```

In `call_tool`:

```python
    if name == "redirect_update":
        redirect_id = arguments.get("redirect_id")
        rule_body: dict = {}
        for key in ("source", "destination", "redirect_type", "active"):
            if arguments.get(key) is not None:
                rule_body[key] = arguments[key]
        if not rule_body:
            return error_response(
                "redirect_update: at least one of source/destination/"
                "redirect_type/active must be supplied"
            )
        try:
            result = client.put(
                f"/redirect_rules/{redirect_id}",
                {"redirect_rule": rule_body},
            )
            return success_response(
                result,
                summary=f"↪️  redirect {redirect_id} updated: {sorted(rule_body.keys())}",
            )
        except Exception as e:
            return error_response(f"redirect_update id={redirect_id} failed: {e}")

    if name == "redirect_delete":
        redirect_id = arguments.get("redirect_id")
        if not arguments.get("force"):
            return error_response(
                f"redirect_delete: refusing to delete rule {redirect_id} without force=true"
            )
        try:
            client.delete(f"/redirect_rules/{redirect_id}")
            return success_response(
                {"deleted": redirect_id},
                summary=f"🗑️  redirect {redirect_id} deleted",
            )
        except Exception as e:
            return error_response(f"redirect_delete id={redirect_id} failed: {e}")
```

- [ ] **Step 4: Run tests pass**

```bash
.venv/bin/python -m pytest tests/test_tools_redirects.py -v
```

- [ ] **Step 5: Lint + commit**

```bash
.venv/bin/ruff check src/voog/mcp/tools/redirects.py tests/test_tools_redirects.py
git add src/voog/mcp/tools/redirects.py tests/test_tools_redirects.py
git commit -m "feat(redirects): add redirect_update, redirect_delete

Closes the redirect-rule lifecycle on the MCP surface — list/add already
existed, update/delete forced sessions to fall back to curl. Same
{redirect_rule: {...}} envelope as redirect_add for consistency."
```

---

## Task 10: Ecommerce settings + site singleton tools

Two small modules. `ecommerce_settings_*` exposes `/ecommerce/v1/settings` (per-language `products_url_slug` is the high-traffic field — see skill memory). `site_*` exposes `/site` (title, code, data dict, favicon).

**Files:**
- Create: `src/voog/mcp/tools/ecommerce_settings.py`
- Create: `src/voog/mcp/tools/site.py`
- Create: `tests/test_tools_ecommerce_settings.py`
- Create: `tests/test_tools_site.py`
- Modify: `src/voog/mcp/server.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tools_ecommerce_settings.py`:

```python
"""Tests for voog.mcp.tools.ecommerce_settings."""

import json
import unittest
from unittest.mock import MagicMock

from voog.mcp.tools import ecommerce_settings as es


class TestGetTools(unittest.TestCase):
    def test_two_tools(self):
        names = sorted(t.name for t in es.get_tools())
        self.assertEqual(
            names,
            ["ecommerce_settings_get", "ecommerce_settings_update"],
        )


class TestGet(unittest.TestCase):
    def test_get_with_translations_include(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.get.return_value = {"settings": {}}
        es.call_tool("ecommerce_settings_get", {}, client)
        client.get.assert_called_once_with(
            "/settings",
            base="https://example.com/admin/api/ecommerce/v1",
            params={"include": "translations"},
        )


class TestUpdate(unittest.TestCase):
    def test_update_currency_attr(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.return_value = {}
        es.call_tool(
            "ecommerce_settings_update",
            {"attributes": {"currency": "EUR"}},
            client,
        )
        path, body = client.put.call_args.args
        self.assertEqual(path, "/settings")
        self.assertEqual(body["settings"]["currency"], "EUR")

    def test_update_products_url_slug_translations(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        client.put.return_value = {}
        es.call_tool(
            "ecommerce_settings_update",
            {
                "translations": {
                    "products_url_slug": {"en": "products"},
                }
            },
            client,
        )
        body = client.put.call_args.args[1]
        self.assertEqual(
            body["settings"]["translations"]["products_url_slug"]["en"],
            "products",
        )

    def test_rejects_empty_call(self):
        client = MagicMock()
        client.ecommerce_url = "https://example.com/admin/api/ecommerce/v1"
        result = es.call_tool("ecommerce_settings_update", {}, client)
        self.assertTrue(result.isError)
        client.put.assert_not_called()
```

Create `tests/test_tools_site.py`:

```python
"""Tests for voog.mcp.tools.site — admin /site singleton."""

import unittest
from unittest.mock import MagicMock

from voog.mcp.tools import site as site_tools


class TestGetTools(unittest.TestCase):
    def test_three_tools(self):
        names = sorted(t.name for t in site_tools.get_tools())
        self.assertEqual(names, ["site_get", "site_set_data", "site_update"])


class TestSiteGet(unittest.TestCase):
    def test_get(self):
        client = MagicMock()
        client.get.return_value = {"id": 1, "title": "Stella"}
        site_tools.call_tool("site_get", {}, client)
        client.get.assert_called_once_with("/site")


class TestSiteUpdate(unittest.TestCase):
    def test_update_title(self):
        client = MagicMock()
        client.put.return_value = {}
        site_tools.call_tool(
            "site_update",
            {"attributes": {"title": "New title"}},
            client,
        )
        path, body = client.put.call_args.args
        self.assertEqual(path, "/site")
        self.assertEqual(body["title"], "New title")

    def test_update_rejects_code(self):
        # site.code is immutable per Voog (and project memory).
        client = MagicMock()
        result = site_tools.call_tool(
            "site_update",
            {"attributes": {"code": "newsubdomain"}},
            client,
        )
        self.assertTrue(result.isError)
        client.put.assert_not_called()


class TestSiteSetData(unittest.TestCase):
    def test_set_data(self):
        client = MagicMock()
        client.put.return_value = {}
        site_tools.call_tool(
            "site_set_data",
            {"key": "buy_together", "value": {"products": []}},
            client,
        )
        path, body = client.put.call_args.args
        self.assertEqual(path, "/site/data/buy_together")
        self.assertEqual(body, {"value": {"products": []}})

    def test_delete_data(self):
        client = MagicMock()
        site_tools.call_tool(
            "site_set_data", {"key": "buy_together", "value": None}, client
        )
        client.delete.assert_called_once_with("/site/data/buy_together")

    def test_rejects_internal_prefix(self):
        client = MagicMock()
        result = site_tools.call_tool(
            "site_set_data",
            {"key": "internal_x", "value": "y"},
            client,
        )
        self.assertTrue(result.isError)
```

- [ ] **Step 2: Run tests to fail**

```bash
.venv/bin/python -m pytest tests/test_tools_ecommerce_settings.py tests/test_tools_site.py -v
```

- [ ] **Step 3: Implement `ecommerce_settings.py`**

Create `src/voog/mcp/tools/ecommerce_settings.py`:

```python
"""MCP tools for Voog ecommerce store settings.

Two tools:
  - ``ecommerce_settings_get``    — GET /settings?include=translations
  - ``ecommerce_settings_update`` — PUT /settings {settings: {...}}

Most-asked-about field: per-language ``products_url_slug`` (e.g. EN
products serving under /en/tooted/... until per-lang slug is set —
project memory has the full story).
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site

# Voog ecommerce settings keys that are translatable per-language.
TRANSLATABLE_SETTINGS = frozenset(
    [
        "products_url_slug",
        "terms_url",
        "company_name",
        "bank_details",
    ]
)


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="ecommerce_settings_get",
            description=(
                "Get ecommerce store settings (currency, tax_rate, "
                "value_date_days, default_language, decimal_places, "
                "company_name, bank_details, terms, privacy_policy, "
                "products_url_slug, etc.). Includes per-language "
                "translations. Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {"site": {"type": "string"}},
                "required": ["site"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="ecommerce_settings_update",
            description=(
                "Update ecommerce settings. attributes: flat root-level "
                "fields (currency, tax_rate, notification_email, ...). "
                "translations: nested {field: {lang: value}} for "
                "translatable settings (products_url_slug, terms_url, "
                "company_name, bank_details). Wraps payload in {settings: "
                "{...}} envelope."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "attributes": {"type": "object"},
                    "translations": {"type": "object"},
                },
                "required": ["site"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
    ]


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})

    if name == "ecommerce_settings_get":
        try:
            data = client.get(
                "/settings",
                base=client.ecommerce_url,
                params={"include": "translations"},
            )
            return success_response(data)
        except Exception as e:
            return error_response(f"ecommerce_settings_get failed: {e}")

    if name == "ecommerce_settings_update":
        attributes = arguments.get("attributes") or {}
        translations = arguments.get("translations") or {}
        if not (attributes or translations):
            return error_response(
                "ecommerce_settings_update: attributes or translations required"
            )
        for field in translations:
            if field not in TRANSLATABLE_SETTINGS:
                return error_response(
                    f"ecommerce_settings_update: translations field {field!r} "
                    f"not supported. Allowed: {sorted(TRANSLATABLE_SETTINGS)}"
                )
        body: dict = dict(attributes)
        if translations:
            body["translations"] = translations
        try:
            data = client.put(
                "/settings",
                {"settings": body},
                base=client.ecommerce_url,
            )
            return success_response(
                data,
                summary=f"⚙️  ecommerce settings updated: {sorted(body.keys())}",
            )
        except Exception as e:
            return error_response(f"ecommerce_settings_update failed: {e}")

    return error_response(f"Unknown tool: {name}")
```

- [ ] **Step 4: Implement `site.py`**

Create `src/voog/mcp/tools/site.py`:

```python
"""MCP tools for the Voog admin /site singleton.

Three tools:
  - ``site_get``       — GET /site
  - ``site_update``    — PUT /site (or PATCH /site for merge)
  - ``site_set_data``  — PUT/DELETE /site/data/{key}

Skill-memory rules captured:
  - site.code is immutable once set (and once site has paid plan).
    Refused client-side.
  - data.internal_* keys are server-protected. Refused client-side.
"""

from mcp.types import CallToolResult, TextContent, Tool

from voog.client import VoogClient
from voog.errors import error_response, success_response
from voog.mcp.tools._helpers import strip_site

IMMUTABLE_SITE_FIELDS = frozenset(["code"])


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="site_get",
            description="Get the site singleton (title, code, data, languages, ...). Read-only.",
            inputSchema={
                "type": "object",
                "properties": {"site": {"type": "string"}},
                "required": ["site"],
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="site_update",
            description=(
                "Update site singleton. attributes: flat root-level fields. "
                "site.code is immutable once set — passing it raises an "
                "error. For per-key data, use site_set_data."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "attributes": {"type": "object"},
                },
                "required": ["site", "attributes"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
        Tool(
            name="site_set_data",
            description=(
                "Set or delete site.data.<key>. value=null deletes the "
                "key. 'internal_*' keys are server-protected and refused "
                "client-side."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {"type": "string"},
                    "key": {"type": "string"},
                    "value": {
                        "type": ["string", "number", "boolean", "object", "array", "null"],
                    },
                },
                "required": ["site", "key"],
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
            },
        ),
    ]


def call_tool(
    name: str, arguments: dict | None, client: VoogClient
) -> list[TextContent] | CallToolResult:
    arguments = strip_site(arguments or {})

    if name == "site_get":
        try:
            return success_response(client.get("/site"))
        except Exception as e:
            return error_response(f"site_get failed: {e}")

    if name == "site_update":
        attributes = arguments.get("attributes") or {}
        if not attributes:
            return error_response("site_update: attributes must be non-empty")
        forbidden = set(attributes) & IMMUTABLE_SITE_FIELDS
        if forbidden:
            return error_response(
                f"site_update: fields {sorted(forbidden)} are immutable"
            )
        try:
            return success_response(
                client.put("/site", attributes),
                summary=f"🌐 site updated: {sorted(attributes.keys())}",
            )
        except Exception as e:
            return error_response(f"site_update failed: {e}")

    if name == "site_set_data":
        key = arguments.get("key") or ""
        value = arguments.get("value")
        if not key.strip():
            return error_response("site_set_data: key must be non-empty")
        if key.startswith("internal_"):
            return error_response(
                f"site_set_data: 'internal_' keys are server-protected (got {key!r})"
            )
        try:
            if value is None:
                client.delete(f"/site/data/{key}")
                return success_response(
                    {"deleted": {"key": key}},
                    summary=f"🗑️  site.data.{key} deleted",
                )
            return success_response(
                client.put(f"/site/data/{key}", {"value": value}),
                summary=f"🌐 site.data.{key} set",
            )
        except Exception as e:
            return error_response(f"site_set_data key={key!r} failed: {e}")

    return error_response(f"Unknown tool: {name}")
```

- [ ] **Step 5: Wire into TOOL_GROUPS**

Edit `src/voog/mcp/server.py` — import + register `ecommerce_settings_tools`, `site_tools`.

- [ ] **Step 6: Run tests pass**

```bash
.venv/bin/python -m pytest tests/test_tools_ecommerce_settings.py tests/test_tools_site.py tests/test_main.py -v
```

- [ ] **Step 7: Lint + commit**

```bash
.venv/bin/ruff check src/voog/mcp/tools/ecommerce_settings.py src/voog/mcp/tools/site.py tests/test_tools_ecommerce_settings.py tests/test_tools_site.py src/voog/mcp/server.py
git add src/voog/mcp/tools/ecommerce_settings.py src/voog/mcp/tools/site.py tests/test_tools_ecommerce_settings.py tests/test_tools_site.py src/voog/mcp/server.py
git commit -m "feat(settings,site): add ecommerce_settings_* and site_* tools

ecommerce_settings_get / _update covers the high-traffic per-language
products_url_slug field (skill memory: 'multi-lingual papercut').

site_get / _update / _set_data cover the /site singleton + per-key data
endpoints. Refuses immutable site.code and protected 'internal_*' keys
client-side."
```

---

## Task 11: Run the full suite, refresh README, bump version, update CHANGELOG

Final integration task — ensure everything composes, refresh public-facing docs, and tag the release.

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml` (version bump 1.1.1 → 1.2.0)
- Modify: `src/voog/client.py` (User-Agent string version bump)

- [ ] **Step 1: Run the full test suite**

```bash
.venv/bin/python -m pytest -q
```

Expected: all green. Investigate any regression before continuing.

- [ ] **Step 2: Run resource URI collision check**

```bash
.venv/bin/python -m pytest tests/test_resource_uri_collisions.py tests/test_main.py -v
```

Confirms the new tool names don't clash with existing ones.

- [ ] **Step 3: Update README's tool inventory**

Replace the tool inventory section in `README.md`. Read the file to find the right spot, then add a row per new group:

```markdown
## Tools

| Group | Tools |
|---|---|
| Sites | `voog_list_sites` |
| Pages | `pages_list`, `page_get`, `page_create`, `page_update`, `page_set_hidden`, `page_set_layout`, `page_set_data`, `page_duplicate`, `page_delete` |
| Articles | `articles_list`, `article_get`, `article_create`, `article_update`, `article_publish`, `article_delete` |
| Layouts | `layouts_pull`, `layouts_push`, `layout_create`, `layout_update`, `layout_rename`, `layout_delete`, `layout_asset_create`, `layout_asset_update`, `layout_asset_delete`, `asset_replace` |
| Texts / contents | `text_get`, `text_update`, `page_add_content` |
| Products | `products_list`, `product_get`, `product_update`, `product_set_images` |
| Ecommerce | `ecommerce_settings_get`, `ecommerce_settings_update` |
| Multilingual | `languages_list`, `nodes_list`, `node_get` |
| Redirects | `redirects_list`, `redirect_add`, `redirect_update`, `redirect_delete` |
| Site | `site_get`, `site_update`, `site_set_data` |
| Snapshot | `pages_snapshot`, `site_snapshot` |
| **Generic passthrough** | `voog_admin_api_call`, `voog_ecommerce_api_call` |
```

(Also link to `docs/voog-mcp-endpoint-coverage.md` from the README intro paragraph.)

- [ ] **Step 4: Update CHANGELOG**

Replace the `## [Unreleased]` section in `CHANGELOG.md` with:

```markdown
## [Unreleased]

## [1.2.0] — 2026-04-30

### Added
- Generic Admin API + Ecommerce v1 passthrough tools `voog_admin_api_call` and `voog_ecommerce_api_call`. Forward any (method, path, body, params) through the configured VoogClient — closes the "fall back to curl" gap when no typed tool exists.
- Articles CRUD: `articles_list`, `article_get`, `article_create`, `article_update`, `article_publish`, `article_delete`. Captures the `autosaved_*` + `publishing:true` semantics from the project memory.
- Page mutations: `page_create`, `page_update`, `page_set_data`, `page_duplicate`. `page_create` supports parallel-translation `node_id` parameter.
- Text content: `text_get`, `text_update`, `page_add_content`.
- Layout body update + asset CRUD: `layout_update`, `layout_delete`, `layout_asset_create`, `layout_asset_update`, `layout_asset_delete`. Pure-API editing without the layouts_pull/layouts_push filesystem detour.
- Multilingual helpers: `languages_list`, `nodes_list`, `node_get`. Enables the parallel-translation workflow without raw API calls.
- Redirect lifecycle: `redirect_update`, `redirect_delete`.
- Ecommerce settings: `ecommerce_settings_get`, `ecommerce_settings_update`. Per-language `products_url_slug` covered.
- Site singleton: `site_get`, `site_update`, `site_set_data`. Refuses immutable `code` and protected `internal_*` keys client-side.
- `docs/voog-mcp-endpoint-coverage.md` — endpoint coverage reference doc.

### Changed
- `product_update` now accepts `attributes` (status, price, sale_price, sku, stock, description, category_ids, image_id, asset_ids, physical_properties, uses_variants, variant_types, variants) and `translations` (nested {field: {lang: value}}) in addition to the legacy `fields` shape. Validates `status` enum {`draft`, `live`}. Backwards-compatible.
- `simplify_languages` and `simplify_nodes` projection helpers added in `voog.projections`.
- VoogClient User-Agent bumped to `voog-mcp/1.2.0`.

### Migration
- Existing `product_update` calls with `fields` keep working — the legacy shape is auto-routed into `translations`.
- New tools are additive; no breaking changes to any v1.1.x tool.
```

- [ ] **Step 5: Bump version**

In `pyproject.toml`, change `version = "1.1.1"` to `version = "1.2.0"`.

In `src/voog/client.py`, change `"User-Agent": "voog-mcp/1.1.1"` to `"User-Agent": "voog-mcp/1.2.0"`.

- [ ] **Step 6: Run the full test suite once more**

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
```

Both green.

- [ ] **Step 7: Commit + push the branch + open the PR**

```bash
git add README.md CHANGELOG.md pyproject.toml src/voog/client.py
git commit -m "chore: release v1.2.0 — endpoint coverage rollout

Adds 33 new MCP tools across articles, pages, texts, layouts,
multilingual, redirects, ecommerce settings, site, plus two generic
API passthrough tools. Eliminates the 'fall back to curl' pattern
when no typed tool exists.

See CHANGELOG.md for the full list."

git push -u origin feat/endpoint-coverage
gh pr create --title "Endpoint coverage rollout (v1.2.0)" \
  --body "$(cat <<'EOF'
## Summary
- Adds two generic API passthrough tools (`voog_admin_api_call`, `voog_ecommerce_api_call`) that close the "fall back to curl" gap when no typed tool exists.
- Adds 31 typed tools covering articles CRUD, page mutation, text content, layout body/asset CRUD, multilingual helpers, redirect lifecycle, ecommerce settings, site singleton.
- Expands `product_update` to the full product envelope (description, status, price, sale_price, sku, stock, category_ids, …) — the original prompt that motivated this work.

## Test plan
- [ ] `pytest -q` is green
- [ ] `ruff check src tests` is clean
- [ ] Smoke: `voog_admin_api_call(method='GET', path='/site')` returns 200
- [ ] Smoke: `product_update` with `attributes.description` updates a draft product on a sandbox site
- [ ] README + CHANGELOG render correctly on github
EOF
)"
```

---

## Self-review checklist (run after generating this plan)

1. **Spec coverage** — every item in the gap-analysis table above maps to at least one task:
   - product description / status / price → Task 3 ✅
   - articles CRUD → Task 4 ✅
   - page create / update / set_data / duplicate → Task 5 ✅
   - text edit + page_add_content → Task 6 ✅
   - layout body update + asset CRUD → Task 7 ✅
   - languages/nodes helpers → Task 8 ✅
   - redirect update/delete → Task 9 ✅
   - ecommerce settings + site singleton → Task 10 ✅
   - everything else (orders, carts, gateways, …) → Task 2 (generic passthrough) ✅

2. **Placeholder scan** — searched for "TBD", "TODO", "implement later", "Add appropriate error handling", "Similar to Task N". None present; every step shows actual test code, actual implementation, actual commands, actual commit message.

3. **Type consistency** — symbols introduced in early tasks are referenced consistently:
   - `ATTR_KEYS`, `TRANSLATABLE_FIELDS`, `VALID_STATUS` (Task 3) — referenced from `_product_update`.
   - `simplify_languages`, `simplify_nodes` (Task 8 step 3) — imported by `multilingual.py` (Task 8 step 4).
   - `_validate_voog_name` reused by `_layout_update` (Task 7) — already exists in `layouts.py`.
   - `build_redirect_payload` referenced via existing import in `redirects.py`; new `_redirect_update` builds its own envelope inline (`{"redirect_rule": {...}}`) consistent with existing `_redirect_add`.
   - `IMMUTABLE_SITE_FIELDS`, `TRANSLATABLE_SETTINGS` are module-local in their respective tools — no cross-module use.

No drift detected.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-30-voog-mcp-endpoint-coverage.md`. Two execution options:

**1. Subagent-Driven (recommended)** — Dispatch a fresh subagent per task, review between tasks, fast iteration. Best for a plan of this size — each task is independently testable and the boundaries are clean.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints for review.

**Which approach?**

If subagent-driven: required sub-skill is `superpowers:subagent-driven-development`. Each task gets a fresh agent + two-stage review.

If inline: required sub-skill is `superpowers:executing-plans`. Batch execution with checkpoints between tasks 2/4/7/10.
