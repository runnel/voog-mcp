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
            {
                "id": 24898880,
                "position": 1,
                "title": "Alt one",
                "filename": "a.jpg",
                "type": "image",
            },
            {
                "id": 24898881,
                "position": 2,
                "title": "Alt two",
                "filename": "b.jpg",
                "type": "image",
            },
            {
                "id": 24898882,
                "position": 3,
                "title": "Alt three",
                "filename": "c.jpg",
                "type": "image",
            },
            {
                "id": 24898883,
                "position": 4,
                "title": "Alt four",
                "filename": "d.jpg",
                "type": "image",
            },
        ],
    }


class TestGetTools(unittest.TestCase):
    def test_returns_three_tools(self):
        names = [t.name for t in media_sets_tools.get_tools()]
        self.assertEqual(
            names,
            [
                "media_set_get",
                "media_set_update_asset_titles",
                "media_set_set_assets",
            ],
        )

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
        result = media_sets_tools.call_tool("media_set_get", {"media_set_id": 1542046}, client)
        payload = _success_payload(result)
        self.assertEqual(payload["assets_count"], 4)
        self.assertEqual(
            [a["id"] for a in payload["assets"]], [24898880, 24898881, 24898882, 24898883]
        )
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
        self.assertEqual(
            [a["id"] for a in body["assets"]], [24898880, 24898881, 24898882, 24898883]
        )
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


class TestMediaSetSetAssets(unittest.TestCase):
    """Issue #140 item 5 — build/reorder a gallery in one call.

    PUT /media_sets/{id} replaces the whole array, which is what silently
    unlinked three of four images on a live site (issue #120). This tool
    makes the replacement explicit and gates the destructive half.
    """

    def _client(self, assets):
        """Fake Voog that APPLIES the write, so read-back verification passes.

        The tool now re-reads the gallery to confirm the order took (Voog
        applies a reorder only partially on the first PUT). A fixture that
        always returned the original order would fail every reorder test for
        the wrong reason.
        """
        client = MagicMock()
        state = {"assets": list(assets)}

        def _get(path):
            return {"id": 5, "title": "Gallery", "assets": state["assets"]}

        def _put(path, payload):
            by_id = {a["id"]: a for a in assets}
            state["assets"] = [{**by_id.get(e["id"], {}), **e} for e in payload.get("assets", [])]
            return {"id": 5, "title": "Gallery", "assets": state["assets"]}

        client.get.side_effect = _get
        client.put.side_effect = _put
        return client

    def test_sets_full_array_in_given_order(self):
        client = self._client(
            [
                {"id": 11, "title": "one", "position": 0},
                {"id": 22, "title": "two", "position": 1},
            ]
        )
        media_sets_tools.call_tool(
            "media_set_set_assets",
            {"media_set_id": 5, "asset_ids": [22, 11]},
            client,
        )
        path, payload = client.put.call_args.args
        self.assertEqual(path, "/media_sets/5")
        self.assertEqual([a["id"] for a in payload["assets"]], [22, 11])

    def test_existing_titles_and_settings_are_carried_over(self):
        client = self._client(
            [{"id": 11, "title": "kept", "settings": {"linkurl": "/x"}, "position": 0}]
        )
        media_sets_tools.call_tool(
            "media_set_set_assets",
            {"media_set_id": 5, "asset_ids": [11]},
            client,
        )
        entry = client.put.call_args.args[1]["assets"][0]
        self.assertEqual(entry["title"], "kept")
        self.assertEqual(entry["settings"], {"linkurl": "/x"})

    def test_titles_argument_applies_to_new_assets(self):
        client = self._client([{"id": 11, "title": "old", "position": 0}])
        media_sets_tools.call_tool(
            "media_set_set_assets",
            {"media_set_id": 5, "asset_ids": [11, 99], "titles": {"99": "new photo"}},
            client,
        )
        by_id = {a["id"]: a for a in client.put.call_args.args[1]["assets"]}
        self.assertEqual(by_id[99]["title"], "new photo")
        self.assertEqual(by_id[11]["title"], "old")

    def test_dropping_an_asset_requires_force(self):
        client = self._client(
            [
                {"id": 11, "title": "one", "position": 0},
                {"id": 22, "title": "two", "position": 1},
            ]
        )
        result = media_sets_tools.call_tool(
            "media_set_set_assets",
            {"media_set_id": 5, "asset_ids": [11]},
            client,
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("unlink", payload["error"])
        self.assertIn("force=true", payload["error"])
        client.put.assert_not_called()

    def test_force_allows_the_removal(self):
        client = self._client(
            [
                {"id": 11, "title": "one", "position": 0},
                {"id": 22, "title": "two", "position": 1},
            ]
        )
        media_sets_tools.call_tool(
            "media_set_set_assets",
            {"media_set_id": 5, "asset_ids": [11], "force": True},
            client,
        )
        self.assertEqual([a["id"] for a in client.put.call_args.args[1]["assets"]], [11])

    def test_pure_reorder_needs_no_force(self):
        client = self._client(
            [
                {"id": 11, "title": "one", "position": 0},
                {"id": 22, "title": "two", "position": 1},
            ]
        )
        result = media_sets_tools.call_tool(
            "media_set_set_assets",
            {"media_set_id": 5, "asset_ids": [22, 11]},
            client,
        )
        self.assertFalse(getattr(result, "isError", False))

    def test_rejects_empty_duplicate_and_non_integer_ids(self):
        client = self._client([{"id": 11, "position": 0}])
        for bad in ([], [11, 11], ["11"], [True]):
            result = media_sets_tools.call_tool(
                "media_set_set_assets",
                {"media_set_id": 5, "asset_ids": bad},
                client,
            )
            self.assertTrue(result.isError, f"{bad!r} should be rejected")
        client.put.assert_not_called()

    def test_annotations_flag_destructive(self):
        # Can unlink images — the host should be able to prompt even though
        # the tool force-gates it itself.
        tools = {t.name: t for t in media_sets_tools.get_tools()}
        ann = tools["media_set_set_assets"].annotations
        self.assertIs(_ann_get(ann, "destructiveHint", "destructive_hint"), True)


if __name__ == "__main__":
    unittest.main()


class TestExplicitPositions(unittest.TestCase):
    """Gallery order must be sent explicitly, not implied by array order.

    Proven live 2026-08-13 on media_set 1561863: PUTting a reordered array
    WITHOUT `position` left two assets sharing position 6 and the order
    partly wrong. With an explicit 1-based `position` the reorder was exact
    and round-tripped. This bites `media_set_update_asset_titles` too — it
    promises to preserve order while editing a title, and without positions
    it could silently reshuffle the gallery.
    """

    def _client(self, assets):
        client = MagicMock()
        client.get.return_value = {"id": 5, "assets": assets}
        client.put.return_value = {"id": 5, "assets": assets}
        return client

    def test_set_assets_sends_one_based_positions_in_order(self):
        client = self._client([{"id": 11, "position": 1}, {"id": 22, "position": 2}])
        media_sets_tools.call_tool(
            "media_set_set_assets", {"media_set_id": 5, "asset_ids": [22, 11]}, client
        )
        sent = client.put.call_args.args[1]["assets"]
        self.assertEqual([(a["id"], a["position"]) for a in sent], [(22, 1), (11, 2)])

    def test_title_edit_pins_positions_too(self):
        client = self._client(
            [
                {"id": 11, "title": "a", "position": 1},
                {"id": 22, "title": "b", "position": 2},
                {"id": 33, "title": "c", "position": 3},
            ]
        )
        media_sets_tools.call_tool(
            "media_set_update_asset_titles", {"media_set_id": 5, "titles": {"22": "new"}}, client
        )
        sent = client.put.call_args.args[1]["assets"]
        self.assertEqual([(a["id"], a["position"]) for a in sent], [(11, 1), (22, 2), (33, 3)])
        self.assertEqual([a["title"] for a in sent], ["a", "new", "c"])

    def test_positions_start_at_one_not_zero(self):
        # Voog reports 1..N; a 0-based first slot is not what it echoes back.
        client = self._client([{"id": 11, "position": 1}])
        media_sets_tools.call_tool(
            "media_set_set_assets", {"media_set_id": 5, "asset_ids": [11]}, client
        )
        self.assertEqual(client.put.call_args.args[1]["assets"][0]["position"], 1)


class TestReorderVerification(unittest.TestCase):
    """Voog applies a gallery reorder only partially on the first PUT.

    Proven live on media_set 1561863: reversing 7 assets left two sharing
    position 6; the identical PUT repeated immediately after produced the
    exact order. So one write is not proof — the tool reads back, retries,
    and refuses to report success on an order that never took.
    """

    def _flaky_client(self, assets, succeed_on):
        """Applies the write only from the Nth attempt onward."""
        client = MagicMock()
        state = {"assets": list(assets), "n": 0}

        def _put(path, payload):
            state["n"] += 1
            if state["n"] >= succeed_on:
                by_id = {a["id"]: a for a in assets}
                state["assets"] = [
                    {**by_id.get(e["id"], {}), **e} for e in payload.get("assets", [])
                ]
            return {"id": 5, "assets": state["assets"]}

        client.put.side_effect = _put
        client.get.side_effect = lambda path: {"id": 5, "assets": state["assets"]}
        return client, state

    def test_retries_until_the_order_takes(self):
        assets = [{"id": 11, "position": 1}, {"id": 22, "position": 2}]
        client, state = self._flaky_client(assets, succeed_on=2)
        result = media_sets_tools.call_tool(
            "media_set_set_assets", {"media_set_id": 5, "asset_ids": [22, 11]}, client
        )
        self.assertFalse(getattr(result, "isError", False))
        self.assertEqual(state["n"], 2)
        self.assertEqual([a["id"] for a in state["assets"]], [22, 11])

    def test_reports_failure_when_order_never_applies(self):
        assets = [{"id": 11, "position": 1}, {"id": 22, "position": 2}]
        client, state = self._flaky_client(assets, succeed_on=99)
        result = media_sets_tools.call_tool(
            "media_set_set_assets", {"media_set_id": 5, "asset_ids": [22, 11]}, client
        )
        self.assertTrue(result.isError)
        payload = json.loads(result.content[0].text)
        self.assertIn("did not apply the requested ORDER", payload["error"])
        self.assertIn("Re-run", payload["error"])
        self.assertEqual(state["n"], 3)

    def test_a_failed_read_back_does_not_hide_the_write(self):
        client = MagicMock()
        client.put.return_value = {"id": 5}
        client.get.side_effect = [
            {"id": 5, "assets": [{"id": 11, "position": 1}]},  # pre-flight GET
            RuntimeError("read failed"),
        ]
        result = media_sets_tools.call_tool(
            "media_set_set_assets", {"media_set_id": 5, "asset_ids": [11]}, client
        )
        # Reported as unverified rather than crashing or claiming success.
        self.assertTrue(result.isError)
        client.put.assert_called_once()
