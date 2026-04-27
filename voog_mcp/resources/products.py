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
exact-or-slashed-sub-path :func:`matches`, strict :func:`_parse_id`, errors
propagate to the server layer (no wrapping into MCP error responses).
"""
import json

from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import Resource

from voog_mcp.client import VoogClient


URI_PREFIX = "voog://products"

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


def matches(uri: str) -> bool:
    return uri == URI_PREFIX or uri.startswith(URI_PREFIX + "/")


async def read_resource(uri: str, client: VoogClient) -> list[ReadResourceContents]:
    if uri == URI_PREFIX:
        products = client.get_all(
            "/products",
            base=client.ecommerce_url,
            params={"include": LIST_INCLUDE},
        )
        return _json_response(_simplify_products(products))

    if not uri.startswith(URI_PREFIX + "/"):
        raise ValueError(f"products resource: unsupported URI {uri!r}")

    sub = uri[len(URI_PREFIX) + 1:]
    parts = sub.split("/")

    if len(parts) == 1:
        product_id = _parse_id(parts[0], uri)
        product = client.get(
            f"/products/{product_id}",
            base=client.ecommerce_url,
            params={"include": DETAIL_INCLUDE},
        )
        return _json_response(product)

    raise ValueError(f"products resource: unsupported URI {uri!r}")


def _parse_id(raw: str, uri: str) -> int:
    try:
        product_id = int(raw)
    except ValueError as e:
        raise ValueError(f"products resource: invalid product id in {uri!r}") from e
    if product_id <= 0:
        raise ValueError(f"products resource: product id must be positive in {uri!r}") from None
    return product_id


def _json_response(data) -> list[ReadResourceContents]:
    return [
        ReadResourceContents(
            content=json.dumps(data, indent=2, ensure_ascii=False),
            mime_type="application/json",
        )
    ]


def _simplify_products(products: list) -> list:
    """Project products list to a curated subset.

    Keeps small ecommerce-relevant fields (status flags, prices) plus the
    translations object (small, useful for multilingual list views, matches
    the existing voog.py products_list CLI shape). Larger fields like
    ``description``, ``physical_properties``, and ``asset_ids`` are stripped
    — clients fetching ``voog://products/{id}`` get the full detail.
    """
    simplified = []
    for product in products:
        simplified.append({
            "id": product.get("id"),
            "name": product.get("name"),
            "slug": product.get("slug"),
            "sku": product.get("sku"),
            "status": product.get("status"),
            "in_stock": product.get("in_stock"),
            "on_sale": product.get("on_sale"),
            "price": product.get("price"),
            "effective_price": product.get("effective_price"),
            "translations": product.get("translations"),
            "updated_at": product.get("updated_at"),
        })
    return simplified
