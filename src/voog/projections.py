"""Shared simplify_* projections used by both tools and resources surfaces.

Both ``voog.mcp.tools.<group>`` and ``voog.mcp.resources.<group>`` expose list
views of the same Voog API entities. Each surface promises the *same shape* —
"caller can't tell which surface produced the data", which keeps Claude's
mental model consistent. Living the projection in one place stops the two
copies from drifting apart silently when one is updated and the other is not.

Same rationale for the include-string constants: tools and resources must
fetch the same superset, otherwise the simplified shapes silently diverge.

Pure stdlib, no MCP / client dependencies — easy to import from either side
without circulars.
"""

# Voog ?include= string for products list views (translations only — list
# views skip variant_types to keep payloads light).
PRODUCTS_LIST_INCLUDE = "translations"

# Voog ?include= for product detail views (full enrichment).
# `variants` is required for per-variant stock — without it Voog returns
# only `variant_types` definitions, not the per-variant `stock` /
# `reserved_quantity` / `variant_attributes_text` fields needed to answer
# "what's the stock on this 9-variant tote" (issue #104).
PRODUCTS_DETAIL_INCLUDE = "variants,variant_types,translations"


def simplify_pages(pages: list) -> list:
    """Project pages to the curated list shape (matches voog.py pages_pull)."""
    simplified = []
    for p in pages:
        lang = p.get("language") or {}
        layout = p.get("layout") or {}
        simplified.append(
            {
                "id": p.get("id"),
                "path": p.get("path"),
                "title": p.get("title"),
                "hidden": p.get("hidden"),
                "layout_id": p.get("layout_id") or layout.get("id"),
                "layout_name": p.get("layout_name") or p.get("layout_title") or layout.get("title"),
                "content_type": p.get("content_type"),
                "parent_id": p.get("parent_id"),
                "language_code": lang.get("code"),
                "public_url": p.get("public_url"),
            }
        )
    return simplified


def simplify_products(products: list) -> list:
    """Project products to a curated subset.

    Keeps small ecommerce-relevant fields (status flags, prices, stock
    summary) plus the translations object (small, useful for multilingual
    list views, matches the existing voog.py products_list CLI shape).
    Larger fields like ``description``, ``physical_properties``, and
    ``asset_ids`` are stripped — clients fetching ``voog://products/{id}``
    get the full detail.

    Stock-summary fields (``stock``, ``reserved_quantity``, ``uses_variants``,
    ``variants_count``) and ``created_at`` were added per issue #104 so
    inventory and "added this week" questions can be answered from the
    list view alone, without an extra detail fetch per product.
    """
    simplified = []
    for product in products:
        simplified.append(
            {
                "id": product.get("id"),
                "name": product.get("name"),
                "slug": product.get("slug"),
                "sku": product.get("sku"),
                "status": product.get("status"),
                "in_stock": product.get("in_stock"),
                "on_sale": product.get("on_sale"),
                "price": product.get("price"),
                "effective_price": product.get("effective_price"),
                "stock": product.get("stock"),
                "reserved_quantity": product.get("reserved_quantity"),
                "uses_variants": product.get("uses_variants"),
                "variants_count": product.get("variants_count"),
                "translations": product.get("translations"),
                "created_at": product.get("created_at"),
                "updated_at": product.get("updated_at"),
            }
        )
    return simplified


def simplify_articles(articles: list) -> list:
    """Project articles list to lightweight metadata (no body field).

    The Voog ``/articles`` list endpoint already omits ``body`` from each item,
    but defensive trimming guarantees the projection even if the API ever
    starts returning bodies (which would balloon list payloads).
    """
    simplified = []
    for article in articles:
        lang = article.get("language") or {}
        page = article.get("page") or {}
        simplified.append(
            {
                "id": article.get("id"),
                "title": article.get("title"),
                "path": article.get("path"),
                "public_url": article.get("public_url"),
                "published": article.get("published"),
                "published_at": article.get("published_at"),
                "updated_at": article.get("updated_at"),
                "created_at": article.get("created_at"),
                "language_code": lang.get("code"),
                "page_id": page.get("id"),
            }
        )
    return simplified


def simplify_layouts(layouts: list) -> list:
    """Project layouts list to lightweight metadata (no body field).

    The Voog ``/layouts`` list endpoint already omits ``body`` from each item,
    but defensive trimming here guarantees the projection even if the API
    starts returning it.
    """
    simplified = []
    for layout in layouts:
        simplified.append(
            {
                "id": layout.get("id"),
                "title": layout.get("title"),
                "component": layout.get("component"),
                "content_type": layout.get("content_type"),
                "updated_at": layout.get("updated_at"),
            }
        )
    return simplified


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
