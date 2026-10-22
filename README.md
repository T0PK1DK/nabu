# Nabu v0.3

Nabu v0.3 keeps entries in SQLite, searches them with FTS5, and verifies an entry only when every claim has a source URL and a retrieval date.

A local cited encyclopedia. Humans use the search box. An agent uses the same database through the API, not the HTML. Unsourced text is stored as a draft and cannot be marked verified. There is no crawler, no embeddings, and no accounts.

Books drift around the tablet. When you ask, the hits fly in, and the Sage opens beside them: a window where cuneiform resolves into an answer with numbered citations back to the entries.

## The Sage

The Sage answers only from entries on the tablet and cites them as [1], [2]. With Cloudflare Workers AI credentials it writes a short answer through a Workers AI model, which is free up to the daily limit. Without them it reads the top entries aloud as written.

```bash
export CF_ACCOUNT_ID=your-account-id
export CF_API_TOKEN=token-with-workers-ai-read-permission
python3 app.py
```

`NABU_CF_MODEL` picks the model. The default is `@cf/meta/llama-3.1-8b-instruct`.

If a query names a site, such as `github.com`, the Sage also offers a doorway to it.

## Learning

When a query has fewer than two hits, Nabu reads one Wikipedia summary through the official API and adds it to the tablet. A learned entry is always a draft. It carries the Wikipedia URL and the date it was read, and it stays a draft until someone checks it and proposes it as verified. Each query is tried once. At most `NABU_LEARN_PER_HOUR` lessons (default 12) are taken an hour. `NABU_LEARN=0` turns learning off.

Grokipedia has no public API, so Nabu does not read from it.

## Run

```bash
python3 app.py
```

Open http://127.0.0.1:8787

`nabu.db` is created on start and seeded from `seed.json` when the tablet is empty. Delete `nabu.db` to reseed.

## Unsourced propose

A claim with no URL is kept, and a request to mark the entry verified is refused:

```bash
curl -sS -X POST http://127.0.0.1:8787/api/propose \
  -H 'Content-Type: application/json' \
  -d '{"slug":"unsourced-note","title":"Unsourced note","kind":"note","summary":"A line with no source.","body":"This entry asks to be verified and has a claim with no URL.","status":"verified","claims":[{"text":"A claim without a source.","retrieved":"2026-10-10"}]}'
```

The response is a draft, and it says why:

```json
{"ok": true, "slug": "unsourced-note", "status": "draft", "reason": "Stored as draft because claim 1 has no source URL. Unsourced text cannot be marked verified.", "requested_status": "verified"}
```

## API

- `GET /api/ask?q=` — the Sage: answer, mode (`ai` or `tablet`), numbered sources, any slugs learned, a doorway if the query names a site, and the hits.
- `GET /api/search?q=` — ranked hits: slug, title, kind, summary, status. An empty `q` lists the tablet.
- `GET /api/e/:slug` — the entry and its claims.
- `POST /api/propose` — create or replace an entry. If any claim lacks a source URL or a retrieval date, status is forced to `draft`.

## Rule

Verified means every stored claim has an http or https URL and a retrieval date. FTS5 is the only ranker. Nabu never scrapes pages. Wikipedia text comes only from its API, one summary per lesson, with attribution, and stays a draft. The Sage answers only from what is on the tablet.

## Preview

`preview/` holds a single-file Nabu built as a claude.ai artifact, where Claude writes the answers. It follows Perplexity's shape: a slim sidebar with your library, an ask box with All, Encyclopedia, How-to, and Compare focus, and a thread where each question gets source cards, an answer with citation pills, an action bar, and related questions that continue the thread.

Nabu adds its own: books that float behind every page; Nabu himself as a living sage, an all-seeing eye in a turning ring of cuneiform that greets you, listens while you type, thinks while he writes, follows your cursor, and narrates each answer; answers shaped by intent (key facts, steps, comparison tables, timelines), a confidence badge, "Not settled" and "May have changed" notes, a Sources tab with live web searches, a lapis and cuneiform look in light and dark, and text that resolves from glyphs as it streams.

It also borrows from Google: instant answers computed on the page with no wait (math, unit conversions, time and date, coin flips, dice), a knowledge panel beside the answer on wide screens, Google-style web results with a Links tab, and search suggestions as you type.

Run `python3 preview/build.py` to produce `preview/nabu.html`.
