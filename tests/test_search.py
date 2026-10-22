#!/usr/bin/env python3
"""Search and empty-state coverage folded from PR #3 onto this branch's tablet."""

import json
import unittest

import app


class SearchFoldedFromPR3(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.init_db()
        app.seed()

    def test_fts_query_prefixes_every_bare_token(self):
        q = app.fts_match("tab dest")
        self.assertIn("tab*", q)
        self.assertIn("dest*", q)
        self.assertTrue(q.endswith("dest*"))

    def test_search_partial_first_token(self):
        slugs = [h["slug"] for h in app.search("tab dest")]
        self.assertIn("tablet-of-destinies", slugs)
        short = [h["slug"] for h in app.search("nab")]
        self.assertIn("nabu", short)
        self.assertIn("nabu-god", short)

    def test_search_unknown_stem_is_empty(self):
        self.assertEqual(app.search("xyzzy-not-written"), [])

    def test_search_or_fallback_when_and_misses(self):
        hits = app.search("nabu xyzzyfoobar")
        self.assertTrue(any(h["slug"] == "nabu" for h in hits), hits)

    def test_render_empty_names_the_asked_line(self):
        html = app.render_empty("xyzzy-not-written")
        self.assertIn("xyzzy-not-written", html)
        self.assertIn("empty-state", html)
        self.assertIn("Open the tablet", html)

    def test_unsourced_propose_stays_draft(self):
        code, body = app.propose(
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
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"], "draft")
        self.assertEqual(body.get("requested_status"), "verified")
        self.assertIn("no source URL", body["reason"])


if __name__ == "__main__":
    unittest.main()
