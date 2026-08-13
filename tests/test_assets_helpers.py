"""Unit tests for voog._assets — asset lookup + derivative waiting.

These encode two Voog behaviours that are expensive to rediscover: a repeated
filename produces a duplicate rather than an overwrite, and derivative URLs
must never be probed over HTTP before the API says they exist.
"""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from voog._assets import (
    DERIVATIVE_LONG_SIDE_CAPS,
    expected_derivative_count,
    find_asset_by_filename,
    is_asset_complete,
    summarize_asset,
    wait_for_derivatives,
)

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "assets" / "derivative_sizes_live.json"


class TestDerivativeRuleAgainstLiveCapture(unittest.TestCase):
    """The cap rule is checked against Voog, not against a second copy of it.

    The pre-1.5 version of this file asserted
    ``DERIVATIVE_HEIGHT_CAPS == (150, 600, 1280, 2048)`` — a constant against
    its own literal, which passes no matter what Voog does. It did: the caps
    were right but applied to the wrong dimension, and the height-only rule
    disagreed with 529 of 1032 real images without a single red test.

    The fixture is a 30-asset sample of the kolm-koma-2026 library captured
    2026-08-13, chosen to include both boundary specimens per cap (largest
    original WITHOUT a given derivative, smallest one WITH it) and 12
    landscape images — the orientation the old rule mispredicted.
    """

    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        cls.assets = cls.fixture["assets"]

    def test_fixture_is_a_real_capture_not_a_stub(self):
        # Guards against the fixture being emptied or hand-edited into
        # agreement with whatever the code currently does.
        self.assertGreaterEqual(len(self.assets), 20)
        self.assertTrue(any(a["width"] > a["height"] for a in self.assets), "no landscape sample")
        self.assertTrue(any(a["height"] > a["width"] for a in self.assets), "no portrait sample")

    def test_predicted_count_matches_every_captured_asset(self):
        for asset in self.assets:
            with self.subTest(filename=asset["filename"]):
                self.assertEqual(
                    expected_derivative_count(asset["width"], asset["height"]),
                    len(asset["sizes"]),
                )

    def test_every_derivative_sits_exactly_on_its_cap(self):
        # This is WHY the rule is long-side based: Voog scales the longer
        # side down onto the cap and lets the other side follow.
        by_thumbnail = {"medium": 150, "block": 600, "large": 1280, "huge": 2048}
        seen = set()
        for asset in self.assets:
            for size in asset["sizes"]:
                cap = by_thumbnail[size["thumbnail"]]
                seen.add(size["thumbnail"])
                self.assertEqual(max(size["width"], size["height"]), cap, size)
        self.assertEqual(seen, set(by_thumbnail), "fixture must exercise every cap")

    def test_caps_constant_matches_the_capture(self):
        observed = sorted({max(s["width"], s["height"]) for a in self.assets for s in a["sizes"]})
        self.assertEqual(sorted(DERIVATIVE_LONG_SIDE_CAPS), observed)

    def test_height_only_rule_would_fail_this_capture(self):
        # Forward-failing guard: if someone reverts to deciding from height,
        # this test names the regression instead of going quietly green.
        def height_only(height):
            return sum(1 for cap in DERIVATIVE_LONG_SIDE_CAPS if height > cap)

        disagreements = [a for a in self.assets if height_only(a["height"]) != len(a["sizes"])]
        self.assertTrue(
            disagreements,
            "fixture no longer distinguishes the two rules — recapture it with "
            "landscape images before trusting this suite",
        )

    def test_landscape_asset_is_not_declared_complete_too_early(self):
        # The user-visible bug: a wide banner reported sizes_complete=True
        # while Voog was still building derivatives, so the caller built a
        # srcset from a partial list.
        wide = next(a for a in self.assets if a["width"] > a["height"] and len(a["sizes"]) >= 2)
        partial = dict(wide, sizes=wide["sizes"][:-1])
        self.assertFalse(is_asset_complete(partial))
        self.assertTrue(is_asset_complete(wide))


class TestExpectedDerivativeCount(unittest.TestCase):
    def test_longer_side_drives_the_count_in_either_orientation(self):
        # A banner and its transpose must agree; the old signature could not
        # express this because it only ever saw one dimension.
        self.assertEqual(expected_derivative_count(2000, 400), 3)
        self.assertEqual(expected_derivative_count(400, 2000), 3)

    def test_counts_across_every_cap_boundary(self):
        self.assertEqual(expected_derivative_count(1600, 2400), 4)
        self.assertEqual(expected_derivative_count(1300, 900), 3)
        self.assertEqual(expected_derivative_count(700, 500), 2)
        self.assertEqual(expected_derivative_count(200, 180), 1)
        self.assertEqual(expected_derivative_count(100, 80), 0)

    def test_a_dimension_exactly_on_a_cap_produces_no_derivative(self):
        # Strictly-greater, confirmed live: the largest original without a
        # `large` derivative measured exactly 1280 on its long side.
        self.assertEqual(expected_derivative_count(600, 400), 1)
        self.assertEqual(expected_derivative_count(1280, 720), 2)

    def test_missing_dimensions_are_zero_not_a_crash(self):
        self.assertEqual(expected_derivative_count(None, None), 0)
        self.assertEqual(expected_derivative_count(0, 0), 0)
        self.assertEqual(expected_derivative_count(None, 2400), 4)


class TestIsAssetComplete(unittest.TestCase):
    def test_record_without_width_is_not_complete(self):
        # Voog reports width and height together; a record carrying only one
        # is mid-description, and guessing from height alone is what shipped
        # the landscape bug.
        self.assertFalse(is_asset_complete({"id": 1, "height": 2400, "sizes": []}))
        self.assertFalse(is_asset_complete({"id": 1, "width": 2400, "sizes": []}))

    def test_non_dict_is_not_complete(self):
        self.assertFalse(is_asset_complete(None))
        self.assertFalse(is_asset_complete([{"id": 1}]))


class TestFindAssetByFilename(unittest.TestCase):
    def test_uses_exact_eq_filter(self):
        # $match / prefix filters are ignored by Voog and return the whole
        # library, which reads as "no match" to a careless caller.
        client = MagicMock()
        client.get.return_value = []
        find_asset_by_filename(client, "bag-NC-MEN-a.jpg")
        self.assertEqual(
            client.get.call_args.args[0],
            "/assets?q.asset.filename.$eq=bag-NC-MEN-a.jpg",
        )

    def test_filename_is_url_quoted(self):
        client = MagicMock()
        client.get.return_value = []
        find_asset_by_filename(client, "õ ja ä.jpg")
        self.assertIn("%20", client.get.call_args.args[0])
        self.assertNotIn(" ", client.get.call_args.args[0])

    def test_returns_only_done_assets(self):
        # A created-but-unconfirmed asset cannot be linked to anything, so
        # reusing one would produce a silently broken reference.
        client = MagicMock()
        client.get.return_value = [
            {"id": 1, "filename": "x.jpg", "status": "created"},
            {"id": 2, "filename": "x.jpg", "status": "done"},
        ]
        self.assertEqual(find_asset_by_filename(client, "x.jpg")["id"], 2)

    def test_ignores_server_side_near_matches(self):
        # Defense against the filter being ignored: Voog returning the full
        # library must not be read as "found it".
        client = MagicMock()
        client.get.return_value = [{"id": 9, "filename": "x-1.jpg", "status": "done"}]
        self.assertIsNone(find_asset_by_filename(client, "x.jpg"))

    def test_non_list_response_is_none(self):
        client = MagicMock()
        client.get.return_value = {"error": "nope"}
        self.assertIsNone(find_asset_by_filename(client, "x.jpg"))


class TestWaitForDerivatives(unittest.TestCase):
    def test_polls_until_sizes_complete(self):
        client = MagicMock()
        incomplete = {"id": 5, "width": 1600, "height": 2400, "sizes": [{"width": 450}]}
        complete = {
            "id": 5,
            "width": 1600,
            "height": 2400,
            "sizes": [{"width": w} for w in (113, 450, 960, 1536)],
        }
        client.get.side_effect = [incomplete, incomplete, complete]
        slept = []
        asset = wait_for_derivatives(client, 5, sleep=slept.append)
        self.assertEqual(asset, complete)
        self.assertEqual(client.get.call_count, 3)
        self.assertEqual(len(slept), 2)

    def test_never_requests_a_derivative_url(self):
        # THE rule: a derivative fetched before Voog made it returns 403 and
        # the CDN caches that 403 for ~1h, poisoning a URL that then becomes
        # valid. Only the API may be asked.
        client = MagicMock()
        client.get.return_value = {"id": 5, "width": 150, "height": 200, "sizes": [{"width": 113}]}
        wait_for_derivatives(client, 5, sleep=lambda _: None)
        for call in client.get.call_args_list:
            self.assertEqual(call.args[0], "/assets/5")

    def test_returns_last_seen_on_timeout(self):
        # The upload itself succeeded — returning a partial record beats
        # failing, because re-reading later fills the sizes in.
        client = MagicMock()
        partial = {"id": 7, "width": 1600, "height": 2400, "sizes": []}
        client.get.return_value = partial
        asset = wait_for_derivatives(client, 7, timeout_s=10, poll_s=5, sleep=lambda _: None)
        self.assertEqual(asset, partial)

    def test_no_dimensions_yet_keeps_polling(self):
        # Voog reports dimensions asynchronously too — a record without
        # them is not yet finished, even with an empty sizes array.
        client = MagicMock()
        client.get.side_effect = [
            {"id": 8, "sizes": []},
            {"id": 8, "width": 80, "height": 100, "sizes": []},
        ]
        asset = wait_for_derivatives(client, 8, sleep=lambda _: None)
        self.assertEqual(asset["height"], 100)

    def test_a_width_arriving_after_the_height_still_keeps_polling(self):
        # The landscape bug in miniature: height lands first, and deciding
        # from it alone would stop the poll one read too early.
        client = MagicMock()
        client.get.side_effect = [
            {"id": 9, "height": 400, "sizes": []},
            {
                "id": 9,
                "width": 2000,
                "height": 400,
                "sizes": [{"width": w} for w in (150, 600, 1280)],
            },
        ]
        asset = wait_for_derivatives(client, 9, sleep=lambda _: None)
        self.assertEqual(len(asset["sizes"]), 3)
        self.assertEqual(client.get.call_count, 2)


class TestSummarizeAsset(unittest.TestCase):
    def test_builds_public_paths_and_sorts_sizes(self):
        summary = summarize_asset(
            {
                "id": 42,
                "filename": "photo.jpg",
                "status": "done",
                "width": 1800,
                "height": 2400,
                "sizes": [
                    {"width": 960, "height": 1280, "filename": "photo_large.webp"},
                    {"width": 113, "height": 150, "filename": "photo_medium.webp"},
                ],
            }
        )
        self.assertEqual(summary["path"], "/photos/photo.jpg")
        self.assertEqual([s["width"] for s in summary["sizes"]], [113, 960])
        self.assertEqual(summary["sizes"][0]["path"], "/photos/photo_medium.webp")

    def test_survives_a_bare_record(self):
        summary = summarize_asset({"id": 1})
        self.assertEqual(summary["sizes"], [])
        self.assertEqual(summary["path"], "")


if __name__ == "__main__":
    unittest.main()
