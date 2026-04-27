# Session prompt: voog-mcp Phase C/D — paralleelne tools/resources ehitus

Paste this into a fresh Claude Code session. Repo: `/Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Tööriistad/` (or `cd` there as first thing).

---

## Task

Build ONE task from voog-mcp Phase C (Tasks 10-14, MCP tools) or Phase D (Tasks 15-19, MCP resources) of the implementation plan. Each task is self-contained — your session ships **one PR with one task**, then ends. Future parallel sessions pick remaining tasks.

**Don't try to do multiple tasks** — the whole point of Phase C/D is paralleelne work, one task per session.

## State you inherit

**voog-mcp main:**
- Phase A foundation (`voog_mcp/` package, pyproject.toml, MCP SDK dep, VoogClient client, voog.py shim)
- Phase B server skeleton (`voog_mcp/server.py` with `init_site` + `init_tools` async setup, stdio transport)
- Task 9 (Phase C, first tool group) — **established the TOOL_GROUPS dispatcher pattern** that future Phase C tasks USE without conflict
- 27+ unit tests pass via `.venv/bin/python -m unittest discover tests`

**Critical pattern from Task 9 (DO NOT recreate):**
- `voog_mcp/server.py` has a `TOOL_GROUPS: list[ToolGroup]` registry list
- Each Phase C task adds **exactly 2 lines** to `server.py`:
  1. `from voog_mcp.tools import <yourgroup> as <yourgroup>_tools` (one import line)
  2. `TOOL_GROUPS.append(<yourgroup>_tools)` (one append line)
- This minimizes merge conflicts when 5+ parallel sessions all add their group simultaneously

**voog-mcp branches:** Only `main`. No stale feature branches. PR #1, #2, #3, #4, #13, #14, #15 merged.

## Required reading (in this order)

1. **`Tööriistad/docs/specs/2026-04-26-mcp-server.md`** — full spec. Read § 3 (architectural decisions), § 4 (tool inventory), § 5 (resource inventory), § 6 (capabilities).
2. **`Tööriistad/docs/plans/2026-04-26-mcp-server-plan.md`** — implementation plan. Find your task description (Tasks 10-19 are independent). Read the parallelization strategy at the top.
3. **`Tööriistad/voog_mcp/server.py`** — see how Task 9 wired pages tools via TOOL_GROUPS. Mirror that pattern.
4. **`Tööriistad/voog_mcp/tools/pages.py`** — Task 9 reference implementation for tools. JSON schemas, `get_tools()` function, `async call_tool()` dispatch.
5. **`Tööriistad/voog_mcp/client.py`** — VoogClient class. Your tool/resource calls `client.get(path, base=client.ecommerce_url)` etc.
6. **`Tööriistad/voog_mcp/errors.py`** — error/success response helpers. Use them.
7. **`~/.claude/skills/voog/SKILL.md`** — Voog API gotchas, especially the two API URLs (`/admin/api/` vs `/admin/api/ecommerce/v1/`), `new_record?` content area papercut, layout id behavior.

## Mandatory pre-flight

```bash
cd /Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Tööriistad

# Pull latest main
git checkout main && git pull

# Verify .venv installed and tests pass
.venv/bin/python -m unittest discover tests 2>/dev/null; echo "exit: $?"
# Expected: exit 0
# If .venv missing: python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# Verify voog-mcp registers as console script
.venv/bin/voog-mcp 2>&1 | head -3
# Expected: prints docstring or hangs waiting for stdin (kill with Ctrl+C)
```

## Task table — pick ONE

### Phase C: Tools (each adds one tool group)

| Task | File | Tools | Notes |
|---|---|---|---|
| **10** | `voog_mcp/tools/pages_mutate.py` | `page_set_hidden`, `page_set_layout`, `page_delete` | `destructiveHint: true` for `page_delete` |
| **11** | `voog_mcp/tools/layouts.py` | `layout_rename`, `layouts_pull`, `layouts_push` | Add `target_dir` parameter to pull/push (don't use cwd) |
| **12** | `voog_mcp/tools/snapshot.py` | `pages_snapshot`, `site_snapshot` | Sync only (v1); progress notifications deferred to v0.3 |
| **13** | `voog_mcp/tools/products.py` | `products_list`, `product_get`, `product_update`, `product_set_images` | Use `client.ecommerce_url` for base |
| **14** | `voog_mcp/tools/redirects.py` | `redirects_list`, `redirect_add` | Small task, good warm-up |

### Phase D: Resources (each adds one URI handler group)

| Task | File | URIs | Notes |
|---|---|---|---|
| **15** | `voog_mcp/resources/pages.py` | `voog://pages`, `voog://pages/{id}`, `voog://pages/{id}/contents` | |
| **16** | `voog_mcp/resources/layouts.py` | `voog://layouts`, `voog://layouts/{id}` | Body returns `text/plain` (.tpl source) |
| **17** | `voog_mcp/resources/articles.py` | `voog://articles`, `voog://articles/{id}` | Body is HTML |
| **18** | `voog_mcp/resources/products.py` | `voog://products`, `voog://products/{id}` | Use `?include=variant_types,translations` |
| **19** | `voog_mcp/resources/redirects.py` | `voog://redirects` | Smallest task |

**Pick by:** check open voog-mcp PRs first. Avoid duplicate work:
```bash
/opt/homebrew/bin/gh pr list --repo runnel/voog-mcp --state open --json number,title,headRefName
```
If a task is already in an open PR or in-progress branch, pick a different one.

## TDD requirements

For every task:
1. Write the failing test FIRST (`tests/test_tools_<group>.py` or `tests/test_resources_<group>.py`)
2. Run test, confirm fails
3. Implement minimal code to pass
4. Run test, confirm passes
5. Smoke test against `runnel.ee` API where applicable (use `RUN_SMOKE=1 ` env var prefix for any test that hits real API)
6. Run full suite — no regressions
7. Commit
8. Then wire into `server.py` (the 2-line addition)
9. Run integration test verifying server lists your new tools/resources
10. Commit
11. Push branch + open PR

## Constraints

- **One task = one PR. Base = main, NOT stacked.** Stacked PRs caused mass-closure during PR #4-#12 (lesson: GitHub's `--delete-branch` cascades through stack). Always base on main.
- **TOOL_GROUPS pattern is mandatory.** Don't add an `elif` branch in `handle_call_tool` — append to the registry. If the registry isn't there yet, you're on the wrong main; pull again.
- **stdlib + mcp SDK only** for runtime deps. No `requests`, no `pydantic` extras. Tests use stdlib `unittest` + `unittest.mock`. (Aspirational; if mcp SDK pulls in pydantic transitively that's fine.)
- **`.venv/bin/python` always**, never system Python. The repo requires Python 3.10+ (mcp SDK requirement) and your system Python is 3.9.
- **`init_site()` lazy-init pattern is sacred.** Don't import-time-side-effect anything. Tests must be able to `import voog` and `import voog_mcp.tools.<group>` from `/tmp/`.
- **Logging to stderr only.** stdout is the JSON-RPC channel. Use `logger.info(...)` (configured to stderr in `__main__.py`), not `print()`.

## Session plan

1. Read required docs (above) — under 30 min
2. Run pre-flight (pull + tests pass) — 5 min
3. Pick a task from the table (avoid duplicates) — 2 min
4. Create branch `feat/mcp-task-NN-<short-slug>` from `main`
5. Use **`subagent-driven-development` skill** with the full task text from the plan + critical context (TOOL_GROUPS pattern, VoogClient API, error helpers)
6. Implementer writes test → impl → wire to server.py → smoke test → commit. Spec compliance review. Code quality review.
7. Push branch + open PR with structured body (summary, test plan, deferred notes if any)
8. Report PR link back to the user. **Don't self-merge** — user reviews.

## Known decisions (don't re-litigate)

(From spec § 3 — these are settled.)

- **MCP SDK over raw protocol** — accept `mcp>=0.9.0` as runtime dep. Don't try to roll your own JSON-RPC.
- **One server per site** — multi-tenant deferred. Each Voog site = its own `claude_desktop_config.json` entry with own env vars. No site-arg in tools.
- **Tools vs Resources split** — mutations are always tools, single-object reads are resources, list reads are both. (Already encoded in task table.)
- **Package structure (`voog_mcp/tools/`, `voog_mcp/resources/`)** — don't restructure.
- **Backward compat: `voog.py` CLI works as shim.** Don't delete or break it. Tests in `tests/test_voog.py` must keep passing.
- **Env vars for config: `VOOG_HOST`, `VOOG_API_TOKEN`.** Not voog-site.json (that's CLI-only).
- **stdio transport for MVP.** HTTP/SSE deferred.
- **No progress notifications (v0.1).** `pages_snapshot` and `site_snapshot` block synchronously. v0.3 territory.
- **No prompts (v0.1).** Tools + resources only. Prompts are v0.3.
- **Deferred polish items** (mcp version pin tightening, additionalProperties on tool schemas, type Protocol for ToolGroup contract): track separately, not blocker.

## Don't do

- **Don't make stacked PRs.** Always base=main. (Lesson learned: PR #4-#12 mass-closed when bases got auto-deleted.)
- **Don't extend the `elif` chain in `handle_call_tool`.** Use TOOL_GROUPS append. If you find yourself thinking "I'll just add another elif", stop — you missed the pattern.
- **Don't add new top-level dependencies** beyond `mcp`. Stick to stdlib for everything else.
- **Don't import the package at module load** in ways that trigger `init_site()`. Lazy-init only — tests need to import without a Voog API token in env.
- **Don't print to stdout.** Ever. It corrupts the JSON-RPC channel.
- **Don't merge your own PR.** User reviews. Even if tests pass.
- **Don't aggregate tasks.** One task per session per PR. Even if you "have time for two" — leave the second for another parallel session.
- **Don't use system Python.** Always `.venv/bin/python` and `.venv/bin/voog-mcp`.

## File locations recap

- voog-mcp repo: `/Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Tööriistad/`
- spec: `Tööriistad/docs/specs/2026-04-26-mcp-server.md`
- plan: `Tööriistad/docs/plans/2026-04-26-mcp-server-plan.md`
- voog skill: `~/.claude/skills/voog/SKILL.md`
- API key: `Claude/.env` env var `RUNNEL_VOOG_API_KEY` (smoke tests against runnel.ee)
- gh CLI: `/opt/homebrew/bin/gh`
- Python: `.venv/bin/python` (3.11)
- Sister project (separate track, don't touch): `runnel-voog` at `/Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Isiklik/Runnel/runnel-voog/`

---

**Edu! Üks task, üks PR, anna link tagasi review'ks.**
