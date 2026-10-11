# Nabu v0.2

Nabu v0.2 keeps entries in SQLite, searches them with FTS5, and verifies an entry only when every claim has a source URL and a retrieval date.

A local cited encyclopedia. Humans use the search box. An agent uses the same database through the API, not the HTML. Unsourced text is stored as a draft and cannot be marked verified. There is no crawler, no embeddings, and no accounts.

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

- `GET /api/search?q=` — ranked hits: slug, title, kind, summary, status. An empty `q` lists the tablet.
- `GET /api/e/:slug` — the entry and its claims.
- `POST /api/propose` — create or replace an entry. If any claim lacks a source URL or a retrieval date, status is forced to `draft`.

## Rule

Verified means every stored claim has an http or https URL and a retrieval date. FTS5 is the only ranker. Pages are not scraped from Wikipedia.
