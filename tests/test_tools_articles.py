"""Tests for voog.mcp.tools.articles — blog article CRUD."""

import json
import unittest
from unittest.mock import MagicMock

from voog.mcp.tools import articles as articles_tools


class TestGetTools(unittest.TestCase):
    def test_six_tools_registered(self):
        names = sorted(t.name for t in articles_tools.get_tools())
        self.assertEqual(
            names,
            [
                "article_create",
                "article_delete",
                "article_get",
                "article_publish",
                "article_update",
                "articles_list",
            ],
        )

    def test_read_tools_annotations(self):
        tools = {t.name: t for t in articles_tools.get_tools()}
        for name in ("articles_list", "article_get"):
            ann = tools[name].annotations
            self.assertIs(ann.readOnlyHint, True)
            self.assertIs(ann.destructiveHint, False)
            self.assertIs(ann.idempotentHint, True)

    def test_delete_annotations(self):
        tools = {t.name: t for t in articles_tools.get_tools()}
        ann = tools["article_delete"].annotations
        self.assertIs(ann.readOnlyHint, False)
        self.assertIs(ann.destructiveHint, True)
        self.assertIs(ann.idempotentHint, False)


class TestArticlesList(unittest.TestCase):
    def test_list_returns_simplified(self):
        client = MagicMock()
        client.get_all.return_value = [
            {
                "id": 1,
                "title": "T1",
                "path": "blog/t1",
                "language": {"code": "et"},
                "page": {"id": 5},
            }
        ]
        result = articles_tools.call_tool("articles_list", {}, client)
        client.get_all.assert_called_once_with("/articles")
        items = json.loads(result[1].text)
        self.assertEqual(items[0]["id"], 1)
        self.assertEqual(items[0]["language_code"], "et")
        self.assertEqual(items[0]["page_id"], 5)


class TestArticleGet(unittest.TestCase):
    def test_get_returns_full_article(self):
        client = MagicMock()
        client.get.return_value = {"id": 7, "title": "X", "body": "<p>x</p>"}
        result = articles_tools.call_tool("article_get", {"article_id": 7}, client)
        client.get.assert_called_once_with("/articles/7")
        body = json.loads(result[0].text)
        self.assertEqual(body["id"], 7)


class TestArticleCreate(unittest.TestCase):
    def test_create_minimal(self):
        client = MagicMock()
        # Voog: create returns the new article without title set; the
        # follow-up PUT autosaved_title is what makes the title appear.
        client.post.return_value = {"id": 99}
        articles_tools.call_tool(
            "article_create",
            {"page_id": 5, "title": "New Post"},
            client,
        )
        client.post.assert_called_once()
        path, body = client.post.call_args.args
        self.assertEqual(path, "/articles")
        self.assertEqual(body["page_id"], 5)
        self.assertEqual(body["autosaved_title"], "New Post")
        # Created articles default to unpublished.
        self.assertNotIn("publishing", body)

    def test_create_with_body_and_publish(self):
        client = MagicMock()
        client.post.return_value = {"id": 99}
        articles_tools.call_tool(
            "article_create",
            {
                "page_id": 5,
                "title": "P",
                "body": "<p>hi</p>",
                "excerpt": "short",
                "publish": True,
            },
            client,
        )
        body = client.post.call_args.args[1]
        self.assertEqual(body["autosaved_body"], "<p>hi</p>")
        self.assertEqual(body["autosaved_excerpt"], "short")
        self.assertIs(body["publishing"], True)

    def test_create_requires_page_id_and_title(self):
        client = MagicMock()
        result = articles_tools.call_tool("article_create", {"page_id": 5}, client)
        self.assertTrue(result.isError)
        client.post.assert_not_called()

    def test_article_create_preserves_empty_string_body(self):
        # Empty string is a legitimate "set this field to empty" input and
        # must NOT be silently dropped (matches article_update semantics).
        client = MagicMock()
        client.post.return_value = {"id": 99}
        articles_tools.call_tool(
            "article_create",
            {
                "page_id": 5,
                "title": "T",
                "body": "",
                "excerpt": "",
                "description": "",
                "path": "",
                "tag_names": [],
            },
            client,
        )
        body = client.post.call_args.args[1]
        self.assertEqual(body["autosaved_body"], "")
        self.assertEqual(body["autosaved_excerpt"], "")
        self.assertEqual(body["description"], "")
        self.assertEqual(body["path"], "")
        self.assertEqual(body["tag_names"], [])

    def test_article_create_omits_truly_absent_fields(self):
        # When optional fields are not in arguments at all, they must not
        # appear in the POST body (regression — don't send None or
        # placeholder values).
        client = MagicMock()
        client.post.return_value = {"id": 99}
        articles_tools.call_tool(
            "article_create",
            {"page_id": 5, "title": "T"},
            client,
        )
        body = client.post.call_args.args[1]
        self.assertNotIn("autosaved_body", body)
        self.assertNotIn("autosaved_excerpt", body)
        self.assertNotIn("description", body)
        self.assertNotIn("path", body)
        self.assertNotIn("image_id", body)
        self.assertNotIn("tag_names", body)
        self.assertNotIn("data", body)
        self.assertNotIn("publishing", body)


class TestArticleUpdate(unittest.TestCase):
    def test_update_uses_autosaved_fields(self):
        client = MagicMock()
        client.put.return_value = {"id": 99}
        articles_tools.call_tool(
            "article_update",
            {
                "article_id": 99,
                "title": "Updated",
                "body": "<p>updated</p>",
                "excerpt": "ex",
                "description": "meta",
            },
            client,
        )
        client.put.assert_called_once()
        path, body = client.put.call_args.args
        self.assertEqual(path, "/articles/99")
        self.assertEqual(body["autosaved_title"], "Updated")
        self.assertEqual(body["autosaved_body"], "<p>updated</p>")
        self.assertEqual(body["autosaved_excerpt"], "ex")
        self.assertEqual(body["description"], "meta")  # NOT autosaved
        self.assertNotIn("title", body)
        self.assertNotIn("body", body)

    def test_update_path_and_image_id(self):
        client = MagicMock()
        client.put.return_value = {"id": 99}
        articles_tools.call_tool(
            "article_update",
            {"article_id": 99, "path": "blog/x", "image_id": 1234},
            client,
        )
        body = client.put.call_args.args[1]
        self.assertEqual(body["path"], "blog/x")
        self.assertEqual(body["image_id"], 1234)

    def test_update_data_field(self):
        client = MagicMock()
        client.put.return_value = {"id": 99}
        articles_tools.call_tool(
            "article_update",
            {"article_id": 99, "data": {"item_image": {"original_id": 7}}},
            client,
        )
        body = client.put.call_args.args[1]
        self.assertEqual(body["data"]["item_image"]["original_id"], 7)

    def test_update_rejects_empty(self):
        client = MagicMock()
        result = articles_tools.call_tool("article_update", {"article_id": 99}, client)
        self.assertTrue(result.isError)
        client.put.assert_not_called()


class TestArticlePublish(unittest.TestCase):
    def test_publish_sends_all_autosaved_and_publishing_true(self):
        client = MagicMock()
        # Per skill memory: publish must include all autosaved_* in the
        # SAME PUT as publishing:true so values are copied to published
        # fields atomically. Implementation reads the article first to
        # get the autosaved values, then replays them.
        client.get.return_value = {
            "id": 99,
            "autosaved_title": "Final Title",
            "autosaved_body": "<p>final</p>",
            "autosaved_excerpt": "final ex",
        }
        client.put.return_value = {"id": 99}
        articles_tools.call_tool("article_publish", {"article_id": 99}, client)
        body = client.put.call_args.args[1]
        self.assertEqual(body["autosaved_title"], "Final Title")
        self.assertEqual(body["autosaved_body"], "<p>final</p>")
        self.assertEqual(body["autosaved_excerpt"], "final ex")
        self.assertIs(body["publishing"], True)

    def test_rejects_when_all_autosaved_null(self):
        # If a freshly-created article never had any content set, publishing
        # would produce an empty post. Reject with a clear error pointing at
        # article_update.
        client = MagicMock()
        client.get.return_value = {
            "id": 99,
            "autosaved_title": None,
            "autosaved_body": None,
            "autosaved_excerpt": None,
        }
        result = articles_tools.call_tool("article_publish", {"article_id": 99}, client)
        self.assertTrue(result.isError)
        client.put.assert_not_called()


class TestArticleDelete(unittest.TestCase):
    def test_requires_force(self):
        client = MagicMock()
        result = articles_tools.call_tool("article_delete", {"article_id": 99}, client)
        self.assertTrue(result.isError)
        client.delete.assert_not_called()

    def test_force_true_deletes(self):
        client = MagicMock()
        articles_tools.call_tool(
            "article_delete",
            {"article_id": 99, "force": True},
            client,
        )
        client.delete.assert_called_once_with("/articles/99")
