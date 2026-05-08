"""Tests for voog.mcp.tools.multilingual — languages, nodes."""

import json
import unittest
from unittest.mock import MagicMock

from voog.mcp.tools import multilingual as mt


class TestGetTools(unittest.TestCase):
    def test_eight_tools_registered(self):
        names = sorted(t.name for t in mt.get_tools())
        self.assertEqual(
            names,
            [
                "language_create",
                "language_delete",
                "languages_list",
                "node_get",
                "node_move",
                "node_relocate",
                "node_update",
                "nodes_list",
            ],
        )


class TestLanguagesList(unittest.TestCase):
    def test_returns_simplified_list(self):
        client = MagicMock()
        client.get_all.return_value = [
            {
                "id": 627583,
                "code": "et",
                "title": "Eesti",
                "default_language": True,
                "published": True,
                "position": 1,
            },
            {
                "id": 627582,
                "code": "en",
                "title": "English",
                "default_language": False,
                "published": True,
                "position": 2,
            },
        ]
        result = mt.call_tool("languages_list", {}, client)
        client.get_all.assert_called_once_with("/languages")
        items = json.loads(result[1].text)
        self.assertEqual(items[0]["code"], "et")
        self.assertIs(items[0]["default_language"], True)


class TestNodesList(unittest.TestCase):
    def test_returns_simplified_list(self):
        client = MagicMock()
        client.get_all.return_value = [
            {"id": 1, "title": "Home", "parent_id": None, "position": 1},
            {"id": 2, "title": "Sub", "parent_id": 1, "position": 1},
        ]
        result = mt.call_tool("nodes_list", {}, client)
        client.get_all.assert_called_once_with("/nodes")
        items = json.loads(result[1].text)
        self.assertEqual(len(items), 2)


class TestNodeGet(unittest.TestCase):
    def test_returns_node(self):
        client = MagicMock()
        client.get.return_value = {
            "id": 5,
            "title": "Pood",
            "pages": [
                {"id": 100, "language_id": 627583},
                {"id": 101, "language_id": 627582},
            ],
        }
        result = mt.call_tool("node_get", {"node_id": 5}, client)
        client.get.assert_called_once_with("/nodes/5")
        body = json.loads(result[0].text)
        self.assertEqual(body["id"], 5)
        self.assertEqual(len(body["pages"]), 2)


class TestLanguageCreate(unittest.TestCase):
    def test_create_in_get_tools(self):
        names = {t.name for t in mt.get_tools()}
        self.assertIn("language_create", names)

    def test_create_minimum_payload(self):
        client = MagicMock()
        client.post.return_value = {"id": 99, "code": "et", "title": "Eesti"}
        mt.call_tool(
            "language_create",
            {"code": "et", "title": "Eesti"},
            client,
        )
        client.post.assert_called_once_with(
            "/languages",
            {"code": "et", "title": "Eesti"},
        )

    def test_create_full_payload(self):
        client = MagicMock()
        client.post.return_value = {"id": 99}
        mt.call_tool(
            "language_create",
            {
                "code": "en",
                "title": "English",
                "region": "GB",
                "site_title": "My site",
                "site_header": "Welcome!",
                "default_language": False,
                "published": True,
                "content_origin_id": 5,
            },
            client,
        )
        sent_body = client.post.call_args[0][1]
        self.assertNotIn("language", sent_body)
        self.assertEqual(sent_body["code"], "en")
        self.assertEqual(sent_body["region"], "GB")
        self.assertEqual(sent_body["site_title"], "My site")
        self.assertEqual(sent_body["content_origin_id"], 5)
        self.assertIs(sent_body["default_language"], False)

    def test_create_no_envelope_wrapper(self):
        client = MagicMock()
        client.post.return_value = {"id": 1}
        mt.call_tool(
            "language_create",
            {"code": "et", "title": "Eesti"},
            client,
        )
        sent_body = client.post.call_args[0][1]
        self.assertNotIn("language", sent_body)
        self.assertIn("code", sent_body)

    def test_create_requires_code(self):
        client = MagicMock()
        result = mt.call_tool(
            "language_create",
            {"title": "Eesti"},
            client,
        )
        client.post.assert_not_called()
        self.assertTrue(result.isError)

    def test_create_requires_title(self):
        client = MagicMock()
        result = mt.call_tool(
            "language_create",
            {"code": "et"},
            client,
        )
        client.post.assert_not_called()
        self.assertTrue(result.isError)

    def test_create_annotations(self):
        tools = {t.name: t for t in mt.get_tools()}
        ann = tools["language_create"].annotations
        self.assertIs(ann.readOnlyHint, False)
        self.assertIs(ann.destructiveHint, False)
        self.assertIs(ann.idempotentHint, False)

    def test_code_schema_locks_to_two_chars(self):
        # Voog stores region separately — `code` must be exactly 2 chars
        # (ISO 639-1). PR #112 review: regression guard against drift back
        # to permissive maxLength that round-trips a 422 from Voog.
        tools = {t.name: t for t in mt.get_tools()}
        code_schema = tools["language_create"].inputSchema["properties"]["code"]
        self.assertEqual(code_schema["minLength"], 2)
        self.assertEqual(code_schema["maxLength"], 2)


class TestLanguageDelete(unittest.TestCase):
    def test_delete_in_get_tools(self):
        names = {t.name for t in mt.get_tools()}
        self.assertIn("language_delete", names)

    def test_delete_requires_force(self):
        client = MagicMock()
        result = mt.call_tool("language_delete", {"language_id": 7}, client)
        client.delete.assert_not_called()
        self.assertTrue(result.isError)

    def test_delete_with_force_calls_client(self):
        client = MagicMock()
        client.delete.return_value = None
        mt.call_tool(
            "language_delete",
            {"language_id": 7, "force": True},
            client,
        )
        client.delete.assert_called_once_with("/languages/7")

    def test_delete_annotations(self):
        tools = {t.name: t for t in mt.get_tools()}
        ann = tools["language_delete"].annotations
        self.assertIs(ann.readOnlyHint, False)
        self.assertIs(ann.destructiveHint, True)
        self.assertIs(ann.idempotentHint, False)


class TestNodeUpdate(unittest.TestCase):
    def test_update_in_get_tools(self):
        names = {t.name for t in mt.get_tools()}
        self.assertIn("node_update", names)

    def test_update_calls_put_with_flat_body(self):
        client = MagicMock()
        client.put.return_value = {"id": 3, "title": "New title"}
        mt.call_tool(
            "node_update",
            {"node_id": 3, "title": "New title"},
            client,
        )
        client.put.assert_called_once_with("/nodes/3", {"title": "New title"})

    def test_update_no_envelope_wrapper(self):
        client = MagicMock()
        client.put.return_value = {"id": 3}
        mt.call_tool(
            "node_update",
            {"node_id": 3, "title": "X"},
            client,
        )
        sent_body = client.put.call_args[0][1]
        self.assertNotIn("node", sent_body)
        self.assertIn("title", sent_body)

    def test_update_requires_title(self):
        client = MagicMock()
        result = mt.call_tool(
            "node_update",
            {"node_id": 3},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)

    def test_update_rejects_empty_title(self):
        client = MagicMock()
        result = mt.call_tool(
            "node_update",
            {"node_id": 3, "title": "   "},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)

    def test_update_annotations(self):
        tools = {t.name: t for t in mt.get_tools()}
        ann = tools["node_update"].annotations
        self.assertIs(ann.readOnlyHint, False)
        self.assertIs(ann.destructiveHint, False)
        self.assertIs(ann.idempotentHint, True)


class TestNodeMove(unittest.TestCase):
    def test_move_in_get_tools(self):
        names = {t.name for t in mt.get_tools()}
        self.assertIn("node_move", names)

    def test_move_uses_query_params(self):
        client = MagicMock()
        client.put.return_value = {"id": 3, "parent_id": 2, "position": 1}
        mt.call_tool(
            "node_move",
            {"node_id": 3, "parent_id": 2, "position": 1},
            client,
        )
        call = client.put.call_args
        self.assertEqual(call[0][0], "/nodes/3/move")
        # params= is keyword-only on VoogClient.put
        self.assertEqual(call.kwargs["params"], {"parent_id": 2, "position": 1})

    def test_move_position_optional(self):
        client = MagicMock()
        client.put.return_value = {"id": 3}
        mt.call_tool(
            "node_move",
            {"node_id": 3, "parent_id": 2},
            client,
        )
        # When position is omitted, do NOT inject a default — let Voog
        # apply its server-side default (1). This way the schema's
        # documentation of "default 1" stays a server contract, not an
        # MCP client contract.
        params = client.put.call_args.kwargs["params"]
        self.assertEqual(params, {"parent_id": 2})
        self.assertNotIn("position", params)

    def test_move_requires_parent_id(self):
        client = MagicMock()
        result = mt.call_tool(
            "node_move",
            {"node_id": 3},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)

    def test_move_no_body(self):
        # Voog's move endpoint takes inputs in the query string. The PUT
        # body should be None/empty — sending an unrelated body could
        # confuse the server.
        client = MagicMock()
        client.put.return_value = {}
        mt.call_tool(
            "node_move",
            {"node_id": 3, "parent_id": 2},
            client,
        )
        call = client.put.call_args
        # data is the second positional or the `data=` kwarg, or omitted
        if len(call[0]) >= 2:
            self.assertIn(call[0][1], (None, {}))
        else:
            data_kwarg = call.kwargs.get("data")
            self.assertIn(data_kwarg, (None, {}))

    def test_move_annotations(self):
        tools = {t.name: t for t in mt.get_tools()}
        ann = tools["node_move"].annotations
        # Re-issuing the same move yields the same tree state — idempotent.
        # No data loss — not destructive.
        self.assertIs(ann.readOnlyHint, False)
        self.assertIs(ann.destructiveHint, False)
        self.assertIs(ann.idempotentHint, True)

    def test_move_rejects_bool_parent_id(self):
        # `bool` is a subclass of int in Python — explicit reject so
        # True/False don't slip through as 1/0. PR #113 review.
        client = MagicMock()
        result = mt.call_tool(
            "node_move",
            {"node_id": 3, "parent_id": True},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)

    def test_move_rejects_bool_position(self):
        client = MagicMock()
        result = mt.call_tool(
            "node_move",
            {"node_id": 3, "parent_id": 2, "position": False},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)


class TestNodeRelocate(unittest.TestCase):
    def test_relocate_in_get_tools(self):
        names = {t.name for t in mt.get_tools()}
        self.assertIn("node_relocate", names)

    def test_relocate_with_before(self):
        client = MagicMock()
        client.put.return_value = {"id": 3}
        mt.call_tool(
            "node_relocate",
            {"node_id": 3, "before": 2},
            client,
        )
        client.put.assert_called_once_with("/nodes/3/relocate", {"before": 2})

    def test_relocate_with_after(self):
        client = MagicMock()
        client.put.return_value = {"id": 3}
        mt.call_tool(
            "node_relocate",
            {"node_id": 3, "after": 5},
            client,
        )
        client.put.assert_called_once_with("/nodes/3/relocate", {"after": 5})

    def test_relocate_with_parent_node_id(self):
        client = MagicMock()
        client.put.return_value = {"id": 3}
        mt.call_tool(
            "node_relocate",
            {"node_id": 3, "parent_node_id": 7},
            client,
        )
        client.put.assert_called_once_with("/nodes/3/relocate", {"parent_node_id": 7})

    def test_relocate_requires_at_least_one_field(self):
        client = MagicMock()
        result = mt.call_tool(
            "node_relocate",
            {"node_id": 3},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)

    def test_relocate_rejects_multiple_fields(self):
        # Voog's docs are silent on what happens when more than one is
        # supplied; defensive rejection keeps the contract clean and
        # forces the caller to be explicit.
        client = MagicMock()
        result = mt.call_tool(
            "node_relocate",
            {"node_id": 3, "before": 2, "after": 5},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)

    def test_relocate_no_envelope_wrapper(self):
        client = MagicMock()
        client.put.return_value = {"id": 3}
        mt.call_tool(
            "node_relocate",
            {"node_id": 3, "before": 2},
            client,
        )
        sent_body = client.put.call_args[0][1]
        self.assertNotIn("node", sent_body)
        self.assertIn("before", sent_body)

    def test_relocate_annotations(self):
        tools = {t.name: t for t in mt.get_tools()}
        ann = tools["node_relocate"].annotations
        self.assertIs(ann.readOnlyHint, False)
        self.assertIs(ann.destructiveHint, False)
        self.assertIs(ann.idempotentHint, True)
