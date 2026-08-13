"""Unit tests for voog._assets — asset lookup + derivative waiting.

These encode two Voog behaviours that are expensive to rediscover: a repeated
filename produces a duplicate rather than an overwrite, and derivative URLs
must never be probed over HTTP before the API says they exist.
"""

import unittest
from unittest.mock import MagicMock

from voog._assets import (
    DERIVATIVE_HEIGHT_CAPS,
    expected_derivative_count,
    find_asset_by_filename,
    summarize_asset,
    wait_for_derivatives,
)


class TestExpectedDerivativeCount(unittest.TestCase):
    def test_caps_are_the_documented_heights(self):
        # Voog caps derivative HEIGHT at these values; widths follow the
        # source aspect ratio and must never be assumed.
        self.assertEqual(DERIVATIVE_HEIGHT_CAPS, (150, 600, 1280, 2048))

    def test_taller_original_yields_more_derivatives(self):
        self.assertEqual(expected_derivative_count(2400), 4)
        self.assertEqual(expected_derivative_count(1300), 3)
        self.assertEqual(expected_derivative_count(700), 2)
        self.assertEqual(expected_derivative_count(200), 1)
        self.assertEqual(expected_derivative_count(100), 0)

    def test_missing_height_is_zero_not_a_crash(self):
        self.assertEqual(expected_derivative_count(None), 0)
        self.assertEqual(expected_derivative_count(0), 0)


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
        incomplete = {"id": 5, "height": 2400, "sizes": [{"width": 450}]}
        complete = {"id": 5, "height": 2400, "sizes": [{"width": w} for w in (113, 450, 960, 1536)]}
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
        client.get.return_value = {"id": 5, "height": 200, "sizes": [{"width": 113}]}
        wait_for_derivatives(client, 5, sleep=lambda _: None)
        for call in client.get.call_args_list:
            self.assertEqual(call.args[0], "/assets/5")

    def test_returns_last_seen_on_timeout(self):
        # The upload itself succeeded — returning a partial record beats
        # failing, because re-reading later fills the sizes in.
        client = MagicMock()
        partial = {"id": 7, "height": 2400, "sizes": []}
        client.get.return_value = partial
        asset = wait_for_derivatives(client, 7, timeout_s=10, poll_s=5, sleep=lambda _: None)
        self.assertEqual(asset, partial)

    def test_no_height_yet_keeps_polling(self):
        # Voog reports dimensions asynchronously too — a record without
        # height is not yet finished, even with an empty sizes array.
        client = MagicMock()
        client.get.side_effect = [
            {"id": 8, "sizes": []},
            {"id": 8, "height": 100, "sizes": []},
        ]
        asset = wait_for_derivatives(client, 8, sleep=lambda _: None)
        self.assertEqual(asset["height"], 100)


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
