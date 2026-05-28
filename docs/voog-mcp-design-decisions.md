# voog-mcp design decisions

This doc captures the cross-cutting policies that shape voog-mcp's
foot-gun handling. The Voog ecommerce v1 API has a handful of
quirky shapes (POST-vs-PUT envelope differences, destructive PUTs,
full-replace vs merge semantics on `data`). When we ship a wrapper,
we have to choose between two responses to each quirk: auto-fix it
silently, or document it and force-gate the destructive path.

## Auto-fix vs document policy

**Auto-fix** when:

- The fix is a deterministic shape translation with no semantic loss
  (the caller's data is preserved, only the wire format changes).
- The fix lives entirely in the wrapper — no Voog API support required.
- The "bad" shape has no legitimate use case the wrapper would block by
  applying the fix.
- Auto-fixing strictly improves outcomes; the caller can't accidentally
  trip the gun via this surface.

Examples in v1.4:

- `product_update` accepts `asset_ids` (POST shape) and translates to
  `assets:[{id}]` internally before PUT — no semantic loss, only the
  PUT-on-Voog gotcha is hidden.
- `page_update(data=...)` / `article_update(data=...)` route through
  PATCH instead of PUT (Phase 2 S4) — Voog merges, no clobber. The
  alternative was to keep PUT and document the clobber; we chose
  auto-fix because the per-key `page_set_data` already provided the
  merge semantics, and PUT-clobber was strictly worse for every
  observable use case. (Tagged BREAKING CHANGE because the bytes on
  the wire change; behaviour is strictly safer.)

**Document + force-gate** when:

- A "safe" fix would require Voog server-side changes (e.g. introducing
  a non-destructive `variants` PUT path that doesn't already exist).
- The destructive form is legitimately useful in some workflows — we
  shouldn't ban it, only require explicit acknowledgement.
- The shape is wide enough that a wrapper guess would be fragile (e.g.
  fetching the full variant list and merging client-side is correct but
  expensive, and gets out of sync with the live server state).

Examples in v1.4:

- `product_update` `variants` without `variant_attributes` wipes ALL
  variants on Voog PUT (even ones with stable `id`). Forcing
  `variant_attributes` alongside (or rejecting until the caller sets
  `force=true`) is the right shape — we cannot transparently rebuild
  the full variant list without an extra GET, and even with the GET
  we'd race the actual store state. So: documented in the tool
  description; gated behind `force=true` to acknowledge. The
  underlying fix is a schema-tier API redesign Voog has not done.
- Deletes across the surface (`webhook_delete`, `element_delete`,
  `page_delete`, etc.) follow the same pattern — `force=true` required.
  No auto-fix because the user's intent is irreducible.

## How future foot-guns get triaged

When a Voog API quirk is reported (PR review, user feedback, audit
finding):

1. Reproduce empirically against a live tenant. Synthetic fixtures lie;
   the actual quirk may be subtler or already fixed server-side.
2. Decide which side of the auto-fix-vs-document line it falls on,
   using the criteria above. Default to auto-fix when the wrapper can
   reliably translate; default to document+force-gate when the
   wrapper can't act without risk.
3. If auto-fix:
   - Implement the translation in the handler, behind no flag.
   - Add a regression test against the bad shape (caller sends bad
     shape, wrapper sends good shape).
   - Document the quirk in the tool description AND in a CHANGELOG
     `Fixed` entry — operators learn what the wrapper now handles.
   - If the auto-fix changes wire bytes for any existing caller, tag
     it as a BREAKING CHANGE and bump the minor version (voog-mcp
     ships breaking changes in minor versions pre-1.0 — see CHANGELOG
     header).
4. If document+force-gate:
   - Add an explicit `force` parameter to the affected tool (boolean,
     default false).
   - Reject the destructive call when `force=false`, with an error
     message that names the destructive consequence and the
     `force=true` opt-in.
   - Document the gun in the tool description.
   - Cover with a schema-default-drift test in
     `tests/test_schema_defaults.py`.
5. Update `docs/voog-mcp-endpoint-coverage.md` if the affected endpoint
   row needs new notes.
6. If the quirk's root cause is a Voog API design issue (not a wrapper
   issue), file a doc-only note in this file under "Open quirks
   pending Voog-side fix" — running list of things we'd remove the
   force-gate for if Voog ever fixed them.

## Open quirks pending Voog-side fix

(Empty as of v1.4 — populate as new findings come in.)

## Related project memory

- `feedback_voog_assets_vs_asset_ids` — the `assets:[{id}]` vs
  `asset_ids` quirk.
- `feedback_voog_variants_destructive_put` — the `variants` PUT
  destructive default.
- `feedback_voog_media_sets_destructive_put` — media_sets PUT is
  destructive too; gallery assets dropped if not echoed back.
- `feedback_workaround_chain_schema_fix` — two consumer-side
  workarounds should trigger a schema-tier fix, not a third
  workaround. Same dichotomy from a different angle.
