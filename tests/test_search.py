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

    def test_drops_invented_sites_and_reads_html(self):
        urls = {"https://en.wikipedia.org/wiki/Nabu"}
        self.assertTrue(app.keep_known_sources("@tldr A real line [1]", urls))
        self.assertTrue(app.keep_known_sources("@site Nabu | https://en.wikipedia.org/wiki/Nabu | reference | god", urls))
        self.assertFalse(app.keep_known_sources("@site Guess | https://example.invalid/made-up | official | no", urls))
        text = app.html_to_text("<html><script>void 0</script><p>Nabu keeps the tablet.</p></html>")
        self.assertIn("Nabu keeps the tablet.", text)
        self.assertNotIn("void", text)
        self.assertLessEqual(len(text), 3000)

    def test_source_cache_and_rate_limit(self):
        app._SOURCE_CACHE.clear()
        app._RATE.clear()
        first = app.gather_sources("nabu")
        second = app.gather_sources("nabu")
        self.assertIs(first, second)
        old_limit = app.RATE_LIMIT
        app.RATE_LIMIT = 2
        try:
            self.assertTrue(app.allow_answer("test-ip"))
            self.assertTrue(app.allow_answer("test-ip"))
            self.assertFalse(app.allow_answer("test-ip"))
        finally:
            app.RATE_LIMIT = old_limit
            app._RATE.clear()

    def test_sourced_propose_can_verify(self):
        code, body = app.propose(
            {
                "slug": "high-conf-note",
                "title": "High confidence note",
                "kind": "note",
                "summary": "A sourced line.",
                "body": "Saved from a high-confidence answer.",
                "status": "verified",
                "claims": [
                    {
                        "text": "Nabu is a cited answer engine.",
                        "url": "https://en.wikipedia.org/wiki/Nabu",
                        "retrieved": "2026-10-22",
                    },
                    {
                        "text": "He is also a Babylonian scribe-god.",
                        "url": "https://en.wikipedia.org/wiki/Nabu_(god)",
                        "retrieved": "2026-10-22",
                    },
                ],
            }
        )
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "verified")

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
