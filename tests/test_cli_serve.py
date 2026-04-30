"""Tests for voog.cli.commands.serve — local proxy + asset auto-discovery.

The serve command spins up an HTTPServer; tests verify the handler
construction (asset pattern + per-asset path mapping) without actually
opening sockets. The proxy and local-asset paths are exercised through
the handler's `do_GET` method via fake request/response objects.
"""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from voog.cli.commands import serve as serve_cmd


class TestAssetPattern(unittest.TestCase):
    def test_empty_assets_returns_none(self):
        self.assertIsNone(serve_cmd._build_asset_pattern({}))

    def test_pattern_matches_versioned_src_and_href(self):
        pattern = serve_cmd._build_asset_pattern({"main.css": "stylesheets/main.css"})
        # src= and href= variants both match
        self.assertIsNotNone(pattern.search('<link href="https://cdn/main.css?v=123">'))
        self.assertIsNotNone(pattern.search('<script src="https://cdn/main.css?v=abc"></script>'))

    def test_pattern_does_not_match_unknown_asset(self):
        pattern = serve_cmd._build_asset_pattern({"main.css": "stylesheets/main.css"})
        self.assertIsNone(pattern.search('<link href="https://cdn/other.css?v=1">'))

    def test_pattern_does_not_match_unversioned_url(self):
        # Pattern requires ?v= — production HTML always has it. This
        # avoids accidentally rewriting URLs without cache busting.
        pattern = serve_cmd._build_asset_pattern({"main.css": "stylesheets/main.css"})
        self.assertIsNone(pattern.search('<link href="https://cdn/main.css">'))


class TestServeLocalPath(unittest.TestCase):
    """Verify the path-traversal guard on /_local/ requests via a real
    handler instance (the handler closures depend on local_dir)."""

    def _make_handler_class(self, local_dir: Path):
        return serve_cmd._build_handler(host="example.com", local_dir=local_dir, local_assets={})

    def _fake_request_for(self, path: str, handler_cls):
        # Build a handler instance without going through HTTPServer/socket.
        # We exercise _serve_local directly with an instance whose
        # send_error / send_response / send_header / wfile attrs are mocked.
        handler = handler_cls.__new__(handler_cls)
        handler.path = path
        handler.send_error = MagicMock()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = MagicMock()
        return handler

    def test_dotdot_path_traversal_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler_cls = self._make_handler_class(Path(tmp))
            handler = self._fake_request_for("/_local/../etc/passwd", handler_cls)
            handler._serve_local()
            handler.send_error.assert_called_once_with(403, "Forbidden")

    def test_missing_file_returns_404(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler_cls = self._make_handler_class(Path(tmp))
            handler = self._fake_request_for("/_local/missing.css", handler_cls)
            handler._serve_local()
            handler.send_error.assert_called_once()
            self.assertEqual(handler.send_error.call_args.args[0], 404)

    def test_existing_file_served_with_correct_mime(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "main.css").write_text("body { color: red; }", encoding="utf-8")
            handler_cls = self._make_handler_class(tmp_path)
            handler = self._fake_request_for("/_local/main.css", handler_cls)
            handler._serve_local()
            handler.send_response.assert_called_once_with(200)
            # Content-Type set to text/css
            ct_calls = [
                c for c in handler.send_header.call_args_list if c.args[0] == "Content-Type"
            ]
            self.assertEqual(len(ct_calls), 1)
            self.assertEqual(ct_calls[0].args[1], "text/css")


class TestServeStartup(unittest.TestCase):
    """Confirms run() prints what's expected and respects --port arg."""

    def test_run_prints_proxy_target_and_local_assets(self):
        client = MagicMock()
        client.host = "example.com"
        args = MagicMock()
        args.port = 9999

        # Patch HTTPServer to avoid binding a socket; assert .serve_forever()
        # raises KeyboardInterrupt to drop out of the loop cleanly.
        fake_httpd = MagicMock()
        fake_httpd.serve_forever.side_effect = KeyboardInterrupt
        with patch("voog.cli.commands.serve.HTTPServer", return_value=fake_httpd):
            with patch("voog.cli.commands.serve.discover_local_assets", return_value={}):
                with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    rc = serve_cmd.run(args, client)
        self.assertEqual(rc, 0)
        out = stdout.getvalue()
        self.assertIn("https://example.com", out)
        self.assertIn(":9999", out)


if __name__ == "__main__":
    unittest.main()
