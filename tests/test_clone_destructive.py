"""Tests for the clone's destructive and resume-critical paths.

The PR #142 review ran a 20-mutation sweep against the first version of this
feature and **13 mutations survived** — every one of them in code that
destroys or carries content: "never delete", "copy no article body", "write
an empty body to every layout", "put every gallery entry at position 0".
`contents`, `articles`, `cleanup` and `layout_assets` had no tests at all.

This file exists to kill those mutations. Every test here names the specific
wrong behaviour it forbids, and none of them touch the network — `_download`
is patched, because the earlier budget test opened eight real TLS
connections to media.voog.com and passed identically with the network cut.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests.test_clone_phases import FakeVoog, _run, _source_site, _target_site


def _no_network():
    """Patch the clone's byte fetcher. No test here may reach the internet."""
    return patch("voog.clone.phases._download", return_value=b"bytes")


def _no_upload():
    return patch(
        "voog.clone.phases._upload_asset_bytes",
        side_effect=lambda tgt, *, filename, content_type, blob: {
            "id": abs(hash(filename)) % 100000,
            "filename": filename,
            "public_url": f"https://media.voog.com/0000/0053/4382/photos/{filename}",
        },
    )


def _page_with_contents(source, *, areas):
    source.contents["/pages/200/contents"] = areas


def _text_area(name, text_id, position=1):
    return {
        "id": text_id,
        "name": name,
        "content_type": "text",
        "position": position,
        "text": {"id": text_id},
    }


def _gallery_area(name, asset_ids, position=2):
    return {
        "id": 555,
        "name": name,
        "content_type": "gallery",
        "position": position,
        "gallery": {
            "kind": "grid",
            "settings": {},
            "assets": [
                {"id": a, "position": n + 1, "title": f"t{n}", "settings": {}}
                for n, a in enumerate(asset_ids)
            ],
        },
    }


class TestContentsAreNotSilentlyDestroyed(unittest.TestCase):
    """`_sync_contents` DELETES the target's areas before recreating them.

    The review's blocker #1: `contents_done` was written unconditionally, so
    a parent whose deletes succeeded and whose POSTs all failed was recorded
    as finished with zero areas. The next run skipped it, reported
    `skipped=1` with no problems, and the page stayed empty forever.
    """

    def _setup(self, *, post_fails):
        src = _source_site()
        src.texts[42] = "<p>body</p>"
        _page_with_contents(src, areas=[_text_area("body", 42)])
        tgt = _target_site(
            pages=[
                {
                    "id": 700,
                    "title": "Old",
                    "path": "",
                    "slug": "",
                    "content_type": "page",
                    "node": {"id": 1, "parent_id": None},
                }
            ]
        )
        # The target already has content that the rebuild will delete.
        tgt.contents["/pages/700/contents"] = [
            {"id": 901, "name": "old-body", "content_type": "text"}
        ]
        if post_fails:
            real_post = tgt.post

            def _post(path, body=None, **kw):
                if path.endswith("/contents"):
                    raise RuntimeError("502 Bad Gateway")
                return real_post(path, body, **kw)

            tgt.post = _post
        return src, tgt

    def test_deletes_actually_happen(self):
        # Kills the "never delete" mutation: without the delete the target
        # keeps its old area and ends up with both.
        src, tgt = self._setup(post_fails=False)
        with TemporaryDirectory() as tmp:
            _run(src, tgt, ["site", "pages", "contents"], state_dir=tmp)
        self.assertIn("/pages/700/contents/901", tgt.deleted)

    def test_a_failed_rebuild_is_not_marked_done(self):
        src, tgt = self._setup(post_fails=True)
        with TemporaryDirectory() as tmp:
            result = _run(src, tgt, ["site", "pages", "contents"], state_dir=tmp)
            # The language-level areas were empty and legitimately complete,
            # so the file exists — what must be absent is the PAGE entry.
            done = json.loads((Path(tmp) / "contents_done.json").read_text())
            self.assertNotIn(
                "200", done, "a parent whose areas were destroyed must not be marked done"
            )
        reasons = " ".join(
            p["reason"] for r in result["reports"] for p in (r.get("problems") or [])
        )
        self.assertIn("DESTRUCTIVE PARTIAL", reasons)

    def test_a_failed_rebuild_is_retried_on_the_next_run(self):
        src, tgt = self._setup(post_fails=True)
        with TemporaryDirectory() as tmp:
            _run(src, tgt, ["site", "pages", "contents"], state_dir=tmp)
            # Voog recovers; the resume must actually rebuild.
            tgt.post = FakeVoog.post.__get__(tgt)
            second = _run(src, tgt, ["contents"], state_dir=tmp)
        contents = {r["phase"]: r for r in second["reports"]}["contents"]
        self.assertEqual(contents["created"], 1, "the destroyed page must be rebuilt")

    def test_a_successful_rebuild_is_marked_done_and_skipped_after(self):
        src, tgt = self._setup(post_fails=False)
        with TemporaryDirectory() as tmp:
            _run(src, tgt, ["site", "pages", "contents"], state_dir=tmp)
            second = _run(src, tgt, ["contents"], state_dir=tmp)
        contents = {r["phase"]: r for r in second["reports"]}["contents"]
        self.assertEqual(contents["created"], 0)
        self.assertGreaterEqual(contents["skipped"], 1)

    def test_text_bodies_are_copied_and_rewritten(self):
        # Kills "copy no body" and "skip the rewrite".
        src, tgt = self._setup(post_fails=False)
        src.texts[42] = '<a href="https://source.voog.com/tood">x</a>'
        with TemporaryDirectory() as tmp:
            _run(src, tgt, ["site", "pages", "contents"], state_dir=tmp)
        bodies = list(tgt.texts.values())
        self.assertTrue(any('href="/tood"' in b for b in bodies), bodies)
        self.assertFalse(any("source.voog.com" in b for b in bodies), bodies)


class TestGalleryPositions(unittest.TestCase):
    def test_every_gallery_entry_carries_an_explicit_1_based_position(self):
        # Kills the "position: 0" mutation, which is the v1.4.4 regression
        # re-implemented: without explicit 1-based positions Voog does not
        # apply the requested gallery order at all.
        src = _source_site()
        _page_with_contents(src, areas=[_gallery_area("img", [1, 2, 3])])
        src.assets = [
            {
                "id": i,
                "filename": f"{i}.jpg",
                "size": 10,
                "type": "image",
                "public_url": f"https://media.voog.com/0000/0047/4574/photos/{i}.jpg",
            }
            for i in (1, 2, 3)
        ]
        tgt = _target_site()
        with TemporaryDirectory() as tmp, _no_network(), _no_upload():
            _run(src, tgt, ["assets", "site", "pages", "contents"], state_dir=tmp)
        self.assertTrue(tgt.media_sets, "no gallery was written")
        entries = next(iter(tgt.media_sets.values()))
        self.assertEqual([e["position"] for e in entries], [1, 2, 3])

    def test_an_image_missing_from_asset_map_is_reported_and_the_rest_renumbered(self):
        src = _source_site()
        _page_with_contents(src, areas=[_gallery_area("img", [1, 2, 3])])
        src.assets = [
            {
                "id": 1,
                "filename": "1.jpg",
                "size": 10,
                "type": "image",
                "public_url": "https://media.voog.com/0000/0047/4574/photos/1.jpg",
            }
        ]
        tgt = _target_site()
        with TemporaryDirectory() as tmp, _no_network(), _no_upload():
            result = _run(src, tgt, ["assets", "site", "pages", "contents"], state_dir=tmp)
        entries = next(iter(tgt.media_sets.values()))
        # No hole in the sequence for Voog to interpret.
        self.assertEqual([e["position"] for e in entries], list(range(1, len(entries) + 1)))
        reasons = " ".join(
            p["reason"] for r in result["reports"] for p in (r.get("problems") or [])
        )
        self.assertIn("asset_map", reasons)


class TestArticles(unittest.TestCase):
    """Review blocker #2: `POST /articles` does not carry the body.

    Recording the article as done at creation time meant a failure on the
    following body PUT left an empty stub marked complete — `created: 1`,
    then `skipped: 1` on every resume, with the content gone for good.
    """

    def _setup(self, *, put_fails=False):
        src = _source_site()
        src.articles = [{"id": 800, "title": "Post", "created_at": "2024-01-01"}]
        src.article_detail = {
            "id": 800,
            "title": "Post",
            "path": "post",
            "published": True,
            "body": '<p>See <a href="https://source.voog.com/x">this</a></p>',
            "excerpt": "e",
            "data": {},
            "tag_names": ["t"],
            "page": {"id": 200},
            "image": None,
        }
        real_get = src.get

        def _get(path, **kw):
            if path == "/articles/800":
                return src.article_detail
            if path == "/articles/800/contents":
                return []
            return real_get(path, **kw)

        src.get = _get
        tgt = _target_site()
        if put_fails:
            real_put = tgt.put

            def _put(path, body=None, **kw):
                if path.startswith("/articles/"):
                    raise RuntimeError("503 Service Unavailable")
                return real_put(path, body, **kw)

            tgt.put = _put
        return src, tgt

    def test_the_article_body_is_written_and_rewritten(self):
        # Kills "copy no article body".
        src, tgt = self._setup()
        with TemporaryDirectory() as tmp:
            _run(src, tgt, ["site", "pages", "articles"], state_dir=tmp)
        bodies = [b.get("autosaved_body") for _p, b in tgt.article_puts]
        self.assertTrue(any("this" in (b or "") for b in bodies), tgt.article_puts)
        self.assertFalse(any("source.voog.com" in (b or "") for b in bodies))

    def test_a_body_write_failure_leaves_the_article_unfinished_not_done(self):
        src, tgt = self._setup(put_fails=True)
        with TemporaryDirectory() as tmp:
            result = _run(src, tgt, ["site", "pages", "articles"], state_dir=tmp)
            self.assertFalse(
                (Path(tmp) / "article_map.json").exists(),
                "an article without its body must not be marked done",
            )
            self.assertTrue(
                (Path(tmp) / "article_shell.json").exists(),
                "the created article's id must be recorded so a resume "
                "continues it rather than creating a duplicate",
            )
        reasons = " ".join(
            p["reason"] for r in result["reports"] for p in (r.get("problems") or [])
        )
        self.assertIn("BODY", reasons)
        articles = {r["phase"]: r for r in result["reports"]}["articles"]
        self.assertEqual(articles["created"], 0, "an empty stub is not a created article")

    def test_a_resume_finishes_the_stub_without_creating_a_duplicate(self):
        src, tgt = self._setup(put_fails=True)
        with TemporaryDirectory() as tmp:
            _run(src, tgt, ["site", "pages", "articles"], state_dir=tmp)
            posted_before = len([p for p, _b in tgt.posted if p == "/articles"])
            tgt.put = FakeVoog.put.__get__(tgt)
            second = _run(src, tgt, ["articles"], state_dir=tmp)
            posted_after = len([p for p, _b in tgt.posted if p == "/articles"])
        self.assertEqual(posted_before, 1)
        self.assertEqual(posted_after, 1, "the resume must not POST a second article")
        articles = {r["phase"]: r for r in second["reports"]}["articles"]
        self.assertEqual(articles["created"], 1)

    def test_a_source_draft_stays_a_draft(self):
        src, tgt = self._setup()
        src.article_detail["published"] = False
        with TemporaryDirectory() as tmp:
            _run(src, tgt, ["site", "pages", "articles"], state_dir=tmp)
        publishing = [
            b.get("publishing") for _p, b in tgt.article_puts if "publishing" in (b or {})
        ]
        self.assertEqual(publishing, [False], "a draft must not be published by the clone")

    def test_a_missing_published_field_is_treated_as_draft(self):
        src, tgt = self._setup()
        del src.article_detail["published"]
        with TemporaryDirectory() as tmp:
            _run(src, tgt, ["site", "pages", "articles"], state_dir=tmp)
        publishing = [
            b.get("publishing") for _p, b in tgt.article_puts if "publishing" in (b or {})
        ]
        self.assertEqual(publishing, [False])


class TestLayouts(unittest.TestCase):
    def test_layout_bodies_are_copied_and_rewritten(self):
        # Kills "write an empty body to every target layout" and the
        # separate gap the review found: layout bodies were never rewritten,
        # so every cloned template kept pulling images from the source site.
        src = _source_site()
        src.layouts[0]["body"] = (
            '<img src="https://media.voog.com/0000/0047/4574/photos/x.jpg">'
            '<a href="https://source.voog.com/tood">t</a>'
        )
        tgt = _target_site()
        with TemporaryDirectory() as tmp:
            _run(src, tgt, ["layouts"], state_dir=tmp)
        written = [b.get("body") for p, b in tgt.puts if p.startswith("/layouts/")]
        self.assertTrue(written, "no layout body was written")
        self.assertIn("0000/0053/4382", written[0])
        self.assertIn('href="/tood"', written[0])
        self.assertNotIn("0000/0047/4574", written[0])

    def test_layouts_sharing_a_title_but_not_a_content_type_stay_separate(self):
        # Keying on title alone collapsed a `page` and a `blog` layout onto
        # one target layout; the second body overwrote the first and the
        # report said `updated: 2` with no problem recorded.
        src = _source_site()
        src.layouts = [
            {"id": 10, "title": "Front", "content_type": "page", "component": False, "body": "P"},
            {"id": 11, "title": "Front", "content_type": "blog", "component": False, "body": "B"},
        ]
        tgt = _target_site()
        with TemporaryDirectory() as tmp:
            _run(src, tgt, ["layouts"], state_dir=tmp)
        # Both bodies existing is NOT the property: the old title-only key
        # PUT them both at the SAME target layout, so the second overwrote
        # the first and both strings still showed up in the call log. What
        # matters is that they landed on two DIFFERENT target layouts.
        targets = [p for p, _b in tgt.puts if p.startswith("/layouts/")]
        targets += [f"/layouts/{tgt.layouts[-1]['id']}" for p, _b in tgt.posted if p == "/layouts"]
        self.assertEqual(
            len(set(targets)),
            2,
            f"the two layouts collapsed onto one target: {targets}",
        )


class TestLayoutAssets(unittest.TestCase):
    def test_text_asset_data_is_copied_and_rewritten(self):
        src = _source_site()
        src.layout_assets = [{"id": 1, "filename": "site.css", "editable": True}]
        real_get = src.get

        def _get(path, **kw):
            if path == "/layout_assets/1":
                return {
                    "id": 1,
                    "filename": "site.css",
                    "data": "body{background:url(https://media.voog.com/0000/0047/4574/p/bg.png)}",
                }
            return real_get(path, **kw)

        src.get = _get
        tgt = _target_site()
        with TemporaryDirectory() as tmp:
            _run(src, tgt, ["layout_assets"], state_dir=tmp)
        written = [b.get("data") for p, b in tgt.posted if p == "/layout_assets"]
        self.assertTrue(written, "no layout asset was written")
        self.assertIn("0000/0053/4382", written[0])


class TestCleanup(unittest.TestCase):
    def test_a_layout_still_used_by_a_page_is_kept(self):
        src = _source_site()
        tgt = _target_site(
            layouts=[
                {"id": 50, "title": "Front", "content_type": "page", "component": False},
                {"id": 51, "title": "Orphan", "content_type": "page", "component": False},
            ],
            pages=[
                {"id": 700, "path": "x", "layout": {"id": 51}, "node": {"id": 1, "parent_id": None}}
            ],
        )
        with TemporaryDirectory() as tmp:
            _run(src, tgt, ["cleanup"], state_dir=tmp)
        self.assertNotIn("/layouts/51", tgt.deleted, "an in-use layout must never be deleted")

    def test_an_unused_target_only_layout_is_deleted(self):
        src = _source_site()
        tgt = _target_site(
            layouts=[
                {"id": 50, "title": "Front", "content_type": "page", "component": False},
                {"id": 52, "title": "Leftover", "content_type": "page", "component": False},
            ],
        )
        with TemporaryDirectory() as tmp:
            _run(src, tgt, ["cleanup"], state_dir=tmp)
        self.assertIn("/layouts/52", tgt.deleted)

    def test_a_layout_the_clone_itself_created_is_kept(self):
        src = _source_site()
        tgt = _target_site()
        with TemporaryDirectory() as tmp:
            _run(src, tgt, ["layouts", "cleanup"], state_dir=tmp)
        self.assertEqual([d for d in tgt.deleted if d.startswith("/layouts/")], [])


class TestMenuOrder(unittest.TestCase):
    def test_a_pure_resume_does_not_reorder_the_menu(self):
        # Re-running it costs two requests per page and silently reverts any
        # ordering the operator has done on the target since.
        src, tgt = _source_site(), _target_site()
        with TemporaryDirectory() as tmp:
            _run(src, tgt, ["site", "pages"], state_dir=tmp)
            before = len([p for p, _b in tgt.puts if "/move" in p])
            second = _run(src, tgt, ["pages"], state_dir=tmp)
            after = len([p for p, _b in tgt.puts if "/move" in p])
        self.assertEqual(before, after, "no node should be moved on a pure resume")
        notes = " ".join({r["phase"]: r for r in second["reports"]}["pages"].get("notes") or [])
        self.assertIn("menu order not touched", notes)


if __name__ == "__main__":
    unittest.main()


class TestQuotaErrorDetection(unittest.TestCase):
    """`_is_quota_error` must read the response BODY, not `str(exc)`.

    `VoogClient` raises a bare `httpx.HTTPStatusError` whose `str()` is the
    status-and-URL line only; `quota_exceeded` lives in the body. Checking
    the plain string matched nothing, which made the "stops cleanly rather
    than hitting 422 partway through" promise false in exactly the mode that
    motivated it — a plan whose cap Voog does not advertise, where the 422
    is the only signal there is (PR #142 review, blocker #3).
    """

    def _voog_422(self):
        import httpx

        request = httpx.Request("POST", "https://t.voog.com/admin/api/assets")
        response = httpx.Response(
            422,
            request=request,
            json={"message": "Quota exceeded", "errors": {"base": ["quota_exceeded"]}},
        )
        return httpx.HTTPStatusError("Client error '422'", request=request, response=response)

    def test_a_real_voog_quota_422_is_recognised(self):
        from voog.clone.phases import _is_quota_error

        exc = self._voog_422()
        # The failure mode being guarded: the status line alone says nothing.
        self.assertNotIn("quota", str(exc).lower())
        self.assertTrue(_is_quota_error(exc))

    def test_an_unrelated_422_is_not_mistaken_for_a_quota_error(self):
        import httpx

        from voog.clone.phases import _is_quota_error

        request = httpx.Request("POST", "https://t.voog.com/admin/api/pages")
        response = httpx.Response(
            422, request=request, json={"errors": {"layout_id": ["not in available layouts list"]}}
        )
        exc = httpx.HTTPStatusError("Client error '422'", request=request, response=response)
        self.assertFalse(_is_quota_error(exc))

    def test_the_first_quota_refusal_stops_the_rest_of_the_batch(self):
        src = _source_site()
        src.assets = [
            {
                "id": i,
                "filename": f"f{i}.jpg",
                "size": 10,
                "type": "image",
                "public_url": f"https://media.voog.com/0000/0047/4574/photos/f{i}.jpg",
            }
            for i in range(1, 13)
        ]
        tgt = _target_site()
        attempts = {"n": 0}

        def _upload(t, *, filename, content_type, blob):
            attempts["n"] += 1
            raise self._voog_422()

        with (
            TemporaryDirectory() as tmp,
            _no_network(),
            patch("voog.clone.phases._upload_asset_bytes", side_effect=_upload),
        ):
            result = _run(src, tgt, ["assets"], state_dir=tmp, max_workers=2)
        # Bounded by the worker pool, not by the number of assets: without
        # the in-worker stop every one of the 12 would have tried.
        self.assertLess(attempts["n"], 12, f"{attempts['n']} uploads attempted after the refusal")
        assets_report = {r["phase"]: r for r in result["reports"]}["assets"]
        self.assertGreater(assets_report.get("not_uploaded_due_to_quota", 0), 0)


class TestArticleCompletionIsRecorded(unittest.TestCase):
    def test_a_fully_copied_article_is_skipped_on_the_next_run(self):
        # Kills "record the shell but never the map": without article_map
        # every resume would re-do the body, cover and contents of every
        # article that already finished.
        src = _source_site()
        src.articles = [{"id": 800, "title": "Post", "created_at": "2024-01-01"}]
        detail = {
            "id": 800,
            "title": "Post",
            "path": "post",
            "published": True,
            "body": "<p>b</p>",
            "excerpt": "",
            "data": {},
            "tag_names": [],
            "page": {"id": 200},
            "image": None,
        }
        real_get = src.get

        def _get(path, **kw):
            if path == "/articles/800":
                return detail
            if path == "/articles/800/contents":
                return []
            return real_get(path, **kw)

        src.get = _get
        tgt = _target_site()
        with TemporaryDirectory() as tmp:
            first = _run(src, tgt, ["site", "pages", "articles"], state_dir=tmp)
            self.assertTrue(
                (Path(tmp) / "article_map.json").exists(),
                "a finished article must be recorded as done",
            )
            puts_before = len(tgt.article_puts)
            second = _run(src, tgt, ["articles"], state_dir=tmp)
        self.assertEqual({r["phase"]: r for r in first["reports"]}["articles"]["created"], 1)
        articles = {r["phase"]: r for r in second["reports"]}["articles"]
        self.assertEqual(articles["created"], 0)
        self.assertEqual(articles["skipped"], 1)
        self.assertEqual(len(tgt.article_puts), puts_before, "no re-write on a resume")
