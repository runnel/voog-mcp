"""MCP resources for Voog ecommerce products.

Phase D resource group covering two URI shapes:

  - ``voog://products``        — list all products (simplified: id, name, slug,
                                  sku, status, in_stock, on_sale, price,
                                  effective_price, translations, updated_at)
  - ``voog://products/{id}``   — full product details (with variant_types,
                                  translations, assets, etc.)

Unlike pages/layouts/articles/redirects which use the Admin API base, products
live on the ``ecommerce/v1`` API; ``client.ecommerce_url`` is passed as ``base``.

Per spec § 5: list view uses ``?include=translations`` (matches the existing
``products_list`` CLI shape), single product view uses
``?include=variant_types,translations`` (variants + i18n metadata for the
detail view).

Pattern mirrors :mod:`voog_mcp.resources.pages`: ``URI_PREFIX`` constant,
exact-or-slashed-sub-path :func:`matches`, strict :func:`parse_id` (shared),
errors propagate to the server layer (no wrapping into MCP error responses).

The list view's curated shape comes from :func:`voog_mcp.projections.simplify_products`,
shared with :mod:`voog_mcp.tools.products` so the ``products_list`` tool and
the ``voog://products`` resource can't drift out of sync.
"""
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import Resource

from voog_mcp.client import VoogClient
from voog_mcp.projections import simplify_products
from voog_mcp.resources._helpers import json_response, parse_id, prefix_matcher


URI_PREFIX = "voog://products"
matches = prefix_matcher(URI_PREFIX)

LIST_INCLUDE = "translations"
DETAIL_INCLUDE = "variant_types,translations"


def get_resources() -> list[Resource]:
    return [
        Resource(
            uri=URI_PREFIX,
            name="Products",
            description=(
                "All ecommerce products on the Voog site (simplified: id, name, slug, "
                "sku, status, in_stock, on_sale, price, effective_price, translations, "
                "updated_at). Full product details (with variant_types) at "
                "voog://products/{id}."
            ),
            mimeType="application/json",
        ),
    ]


async def read_resource(uri: str, client: VoogClient) -> list[ReadResourceContents]:
    if uri == URI_PREFIX:
        products = client.get_all(
            "/products",
            base=client.ecommerce_url,
            params={"include": LIST_INCLUDE},
        )
        return json_response(simplify_products(products))

    if not uri.startswith(URI_PREFIX + "/"):
        raise ValueError(f"products resource: unsupported URI {uri!r}")

    sub = uri[len(URI_PREFIX) + 1:]
    parts = sub.split("/")

    if len(parts) == 1:
        product_id = parse_id(parts[0], uri, group_name="products")
        product = client.get(
            f"/products/{product_id}",
            base=client.ecommerce_url,
            params={"include": DETAIL_INCLUDE},
        )
        return json_response(product)

    raise ValueError(f"products resource: unsupported URI {uri!r}")
