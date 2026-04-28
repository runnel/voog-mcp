"""voog serve — local proxy that swaps known assets with cwd files."""
from __future__ import annotations

import re
import ssl
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from voog.api.serve import discover_local_assets
from voog.client import VoogClient


def add_arguments(subparsers):
    p = subparsers.add_parser(
        "serve", help="Local proxy server (asset auto-discovery from javascripts/, stylesheets/)"
    )
    p.add_argument("--port", type=int, default=8080)
    p.set_defaults(func=run)


def run(args, client: VoogClient) -> int:
    local_dir = Path.cwd()
    local_assets = discover_local_assets(local_dir)
    print(f"Proxying https://{client.host} on http://localhost:{args.port}")
    print(f"Discovered {len(local_assets)} local assets:")
    for name in sorted(local_assets):
        print(f"  /_local/{local_assets[name]}")
    if not local_assets:
        print("  (none — create files under javascripts/ or stylesheets/)")

    handler_cls = _build_handler(client.host, local_dir, local_assets)
    httpd = HTTPServer(("localhost", args.port), handler_cls)
    print(f"\nReady. Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def _build_handler(host: str, local_dir: Path, local_assets: dict[str, str]):
    """Return a BaseHTTPRequestHandler subclass closed over (host, local_dir, local_assets)."""
    asset_pattern = _build_asset_pattern(local_assets)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # quiet by default

        def do_GET(self):
            # Local asset path: /_local/<rel_path>
            if self.path.startswith("/_local/"):
                self._serve_local()
                return
            self._proxy_to_voog()

        def _proxy_to_voog(self):
            url = f"https://{host}{self.path}"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "voog-mcp serve"})
                with urllib.request.urlopen(req, context=ssl.create_default_context()) as resp:
                    ct = resp.headers.get("Content-Type", "application/octet-stream")
                    body = resp.read()
                    status = resp.status
            except urllib.error.HTTPError as e:
                ct = e.headers.get("Content-Type", "text/plain") if e.headers else "text/plain"
                body = e.read() if hasattr(e, "read") else b""
                status = e.code
            if "text/html" in ct and asset_pattern:
                body = asset_pattern.sub(
                    lambda m: f'src="/_local/{local_assets[m.group(1)]}"'
                    if m.group(0).startswith("src=")
                    else f'href="/_local/{local_assets[m.group(1)]}"',
                    body.decode("utf-8", errors="replace"),
                ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_local(self):
            rel_path = self.path[len("/_local/"):]
            if ".." in rel_path:
                self.send_error(403, "Forbidden")
                return
            filepath = local_dir / rel_path
            if not filepath.exists() or not filepath.is_file():
                self.send_error(404, f"Not found: {rel_path}")
                return
            ext = filepath.suffix.lower()
            mime = {
                ".js": "application/javascript",
                ".css": "text/css",
                ".html": "text/html",
                ".json": "application/json",
                ".svg": "image/svg+xml",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".woff2": "font/woff2",
                ".woff": "font/woff",
            }.get(ext, "application/octet-stream")
            data = filepath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def _build_asset_pattern(local_assets: dict[str, str]):
    if not local_assets:
        return None
    names = "|".join(re.escape(n) for n in local_assets)
    return re.compile(rf'(?:src|href)="[^"]*/({names})\?v=[^"]*"')
