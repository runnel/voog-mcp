"""Tests for voog.clone.rewrite.

Left un-rewritten, a clone renders perfectly *while serving every image from
the site it was copied from*. It looks like success, keeps working until the
source is taken down, and makes the clone useless as a standalone site — so
these are the tests for the failure that does not announce itself.
"""

import unittest

from voog.clone.rewrite import UrlRewriter, media_prefix_of


def _rewriter(
    hosts=("kolmkoma.ee",), src="media.voog.com/0000/0047/4574", tgt="media.voog.com/0000/0053/4382"
):
    return UrlRewriter(source_hosts=list(hosts), source_media_prefix=src, target_media_prefix=tgt)


class TestMediaPrefix(unittest.TestCase):
    def test_extracts_the_per_site_storage_triple(self):
        assets = [
            {"public_url": "https://media.voog.com/0000/0053/4382/photos/a.jpg"},
        ]
        self.assertEqual(media_prefix_of(assets), "media.voog.com/0000/0053/4382")

    def test_empty_library_yields_none_not_a_guess(self):
        # Callers must read None as "do not rewrite media URLs". Substituting
        # a guessed prefix would point every image at a site that isn't ours.
        self.assertIsNone(media_prefix_of([]))
        self.assertIsNone(media_prefix_of([{"public_url": "https://cdn.example.tld/x.jpg"}]))
        self.assertIsNone(media_prefix_of(None))

    def test_skips_malformed_entries(self):
        assets = [
            None,
            "nope",
            {},
            {"public_url": None},
            {"public_url": "https://media.voog.com/0000/0001/0002/photos/z.png"},
        ]
        self.assertEqual(media_prefix_of(assets), "media.voog.com/0000/0001/0002")


class TestMediaUrlRewriting(unittest.TestCase):
    def test_source_cdn_prefix_becomes_the_target_one(self):
        html = '<img src="https://media.voog.com/0000/0047/4574/photos/hero.jpg">'
        self.assertIn("0000/0053/4382", _rewriter().text(html))
        self.assertNotIn("0000/0047/4574", _rewriter().text(html))

    def test_nothing_is_rewritten_when_a_prefix_is_unknown(self):
        html = '<img src="https://media.voog.com/0000/0047/4574/photos/hero.jpg">'
        self.assertEqual(_rewriter(src=None).text(html), html)
        self.assertEqual(_rewriter(tgt=None).text(html), html)

    def test_identical_prefixes_are_a_no_op(self):
        r = _rewriter(src="media.voog.com/0000/1/2", tgt="media.voog.com/0000/1/2")
        self.assertFalse(r.rewrites_media)


class TestHostRewriting(unittest.TestCase):
    def test_absolute_source_links_become_root_relative(self):
        self.assertEqual(
            _rewriter().text('<a href="https://kolmkoma.ee/tood">x</a>'), '<a href="/tood">x</a>'
        )

    def test_every_spelling_of_the_host_is_caught(self):
        # Content authored over years carries all of these; rewriting only
        # the canonical form leaves the rest pointing home.
        for url in (
            "https://www.kolmkoma.ee/meist",
            "http://www.kolmkoma.ee/meist",
            "https://kolmkoma.ee/meist",
            "http://kolmkoma.ee/meist",
            "//kolmkoma.ee/meist",
            "//www.kolmkoma.ee/meist",
        ):
            with self.subTest(url=url):
                self.assertEqual(
                    _rewriter().text(f'<a href="{url}">x</a>'), '<a href="/meist">x</a>'
                )

    def test_the_bare_origin_becomes_a_single_slash(self):
        # Not "//", which a browser reads as protocol-relative and resolves
        # against the host named by the next path segment.
        self.assertEqual(
            _rewriter().text('<a href="https://kolmkoma.ee">home</a>'), '<a href="/">home</a>'
        )
        self.assertNotIn("//", _rewriter().text('<a href="https://kolmkoma.ee/">home</a>'))

    def test_a_www_source_host_also_matches_the_apex(self):
        r = _rewriter(hosts=("www.kolmkoma.ee",))
        self.assertEqual(r.text('<a href="https://kolmkoma.ee/x">y</a>'), '<a href="/x">y</a>')

    def test_other_hosts_are_left_alone(self):
        html = '<a href="https://voog.com/help">docs</a>'
        self.assertEqual(_rewriter().text(html), html)

    def test_a_longer_host_is_consumed_before_its_substring(self):
        # `https://x.ee` contains `//x.ee`; consuming the short form first
        # would strand a bare `https:` in front of a now-relative path.
        out = _rewriter().text('<a href="https://kolmkoma.ee/a">a</a>')
        self.assertNotIn("https:", out)


class TestDataRewriting(unittest.TestCase):
    def test_urls_nested_anywhere_in_a_data_hash_are_rewritten(self):
        data = {
            "gallery": {"photos": [{"src": "https://kolmkoma.ee/photos/a.jpg"}]},
            "style": {"bg": "url(https://media.voog.com/0000/0047/4574/photos/bg.png)"},
            "unrelated": 42,
            "flag": True,
        }
        out = _rewriter().data(data)
        self.assertEqual(out["gallery"]["photos"][0]["src"], "/photos/a.jpg")
        self.assertIn("0000/0053/4382", out["style"]["bg"])
        # Non-string values survive the JSON round trip unchanged.
        self.assertEqual(out["unrelated"], 42)
        self.assertIs(out["flag"], True)

    def test_estonian_characters_survive_the_round_trip(self):
        data = {"title": "Töö ja õu — ülevaade", "note": "šokolaad žanr"}
        self.assertEqual(_rewriter().data(data), data)

    def test_quotes_and_backslashes_survive(self):
        # The rewrite runs on serialised JSON, so a substitution must never
        # be able to produce something that fails to parse back.
        data = {"raw": 'He said "hi" \\ then left', "path": "C:\\tmp\\x"}
        self.assertEqual(_rewriter().data(data), data)

    def test_empty_inputs_pass_through(self):
        self.assertIsNone(_rewriter().data(None))
        self.assertEqual(_rewriter().data({}), {})
        self.assertIsNone(_rewriter().text(None))
        self.assertEqual(_rewriter().text(""), "")


class TestNoSourceHosts(unittest.TestCase):
    def test_a_rewriter_without_hosts_only_touches_media(self):
        r = UrlRewriter(
            source_hosts=[],
            source_media_prefix="media.voog.com/0000/0047/4574",
            target_media_prefix="media.voog.com/0000/0053/4382",
        )
        html = '<a href="https://kolmkoma.ee/x">y</a> <img src="https://media.voog.com/0000/0047/4574/p.jpg">'
        out = r.text(html)
        self.assertIn("https://kolmkoma.ee/x", out)
        self.assertIn("0000/0053/4382", out)

    def test_blank_host_entries_are_ignored(self):
        r = UrlRewriter(
            source_hosts=["", None, "kolmkoma.ee"],
            source_media_prefix=None,
            target_media_prefix=None,
        )
        self.assertEqual(r.text('<a href="https://kolmkoma.ee/a">a</a>'), '<a href="/a">a</a>')


if __name__ == "__main__":
    unittest.main()
