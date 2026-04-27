# Handoff prompts: voog-mcp Phase E + deferred follow-ups

Five self-contained session prompts. Run each in a fresh Claude Code session. Each one ships **one PR**, base = `main` (not stacked). Order is suggested, not required — sessions are independent.

**Common state across all sessions:**
- Repo: `/Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Tööriistad/`
- Phase A/B/C/D + helpers refactor merged. 266+ tests pass on main.
- Spec: `docs/specs/2026-04-26-mcp-server.md`
- Plan: `docs/plans/2026-04-26-mcp-server-plan.md`
- Original kickoff: `docs/handoff-prompts/mcp-phase-c-d-kickoff.md` (still relevant for ground rules)
- Skill: `~/.claude/skills/voog/SKILL.md`
- Python: `.venv/bin/python` (3.11), `.venv/bin/voog-mcp`
- gh CLI: `/opt/homebrew/bin/gh`
- API key: `Claude/.env` env var `RUNNEL_VOOG_API_KEY`

**Common rules (don't re-litigate):**
- Base = main, NOT stacked. One PR per session.
- TOOL_GROUPS / RESOURCE_GROUPS dispatcher — 1 import + 1 append in `server.py`.
- stdout = JSON-RPC. Use `logger.info(...)`, never `print()` in MCP runtime code.
- Explicit MCP annotation triples on every tool (`readOnlyHint`, `destructiveHint`, `idempotentHint`).
- TDD: write failing test, implement, verify green. Tests use `unittest` + `unittest.mock` stdlib.
- Don't merge own PR. User reviews.
- Sync race awareness: `Tööriistad/` is in Dropbox. `git checkout` may flip you to a parallel session's branch. Verify `git branch --show-current` before each commit. If a leaked file appears, `git stash push -m "leak-$(date +%s)" -- <file>` to preserve it safely.

---

## Session 1: Annotation finalization (Phase E Task 20)

**Branch:** `feat/mcp-task-20-annotation-finalization`
**Estimated complexity:** small — pure-text edits + test tightening, ~30 min

### Background

After PR #27 (Task 10), the codebase moved to **always-explicit annotation triples** on every tool. But three tool files predate that pattern and still have partial or missing annotations:

| File | Tool | Current | Should be |
|---|---|---|---|
| `voog_mcp/tools/pages.py` | `pages_list` | `{readOnlyHint: True}` | `{readOnlyHint: True, destructiveHint: False, idempotentHint: True}` |
| `voog_mcp/tools/pages.py` | `page_get` | `{readOnlyHint: True}` | same as above |
| `voog_mcp/tools/pages.py` | `pages_pull` | `{readOnlyHint: True}` | same as above |
| `voog_mcp/tools/redirects.py` | `redirects_list` | `{readOnlyHint: True}` | same as above |
| `voog_mcp/tools/redirects.py` | `redirect_add` | **none** | `{readOnlyHint: False, destructiveHint: False, idempotentHint: False}` |

**Why this matters:** MCP spec defaults `destructiveHint=true` when `readOnlyHint=false`. `redirect_add` with no annotations is treated as destructive by spec-conformant clients — exact opposite of intent (adding a redirect is additive, not destructive).

**Why `redirect_add` has `idempotentHint=False`:** calling `redirect_add` twice with the same source/destination either creates a duplicate rule OR Voog's API errors on the conflict. Either way, repeated calls have additional effect — not idempotent.

### Task

1. Update annotations on all 5 tools above.
2. Tighten the existing test files to assert the full triple with `assertIs(..., expected)`. Each tool group already has an `_ann_get` helper from PR #27 era — reuse it.
3. Tests to add/tighten:
   - `tests/test_tools_pages.py` — replace permissive read-only check with full-triple assertion (3 tools)
   - `tests/test_tools_redirects.py` — same for `redirects_list`; pin `redirect_add` triple explicitly with `idempotentHint=False`
4. Document the audit in the commit message: list the 5 tools fixed and the spec rationale.

### Pre-flight

```bash
cd /Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Tööriistad
git checkout main && git pull
.venv/bin/python -m unittest discover tests 2>&1 | tail -3   # expect Ran 266 tests, OK
git checkout -b feat/mcp-task-20-annotation-finalization
```

### Verification

```bash
.venv/bin/python -m unittest tests.test_tools_pages tests.test_tools_redirects 2>&1 | tail -3   # expect OK
.venv/bin/python -m unittest discover tests 2>&1 | tail -3   # expect Ran 266+ tests, OK (no regressions)
```

### PR body template

```markdown
## Summary

Phase E Task 20 — backfills explicit MCP annotation triples on the 5 tools that predate the always-explicit pattern established by PR #27.

### Tools updated

| File | Tool | Before | After |
|---|---|---|---|
| pages.py | pages_list | `{readOnlyHint: True}` | full triple |
| pages.py | page_get | `{readOnlyHint: True}` | full triple |
| pages.py | pages_pull | `{readOnlyHint: True}` | full triple |
| redirects.py | redirects_list | `{readOnlyHint: True}` | full triple |
| redirects.py | redirect_add | (none) | full triple |

### Why redirect_add was the most-impactful fix

MCP spec defaults `destructiveHint=true` when `readOnlyHint=false`. With no annotations,
spec-conformant clients treated `redirect_add` as destructive — exact opposite of intent.
`redirect_add` is additive (creates a new rule), not destructive.

### Test plan
- [x] Tests tightened to `assertIs(..., expected)` (not "falsy or missing")
- [x] Full suite green
```

**Stop after PR open. Don't self-merge.**

---

## Session 2: layouts_pull + layouts_push (deferred Task 11b)

**Branch:** `feat/mcp-task-11b-layouts-sync-tools`
**Estimated complexity:** medium — filesystem-touching, manifest handling, ~60 min

### Background

Task 11 (PR #28) shipped `layout_rename` / `layout_create` / `asset_replace` but deferred `layouts_pull` and `layouts_push` because they touch the local filesystem (read/write `.tpl` files + manifest). The `voog.py` CLI still works for these via Bash; this PR brings them to the MCP surface.

The CLI implementations live in `voog.py`:
- `pull()` — fetches all layouts via `GET /layouts/{id}`, writes per-layout `.tpl` files into `layouts/` and `components/` subdirs of `target_dir`, builds `manifest.json` mapping local paths → ids.
- `push()` — reads `manifest.json` + `.tpl` files, PUTs each `{body: ...}` to `/layouts/{id}`. Optional `files` filter to push only selected paths.

### Task

Create `voog_mcp/tools/layouts_sync.py` with two tools:

1. **`layouts_pull(target_dir: string)`** — write all layouts to disk under `target_dir`. Required `target_dir` MUST be absolute path (per snapshot tools convention). Refuses if `target_dir` exists AND has `.tpl` files (same "no silent merge" rationale as `site_snapshot`'s refuse-existing). Returns structured breakdown: `{target_dir, layouts_written, components_written, manifest_path}`.
2. **`layouts_push(target_dir: string, files: array<string> | null)`** — push files (or all if `files=null`) from `target_dir`'s manifest. Returns per-file success/failure breakdown like `page_set_hidden`. Reads manifest, validates each file exists, PUTs in sequence, captures failures per-file.

**Annotations:**
- Both: `readOnlyHint=False, destructiveHint=False, idempotentHint=True` (matches snapshot pattern — disk-write but additive, same input → same output)

**Schema constraints:**
- `target_dir` required, absolute path validation (mirror `_pages_snapshot`)
- `files` optional, defaults to null/all

**Implementation strategy:**
- Don't import from `voog.py` (couples to CLI internals). Reimplement using `client.get_all("/layouts")`, `client.get(f"/layouts/{id}")`, `client.put(f"/layouts/{id}", {"body": ...})`.
- Manifest format: `{<relative_path>: {id, type, updated_at}}` — match existing voog.py shape so MCP-pulled and CLI-pulled trees are interchangeable.
- Filename derivation matches voog.py: layouts → `layouts/<title>.tpl`, components → `components/<title>.tpl`. Helper `_safe_filename(title)` shared with existing `_validate_voog_name` in `voog_mcp.tools.layouts` — consider moving to a tools-level helpers module if natural.

**Wire into server.py:** import + append to `TOOL_GROUPS`. Sentinel `test_phase_c_complete` may need updating — set in `test_tools_snapshot.py` includes 6 modules; this would make it 7. Update the sentinel to include `layouts_sync_tools`.

### Tests

`tests/test_tools_layouts_sync.py` — at minimum:

- Schema shape (2 tools, required fields, absolute path enforcement)
- `layouts_pull`:
  - Writes `manifest.json` + per-layout `.tpl` files for sample data (mock client)
  - Components go to `components/` subdir, layouts to `layouts/`
  - Refuses existing dir with `.tpl` files
  - Allows existing empty/non-tpl dir (for refresh into a fresh location)
  - API failure → `error_response`
  - Relative path rejected
- `layouts_push`:
  - Reads manifest, PUTs per file
  - `files=null` pushes everything
  - `files=["specific.tpl"]` pushes only that
  - Missing manifest → error
  - Missing file in manifest → captured in per-file breakdown, doesn't abort
  - Per-file failures captured
  - Relative path rejected
- Registry: in `TOOL_GROUPS`, no name collisions

### Pre-flight + verification

Same as Session 1, but with `feat/mcp-task-11b-layouts-sync-tools` branch.

**Smoke test:** Skip live smoke for `layouts_push` (mutating). For `layouts_pull`, smoke against runnel.ee using a tempdir is reasonable — should write 50+ `.tpl` files.

**Stop after PR open. Don't self-merge.**

---

## Session 3: product_set_images (deferred from Task 13)

**Branch:** `feat/mcp-product-set-images-tool`
**Estimated complexity:** medium-large — 3-step asset upload protocol, filesystem, ~60-90 min

### Background

Task 13 (PR #30) shipped `products_list` / `product_get` / `product_update` but deferred `product_set_images`. The CLI version reads image files from disk, runs the 3-step Voog asset upload protocol per file (create asset → PUT to upload URL → confirm), then updates the product's `asset_ids`.

The CLI implementation lives in `voog.py`:
- `upload_asset(filepath)` — 3 API calls: POST `/assets`, PUT to returned upload_url, POST `/assets/{id}/confirm`. Returns asset dict with `id`.
- `product_set_images(product_id, filepaths)` — uploads each file via `upload_asset`, then PUTs `/products/{id} {asset_ids: [new ids]}`.

### Task

Create `voog_mcp/tools/products_images.py` with one tool:

1. **`product_set_images(product_id: int, files: array<string>)`** — replaces a product's images. `files` is an array of absolute paths to image files (jpg, png, gif, webp). First file becomes the main image; rest are gallery images. Returns `{product_id, old_asset_ids, new_asset_ids, uploaded: [{filename, asset_id, url}], failed: [{filename, error}]}`.

**Annotations:**
- `readOnlyHint=False, destructiveHint=True, idempotentHint=False`
- Why `destructiveHint=True`: replaces existing images. Old asset_ids are unlinked from the product (the assets themselves remain in Voog's asset library, but the association is gone — caller has to manually re-link if they want to undo).
- Why `idempotentHint=False`: each call uploads new asset records (different ids), so repeat calls aren't equivalent.

**Schema:**
- `product_id`: integer, required
- `files`: array of strings, `minItems: 1`, required. Each string is an absolute path to a local image file.
- Optional `force: boolean` (default false) — defensive opt-in like `page_delete`. Without `force=true`, refuse to replace existing images. The MCP `destructiveHint` annotation tells the client; the in-band `force` is the second safety layer.

**Implementation:**
- Validate each file exists + has supported extension (`.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`).
- Reject non-absolute paths (mirror snapshot pattern).
- Run 3-step upload per file via `client.post`/`client.put`. The PUT to upload URL is special — it goes to a Voog-provided URL with the file bytes as binary body, NOT the standard JSON request. May need a new method on `VoogClient` (e.g. `put_binary(url, file_bytes)`) OR use `urllib.request` directly inside the tool.
- After all uploads succeed, PUT `/products/{id} {product: {asset_ids: [...]}}` on `client.ecommerce_url` base.
- If any single upload fails, capture in `failed` array. **Question to answer in PR description:** if some uploads succeed but others fail, do we partially update the product or roll back? Recommendation: do the final PUT only if ALL uploads succeeded — otherwise the product has a half-set of images. Document this choice clearly.

**CONTENT_TYPES constant:** copy from `voog.py` (`{".jpg": "image/jpeg", ".png": "image/png", ...}`) — keep file-format whitelist explicit.

### Tests

`tests/test_tools_products_images.py` — at minimum:
- Schema shape, full annotation triple
- File existence check (non-existing path → error before any API call)
- Unsupported extension rejected (e.g. `.txt`, `.pdf`)
- Relative path rejected
- `force=false` (default) blocks replacement when product has existing images
- `force=true` proceeds
- All-success: 3-step upload mocked per file, final product PUT receives correct `asset_ids`
- One-upload-fails: documented partial-failure semantics (recommend: don't update product if any upload failed)
- API error during product PUT → captured (uploads already done — surface them in result)
- Registry: in `TOOL_GROUPS`, no name collisions

**No live smoke** — mutating + creates new asset records on runnel.ee. Mock-only.

### Pre-flight + verification

Same as Session 1.

**Stop after PR open. Don't self-merge.**

---

## Session 4: MCP integration tests per group (Phase E Task 21)

**Branch:** `feat/mcp-task-21-integration-tests`
**Estimated complexity:** medium-large — subprocess + JSON-RPC plumbing, ~90 min

### Background

`tests/test_mcp_integration.py` currently has one initialize-handshake test. Phase E Task 21 expands it to verify each tool group end-to-end via subprocess + JSON-RPC, with `RUN_SMOKE=1` env var gating real-API tests.

### Task

Expand `tests/test_mcp_integration.py` to include one test per tool group AND one per resource group, all gated on `RUN_SMOKE=1`. Each test:

1. Spawn `voog-mcp` subprocess with `VOOG_HOST=runnel.ee, VOOG_API_TOKEN=$RUNNEL_VOOG_API_KEY`
2. Send `initialize` request
3. Send `tools/list` (or `resources/list`) — assert the expected tools/resources are present
4. Call ONE representative read-only tool/resource per group
5. Assert response shape (no errors, expected fields in result)
6. Cleanup subprocess

**Read-only tools to test (mutating tools skipped — risky on live runnel.ee):**
- `pages_list` (pages group)
- `redirects_list` (redirects group)
- `products_list` (products group)
- `pages_pull` (pages group, second tool)

**Read-only resources to test:**
- `voog://pages` (pages resource)
- `voog://layouts` (layouts resource)
- `voog://layouts/{id}` (single layout — pick smallest id from list response)
- `voog://articles` (articles resource)
- `voog://products` (products resource)
- `voog://redirects` (redirects resource — even if 0 items, contract verified)

**Test design notes:**
- All tests gated `@unittest.skipUnless(os.environ.get("RUN_SMOKE"), "RUN_SMOKE=1 required")`
- Helper: `_call_jsonrpc(proc, method, params, id)` writes line, reads line, returns parsed response. Reusable across tests.
- Subprocess timeout: 30s per test (some snapshot-style ops are slow; basic reads should be <2s).
- Cleanup: `proc.terminate(); proc.wait(timeout=5)` in `tearDown`.

### Verification

```bash
# Without smoke flag — tests skip cleanly
.venv/bin/python -m unittest tests.test_mcp_integration   # all skipped

# With smoke — real API calls
RUN_SMOKE=1 .venv/bin/python -m unittest tests.test_mcp_integration -v   # all pass

# Full suite still green without RUN_SMOKE
.venv/bin/python -m unittest discover tests   # 266+ pass, integration tests skipped
```

### PR body template

Note that integration tests are **opt-in via `RUN_SMOKE=1`** — they shouldn't run in CI by default (they hit live API + need credentials). Document this clearly in the test file docstring and PR body.

**Stop after PR open. Don't self-merge.**

---

## Session 5: README + claude_desktop_config + voog skill update (Phase E Tasks 22+23)

**Branch:** `feat/mcp-task-22-23-readme-and-skill`
**Estimated complexity:** small-medium — docs only, ~45 min

### Background

The voog-mcp repo's README is bare. Voog skill (`~/.claude/skills/voog/SKILL.md`) describes the CLI but not the MCP server. Phase E Tasks 22 + 23 fill those gaps — combining into one PR because both are docs and they cross-reference each other.

### Task

#### Part A: README.md (Task 22)

Update `Tööriistad/README.md` with:

1. **What is voog-mcp** — one-paragraph summary (Voog CMS MCP server, exposes Liquid templates / pages / products / ecommerce as MCP tools + resources). Link to spec.
2. **Installation** — `pip install -e .` from repo root, requires Python 3.10+, MCP SDK pulls automatically.
3. **Configuration** — example `claude_desktop_config.json` snippet:
   ```json
   {
     "mcpServers": {
       "voog-runnel": {
         "command": "voog-mcp",
         "env": {
           "VOOG_HOST": "runnel.ee",
           "VOOG_API_TOKEN": "..."
         }
       }
     }
   }
   ```
   Per-site server entries — `voog-stella`, `voog-runnel`, etc.
4. **Tool inventory** — concise table with all tool names + one-line descriptions. Group by file (pages / pages_mutate / layouts / snapshot / products / redirects / + Phase E additions if those PRs land first). Link to spec § 4.
5. **Resource URIs** — table: `voog://pages`, `voog://pages/{id}`, `voog://pages/{id}/contents`, `voog://layouts`, `voog://layouts/{id}` (text/plain), etc. Link to spec § 5.
6. **CLI fallback** — `voog.py` still works via Bash for filesystem-heavy operations (asset uploads, big snapshots). Cross-reference: when to use MCP vs CLI.
7. **Development** — `.venv` setup, running tests (`unittest discover`), `RUN_SMOKE=1` for integration tests.

#### Part B: ~/.claude/skills/voog/SKILL.md (Task 23)

Add a new section "**voog-mcp server**" to the existing voog skill. Cover:

1. **When MCP server vs CLI:** MCP for everyday "list/get/update via Claude" workflows; CLI for batch ops + filesystem-heavy work.
2. **Registering in `claude_desktop_config.json`** — mirrors README example, but in skill-context phrasing.
3. **Tool naming convention:** `mcp__voog-runnel__pages_list`, `mcp__voog-stella__layout_create`, etc. (server-name prefix per Anthropic MCP convention).
4. **Resource URIs** — list of available URIs, when to use which. Particularly: read `voog://pages` once at session start to give Claude the page structure context.
5. **Common workflows:**
   - "Show me all pages with hidden=true" → `pages_list` (or read `voog://pages`)
   - "Rename layout X to Y" → `layout_rename`
   - "Backup before risky op" → `site_snapshot` (or pages_snapshot for lighter)
   - "Add redirect from /old to /new" → `redirect_add`
6. **Annotation hints:** `destructiveHint=true` triggers user confirmation in Claude — `page_delete`, `product_set_images` (when added). Server will refuse without `force=true` regardless.
7. **Two API surfaces:** `/admin/api/` (Voog Admin) vs `/admin/api/ecommerce/v1/` (ecommerce). Products live on the latter; everything else on the former. Most MCP tools handle this transparently — the user doesn't have to think about it. Cross-link to API gotchas already in the skill.

### Verification

- README renders cleanly on GitHub (preview the markdown)
- Skill markdown is parsed-as-skill correctly (test: ask Claude in a fresh session "load voog skill" and verify it sees the new MCP section)
- All cross-references resolve (file paths in the README, doc links in the skill)

### Pre-flight + verification

```bash
cd /Users/runnel/Library/CloudStorage/Dropbox/Documents/Claude/Tööriistad
git checkout main && git pull
git checkout -b feat/mcp-task-22-23-readme-and-skill
# Edit README.md
# Edit ~/.claude/skills/voog/SKILL.md (CAUTION — this is a global skill file, separate repo concerns)
# Verify with: cat ~/.claude/skills/voog/SKILL.md | head -20  to confirm structure intact
```

**NB skill location:** `~/.claude/skills/voog/SKILL.md` is in the user's global skills directory, NOT in the voog-mcp repo. Skill file changes need to be tracked and committed separately. The PR for THIS task should:
- Commit `Tööriistad/README.md` change (in voog-mcp repo)
- Note in PR description that the skill file was also updated, and provide the diff inline for reference (since skill repo is separate)

**Stop after PR open. Don't self-merge.**

---

## After all 5 sessions land

The voog-mcp project will be at v0.1.0-complete:
- Phase A foundation, Phase B server skeleton
- Phase C: 6/6 tool groups + 11b layouts-sync + product_set_images
- Phase D: 5/5 resource groups
- Phase E: annotations finalized, integration tests with RUN_SMOKE gate, README + skill docs

Remaining v0.2 / v0.3 territory (out of scope for these 5 sessions):
- Progress notifications for `pages_snapshot` / `site_snapshot` (v0.3)
- MCP prompts (v0.3)
- HTTP/SSE transport (v0.3 — currently stdio only)
- PyPI release (v0.2)

**For each session:** open the PR, post the link back to the user. User reviews, requests changes if any, merges. Then the next session can start (in parallel or sequentially — they don't depend on each other).
