"""MCP resources for Voog ecommerce products.

Phase D resource group covering two URI shapes:

  - ``voog://{site}/products``        — list all products (simplified: id, name, slug,
                                         sku, status, in_stock, on_sale, price,
                                         effective_price, translations, updated_at)
  - ``voog://{site}/products/{id}``   — full product details (with variant_types,
                                         translations, assets, etc.)

Unlike pages/layouts/articles/redirects which use the Admin API base, products
live on the ``ecommerce/v1`` API; ``client.ecommerce_url`` is passed as ``base``.

Per spec § 5: list view uses ``?include=translations`` (matches the existing
``products_list`` CLI shape), single product view uses
``?include=variant_types,translations`` (variants + i18n metadata for the
detail view).

Pattern mirrors :mod:`voog_mcp.resources.pages`: ``URI_TEMPLATE`` constant,
site-namespaced :func:`matches`, strict :func:`parse_id` (shared),
errors propagate to the server layer (no wrapping into MCP error responses).

The list view's curated shape comes from :func:`voog_mcp.projections.simplify_products`,
shared with :mod:`voog_mcp.tools.products` so the ``products_list`` tool and
the ``voog://{site}/products`` resource can't drift out of sync.
"""

import re

from mcp.types import Resource

from voog.client import VoogClient
from voog.mcp.resources._helpers import (
    ReadResourceContents,
    json_response,
    parse_id,
)
from voog.projections import (
    PRODUCTS_DETAIL_INCLUDE,
    PRODUCTS_LIST_INCLUDE,
    simplify_products,
)

URI_TEMPLATE = "voog://{site}/products"
_URI_RE = re.compile(r"^voog://[^/]+/products(/.*)?$")


def get_uri_patterns() -> list[str]:
    """URI patterns claimed by this group — read by the startup collision guard."""
    return [URI_TEMPLATE]


def matches(uri: str) -> bool:
    return bool(_URI_RE.match(uri))


def _strip_site(uri: str) -> str:
    """voog://stella/products/42 → /products/42"""
    rest = uri[len("voog://") :]
    _, _, path = rest.partition("/")
    return "/" + path


def get_resources() -> list[Resource]:
    return [
        Resource(
            uri=URI_TEMPLATE,
            name="Products",
            description=(
                "All ecommerce products on the Voog site (simplified: id, name, slug, "
                "sku, status, in_stock, on_sale, price, effective_price, translations, "
                "updated_at). Full product details (with variant_types) at "
                "voog://{site}/products/{id}."
            ),
            mimeType="application/json",
        ),
    ]


def read_resource(uri: str, client: VoogClient) -> list[ReadResourceContents]:
    local = _strip_site(uri)  # e.g. /products or /products/42

    if local == "/products":
        products = client.get_all(
            "/products",
            base=client.ecommerce_url,
            params={"include": PRODUCTS_LIST_INCLUDE},
        )
        return json_response(simplify_products(products))

    if not local.startswith("/products/"):
        raise ValueError(f"products resource: unsupported URI {uri!r}")

    sub = local[len("/products/") :]
    parts = sub.split("/")

    if len(parts) == 1:
        product_id = parse_id(parts[0], uri, group_name="products")
        product = client.get(
            f"/products/{product_id}",
            base=client.ecommerce_url,
            params={"include": PRODUCTS_DETAIL_INCLUDE},
        )
        return json_response(product)

    raise ValueError(f"products resource: unsupported URI {uri!r}")
