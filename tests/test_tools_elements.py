"""Tests for voog.mcp.tools.elements — Elements CRUD."""

import json
import unittest
from unittest.mock import MagicMock

from voog.mcp.tools import elements as et


class TestGetTools(unittest.TestCase):
    def test_six_tools_registered(self):
        names = sorted(t.name for t in et.get_tools())
        self.assertEqual(
            names,
            [
                "element_create",
                "element_definitions_list",
                "element_delete",
                "element_get",
                "element_update",
                "elements_list",
            ],
        )


class TestElementsList(unittest.TestCase):
    def test_in_get_tools(self):
        names = {t.name for t in et.get_tools()}
        self.assertIn("elements_list", names)

    def test_bare_call_no_filters(self):
        client = MagicMock()
        client.get_all.return_value = [
            {
                "id": 1,
                "title": "Alpha",
                "path": "alpha",
                "page_id": 7,
                "element_definition_id": 3,
                "position": 1,
                "values": {"x": "y"},
            },
        ]
        result = et.call_tool("elements_list", {}, client)
        # No filters → params=None
        client.get_all.assert_called_once_with("/elements", params=None)
        items = json.loads(result[1].text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], 1)
        # values stripped from list projection
        self.assertNotIn("values", items[0])

    def test_filters_forwarded(self):
        client = MagicMock()
        client.get_all.return_value = []
        et.call_tool(
            "elements_list",
            {
                "page_id": 7,
                "language_code": "et",
                "element_definition_id": 3,
            },
            client,
        )
        params = client.get_all.call_args.kwargs["params"]
        self.assertEqual(params["page_id"], 7)
        self.assertEqual(params["language_code"], "et")
        self.assertEqual(params["element_definition_id"], 3)

    def test_include_values_filter(self):
        client = MagicMock()
        client.get_all.return_value = []
        et.call_tool(
            "elements_list",
            {"include_values": True},
            client,
        )
        params = client.get_all.call_args.kwargs["params"]
        self.assertIs(params["include_values"], True)

    def test_include_values_returns_values_in_projection(self):
        # PR #116 review: include_values=true must thread through to the
        # projection so the tool description's promise is honoured. Without
        # this thread, three-layer contract drifted: tool-desc said yes,
        # schema-param-desc said yes, projection unconditionally stripped.
        client = MagicMock()
        client.get_all.return_value = [
            {
                "id": 1,
                "title": "Alpha",
                "values": {"caption": "hello", "image": "https://x"},
            },
        ]
        result = et.call_tool(
            "elements_list",
            {"include_values": True},
            client,
        )
        items = json.loads(result[1].text)
        self.assertEqual(items[0]["id"], 1)
        self.assertIn("values", items[0])
        self.assertEqual(items[0]["values"]["caption"], "hello")

    def test_no_include_values_strips_values(self):
        # Default behaviour preserved: bare elements_list omits values.
        client = MagicMock()
        client.get_all.return_value = [
            {"id": 1, "title": "Alpha", "values": {"k": "v"}},
        ]
        result = et.call_tool("elements_list", {}, client)
        items = json.loads(result[1].text)
        self.assertNotIn("values", items[0])

    def test_annotations(self):
        tools = {t.name: t for t in et.get_tools()}
        ann = tools["elements_list"].annotations
        self.assertIs(ann.readOnlyHint, True)
        self.assertIs(ann.destructiveHint, False)
        self.assertIs(ann.idempotentHint, True)


class TestElementGet(unittest.TestCase):
    def test_in_get_tools(self):
        names = {t.name for t in et.get_tools()}
        self.assertIn("element_get", names)

    def test_returns_full_element(self):
        client = MagicMock()
        client.get.return_value = {
            "id": 5,
            "title": "Sample",
            "values": {"foo": "bar"},
        }
        result = et.call_tool("element_get", {"element_id": 5}, client)
        client.get.assert_called_once_with("/elements/5")
        body = json.loads(result[0].text)
        self.assertEqual(body["id"], 5)
        # Detail view DOES include values (unlike list projection).
        self.assertEqual(body["values"]["foo"], "bar")

    def test_annotations(self):
        tools = {t.name: t for t in et.get_tools()}
        ann = tools["element_get"].annotations
        self.assertIs(ann.readOnlyHint, True)
        self.assertIs(ann.destructiveHint, False)
        self.assertIs(ann.idempotentHint, True)


class TestElementDefinitionsList(unittest.TestCase):
    def test_in_get_tools(self):
        names = {t.name for t in et.get_tools()}
        self.assertIn("element_definitions_list", names)

    def test_returns_simplified(self):
        client = MagicMock()
        client.get_all.return_value = [
            {
                "id": 3,
                "title": "Portfolio Item",
                "data": {
                    "properties": {
                        "image": {"key": "image", "data_type": "image"},
                        "caption": {"key": "caption", "data_type": "string"},
                    },
                },
            },
        ]
        result = et.call_tool("element_definitions_list", {}, client)
        client.get_all.assert_called_once_with("/element_definitions")
        items = json.loads(result[1].text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], 3)
        # property_keys sorted for determinism
        self.assertEqual(items[0]["property_keys"], ["caption", "image"])

    def test_handles_missing_data(self):
        client = MagicMock()
        client.get_all.return_value = [
            {"id": 1, "title": "Empty"},  # no `data` key at all
        ]
        result = et.call_tool("element_definitions_list", {}, client)
        items = json.loads(result[1].text)
        self.assertEqual(items[0]["property_keys"], [])

    def test_annotations(self):
        tools = {t.name: t for t in et.get_tools()}
        ann = tools["element_definitions_list"].annotations
        self.assertIs(ann.readOnlyHint, True)
        self.assertIs(ann.destructiveHint, False)
        self.assertIs(ann.idempotentHint, True)


class TestElementCreate(unittest.TestCase):
    def test_in_get_tools(self):
        names = {t.name for t in et.get_tools()}
        self.assertIn("element_create", names)

    def test_minimum_with_definition_id(self):
        client = MagicMock()
        client.post.return_value = {"id": 42, "title": "Hello"}
        et.call_tool(
            "element_create",
            {
                "element_definition_id": 3,
                "page_id": 7,
                "title": "Hello",
            },
            client,
        )
        client.post.assert_called_once_with(
            "/elements",
            {
                "element_definition_id": 3,
                "page_id": 7,
                "title": "Hello",
            },
        )

    def test_minimum_with_definition_title(self):
        # Voog accepts element_definition_title as alternative key.
        client = MagicMock()
        client.post.return_value = {"id": 42}
        et.call_tool(
            "element_create",
            {
                "element_definition_title": "Portfolio Item",
                "page_id": 7,
                "title": "Hello",
            },
            client,
        )
        sent_body = client.post.call_args[0][1]
        self.assertEqual(sent_body["element_definition_title"], "Portfolio Item")
        self.assertNotIn("element_definition_id", sent_body)

    def test_full_payload(self):
        client = MagicMock()
        client.post.return_value = {"id": 42}
        et.call_tool(
            "element_create",
            {
                "element_definition_id": 3,
                "page_id": 7,
                "title": "Hello",
                "path": "hello-custom",
                "values": {"image": "https://x", "caption": "y"},
            },
            client,
        )
        sent_body = client.post.call_args[0][1]
        self.assertNotIn("element", sent_body)  # no envelope
        self.assertEqual(sent_body["path"], "hello-custom")
        self.assertEqual(sent_body["values"]["caption"], "y")

    def test_no_envelope_wrapper(self):
        client = MagicMock()
        client.post.return_value = {"id": 1}
        et.call_tool(
            "element_create",
            {"element_definition_id": 1, "page_id": 1, "title": "X"},
            client,
        )
        sent_body = client.post.call_args[0][1]
        self.assertNotIn("element", sent_body)
        self.assertIn("title", sent_body)

    def test_requires_page_id(self):
        client = MagicMock()
        result = et.call_tool(
            "element_create",
            {"element_definition_id": 1, "title": "X"},
            client,
        )
        client.post.assert_not_called()
        self.assertTrue(result.isError)

    def test_requires_title(self):
        client = MagicMock()
        result = et.call_tool(
            "element_create",
            {"element_definition_id": 1, "page_id": 7},
            client,
        )
        client.post.assert_not_called()
        self.assertTrue(result.isError)

    def test_requires_definition_id_or_title(self):
        client = MagicMock()
        result = et.call_tool(
            "element_create",
            {"page_id": 7, "title": "X"},
            client,
        )
        client.post.assert_not_called()
        self.assertTrue(result.isError)

    def test_rejects_bool_element_definition_id(self):
        # PR #116 review: bool is a subclass of int — explicit reject so
        # True/False don't slip through and land as element_definition_id
        # in the body. Mirrors the page_id pattern (Phase 6 review).
        client = MagicMock()
        result = et.call_tool(
            "element_create",
            {"element_definition_id": True, "page_id": 7, "title": "X"},
            client,
        )
        client.post.assert_not_called()
        self.assertTrue(result.isError)

    def test_annotations(self):
        tools = {t.name: t for t in et.get_tools()}
        ann = tools["element_create"].annotations
        self.assertIs(ann.readOnlyHint, False)
        self.assertIs(ann.destructiveHint, False)
        self.assertIs(ann.idempotentHint, False)


class TestElementUpdate(unittest.TestCase):
    def test_in_get_tools(self):
        names = {t.name for t in et.get_tools()}
        self.assertIn("element_update", names)

    def test_partial_title(self):
        client = MagicMock()
        client.put.return_value = {"id": 5}
        et.call_tool(
            "element_update",
            {"element_id": 5, "title": "New title"},
            client,
        )
        client.put.assert_called_once_with("/elements/5", {"title": "New title"})

    def test_partial_values(self):
        client = MagicMock()
        client.put.return_value = {"id": 5}
        et.call_tool(
            "element_update",
            {"element_id": 5, "values": {"caption": "updated"}},
            client,
        )
        client.put.assert_called_once_with("/elements/5", {"values": {"caption": "updated"}})

    def test_full_payload(self):
        client = MagicMock()
        client.put.return_value = {"id": 5}
        et.call_tool(
            "element_update",
            {
                "element_id": 5,
                "title": "T",
                "path": "p",
                "values": {"k": "v"},
            },
            client,
        )
        sent_body = client.put.call_args[0][1]
        self.assertNotIn("element", sent_body)
        self.assertNotIn("element_id", sent_body)
        self.assertEqual(sent_body["title"], "T")
        self.assertEqual(sent_body["path"], "p")
        self.assertEqual(sent_body["values"], {"k": "v"})

    def test_requires_at_least_one_field(self):
        client = MagicMock()
        result = et.call_tool(
            "element_update",
            {"element_id": 5},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)

    def test_no_envelope_wrapper(self):
        client = MagicMock()
        client.put.return_value = {"id": 1}
        et.call_tool(
            "element_update",
            {"element_id": 1, "title": "X"},
            client,
        )
        sent_body = client.put.call_args[0][1]
        self.assertNotIn("element", sent_body)

    def test_annotations(self):
        tools = {t.name: t for t in et.get_tools()}
        ann = tools["element_update"].annotations
        self.assertIs(ann.readOnlyHint, False)
        self.assertIs(ann.destructiveHint, False)
        self.assertIs(ann.idempotentHint, True)


class TestElementDelete(unittest.TestCase):
    def test_in_get_tools(self):
        names = {t.name for t in et.get_tools()}
        self.assertIn("element_delete", names)

    def test_requires_force(self):
        client = MagicMock()
        result = et.call_tool("element_delete", {"element_id": 5}, client)
        client.delete.assert_not_called()
        self.assertTrue(result.isError)

    def test_with_force_calls_client(self):
        client = MagicMock()
        client.delete.return_value = None
        et.call_tool(
            "element_delete",
            {"element_id": 5, "force": True},
            client,
        )
        client.delete.assert_called_once_with("/elements/5")

    def test_annotations(self):
        tools = {t.name: t for t in et.get_tools()}
        ann = tools["element_delete"].annotations
        self.assertIs(ann.readOnlyHint, False)
        self.assertIs(ann.destructiveHint, True)
        self.assertIs(ann.idempotentHint, False)


class TestServerToolRegistry(unittest.TestCase):
    def test_elements_in_tool_groups(self):
        from voog.mcp import server

        self.assertIn(et, server.TOOL_GROUPS)
