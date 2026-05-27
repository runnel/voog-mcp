"""Capture + sanitise ecommerce response fixtures from a live Voog site.

Usage:
    source .venv/bin/activate
    STELLA_OLD_API_KEY=... python scripts/capture_ecommerce_fixtures.py \
        --host stellasoomlais.voog.com --token-env STELLA_OLD_API_KEY

Reads token from $STELLA_OLD_API_KEY by default. Writes JSON files into
tests/fixtures/ecommerce/ with a `_meta` header. Re-running overwrites
existing fixtures - review the diff manually before commit.

Default host is Stella's old archive site (stellasoomlais.voog.com) so the
throwaway category / duplicated product / 251-entry bulk probe do not touch
the customer-facing production site (stellasoomlais.com). Override --host
+ --token-env for other Voog sites.

PII sanitisation:
  - email-like values  -> 'customer+1@example.com'
  - phone-like values  -> '+372 5000 0000'
  - name keys inside an order -> 'Test Customer'
  - address keys       -> placeholder values
  - signed URLs        -> 'https://example.com/<redacted-signed>'
  - large numeric ids  -> small deterministic ints (per-fixture counter)

The sanitiser is intentionally defensive - sanitisation_version=1 means
'all known v1 rules applied'. If sanitisation rules change, bump the
version and re-capture.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# Phone: any run of 6+ digits (covers Estonian 7-digit mobiles + international).
PHONE_RE = re.compile(r"\+?\d[\d\s()-]{4,}\d")
SIGNED_URL_RE = re.compile(r"https?://[^\s\"]+[?&](token|signature|sig|key)=[^&\s\"]+")

# Always-PII keys regardless of where they appear.
_ALWAYS_PII_NAME_KEYS = frozenset(
    {"customer_name", "first_name", "last_name", "company_name", "contact_name"}
)
# When walking into a value whose key is one of these, the inner object is a
# personal-info container; sanitise its `name` / `phone` / `email` / `vat_code`.
_PII_CONTAINER_KEYS = frozenset({"customer", "billing_address", "shipping_address"})
_PII_CONTAINER_NAME_FIELDS = frozenset({"name", "phone", "email", "vat_code"})
_ADDRESS_KEYS = frozenset(
    {"address", "address1", "address2", "street", "city", "country", "country_code",
     "postal_code", "state", "region", "zip", "zip_code", "instructions"}
)


def _scrub_string(s: str) -> str:
    s = EMAIL_RE.sub("customer+1@example.com", s)
    s = SIGNED_URL_RE.sub("https://example.com/<redacted-signed>", s)
    # phone regex AFTER URL/email since URLs may contain digits
    s = PHONE_RE.sub("+372 5000 0000", s)
    return s


def _sanitise(
    value: Any,
    *,
    pii_container: bool = False,
    id_counter: list[int],
) -> Any:
    """Recursive walk.

    pii_container is True only for the immediate children of a key in
    _PII_CONTAINER_KEYS - it does NOT propagate deeper, so we don't
    accidentally rewrite `items[].product.name` (a product title, not a
    customer name) just because we're nested under an order.
    """
    if isinstance(value, dict):
        out: dict = {}
        for k, v in value.items():
            if pii_container and k in _PII_CONTAINER_NAME_FIELDS:
                if isinstance(v, str):
                    out[k] = "Test Customer" if k == "name" else (
                        "customer+1@example.com" if k == "email" else (
                            "+372 5000 0000" if k == "phone" else
                            f"<sanitised {k}>"
                        )
                    )
                else:
                    out[k] = v
            elif k in _ALWAYS_PII_NAME_KEYS:
                out[k] = "Test Customer"
            elif k in _ADDRESS_KEYS:
                if isinstance(v, str) and v:
                    out[k] = f"<sanitised {k}>"
                else:
                    out[k] = v
            elif k in _PII_CONTAINER_KEYS and isinstance(v, dict):
                out[k] = _sanitise(v, pii_container=True, id_counter=id_counter)
            else:
                out[k] = _sanitise(v, pii_container=False, id_counter=id_counter)
        return out
    if isinstance(value, list):
        return [_sanitise(v, pii_container=pii_container, id_counter=id_counter)
                for v in value]
    if isinstance(value, str):
        return _scrub_string(value)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1000:
        id_counter[0] += 1
        return id_counter[0]
    return value


def _http_request(method: str, url: str, token: str, body: dict | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "X-API-Token": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "voog-mcp-fixture-capture/1.4",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        if not raw:
            return None
        return json.loads(raw)


def _save_fixture(
    out_dir: Path,
    name: str,
    method: str,
    endpoint: str,
    payload: Any,
    gotchas: list[str],
    *,
    source_site: str,
    voog_host: str,
) -> None:
    id_counter = [9000]
    sanitised = _sanitise(payload, pii_container=False, id_counter=id_counter)
    record = {
        "_meta": {
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_site": source_site,
            "voog_host": voog_host,
            "endpoint": endpoint,
            "method": method,
            "sanitisation_version": 1,
            "gotchas": gotchas,
        },
        "response": sanitised,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"  wrote {name}.json", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--host",
        default="stellasoomlais.voog.com",
        help="Voog host (default: stellasoomlais.voog.com - Stella's archive site).",
    )
    parser.add_argument(
        "--token-env",
        default="STELLA_OLD_API_KEY",
        help="Env var holding the API token (default: STELLA_OLD_API_KEY).",
    )
    parser.add_argument(
        "--source-label",
        default="stella-old",
        help="_meta.source_site label written into each fixture.",
    )
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "ecommerce"),
    )
    parser.add_argument(
        "--skip-mutations",
        action="store_true",
        help="Skip create/update/delete/duplicate captures (read-only run).",
    )
    args = parser.parse_args()

    token = os.environ.get(args.token_env)
    if not token:
        sys.stderr.write(f"error: env {args.token_env} is not set\n")
        return 2
    out_dir = Path(args.out)
    base = f"https://{args.host}/admin/api/ecommerce/v1"
    src = args.source_label
    host = args.host

    def save(name, method, endpoint, payload, gotchas):
        _save_fixture(
            out_dir, name, method, endpoint, payload, gotchas,
            source_site=src, voog_host=host,
        )

    # --- READS ---
    print("Capturing read endpoints...", file=sys.stderr)
    cats = _http_request("GET", f"{base}/categories?per_page=10", token)
    save("categories_list", "GET", "/categories", cats, [])
    if cats:
        first_id = cats[0]["id"]
        save(
            "category_get", "GET", f"/categories/{first_id}",
            _http_request("GET", f"{base}/categories/{first_id}", token), [],
        )
    orders = _http_request("GET", f"{base}/orders?per_page=5", token)
    save("orders_list", "GET", "/orders", orders,
         ["sanitised: customer email/name/address replaced"])
    if orders:
        order_id = orders[0]["id"]
        save(
            "order_get", "GET", f"/orders/{order_id}",
            _http_request("GET", f"{base}/orders/{order_id}", token),
            ["sanitised: customer email/name/address replaced"],
        )
    discounts = _http_request("GET", f"{base}/discounts?per_page=10", token)
    save("discounts_list", "GET", "/discounts", discounts, [])
    if discounts:
        did = discounts[0]["id"]
        save(
            "discount_get", "GET", f"/discounts/{did}",
            _http_request("GET", f"{base}/discounts/{did}", token), [],
        )
    rules = _http_request("GET", f"{base}/cart_rules?per_page=10", token)
    save("cart_rules_list", "GET", "/cart_rules", rules, [])
    if rules:
        rid = rules[0]["id"]
        save(
            "cart_rule_get", "GET", f"/cart_rules/{rid}",
            _http_request("GET", f"{base}/cart_rules/{rid}", token), [],
        )
    save(
        "shipping_methods_list", "GET", "/shipping_methods",
        _http_request("GET", f"{base}/shipping_methods", token), [],
    )
    save(
        "gateways_list", "GET", "/gateways",
        _http_request("GET", f"{base}/gateways", token), [],
    )

    if args.skip_mutations:
        print("Skipping mutations (--skip-mutations).", file=sys.stderr)
        return 0

    # --- WRITES (touch a throwaway product/category) ---
    # Every mutation that creates a resource is wrapped in try/finally so
    # cleanup DELETE runs even if a subsequent operation in the same block
    # raises.
    print("Capturing mutation endpoints (creates throwaway resources)...", file=sys.stderr)

    # Create a throwaway category to capture create + update envelopes,
    # then guaranteed-delete it.
    created_cat = None
    try:
        cat_create_body = {"category": {"name": "fixture-throwaway-cat"}}
        created_cat = _http_request(
            "POST", f"{base}/categories", token, cat_create_body
        )
        save("category_create", "POST", "/categories", created_cat, [])

        cat_update_body = {"category": {"name": "fixture-throwaway-cat-renamed"}}
        updated_cat = _http_request(
            "PUT", f"{base}/categories/{created_cat['id']}", token, cat_update_body
        )
        save("category_update", "PUT", "/categories/{id}", updated_cat, [])
    finally:
        if created_cat and isinstance(created_cat, dict) and "id" in created_cat:
            try:
                _http_request(
                    "DELETE", f"{base}/categories/{created_cat['id']}", token
                )
                print(f"  cleanup: deleted category {created_cat['id']}",
                      file=sys.stderr)
            except Exception as cleanup_err:  # noqa: BLE001
                sys.stderr.write(
                    f"warn: failed to clean up throwaway category "
                    f"{created_cat['id']}: {cleanup_err}\n"
                )

    # Product duplicate - pick the first product, duplicate, capture, delete duplicate.
    products = _http_request("GET", f"{base}/products?per_page=1", token)
    if products:
        prod_id = products[0]["id"]
        dup = None
        try:
            dup = _http_request(
                "POST", f"{base}/products/{prod_id}/duplicate", token, {}
            )
            save(
                "products_duplicate",
                "POST",
                "/products/{id}/duplicate",
                dup,
                ["duplicate inherits status=draft per Voog docs"],
            )
            # Stash DELETE response shape (Voog returns 204 - payload is None).
            save(
                "products_delete",
                "DELETE",
                "/products/{id}",
                None,
                ["204 No Content - handler must tolerate None body"],
            )
        finally:
            if dup and isinstance(dup, dict) and "id" in dup:
                try:
                    _http_request("DELETE", f"{base}/products/{dup['id']}", token)
                    print(f"  cleanup: deleted duplicated product {dup['id']}",
                          file=sys.stderr)
                except Exception as cleanup_err:  # noqa: BLE001
                    sys.stderr.write(
                        f"warn: failed to clean up duplicated product "
                        f"{dup['id']}: {cleanup_err}\n"
                    )

    # Bulk update - the actual Voog API uses {actions, target_ids} shape, NOT
    # the per-row {products: [{id, ...}]} shape the plan originally assumed.
    # Discovered at fixture-capture time 2026-05-27.
    drafts = _http_request(
        "GET", f"{base}/products?q.product.status.$eq=draft&per_page=1", token
    )
    if drafts:
        bulk_body = {
            "actions": [
                {"target_field": "status", "action": "set", "value": "draft"}
            ],
            "target_ids": [drafts[0]["id"]],
        }
        bulk_result = _http_request("PUT", f"{base}/products", token, bulk_body)
        save(
            "products_bulk_update",
            "PUT",
            "/products",
            bulk_result,
            [
                "Voog bulk shape is {actions: [...], target_ids: [...] | 'all'},"
                " NOT {products: [{id, ...}]} per-row.",
                "Response: {counters: {processed, failed}, processed_ids: [...],"
                " failed_ids: [...]}.",
                "status=set draft on a draft is a no-op write.",
            ],
        )
    else:
        sys.stderr.write(
            "warn: no draft product available - products_bulk_update.json must be "
            "captured manually or via a temporary draft.\n"
        )

    # --- Empirical batch-size probe ---
    # No documented cap on target_ids length. Probe 251 and 1001 to find the
    # real ceiling; duplicates collapse so this is harmless.
    if drafts:
        probe_id = drafts[0]["id"]
        for n in (251, 1001):
            probe_body = {
                "actions": [
                    {"target_field": "status", "action": "set", "value": "draft"}
                ],
                "target_ids": [probe_id] * n,
            }
            print(
                f"Probing bulk-update cap: PUT {n} target_ids...",
                file=sys.stderr,
            )
            try:
                probe_result = _http_request(
                    "PUT", f"{base}/products", token, probe_body
                )
                save(
                    f"products_bulk_update_probe_{n}",
                    "PUT",
                    "/products",
                    probe_result,
                    [
                        f"PROBE: {n} target_ids accepted (HTTP 200). Duplicate"
                        f" ids collapsed by server (processed=1 expected).",
                    ],
                )
                print(
                    f"  probe: {n} target_ids accepted",
                    file=sys.stderr,
                )
            except urllib.error.HTTPError as e:
                try:
                    err_body = json.loads(e.read())
                except Exception:  # noqa: BLE001
                    err_body = {"raw_error": "non-JSON body", "status": e.code}
                save(
                    f"products_bulk_update_probe_{n}",
                    "PUT",
                    "/products",
                    err_body,
                    [
                        f"PROBE: {n} target_ids rejected with HTTP {e.code}",
                    ],
                )
                print(
                    f"  probe: {n} target_ids rejected HTTP {e.code}",
                    file=sys.stderr,
                )
                break  # found the ceiling; no point probing higher

    print("Done.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
