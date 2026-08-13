"""MCP tool for cross-site cloning (issue #140 item 2).

One tool, ``site_clone``, driving the phase engine in :mod:`voog.clone`.

**Why one tool and not nine.** The phases are not independent operations a
caller picks between — they are one pipeline with hard ordering, sharing a
single state directory. Nine tools would present nine ways to run them out
of order, each producing a plausible-looking report over a broken site, and
the LLM has no way to know that `contents` before `pages` is meaningless.
A `phases` argument keeps the composition available (resume at `articles`,
re-run just `assets` after a quota raise) while the tool keeps the ordering.

**Why this tool needs two clients.** Every other tool in this package acts
on one site, so the server resolves ``site`` to a client and hands it over.
A clone needs a second, and building one inside the tool from raw config
would bypass the factory's per-site request budget and daily quota — the
rails that exist precisely to stop a runaway loop, on the one tool that
issues thousands of requests. The module therefore opts in to receiving the
factory (``NEEDS_CLIENT_FACTORY``) so the target client comes from the same
cache, with the same accounting, as the source.
"""

from __future__ import annotations

from mcp.types import CallToolResult, TextContent, Tool

from voog.clone import KNOWN_LIMITS, PHASE_ORDER, run_clone
from voog.clone.state import CloneStateError
from voog.errors import error_response, success_response

# Signals to voog.mcp.server that this group's call_tool takes a fourth
# argument: the ClientFactory. See the module docstring.
NEEDS_CLIENT_FACTORY = True

_ALL_PHASES = ("plan", *PHASE_ORDER)


def get_tools() -> list[Tool]:
    return [
        Tool(
            name="site_clone",
            description=(
                "Copy one Voog site's content onto another (layouts, layout "
                "assets, media, site settings, pages, content areas, articles). "
                "`site` is the SOURCE (read-only); `target_site` is OVERWRITTEN. "
                "Both must be names from voog_list_sites.\n"
                "\n"
                "DRY RUN BY DEFAULT — without force=true nothing is written and "
                "the result reports what would happen. Run phase 'plan' first: "
                "it returns source object counts, the target's remaining asset "
                "quota, and the specific things Voog will not let a clone "
                "reproduce.\n"
                "\n"
                "RESUMABLE. `state_dir` holds source-id -> target-id maps that "
                "make every phase re-runnable: a run stopped by an asset quota "
                "or a transient error is continued by calling again with the "
                "same state_dir, and nothing is created twice. Use a FRESH "
                "state_dir per source/target pair — the tool refuses a directory "
                "that belongs to a different pair.\n"
                "\n"
                "Phases run in dependency order regardless of the order you "
                "list them (a page cannot reference a layout that does not "
                "exist yet): " + ", ".join(PHASE_ORDER) + ".\n"
                "\n"
                "NOT COPIED: ecommerce (products, categories, discounts, cart "
                "rules, orders), elements, redirects, webhooks, forms, "
                "comments. Languages are matched by code, never created.\n"
                "\n"
                "Voog limits the clone cannot work around: " + " ".join(KNOWN_LIMITS)
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "site": {
                        "type": "string",
                        "description": "SOURCE site name from voog_list_sites (read-only).",
                    },
                    "target_site": {
                        "type": "string",
                        "description": (
                            "TARGET site name from voog_list_sites. Its content "
                            "areas are rebuilt and its layouts overwritten."
                        ),
                    },
                    "state_dir": {
                        "type": "string",
                        "description": (
                            "Absolute path holding the resume state. Reuse the "
                            "same directory to continue an interrupted run; use "
                            "a fresh one for a different source/target pair."
                        ),
                    },
                    "phases": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(_ALL_PHASES)},
                        "description": (
                            "Which phases to run. Omit for the full pipeline. "
                            "'plan' is a read-only preflight and runs alone."
                        ),
                    },
                    "force": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Required to write anything. Without it the run is a "
                            "dry run. This tool OVERWRITES the target site."
                        ),
                    },
                    "asset_budget_bytes": {
                        "type": "integer",
                        "description": (
                            "Cap on bytes uploaded in the assets phase. Defaults "
                            "to the target's remaining quota when Voog reports "
                            "one. Set it lower to leave headroom."
                        ),
                    },
                    "max_workers": {
                        "type": "integer",
                        "default": 4,
                        "description": "Parallel asset uploads (1-8).",
                    },
                },
                "required": ["site", "target_site", "state_dir"],
                "additionalProperties": False,
            },
            annotations={
                "readOnlyHint": False,
                # Overwrites a whole site. The strongest hint available, and
                # the force gate on top of it.
                "destructiveHint": True,
                # Re-running converges rather than duplicating — that is what
                # the state maps buy — but only within one state_dir.
                "idempotentHint": True,
            },
        ),
    ]


_KNOWN_TOOLS = frozenset({"site_clone"})


def call_tool(
    name: str, arguments: dict | None, client, factory=None
) -> list[TextContent] | CallToolResult:
    arguments = arguments or {}
    if name not in _KNOWN_TOOLS:
        return error_response(f"Unknown tool: {name}")
    with client.with_tool(name):
        return _site_clone(arguments, client, factory)


def _site_clone(arguments: dict, client, factory) -> list[TextContent] | CallToolResult:
    source_name = arguments.get("site")
    target_name = arguments.get("target_site")
    state_dir = arguments.get("state_dir")

    if not target_name or not isinstance(target_name, str):
        return error_response("site_clone: target_site must be a site name from voog_list_sites")
    if not state_dir or not isinstance(state_dir, str) or not state_dir.startswith("/"):
        return error_response("site_clone: state_dir must be an absolute path")
    if factory is None:
        return error_response(
            "site_clone: no client factory available — this tool needs to resolve "
            "a second site and cannot run outside the MCP server."
        )

    max_workers = arguments.get("max_workers", 4)
    if (
        not isinstance(max_workers, int)
        or isinstance(max_workers, bool)
        or not 1 <= max_workers <= 8
    ):
        return error_response("site_clone: max_workers must be an integer between 1 and 8")

    budget = arguments.get("asset_budget_bytes")
    if budget is not None and (
        not isinstance(budget, int) or isinstance(budget, bool) or budget < 0
    ):
        return error_response("site_clone: asset_budget_bytes must be a non-negative integer")

    try:
        target = factory.for_site(target_name)
    except Exception as exc:
        return error_response(f"site_clone: cannot resolve target_site {target_name!r}: {exc}")

    force = bool(arguments.get("force", False))
    try:
        result = run_clone(
            source=client,
            target=target,
            source_name=source_name,
            target_name=target_name,
            state_dir=state_dir,
            phases=arguments.get("phases"),
            dry_run=not force,
            asset_budget_bytes=budget,
            max_workers=max_workers,
        )
    except (CloneStateError, ValueError) as exc:
        return error_response(f"site_clone: {exc}")
    except Exception as exc:
        return error_response(f"site_clone: run failed before any phase completed: {exc}")

    return success_response(result, summary=_summarize(result))


def _summarize(result: dict) -> str:
    phases = result.get("reports") or []
    created = sum(r.get("created", 0) for r in phases)
    updated = sum(r.get("updated", 0) for r in phases)
    problems = result.get("problem_count", 0)
    mode = "DRY RUN — nothing written" if result.get("dry_run") else "applied"
    head = (
        f"🧬 site_clone {result.get('source')} → {result.get('target')} [{mode}]: "
        f"{created} created, {updated} updated across "
        f"{len(result.get('phases_run') or [])} phase(s)"
    )
    if result.get("aborted"):
        head = "⚠️ " + head + " — ABORTED mid-run; re-run with the same state_dir to resume"
    if problems:
        head += f"; {problems} item(s) could not be copied (see reports[].problems)"
    return head
