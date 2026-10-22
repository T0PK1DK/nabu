#!/usr/bin/env python3
"""Tests for Nabu v0.3.1 — judge, search, graph, propose UX, and the HTTP doors."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from nabu.core import (
    best_snippet,
    completeness,
    extract_wikilinks,
    fts_query,
    get_entry,
    graph,
    init_db,
    is_http_url,
    is_iso_date,
    judge,
    propose,
    related_entries,
    revisions_for,
    search,
    seed,
    stats,
    wayback_url,
)


class TmpTablet(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "nabu.db"
        os.environ["NABU_DB"] = str(self.db)
        init_db()
        seed()

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("NABU_DB", None)


class JudgeTests(unittest.TestCase):
    def test_verified_when_every_claim_is_complete(self):
        status, reason = judge(
            [
                {
                    "text": "A sourced line.",
                    "url": "https://example.com/page",
                    "retrieved": "2026-10-10",
                }
            ],
            "verified",
        )
        self.assertEqual(status, "verified")
        self.assertIn("Verified", reason)

    def test_missing_url_cannot_be_verified(self):
        status, reason = judge(
            [{"text": "A claim without a source.", "url": "", "retrieved": "2026-10-10"}],
            "verified",
        )
        self.assertEqual(status, "draft")
        self.assertIn("no source URL", reason)

    def test_javascript_url_is_not_a_source(self):
        status, _ = judge(
            [
                {
                    "text": "Bad scheme.",
                    "url": "javascript:alert(1)",
                    "retrieved": "2026-10-10",
                }
            ],
            "verified",
        )
        self.assertEqual(status, "draft")

    def test_future_date_is_not_valid(self):
        status, reason = judge(
            [
                {
                    "text": "From tomorrow.",
                    "url": "https://example.com/x",
                    "retrieved": "2099-01-01",
                }
            ],
            "verified",
        )
        self.assertEqual(status, "draft")
        self.assertIn("retrieval date", reason)

    def test_empty_claims_are_draft(self):
        status, _ = judge([], "verified")
        self.assertEqual(status, "draft")

    def test_disputed_when_complete(self):
        status, _ = judge(
            [
                {
                    "text": "A sourced line.",
                    "url": "https://example.org/a",
                    "retrieved": "2024-01-02",
                }
            ],
            "disputed",
        )
        self.assertEqual(status, "disputed")


class UrlAndDateTests(unittest.TestCase):
    def test_http_url_needs_host(self):
        self.assertTrue(is_http_url("https://oracc.museum.upenn.edu/x"))
        self.assertFalse(is_http_url("http://localhost/x"))
        self.assertFalse(is_http_url("https://example.com /spaced"))
        self.assertFalse(is_http_url("ftp://example.com/x"))

    def test_iso_date_bounds(self):
        self.assertTrue(is_iso_date("2026-10-10", today="2026-10-21"))
        self.assertFalse(is_iso_date("2026-13-01", today="2026-10-21"))
        self.assertFalse(is_iso_date("1990-01-01", today="2026-10-21"))
        self.assertFalse(is_iso_date("10/10/2026", today="2026-10-21"))

    def test_wayback_url(self):
        url = wayback_url("https://example.com/page", "2026-10-10")
        self.assertEqual(url, "https://web.archive.org/web/20261010/https://example.com/page")
        self.assertIsNone(wayback_url("not-a-url", "2026-10-10"))
        self.assertIsNone(wayback_url("", "2026-10-10"))
        self.assertIsNone(wayback_url("https://example.com/page", ""))
        self.assertIsNone(wayback_url("javascript:alert(1)", "2026-10-10"))


class QueryTests(unittest.TestCase):
    def test_fts_query_keeps_stemming_and_prefix(self):
        q = fts_query('scribes "tablet of destinies"')
        self.assertIn("scribes", q)
        self.assertIn('"tablet of destinies"', q)
        self.assertNotIn('"scribes"', q)

    def test_fts_query_prefixes_last_token(self):
        self.assertTrue(fts_query("tablet dest").endswith("dest*"))

    def test_fts_query_strips_operators(self):
        q = fts_query("foo^bar")
        self.assertNotIn("^", q)
        self.assertIn("foo", q)
        self.assertIn("bar", q)

    def test_wikilinks(self):
        self.assertEqual(
            extract_wikilinks("See [[nabu-god]] and [[tablet-of-destinies|the tablet]]."),
            ["nabu-god", "tablet-of-destinies"],
        )

    def test_fts_query_prefixes_every_bare_token(self):
        q = fts_query("tab dest")
        self.assertIn("tab*", q)
        self.assertIn("dest*", q)
        self.assertTrue(q.endswith("dest*"))

    def test_best_snippet_prefers_highlighted_match(self):
        chosen = best_snippet(
            "plain summary",
            "a \x1eclaim\x1f hit",
            "body without marks",
            fallback="fallback",
        )
        self.assertIn("claim", chosen)
        self.assertIn("\x1e", chosen)
        self.assertEqual(best_snippet("", "…", fallback="summary"), "summary")


class StoreTests(TmpTablet):
    def test_seed_has_verified_and_draft(self):
        tablet = stats()
        self.assertGreaterEqual(tablet["entries"], 8)
        self.assertGreaterEqual(tablet["by_status"]["verified"], 1)
        self.assertGreaterEqual(tablet["by_status"]["draft"], 1)
        gap = get_entry("citation-gap")
        self.assertEqual(gap["status"], "draft")
        self.assertLess(gap["completeness"]["ratio"], 1)

    def test_search_finds_stem_and_claim(self):
        hits = search("scribes")
        slugs = [h["slug"] for h in hits]
        self.assertIn("nabu-god", slugs)
        claim_hits = search("Ezida")
        self.assertTrue(any(h["slug"] == "nabu-god" for h in claim_hits))
        self.assertTrue(
            any("Ezida" in (h.get("snippet") or "") for h in claim_hits),
            claim_hits,
        )

    def test_search_filters_and_snippets(self):
        tools = search("search", kind="tool")
        self.assertTrue(tools)
        self.assertTrue(all(h["kind"] == "tool" for h in tools))
        self.assertTrue(any(h.get("snippet") for h in tools))
        drafts = search("", status="draft")
        self.assertTrue(drafts)
        self.assertTrue(all(h["status"] == "draft" for h in drafts))

    def test_search_partial_first_token(self):
        hits = search("tab dest")
        slugs = [h["slug"] for h in hits]
        self.assertIn("tablet-of-destinies", slugs)
        short = search("nab")
        short_slugs = [h["slug"] for h in short]
        self.assertIn("nabu", short_slugs)
        self.assertIn("nabu-god", short_slugs)

    def test_search_unknown_stem_is_empty(self):
        self.assertEqual(search("xyzzy-not-written"), [])

    def test_search_or_fallback_when_and_misses(self):
        hits = search("nabu xyzzyfoobar")
        self.assertTrue(any(h["slug"] == "nabu" for h in hits), hits)

    def test_search_kind_filter_excludes_other_kinds(self):
        hits = search("nabu", kind="deity")
        self.assertTrue(hits)
        self.assertTrue(all(h["kind"] == "deity" for h in hits))
        slugs = [h["slug"] for h in hits]
        self.assertIn("nabu-god", slugs)
        self.assertNotIn("nabu", slugs)

    def test_search_kind_and_status_together(self):
        hits = search("tablet", kind="object", status="verified")
        self.assertTrue(hits)
        self.assertTrue(all(h["kind"] == "object" and h["status"] == "verified" for h in hits))
        self.assertTrue(any(h["slug"] == "tablet-of-destinies" for h in hits))

    def test_search_empty_query_kind_filter(self):
        notes = search("", kind="note")
        self.assertTrue(notes)
        self.assertTrue(all(h["kind"] == "note" for h in notes))
        listed = search("")
        self.assertGreaterEqual(len(listed), 8)

    def test_search_kind_is_forgiving_about_case(self):
        hits = search("nabu", kind="Deity")
        self.assertTrue(hits)
        self.assertTrue(all(h["kind"] == "deity" for h in hits))

    def test_search_empty_filters_can_miss(self):
        hits = search("", kind="note", status="verified")
        self.assertEqual(hits, [])

    def test_related_prefers_wiki_neighbors(self):
        related = related_entries("nabu")
        slugs = [r["slug"] for r in related]
        self.assertTrue({"nabu-god", "fts5", "tablet-archive"} & set(slugs))

    def test_graph_has_nodes_and_edges(self):
        g = graph()
        slugs = {n["slug"] for n in g["nodes"]}
        self.assertIn("nabu", slugs)
        self.assertTrue(g["edges"])
        self.assertTrue(all("x" in n and "z" in n for n in g["nodes"]))
        self.assertLess(len(g["edges"]), len(g["nodes"]) * 3)

    def test_unsourced_propose_stays_draft(self):
        code, body = propose(
            {
                "slug": "unsourced-note",
                "title": "Unsourced note",
                "kind": "note",
                "summary": "A line with no source.",
                "body": "This entry asks to be verified and has a claim with no URL.",
                "status": "verified",
                "claims": [{"text": "A claim without a source.", "retrieved": "2026-10-10"}],
            }
        )
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "draft")
        self.assertEqual(body["requested_status"], "verified")
        self.assertIn("no source URL", body["reason"])

    def test_propose_writes_revision_on_replace(self):
        first = {
            "slug": "clay-note",
            "title": "Clay note",
            "kind": "note",
            "summary": "First cut.",
            "body": "First body mentioning [[nabu]].",
            "status": "draft",
            "claims": [
                {
                    "text": "First claim.",
                    "url": "https://example.com/one",
                    "retrieved": "2026-01-01",
                }
            ],
        }
        propose(first)
        second = dict(first)
        second["title"] = "Clay note revised"
        second["summary"] = "Second cut."
        second["body"] = "Second body."
        propose(second)
        entry = get_entry("clay-note")
        self.assertEqual(entry["title"], "Clay note revised")
        revs = revisions_for("clay-note")
        self.assertTrue(revs)
        self.assertEqual(revs[0]["title"], "Clay note")

    def test_judge_endpoint_does_not_persist(self):
        code, body = propose(
            {
                "slug": "ghost-note",
                "title": "Ghost",
                "kind": "note",
                "summary": "Not stored.",
                "body": "Only judged.",
                "claims": [],
            },
            persist=False,
        )
        self.assertEqual(code, 200)
        self.assertIsNone(get_entry("ghost-note"))
        self.assertEqual(body["status"], "draft")

    def test_completeness_counts_sourced_claims(self):
        score = completeness(
            [
                {"text": "a", "url": "https://example.com/a", "retrieved": "2026-01-01"},
                {"text": "b", "url": "", "retrieved": ""},
            ]
        )
        self.assertEqual(score["complete"], 1)
        self.assertEqual(score["claims"], 2)

    def test_entry_has_no_wayback_for_unsourced_claim(self):
        gap = get_entry("citation-gap")
        self.assertTrue(gap["claims"])
        self.assertFalse(gap["claims"][0]["url"])
        self.assertIsNone(gap["claims"][0]["archive"])
        self.assertFalse(gap["claims"][0]["sourced"])


class HttpTests(TmpTablet):
    def setUp(self):
        super().setUp()
        os.environ["NABU_PORT"] = "0"
        from app import Handler, Server

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def _json(self, path, method="GET", payload=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if body else {}
        conn.request(method, path, body=body, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        return res.status, json.loads(raw.decode("utf-8")) if raw else {}

    def _html(self, path):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path)
        res = conn.getresponse()
        raw = res.read().decode("utf-8")
        conn.close()
        return res.status, raw

    def test_health_and_search_api(self):
        code, body = self._json("/api/health")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        code, body = self._json("/api/search?q=nabu")
        self.assertEqual(code, 200)
        self.assertTrue(body["hits"])
        code, filtered = self._json("/api/search?q=nabu&kind=deity")
        self.assertEqual(code, 200)
        slugs = [h["slug"] for h in filtered["hits"]]
        self.assertTrue(slugs)
        self.assertTrue(all(h["kind"] == "deity" for h in filtered["hits"]))
        self.assertIn("nabu-god", slugs)
        self.assertNotIn("nabu", slugs)
        code, empty = self._json("/api/search?q=xyzzy-not-written")
        self.assertEqual(code, 200)
        self.assertEqual(empty["hits"], [])

    def test_pages_render(self):
        for path in ("/", "/archive", "/propose", "/ledger", "/e/nabu", "/e/missing"):
            code, html = self._html(path)
            self.assertIn(code, (200, 404), path)
            self.assertIn("Nabu", html)
        code, home = self._html("/")
        self.assertEqual(code, 200)
        self.assertIn('data-facet="kind"', home)
        self.assertIn('data-facet="status"', home)
        self.assertIn("/?kind=tool", home)
        self.assertNotIn("/?status=tool", home)
        code, miss = self._html("/?q=xyzzy-not-written")
        self.assertEqual(code, 200)
        self.assertIn("Nothing on the tablet", miss)
        self.assertIn("xyzzy-not-written", miss)
        self.assertIn("empty-state", miss)
        self.assertIn("Open the tablet", miss)
        code, filtered = self._html("/?q=nabu&kind=deity")
        self.assertEqual(code, 200)
        self.assertIn("/e/nabu-god", filtered)
        self.assertIn('aria-current="true"', filtered)
        code, blank = self._html("/?kind=note&status=verified")
        self.assertEqual(code, 200)
        self.assertIn("Those filters left the tablet blank", blank)
        self.assertIn("Nothing on the tablet", blank)
        self.assertIn("Clear filters", blank)

    def test_entry_offers_revise_and_grouped_claims(self):
        code, html = self._html("/e/nabu")
        self.assertEqual(code, 200)
        self.assertIn("/propose?slug=nabu", html)
        self.assertIn("Revise this tablet", html)
        self.assertIn("oracc.museum.upenn.edu", html)
        missing_code, missing = self._html("/e/missing")
        self.assertEqual(missing_code, 404)
        self.assertNotIn("Revise this tablet", missing)

    def test_propose_prefills_existing_slug(self):
        code, html = self._html("/propose?slug=nabu")
        self.assertEqual(code, 200)
        self.assertIn('value="nabu"', html)
        self.assertIn('value="Nabu"', html)
        self.assertIn("https://oracc.museum.upenn.edu", html)
        self.assertIn("Editing", html)
        self.assertIn("Replace this tablet", html)
        self.assertIn("readonly", html)
        self.assertIn("Back to this tablet", html)
        missing_code, missing = self._html("/propose?slug=not-written-yet")
        self.assertEqual(missing_code, 200)
        self.assertIn("not on the tablet yet", missing)
        self.assertIn("Cut into the tablet", missing)
        self.assertNotIn("Replace this tablet", missing)

    def test_html_404_for_unknown_page(self):
        code, html = self._html("/no-such-door")
        self.assertEqual(code, 404)
        self.assertIn("Not on the tablet", html)

    def test_propose_and_graph_api(self):
        code, body = self._json(
            "/api/propose",
            "POST",
            {
                "slug": "http-note",
                "title": "HTTP note",
                "kind": "note",
                "summary": "Posted through the API.",
                "body": "Posted through the API toward [[nabu]].",
                "status": "verified",
                "claims": [
                    {
                        "text": "Posted through the API.",
                        "url": "https://example.com/api",
                        "retrieved": "2026-10-10",
                    }
                ],
            },
        )
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "verified")
        code, entry = self._json("/api/e/http-note")
        self.assertEqual(code, 200)
        self.assertTrue(entry["claims"][0]["archive"])
        code, g = self._json("/api/graph")
        self.assertEqual(code, 200)
        self.assertTrue(any(n["slug"] == "http-note" for n in g["nodes"]))


if __name__ == "__main__":
    unittest.main()
