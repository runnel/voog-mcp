"""Tests for voog.cli.commands.pages — list/get/create/delete/set-hidden/set-layout/pages-pull.

Pages CLI has many sub-commands; tests focus on the dispatch surface
(argparse → cmd_*), payload shape going to the API client, and exit
codes. The pages-projection logic is tested upstream in
``tests/test_projections.py``.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from voog.cli.commands import pages as pages_cmd


def _make_client():
    return MagicMock()


def _args(**kwargs):
    args = MagicMock()
    for k, v in kwargs.items():
        setattr(args, k, v)
    return args


class TestPagesList(unittest.TestCase):
    def test_pages_list_calls_get_all_pages(self):
        client = _make_client()
        client.get_all.return_value = [
            {"id": 1, "path": "foo", "title": "Foo", "hidden": False, "layout_name": "Default"},
        ]
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            rc = pages_cmd.cmd_pages(_args(), client)
        self.assertEqual(rc, 0)
        client.get_all.assert_called_once_with("/pages")
        self.assertIn("Foo", stdout.getvalue())

    def test_pages_list_empty(self):
        client = _make_client()
        client.get_all.return_value = []
        with patch("sys.stdout", new_callable=io.StringIO):
            rc = pages_cmd.cmd_pages(_args(), client)
        self.assertEqual(rc, 0)


class TestPageGet(unittest.TestCase):
    def test_page_get_calls_get_with_id(self):
        client = _make_client()
        client.get.return_value = {
            "id": 42,
            "title": "About",
            "path": "about",
            "hidden": False,
            "layout_id": 7,
            "language": {"code": "en", "id": 1},
        }
        with patch("sys.stdout", new_callable=io.StringIO):
            rc = pages_cmd.cmd_page(_args(page_id=42), client)
        self.assertEqual(rc, 0)
        client.get.assert_called_once_with("/pages/42")


class TestPageCreate(unittest.TestCase):
    def test_create_minimal_payload(self):
        client = _make_client()
        client.post.return_value = {"id": 100, "path": "foo"}
        with patch("sys.stdout", new_callable=io.StringIO):
            rc = pages_cmd.cmd_page_create(
                _args(
                    title="Foo",
                    slug="foo",
                    language_id=1,
                    layout_id=None,
                    parent_id=None,
                    hidden=False,
                ),
                client,
            )
        self.assertEqual(rc, 0)
        client.post.assert_called_once_with(
            "/pages", {"title": "Foo", "slug": "foo", "language_id": 1}
        )

    def test_create_full_payload(self):
        client = _make_client()
        client.post.return_value = {"id": 100, "path": "blog/foo"}
        with patch("sys.stdout", new_callable=io.StringIO):
            pages_cmd.cmd_page_create(
                _args(
                    title="Foo",
                    slug="foo",
                    language_id=1,
                    layout_id=7,
                    parent_id=10,
                    hidden=True,
                ),
                client,
            )
        client.post.assert_called_once_with(
            "/pages",
            {
                "title": "Foo",
                "slug": "foo",
                "language_id": 1,
                "layout_id": 7,
                "parent_id": 10,
                "hidden": True,
            },
        )

    def test_create_missing_id_in_response_returns_error(self):
        client = _make_client()
        client.post.return_value = {}  # no id
        with patch("sys.stderr", new_callable=io.StringIO) as stderr:
            with patch("sys.stdout", new_callable=io.StringIO):
                rc = pages_cmd.cmd_page_create(
                    _args(
                        title="Foo",
                        slug="foo",
                        language_id=1,
                        layout_id=None,
                        parent_id=None,
                        hidden=False,
                    ),
                    client,
                )
        self.assertEqual(rc, 1)
        self.assertIn("missing id", stderr.getvalue())


class TestPageAddContent(unittest.TestCase):
    def test_add_content_default_name_and_type(self):
        client = _make_client()
        client.post.return_value = {"id": 5, "text": {"id": 9}}
        with patch("sys.stdout", new_callable=io.StringIO):
            rc = pages_cmd.cmd_page_add_content(
                _args(page_id=42, name="body", content_type="text"),
                client,
            )
        self.assertEqual(rc, 0)
        client.post.assert_called_once_with(
            "/pages/42/contents", {"name": "body", "content_type": "text"}
        )

    def test_add_content_named_section(self):
        client = _make_client()
        client.post.return_value = {"id": 5, "text": {"id": 9}}
        with patch("sys.stdout", new_callable=io.StringIO):
            pages_cmd.cmd_page_add_content(
                _args(page_id=42, name="gallery_1", content_type="text"),
                client,
            )
        client.post.assert_called_once_with(
            "/pages/42/contents", {"name": "gallery_1", "content_type": "text"}
        )


class TestPageDelete(unittest.TestCase):
    def test_force_skips_confirmation(self):
        client = _make_client()
        with patch("sys.stdout", new_callable=io.StringIO):
            rc = pages_cmd.cmd_page_delete(_args(page_id=42, force=True), client)
        self.assertEqual(rc, 0)
        client.delete.assert_called_once_with("/pages/42")

    def test_no_force_with_y_confirmation_proceeds(self):
        client = _make_client()
        client.get.return_value = {"id": 42, "title": "Foo", "path": "foo"}
        with patch("builtins.input", return_value="y"):
            with patch("sys.stdout", new_callable=io.StringIO):
                rc = pages_cmd.cmd_page_delete(_args(page_id=42, force=False), client)
        self.assertEqual(rc, 0)
        client.delete.assert_called_once()

    def test_no_force_aborts_when_user_says_n(self):
        client = _make_client()
        client.get.return_value = {"id": 42, "title": "Foo", "path": "foo"}
        with patch("builtins.input", return_value="n"):
            with patch("sys.stdout", new_callable=io.StringIO):
                rc = pages_cmd.cmd_page_delete(_args(page_id=42, force=False), client)
        self.assertEqual(rc, 0)
        client.delete.assert_not_called()

    def test_delete_failure_returns_one_and_prints_to_stderr(self):
        client = _make_client()
        client.delete.side_effect = RuntimeError("API down")
        with patch("sys.stderr", new_callable=io.StringIO) as stderr:
            with patch("sys.stdout", new_callable=io.StringIO):
                rc = pages_cmd.cmd_page_delete(_args(page_id=42, force=True), client)
        self.assertEqual(rc, 1)
        self.assertIn("API down", stderr.getvalue())


class TestPageSetHidden(unittest.TestCase):
    def test_hidden_true_sends_hidden_true_for_each_page(self):
        client = _make_client()
        with patch("sys.stdout", new_callable=io.StringIO):
            rc = pages_cmd.cmd_page_set_hidden(
                _args(page_ids=["1", "2", "3"], hidden="true"),
                client,
            )
        self.assertEqual(rc, 0)
        self.assertEqual(client.put.call_count, 3)
        for call in client.put.call_args_list:
            self.assertEqual(call.args[1], {"hidden": True})

    def test_hidden_false_sends_hidden_false(self):
        client = _make_client()
        with patch("sys.stdout", new_callable=io.StringIO):
            pages_cmd.cmd_page_set_hidden(_args(page_ids=["1"], hidden="false"), client)
        client.put.assert_called_once_with("/pages/1", {"hidden": False})

    def test_partial_failure_returns_1_and_continues(self):
        client = _make_client()
        client.put.side_effect = [None, RuntimeError("boom"), None]
        with patch("sys.stderr", new_callable=io.StringIO):
            with patch("sys.stdout", new_callable=io.StringIO):
                rc = pages_cmd.cmd_page_set_hidden(
                    _args(page_ids=["1", "2", "3"], hidden="true"),
                    client,
                )
        # Failure on page 2 doesn't abort — page 3 still gets processed.
        self.assertEqual(client.put.call_count, 3)
        self.assertEqual(rc, 1)


class TestPageSetLayout(unittest.TestCase):
    def test_set_layout_sends_layout_id(self):
        client = _make_client()
        with patch("sys.stdout", new_callable=io.StringIO):
            rc = pages_cmd.cmd_page_set_layout(_args(page_id=42, layout_id=7), client)
        self.assertEqual(rc, 0)
        client.put.assert_called_once_with("/pages/42", {"layout_id": 7})


class TestPagesPull(unittest.TestCase):
    def test_pages_pull_writes_simplified_json(self):
        client = _make_client()
        client.get_all.return_value = [
            {
                "id": 1,
                "path": "foo",
                "title": "Foo",
                "hidden": False,
                "layout_id": 7,
                "layout_name": "Default",
                "content_type": "page",
                "parent_id": None,
                "language": {"code": "en"},
                "public_url": "https://example.com/foo",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp)
                with patch("sys.stdout", new_callable=io.StringIO):
                    rc = pages_cmd.cmd_pages_pull(_args(), client)
            finally:
                os.chdir(cwd_before)

            data = json.loads((Path(tmp) / "pages.json").read_text())
        self.assertEqual(rc, 0)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], 1)
        self.assertEqual(data[0]["language_code"], "en")


if __name__ == "__main__":
    unittest.main()
