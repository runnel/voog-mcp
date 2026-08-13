"""Rewrite source-site URLs so copied content points at the target.

Copied HTML and ``data`` hashes are full of absolute references to the site
they came from. Left alone, a cloned site renders correctly *while silently
serving every image from the original* — which looks like success, keeps
working until the source is taken down or its assets are purged, and makes
the clone useless as a standalone site. Two classes have to move:

  1. **Media CDN paths.** Voog serves assets from
     ``media.voog.com/0000/0053/4382/photos/…`` where the numeric triple is
     the site's own storage prefix. The target has a different one, so every
     embedded image URL has to be re-pointed at the copy that the asset
     phase uploaded.
  2. **Absolute site links.** ``https://www.kolmkoma.ee/tood`` on the clone
     sends the visitor back to the original. These become root-relative.

Both prefixes are DERIVED, never configured: the reference script hardcoded
``media.voog.com/0000/0047/4574`` and the source hostname, which is exactly
the part that cannot be reused for a second pair of sites.
"""

from __future__ import annotations

import json
import re

# `media.voog.com/0000/0047/4574` — the numeric triple is the per-site
# storage prefix. Captured from any asset's public_url on either side.
_MEDIA_PREFIX_RE = re.compile(r"(media\.voog\.com/\d{4}/\d{4}/\d{4})")


def media_prefix_of(assets: list) -> str | None:
    """Extract the ``media.voog.com/NNNN/NNNN/NNNN`` prefix from an asset list.

    Returns None when no asset carries a recognisable public_url — a site
    with an empty media library, or one serving assets from somewhere this
    pattern does not describe. Callers must treat None as "do not rewrite
    media URLs" rather than substituting a guess.
    """
    for asset in assets or []:
        if not isinstance(asset, dict):
            continue
        match = _MEDIA_PREFIX_RE.search(asset.get("public_url") or "")
        if match:
            return match.group(1)
    return None


def _host_variants(host: str) -> list[str]:
    """Every spelling of a host that can appear in stored content.

    Content is authored over years and through several editors, so the same
    site shows up as ``https://www.example.ee/``, ``http://example.ee/`` and
    protocol-relative ``//example.ee/``. Rewriting only the canonical form
    leaves the others pointing home.
    """
    bare = (host or "").strip().lower().removeprefix("www.")
    if not bare:
        return []
    hosts = [f"www.{bare}", bare]
    variants: list[str] = []
    for candidate in hosts:
        variants.extend([f"https://{candidate}", f"http://{candidate}", f"//{candidate}"])
    return variants


class UrlRewriter:
    """Rewrites one site's URLs into another's. Immutable once built."""

    def __init__(
        self,
        *,
        source_hosts: list[str],
        source_media_prefix: str | None,
        target_media_prefix: str | None,
    ):
        self.source_media_prefix = source_media_prefix
        self.target_media_prefix = target_media_prefix
        # Longest first: `https://www.x.ee` must be consumed before the
        # `//www.x.ee` that is a substring of it, or the leftover `https:`
        # would be stranded in front of a now-relative path.
        seen: set[str] = set()
        self._host_prefixes: list[str] = []
        for host in source_hosts:
            for variant in _host_variants(host):
                if variant not in seen:
                    seen.add(variant)
                    self._host_prefixes.append(variant)
        self._host_prefixes.sort(key=len, reverse=True)

    @property
    def rewrites_media(self) -> bool:
        return bool(
            self.source_media_prefix
            and self.target_media_prefix
            and self.source_media_prefix != self.target_media_prefix
        )

    def text(self, body: str | None) -> str | None:
        """Rewrite a body of HTML (or any string carrying URLs)."""
        if not body:
            return body
        out = body
        if self.rewrites_media:
            out = out.replace(self.source_media_prefix, self.target_media_prefix)
        for prefix in self._host_prefixes:
            # Trailing slash first so `https://x.ee/tood` becomes `/tood`
            # rather than `//tood`, which a browser reads as a protocol-
            # relative URL pointing at the host `tood`.
            out = out.replace(prefix + "/", "/")
            out = out.replace(prefix, "/")
        return out

    def data(self, data: dict | None) -> dict | None:
        """Rewrite URL-ish strings anywhere inside a page/article/site data hash.

        Serialise-rewrite-parse rather than walking the structure: `data`
        holds arbitrary operator-defined nesting (design-editor colour maps,
        photo arrays, per-language blobs) and a hand-written walker would
        miss whichever shape nobody thought of. `json.dumps` escapes `/` as
        `/` and quotes as `\\"`, so no substitution can produce a string
        that fails to parse back.
        """
        if not data:
            return data
        return json.loads(self.text(json.dumps(data, ensure_ascii=False)))
