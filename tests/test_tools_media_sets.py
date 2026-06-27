"""Tests for voog.mcp.tools.media_sets (GitHub issue #120)."""

import json
import unittest
from unittest.mock import MagicMock

from tests._test_helpers import _ann_get
from voog.mcp.tools import media_sets as media_sets_tools


def _success_payload(result):
    """Extract the JSON payload from a success_response list[TextContent]."""
    # success_response with a summary returns [summary, json]; without, [json].
    return json.loads(result[-1].text)


def _make_media_set():
    """A 4-asset media_set in the live GET shape (trimmed)."""
    return {
        "id": 1542046,
        "title": "Gallery",
        "kind": "wall",
        "assets": [
            {"id": 24898880, "position": 1, "title": "Alt one", "filename": "a.jpg", "type": "image"},
            {"id": 24898881, "position": 2, "title": "Alt two", "filename": "b.jpg", "type": "image"},
            {"id": 24898882, "position": 3, "title": "Alt three", "filename": "c.jpg", "type": "image"},
            {"id": 24898883, "position": 4, "title": "Alt four", "filename": "d.jpg", "type": "image"},
        ],
    }


class TestGetTools(unittest.TestCase):
    def test_returns_two_tools(self):
        names = [t.name for t in media_sets_tools.get_tools()]
        self.assertEqual(names, ["media_set_get", "media_set_update_asset_titles"])

    def test_get_is_read_only(self):
        tools = {t.name: t for t in media_sets_tools.get_tools()}
        ann = tools["media_set_get"].annotations
        self.assertIs(_ann_get(ann, "readOnlyHint", "read_only_hint"), True)
        self.assertIs(_ann_get(ann, "destructiveHint", "destructive_hint"), False)
        self.assertIs(_ann_get(ann, "idempotentHint", "idempotent_hint"), True)

    def test_update_is_non_destructive(self):
        # The whole point of the typed tool: mutating but SAFE (preserves the
        # full array), so destructiveHint stays False.
        tools = {t.name: t for t in media_sets_tools.get_tools()}
        ann = tools["media_set_update_asset_titles"].annotations
        self.assertIs(_ann_get(ann, "readOnlyHint", "read_only_hint"), False)
        self.assertIs(_ann_get(ann, "destructiveHint", "destructive_hint"), False)
        self.assertIs(_ann_get(ann, "idempotentHint", "idempotent_hint"), True)

    def test_update_schema_requires_titles(self):
        tools = {t.name: t for t in media_sets_tools.get_tools()}
        schema = tools["media_set_update_asset_titles"].inputSchema
        self.assertIn("titles", schema["required"])
        self.assertEqual(schema["properties"]["media_set_id"]["type"], "integer")


class TestMediaSetGet(unittest.TestCase):
    def test_curates_shape(self):
        client = MagicMock()
        client.get.return_value = _make_media_set()
        result = media_sets_tools.call_tool(
            "media_set_get", {"media_set_id": 1542046}, client
        )
        payload = _success_payload(result)
        self.assertEqual(payload["assets_count"], 4)
        self.assertEqual([a["id"] for a in payload["assets"]], [24898880, 24898881, 24898882, 24898883])
        # No bulky `sizes`/`public_url` in the curated asset shape.
        self.assertNotIn("sizes", payload["assets"][0])

    def test_bool_id_rejected(self):
        client = MagicMock()
        result = media_sets_tools.call_tool("media_set_get", {"media_set_id": True}, client)
        client.get.assert_not_called()
        self.assertTrue(result.isError)


class TestUpdateAssetTitles(unittest.TestCase):
    def test_preserves_all_assets_on_single_title_change(self):
        """The core issue-#120 guarantee: changing one title keeps all 4."""
        client = MagicMock()
        client.get.return_value = _make_media_set()
        client.put.return_value = _make_media_set()
        media_sets_tools.call_tool(
            "media_set_update_asset_titles",
            {"media_set_id": 1542046, "titles": {"24898881": "Edited two"}},
            client,
        )
        path, body = client.put.call_args[0][0], client.put.call_args[0][1]
        self.assertEqual(path, "/media_sets/1542046")
        # Bare body, no envelope.
        self.assertEqual(set(body.keys()), {"assets"})
        # All 4 assets present, in position order.
        self.assertEqual([a["id"] for a in body["assets"]], [24898880, 24898881, 24898882, 24898883])
        # Only the targeted title changed; the rest preserved verbatim.
        by_id = {a["id"]: a["title"] for a in body["assets"]}
        self.assertEqual(by_id[24898881], "Edited two")
        self.assertEqual(by_id[24898880], "Alt one")
        self.assertEqual(by_id[24898883], "Alt four")

    def test_multiple_titles(self):
        client = MagicMock()
        client.get.return_value = _make_media_set()
        client.put.return_value = _make_media_set()
        media_sets_tools.call_tool(
            "media_set_update_asset_titles",
            {"media_set_id": 1542046, "titles": {"24898880": "One!", "24898883": "Four!"}},
            client,
        )
        by_id = {a["id"]: a["title"] for a in client.put.call_args[0][1]["assets"]}
        self.assertEqual(by_id[24898880], "One!")
        self.assertEqual(by_id[24898883], "Four!")
        self.assertEqual(by_id[24898881], "Alt two")

    def test_unknown_asset_id_rejected_no_put(self):
        client = MagicMock()
        client.get.return_value = _make_media_set()
        result = media_sets_tools.call_tool(
            "media_set_update_asset_titles",
            {"media_set_id": 1542046, "titles": {"99999999": "ghost"}},
            client,
        )
        client.put.assert_not_called()
        self.assertTrue(result.isError)

    def test_empty_title_allowed(self):
        client = MagicMock()
        client.get.return_value = _make_media_set()
        client.put.return_value = _make_media_set()
        media_sets_tools.call_tool(
            "media_set_update_asset_titles",
            {"media_set_id": 1542046, "titles": {"24898880": ""}},
            client,
        )
        by_id = {a["id"]: a["title"] for a in client.put.call_args[0][1]["assets"]}
        self.assertEqual(by_id[24898880], "")

    def test_non_string_title_rejected(self):
        client = MagicMock()
        result = media_sets_tools.call_tool(
            "media_set_update_asset_titles",
            {"media_set_id": 1542046, "titles": {"24898880": 5}},
            client,
        )
        client.get.assert_not_called()
        self.assertTrue(result.isError)

    def test_empty_titles_rejected(self):
        client = MagicMock()
        result = media_sets_tools.call_tool(
            "media_set_update_asset_titles",
            {"media_set_id": 1542046, "titles": {}},
            client,
        )
        client.get.assert_not_called()
        self.assertTrue(result.isError)

    def test_noop_when_title_already_current(self):
        client = MagicMock()
        client.get.return_value = _make_media_set()
        result = media_sets_tools.call_tool(
            "media_set_update_asset_titles",
            {"media_set_id": 1542046, "titles": {"24898881": "Alt two"}},
            client,
        )
        client.put.assert_not_called()
        self.assertFalse(getattr(result, "isError", False))

    def test_int_keys_accepted(self):
        """JSON keys are strings, but accept int keys defensively."""
        client = MagicMock()
        client.get.return_value = _make_media_set()
        client.put.return_value = _make_media_set()
        media_sets_tools.call_tool(
            "media_set_update_asset_titles",
            {"media_set_id": 1542046, "titles": {24898880: "Int key"}},
            client,
        )
        by_id = {a["id"]: a["title"] for a in client.put.call_args[0][1]["assets"]}
        self.assertEqual(by_id[24898880], "Int key")

    def test_preserves_per_asset_settings(self):
        client = MagicMock()
        ms = _make_media_set()
        ms["assets"][0]["settings"] = {"linkurl": "http://x", "linktarget": "_blank"}
        client.get.return_value = ms
        client.put.return_value = ms
        media_sets_tools.call_tool(
            "media_set_update_asset_titles",
            {"media_set_id": 1542046, "titles": {"24898880": "New"}},
            client,
        )
        first = client.put.call_args[0][1]["assets"][0]
        self.assertEqual(first["settings"], {"linkurl": "http://x", "linktarget": "_blank"})

    def test_orders_by_position(self):
        client = MagicMock()
        ms = _make_media_set()
        # Scramble the incoming order; positions still 1..4.
        ms["assets"] = [ms["assets"][2], ms["assets"][0], ms["assets"][3], ms["assets"][1]]
        client.get.return_value = ms
        client.put.return_value = ms
        media_sets_tools.call_tool(
            "media_set_update_asset_titles",
            {"media_set_id": 1542046, "titles": {"24898880": "x"}},
            client,
        )
        ids = [a["id"] for a in client.put.call_args[0][1]["assets"]]
        self.assertEqual(ids, [24898880, 24898881, 24898882, 24898883])


if __name__ == "__main__":
    unittest.main()
