"""Tests for voog.clone.phases against a stateful fake Voog.

The fake stores what it is told and answers reads from that store, so these
exercise real phase behaviour — resume, idempotence, quota stops, honest
reporting — rather than asserting that a mock was called.

Live counterpart: the write path was run end-to-end against the real
kolm-koma-2026 site on 2026-08-13 (one page, one text area, one 4-image
gallery, created, verified, re-run for resume, deleted). Two defects found
that way are pinned here: a dry run could not resolve the language map, and
``POST /pages`` is rejected without a valid ``layout_id``.
"""

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from voog.clone import resolve_phases, run_clone
from voog.clone.phases import PHASE_ORDER, CloneContext, PhaseReport, quota_of
from voog.clone.state import CloneState


class FakeVoog:
    """Minimal stateful stand-in for one Voog site."""

    def __init__(
        self,
        *,
        host,
        pages=None,
        layouts=None,
        languages=None,
        assets=None,
        site=None,
        contents=None,
        articles=None,
        layout_assets=None,
    ):
        self.host = host
        self.site_name = host.split(".")[0]
        self.pages = list(pages or [])
        self.layouts = list(layouts or [])
        self.languages = list(languages or [])
        self.assets = list(assets or [])
        self.layout_assets = list(layout_assets or [])
        self.articles = list(articles or [])
        self.site = site or {"primary_domain": host, "public_url": f"https://{host}/", "data": {}}
        self.contents = dict(contents or {})
        self.texts = {}
        self.media_sets = {}
        self.deleted = []
        self.posted = []
        # Every PUT, so destructive-path tests can assert on what was
        # actually written rather than on a mock having been called.
        self.puts = []
        self.article_puts = []
        self._next_id = 9000
        # propagate_tool_context snapshots this when the assets phase fans
        # out uploads across threads.
        self._local = threading.local()

    def _new_id(self):
        self._next_id += 1
        return self._next_id

    # ------------------------------------------------------------- reads
    def get_all(self, path, **kw):
        return {
            "/pages": self.pages,
            "/layouts": self.layouts,
            "/languages": self.languages,
            "/assets": self.assets,
            "/layout_assets": self.layout_assets,
            "/articles": self.articles,
        }[path.split("?")[0]]

    def get(self, path, **kw):
        base = path.split("?")[0]
        if base == "/site":
            return self.site
        if base == "/assets":
            return self.assets[:1]
        if base.startswith("/media_sets/"):
            return {
                "id": int(base.rsplit("/", 1)[1]),
                "assets": self.media_sets.get(int(base.rsplit("/", 1)[1]), []),
            }
        if base.startswith("/texts/"):
            return {
                "id": int(base.rsplit("/", 1)[1]),
                "body": self.texts.get(int(base.rsplit("/", 1)[1]), ""),
            }
        if base.endswith("/contents"):
            return self.contents.get(base, [])
        if base.startswith("/pages/"):
            pid = int(base.rsplit("/", 1)[1])
            for page in self.pages:
                if page["id"] == pid:
                    return page
        raise RuntimeError(f"FakeVoog: unexpected GET {path}")

    # ------------------------------------------------------------ writes
    def post(self, path, body=None, **kw):
        self.posted.append((path, body))
        if path == "/pages":
            if not (body or {}).get("layout_id"):
                raise RuntimeError("422 layout_id not in available layouts list")
            page = {
                "id": self._new_id(),
                "node": {"id": self._new_id(), "parent_id": 1},
                **(body or {}),
            }
            page["path"] = (body or {}).get("slug") or ""
            self.pages.append(page)
            return page
        if path == "/layouts":
            layout = {"id": self._new_id(), **(body or {})}
            self.layouts.append(layout)
            return layout
        if path == "/articles":
            # Voog does NOT store the body from this call — it arrives in
            # the PUT that follows. Modelling that is the whole point of the
            # article two-stage state.
            article = {"id": self._new_id(), "path": (body or {}).get("path")}
            self.articles.append(article)
            return article
        if path == "/layout_assets":
            asset = {"id": self._new_id(), **(body or {})}
            self.layout_assets.append(asset)
            return asset
        if path.endswith("/contents"):
            kind = (body or {}).get("content_type")
            made = {"id": self._new_id(), **(body or {})}
            if kind == "text":
                tid = self._new_id()
                made["text"] = {"id": tid}
                self.texts[tid] = ""
            else:
                gid = self._new_id()
                made["gallery"] = {"id": gid}
                self.media_sets[gid] = []
            self.contents.setdefault(path, []).append(made)
            return made
        return {"id": self._new_id()}

    def put(self, path, body=None, **kw):
        self.puts.append((path, body))
        if path.startswith("/articles/"):
            self.article_puts.append((path, body))
            return {"id": int(path.rsplit("/", 1)[1])}
        if path.startswith("/texts/"):
            self.texts[int(path.rsplit("/", 1)[1])] = (body or {}).get("body", "")
            return {"id": 1}
        if path.startswith("/media_sets/"):
            gid = int(path.rsplit("/", 1)[1])
            self.media_sets[gid] = [
                {"id": e["id"], "position": e.get("position"), "title": e.get("title")}
                for e in (body or {}).get("assets", [])
            ]
            return {"id": gid}
        return {"id": 1}

    def delete(self, path, **kw):
        self.deleted.append(path)
        return None


def _lang(id_, code="et", title="EST"):
    return {"id": id_, "code": code, "title": title}


def _source_site():
    return FakeVoog(
        host="source.voog.com",
        languages=[_lang(100)],
        layouts=[
            {
                "id": 10,
                "title": "Front",
                "content_type": "page",
                "component": False,
                "body": "<p>hi</p>",
            }
        ],
        pages=[
            {
                "id": 200,
                "title": "Home",
                "slug": "",
                "path": "",
                "content_type": "page",
                "hidden": False,
                "data": {},
                "language": {"id": 100},
                "node": {"id": 300, "parent_id": None, "position": 1},
                "layout": {"id": 10},
            }
        ],
        assets=[
            {
                "id": 1,
                "filename": "a.jpg",
                "size": 100,
                "type": "image",
                "public_url": "https://media.voog.com/0000/0047/4574/photos/a.jpg",
            }
        ],
    )


def _target_site(**kw):
    kw.setdefault("languages", [_lang(500)])
    kw.setdefault(
        "layouts",
        [{"id": 50, "title": "Front", "content_type": "page", "component": False, "body": "old"}],
    )
    kw.setdefault(
        "assets",
        [
            {
                "id": 9,
                "filename": "z.jpg",
                "size": 1,
                "type": "image",
                "public_url": "https://media.voog.com/0000/0053/4382/photos/z.jpg",
            }
        ],
    )
    return FakeVoog(host="target.voog.com", **kw)


def _run(source, target, phases, *, dry_run=False, state_dir=None, **kw):
    return run_clone(
        source=source,
        target=target,
        source_name="src",
        target_name="tgt",
        state_dir=state_dir,
        phases=phases,
        dry_run=dry_run,
        **kw,
    )


class TestPhaseSelection(unittest.TestCase):
    def test_no_selection_runs_the_whole_pipeline(self):
        self.assertEqual(resolve_phases(None), list(PHASE_ORDER))
        self.assertEqual(resolve_phases([]), list(PHASE_ORDER))

    def test_an_out_of_order_selection_is_re_sorted(self):
        # Honouring ["contents", "pages"] literally would run contents
        # against an empty page_map and report every page as missing.
        self.assertEqual(resolve_phases(["contents", "pages"]), ["pages", "contents"])

    def test_plan_runs_alone(self):
        self.assertEqual(resolve_phases(["plan", "pages"]), ["plan"])

    def test_an_unknown_phase_is_refused_by_name(self):
        with self.assertRaises(ValueError) as ctx:
            resolve_phases(["pages", "nonsense"])
        self.assertIn("nonsense", str(ctx.exception))


class TestSelfCloneGuard(unittest.TestCase):
    def test_same_site_name_is_refused(self):
        site = _source_site()
        with TemporaryDirectory() as tmp, self.assertRaises(ValueError) as ctx:
            run_clone(
                source=site,
                target=site,
                source_name="x",
                target_name="x",
                state_dir=tmp,
                phases=["site"],
            )
        self.assertIn("same site", str(ctx.exception))

    def test_same_host_under_two_names_is_refused(self):
        # The phases delete-and-rebuild content areas, so this would destroy
        # the source it is reading from — before anyone noticed.
        a, b = _source_site(), _source_site()
        with TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            run_clone(
                source=a,
                target=b,
                source_name="alias-one",
                target_name="alias-two",
                state_dir=tmp,
                phases=["site"],
            )


class TestDryRun(unittest.TestCase):
    def test_a_dry_run_writes_nothing(self):
        src, tgt = _source_site(), _target_site()
        with TemporaryDirectory() as tmp:
            result = _run(src, tgt, ["site", "pages", "contents"], dry_run=True, state_dir=tmp)
        self.assertTrue(result["dry_run"])
        self.assertEqual(tgt.posted, [])
        self.assertEqual(tgt.deleted, [])
        self.assertEqual(len(tgt.pages), 0)

    def test_a_dry_run_resolves_the_language_map(self):
        # Live-found defect: the site phase returned before mapping
        # languages in dry-run mode, so the pages phase always reported "no
        # language mapping" and the dry run told the operator nothing about
        # whether the clone would actually work.
        src, tgt = _source_site(), _target_site()
        with TemporaryDirectory() as tmp:
            result = _run(src, tgt, ["site", "pages"], dry_run=True, state_dir=tmp)
        by_phase = {r["phase"]: r for r in result["reports"]}
        self.assertEqual(by_phase["site"]["language_map"], {"100": 500})
        self.assertEqual(by_phase["pages"]["created"], 1)
        # It may still note the layout fallback (the layouts phase was not
        # requested); what must NOT appear is a language complaint.
        reasons = " ".join(p["reason"] for p in by_phase["pages"].get("problems", []))
        self.assertNotIn("language", reasons)

    def test_a_dry_run_persists_no_state(self):
        src, tgt = _source_site(), _target_site()
        with TemporaryDirectory() as tmp:
            _run(src, tgt, ["site", "pages"], dry_run=True, state_dir=tmp)
            self.assertFalse((Path(tmp) / "page_map.json").exists())
            self.assertFalse((Path(tmp) / "language_map.json").exists())

    def test_a_missing_target_language_is_reported_not_hidden(self):
        src = _source_site()
        tgt = _target_site(languages=[_lang(500, code="en", title="ENG")])
        with TemporaryDirectory() as tmp:
            result = _run(src, tgt, ["site", "pages"], dry_run=True, state_dir=tmp)
        problems = " ".join(
            p["reason"] for r in result["reports"] for p in (r.get("problems") or [])
        )
        self.assertIn("not present on the target", problems)
        self.assertIn("never creates them", problems)


class TestPagesPhase(unittest.TestCase):
    def test_a_page_is_created_with_a_mapped_layout(self):
        # The target's FIRST layout is a decoy: the fallback would pick it,
        # so an assertion satisfied by either path would prove nothing. Only
        # the layout_map lookup can produce id 50 here.
        tgt = _target_site(
            layouts=[
                {"id": 49, "title": "Decoy", "content_type": "page", "component": False},
                {
                    "id": 50,
                    "title": "Front",
                    "content_type": "page",
                    "component": False,
                    "body": "old",
                },
            ]
        )
        src = _source_site()
        with TemporaryDirectory() as tmp:
            _run(src, tgt, ["layouts", "site", "pages"], state_dir=tmp)
        self.assertEqual(len(tgt.pages), 1)
        created = tgt.pages[0]
        # Matched by (title, content_type, component) — not by position.
        self.assertEqual(created["layout_id"], 50)
        self.assertNotEqual(created["layout_id"], 49, "this is the fallback's answer")

    def test_a_page_without_a_usable_mapped_layout_falls_back_and_says_so(self):
        # Live-found: POST /pages is rejected with
        # {"errors":{"layout_id":["not in available layouts list"]}} when the
        # id is absent or belongs to the source site. Voog picks no default.
        src = _source_site()
        src.pages[0]["layout"] = None
        tgt = _target_site()
        with TemporaryDirectory() as tmp:
            result = _run(src, tgt, ["site", "pages"], state_dir=tmp)
        self.assertEqual(len(tgt.pages), 1)
        self.assertEqual(tgt.pages[0]["layout_id"], 50)
        reasons = [p["reason"] for r in result["reports"] for p in (r.get("problems") or [])]
        self.assertTrue(any("names no layout" in r for r in reasons), reasons)

    def test_no_usable_layout_skips_the_page_with_a_clear_reason(self):
        src = _source_site()
        src.pages[0]["layout"] = None
        tgt = _target_site(layouts=[])
        with TemporaryDirectory() as tmp:
            result = _run(src, tgt, ["site", "pages"], state_dir=tmp)
        self.assertEqual(len(tgt.pages), 0)
        reasons = [p["reason"] for r in result["reports"] for p in (r.get("problems") or [])]
        self.assertTrue(any("Voog rejects a page without one" in r for r in reasons), reasons)

    def test_an_existing_page_on_the_same_path_is_updated_not_duplicated(self):
        src = _source_site()
        tgt = _target_site(
            pages=[
                {
                    "id": 700,
                    "title": "Old home",
                    "path": "",
                    "slug": "",
                    "content_type": "page",
                    "node": {"id": 1, "parent_id": None},
                }
            ]
        )
        with TemporaryDirectory() as tmp:
            result = _run(src, tgt, ["site", "pages"], state_dir=tmp)
        self.assertEqual(len(tgt.pages), 1, "must repurpose, not duplicate")
        by_phase = {r["phase"]: r for r in result["reports"]}
        self.assertEqual(by_phase["pages"]["updated"], 1)
        self.assertEqual(by_phase["pages"]["created"], 0)


class TestResume(unittest.TestCase):
    def test_a_second_run_creates_nothing(self):
        # Proven live too: the rerun reported skipped=1 page, skipped=2
        # content areas, and the target's page count did not move.
        src, tgt = _source_site(), _target_site()
        with TemporaryDirectory() as tmp:
            _run(src, tgt, ["site", "pages"], state_dir=tmp)
            self.assertEqual(len(tgt.pages), 1)
            second = _run(src, tgt, ["site", "pages"], state_dir=tmp)
        self.assertEqual(len(tgt.pages), 1)
        by_phase = {r["phase"]: r for r in second["reports"]}
        self.assertEqual(by_phase["pages"]["created"], 0)
        self.assertEqual(by_phase["pages"]["skipped"], 1)


class TestAssetQuota(unittest.TestCase):
    def _site_with_quota(self, used, limit):
        site = _target_site()
        site.site = {
            **site.site,
            "data_usage": used,
            "data": {"internal_trial_assets_quota": limit},
        }
        return site

    def test_quota_is_read_from_the_live_site_record(self):
        # Confirmed live on kolm-koma-2026: GET /site carries data_usage and
        # data.internal_trial_assets_quota (1.23 GB of 5 GB).
        quota = quota_of(self._site_with_quota(1_232_529_194, 5_242_880_000))
        self.assertEqual(quota["used_bytes"], 1_232_529_194)
        self.assertEqual(quota["limit_bytes"], 5_242_880_000)
        self.assertEqual(quota["remaining_bytes"], 4_010_350_806)

    def test_an_unadvertised_quota_is_unknown_not_unlimited(self):
        # The free-plan run that motivated all this hit a hard 422 at ~98 MB
        # with no advertised limit at all.
        self.assertIsNone(quota_of(_target_site())["remaining_bytes"])

    def test_uploads_stop_at_the_budget_and_the_gap_is_reported(self):
        src = _source_site()
        src.assets = [
            {
                "id": i,
                "filename": f"f{i}.jpg",
                "size": 100,
                "type": "image",
                "public_url": f"https://media.voog.com/0000/0047/4574/photos/f{i}.jpg",
            }
            for i in range(1, 6)
        ]
        tgt = self._site_with_quota(0, 10_000)
        with (
            TemporaryDirectory() as tmp,
            patch("voog.clone.phases._download", return_value=b"x" * 100),
            patch(
                "voog.clone.phases._upload_asset_bytes",
                side_effect=lambda t, *, filename, content_type, blob: {
                    "id": 1,
                    "filename": filename,
                    "public_url": "",
                },
            ),
        ):
            result = _run(src, tgt, ["assets"], state_dir=tmp, asset_budget_bytes=250)
        assets_report = result["reports"][0]
        self.assertIn("not_uploaded_due_to_quota", assets_report)
        self.assertGreater(assets_report["not_uploaded_due_to_quota"], 0)
        notes = " ".join(assets_report.get("notes") or [])
        self.assertIn("quota", notes)
        self.assertIn("resumes from the state map", notes)


class TestPhaseFailureIsContained(unittest.TestCase):
    def test_one_phase_raising_still_reports_the_earlier_phases(self):
        # The state maps are already on disk; a report that discards them
        # leaves the operator unable to decide between resume and restart.
        src, tgt = _source_site(), _target_site()

        real_get_all = tgt.get_all

        def _explode_on_pages(path, **kw):
            if path.split("?")[0] == "/pages":
                raise RuntimeError("boom")
            return real_get_all(path, **kw)

        with TemporaryDirectory() as tmp:
            _run(src, tgt, ["site"], state_dir=tmp)
            tgt.get_all = _explode_on_pages
            result = _run(src, tgt, ["site", "pages"], state_dir=tmp)
        self.assertTrue(result["aborted"])
        self.assertEqual(result["phases_run"][-1], "pages")
        self.assertGreaterEqual(result["problem_count"], 1)


class TestReportShape(unittest.TestCase):
    def test_known_limits_travel_with_every_result(self):
        src, tgt = _source_site(), _target_site()
        with TemporaryDirectory() as tmp:
            result = _run(src, tgt, ["site"], dry_run=True, state_dir=tmp)
        joined = " ".join(result["known_limits"])
        self.assertIn("created_at is not settable", joined)
        self.assertIn("published_at", joined)
        self.assertIn("Duplicate article paths", joined)

    def test_a_report_without_problems_omits_the_key(self):
        report = PhaseReport("layouts", created=3)
        self.assertNotIn("problems", report.to_dict())
        report.problem("x", "y")
        self.assertEqual(report.to_dict()["problems"], [{"what": "x", "reason": "y"}])


class TestContext(unittest.TestCase):
    def test_the_rewriter_is_derived_from_both_live_sites(self):
        src, tgt = _source_site(), _target_site()
        with TemporaryDirectory() as tmp:
            ctx = CloneContext(
                source=src,
                target=tgt,
                state=CloneState(Path(tmp)),
                source_name="src",
                target_name="tgt",
            )
            rewriter = ctx.rewriter()
        self.assertTrue(rewriter.rewrites_media)
        self.assertEqual(
            rewriter.text('<img src="https://media.voog.com/0000/0047/4574/photos/a.jpg">'),
            '<img src="https://media.voog.com/0000/0053/4382/photos/a.jpg">',
        )
        self.assertEqual(
            rewriter.text('<a href="https://source.voog.com/x">y</a>'), '<a href="/x">y</a>'
        )

    def test_the_rewriter_is_built_once(self):
        src, tgt = _source_site(), _target_site()
        with TemporaryDirectory() as tmp:
            ctx = CloneContext(
                source=src,
                target=tgt,
                state=CloneState(Path(tmp)),
                source_name="src",
                target_name="tgt",
            )
            self.assertIs(ctx.rewriter(), ctx.rewriter())


if __name__ == "__main__":
    unittest.main()
