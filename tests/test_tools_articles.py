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
                "article_delete_data",
                "article_get",
                "article_publish",
                "article_set_data",
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

    def test_articles_list_no_filters_passes_no_params(self):
        # Regression guard alongside test_list_returns_simplified — make
        # sure the bare-call request line stays unparameterised.
        client = MagicMock()
        client.get_all.return_value = []
        articles_tools.call_tool("articles_list", {}, client)
        client.get_all.assert_called_once_with("/articles")

    def test_articles_list_passes_page_id(self):
        client = MagicMock()
        client.get_all.return_value = []
        articles_tools.call_tool("articles_list", {"page_id": 42}, client)
        client.get_all.assert_called_once_with("/articles", params={"page_id": 42})

    def test_articles_list_passes_language_code(self):
        client = MagicMock()
        client.get_all.return_value = []
        articles_tools.call_tool("articles_list", {"language_code": "et"}, client)
        client.get_all.assert_called_once_with("/articles", params={"language_code": "et"})

    def test_articles_list_passes_language_id(self):
        client = MagicMock()
        client.get_all.return_value = []
        articles_tools.call_tool("articles_list", {"language_id": 627582}, client)
        client.get_all.assert_called_once_with("/articles", params={"language_id": 627582})

    def test_articles_list_passes_single_tag(self):
        client = MagicMock()
        client.get_all.return_value = []
        articles_tools.call_tool("articles_list", {"tag": "news"}, client)
        client.get_all.assert_called_once_with("/articles", params={"tag": "news"})

    def test_articles_list_passes_sort(self):
        client = MagicMock()
        client.get_all.return_value = []
        articles_tools.call_tool("articles_list", {"sort": "article.created_at.$desc"}, client)
        client.get_all.assert_called_once_with(
            "/articles", params={"s": "article.created_at.$desc"}
        )

    def test_articles_list_combines_filters(self):
        client = MagicMock()
        client.get_all.return_value = []
        articles_tools.call_tool(
            "articles_list",
            {
                "page_id": 42,
                "language_code": "en",
                "tag": "news",
                "sort": "article.created_at.$desc",
            },
            client,
        )
        client.get_all.assert_called_once_with(
            "/articles",
            params={
                "page_id": 42,
                "language_code": "en",
                "tag": "news",
                "s": "article.created_at.$desc",
            },
        )


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

    def test_no_autosaved_falls_back_to_get_then_put(self):
        # Regression: when caller does NOT provide autosaved_* args, we still
        # GET the article, extract autosaved_* values, and PUT them back
        # together with publishing:true. This is the existing behaviour;
        # the GET+PUT branch has a documented race window.
        client = MagicMock()
        client.get.return_value = {
            "id": 99,
            "autosaved_title": "T",
            "autosaved_body": "B",
            "autosaved_excerpt": "E",
        }
        client.put.return_value = {"id": 99}
        articles_tools.call_tool("article_publish", {"article_id": 99}, client)
        client.get.assert_called_once_with("/articles/99")
        body = client.put.call_args.args[1]
        self.assertEqual(body["autosaved_title"], "T")
        self.assertEqual(body["autosaved_body"], "B")
        self.assertEqual(body["autosaved_excerpt"], "E")
        self.assertIs(body["publishing"], True)

    def test_explicit_autosaved_skips_get(self):
        # When the caller passes ALL THREE autosaved_* args, the tool skips
        # the GET entirely and PUTs directly. This collapses GET+PUT into a
        # single round trip with no race window — the caller is the source
        # of truth for the content being published.
        client = MagicMock()
        client.put.return_value = {"id": 99}
        articles_tools.call_tool(
            "article_publish",
            {
                "article_id": 99,
                "autosaved_title": "Caller Title",
                "autosaved_body": "<p>caller body</p>",
                "autosaved_excerpt": "caller ex",
            },
            client,
        )
        client.get.assert_not_called()
        client.put.assert_called_once()
        path, body = client.put.call_args.args
        self.assertEqual(path, "/articles/99")
        self.assertEqual(body["autosaved_title"], "Caller Title")
        self.assertEqual(body["autosaved_body"], "<p>caller body</p>")
        self.assertEqual(body["autosaved_excerpt"], "caller ex")
        self.assertIs(body["publishing"], True)
        # Body should contain ONLY the four expected keys — no leakage.
        self.assertEqual(
            set(body.keys()),
            {"autosaved_title", "autosaved_body", "autosaved_excerpt", "publishing"},
        )

    def test_partial_autosaved_rejected(self):
        # Mixed (some autosaved_* args provided, some missing) is ambiguous:
        # the caller may have forgotten or intended partial. Force them to
        # be explicit — either pass all three (fast path) or none (GET+PUT).
        client = MagicMock()
        result = articles_tools.call_tool(
            "article_publish",
            {"article_id": 99, "autosaved_title": "Only Title"},
            client,
        )
        self.assertTrue(result.isError)
        client.get.assert_not_called()
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


class TestArticlesListSchema(unittest.TestCase):
    def test_articles_list_schema_exposes_filter_args(self):
        tool = next(t for t in articles_tools.get_tools() if t.name == "articles_list")
        props = tool.inputSchema["properties"]
        for arg in ("page_id", "language_code", "language_id", "tag", "sort"):
            self.assertIn(arg, props, f"articles_list schema missing {arg!r}")

    def test_articles_list_only_site_required(self):
        tool = next(t for t in articles_tools.get_tools() if t.name == "articles_list")
        self.assertEqual(tool.inputSchema["required"], ["site"])

    def test_articles_list_string_filters_have_minlength(self):
        # Empty-string filter values would silently pass through to Voog.
        # Schema-level minLength:1 rejects this at the MCP boundary.
        tool = next(t for t in articles_tools.get_tools() if t.name == "articles_list")
        props = tool.inputSchema["properties"]
        for arg in ("language_code", "tag", "sort"):
            self.assertEqual(
                props[arg].get("minLength"),
                1,
                f"articles_list schema {arg!r} missing minLength:1",
            )


class TestArticleSetData(unittest.TestCase):
    def test_set_data_calls_client(self):
        client = MagicMock()
        client.put.return_value = {"key": "color", "value": "red"}
        articles_tools.call_tool(
            "article_set_data",
            {"article_id": 7, "key": "color", "value": "red"},
            client,
        )
        client.put.assert_called_once_with(
            "/articles/7/data/color", {"value": "red"}
        )

    def test_set_data_rejects_internal_key(self):
        client = MagicMock()
        result = articles_tools.call_tool(
            "article_set_data",
            {"article_id": 7, "key": "internal_admin", "value": "x"},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)

    def test_set_data_rejects_empty_key(self):
        client = MagicMock()
        result = articles_tools.call_tool(
            "article_set_data",
            {"article_id": 7, "key": "", "value": "x"},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)

    def test_set_data_rejects_traversal_key(self):
        client = MagicMock()
        result = articles_tools.call_tool(
            "article_set_data",
            {"article_id": 7, "key": "../escape", "value": "x"},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)

    def test_set_data_accepts_complex_values(self):
        # value can be string|number|boolean|object|array per schema
        client = MagicMock()
        client.put.return_value = {"key": "tags", "value": ["a", "b"]}
        articles_tools.call_tool(
            "article_set_data",
            {"article_id": 7, "key": "tags", "value": ["a", "b"]},
            client,
        )
        client.put.assert_called_once_with(
            "/articles/7/data/tags", {"value": ["a", "b"]}
        )

    def test_set_data_in_get_tools(self):
        names = {t.name for t in articles_tools.get_tools()}
        self.assertIn("article_set_data", names)


class TestArticleDeleteData(unittest.TestCase):
    def test_delete_data_requires_force(self):
        client = MagicMock()
        result = articles_tools.call_tool(
            "article_delete_data",
            {"article_id": 7, "key": "color"},
            client,
        )
        client.delete.assert_not_called()
        self.assertTrue(result.isError)

    def test_delete_data_with_force_calls_client(self):
        client = MagicMock()
        client.delete.return_value = None
        articles_tools.call_tool(
            "article_delete_data",
            {"article_id": 7, "key": "color", "force": True},
            client,
        )
        client.delete.assert_called_once_with("/articles/7/data/color")

    def test_delete_data_rejects_internal_key_even_with_force(self):
        client = MagicMock()
        result = articles_tools.call_tool(
            "article_delete_data",
            {"article_id": 7, "key": "internal_admin", "force": True},
            client,
        )
        client.delete.assert_not_called()
        self.assertTrue(result.isError)

    def test_delete_data_in_get_tools(self):
        names = {t.name for t in articles_tools.get_tools()}
        self.assertIn("article_delete_data", names)

    def test_delete_data_destructive_annotation(self):
        tools = {t.name: t for t in articles_tools.get_tools()}
        ann = tools["article_delete_data"].annotations
        self.assertIs(ann.readOnlyHint, False)
        self.assertIs(ann.destructiveHint, True)
        self.assertIs(ann.idempotentHint, False)
