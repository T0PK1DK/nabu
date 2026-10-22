# Nabu v0.3.1

A local cited encyclopedia. Humans search the tablet. An agent uses the same SQLite file through the API. Unsourced text is stored as a draft and cannot be marked verified. There is no crawler, no embeddings, and no accounts.

Version 0.3 adds claim-aware FTS5 search (Porter stemming, prefix match, column-weighted BM25, snippets), kind and status filters, `[[wikilinks]]` with backlinks, related entries, revision snapshots, a human propose form, a ledger, and a 3D tablet archive. Verification now requires an http(s) URL **and** an ISO retrieval date (`YYYY-MM-DD`). Each sourced claim also gets a constructed [Wayback Machine](https://web.archive.org/) locator.

Version 0.3.1 revises an existing tablet from `/propose?slug=…`, shows a live citation-completeness meter, groups claims by source host, and fixes FTS snippets so a claim-only match quotes the claim instead of the summary. Search filters are chips. The archive no longer follows a drag as a click.

Search prefixes every bare token, and if an AND query is empty it retries as OR. A miss shows the asked line, the active filters, and a way back to the open tablet.

## Run

```bash
python3 app.py
```

Open http://127.0.0.1:8787

`nabu.db` is created on start and seeded from `seed.json` when the tablet is empty. Delete `nabu.db` to reseed. The schema migrates in place if an older FTS table is present.

```bash
NABU_HOST=127.0.0.1 NABU_PORT=8787 NABU_DB=./nabu.db python3 app.py
```

## Test

```bash
python3 -m unittest discover -s tests -v
```

CI runs the same command on every push ([`.github/workflows/test.yml`](.github/workflows/test.yml)).

## Doors

| Path | What it is |
| --- | --- |
| `/` | Search the tablet. Partial stems match. Filter by kind or status chips. A miss is named, not blank. |
| `/e/:slug` | An entry, its claims grouped by host, related tablets, backlinks, revisions. **Revise this tablet** opens the propose form already filled. |
| `/archive` | 3D hall of tablets. Orbit, pinch or scroll to approach, click to read. Not a game. |
| `/propose` | Cut a new line. Same judge as the API. The completeness meter updates as claims change. |
| `/propose?slug=nabu` | Load that tablet into the form and replace it. A replace still writes a revision. |
| `/ledger` | Counts, kinds, source hosts. |

## Unsourced propose

A claim with no URL is kept, and a request to mark the entry verified is refused:

```bash
curl -sS -X POST http://127.0.0.1:8787/api/propose \
  -H 'Content-Type: application/json' \
  -d '{"slug":"unsourced-note","title":"Unsourced note","kind":"note","summary":"A line with no source.","body":"This entry asks to be verified and has a claim with no URL.","status":"verified","claims":[{"text":"A claim without a source.","retrieved":"2026-10-10"}]}'
```

The response is a draft, and it says why:

```json
{"ok": true, "slug": "unsourced-note", "status": "draft", "reason": "Stored as draft because claim 1 has no source URL. Unsourced text cannot be marked verified.", "requested_status": "verified", "completeness": {"claims": 1, "sourced": 0, "dated": 1, "complete": 0, "ratio": 0.0}, "links": []}
```

Judge the same payload without writing:

```bash
curl -sS -X POST http://127.0.0.1:8787/api/judge \
  -H 'Content-Type: application/json' \
  -d '{"slug":"unsourced-note","title":"Unsourced note","kind":"note","summary":"A line with no source.","body":"Only judged.","status":"verified","claims":[{"text":"A claim without a source.","retrieved":"2026-10-10"}]}'
```

## API

- `GET /api/search?q=&kind=&status=&limit=` — ranked hits with snippets. Empty `q` lists the tablet.
- `GET /api/e/:slug` — the entry, claims (with Wayback links), related, backlinks, revisions.
- `GET /api/e/:slug/revisions` — earlier cuts of that slug.
- `GET /api/graph` — nodes and edges for the archive hall.
- `GET /api/stats` — counts by status, kind, and source host.
- `GET /api/export` — the whole tablet as JSON.
- `GET /api/health` — liveness and version.
- `POST /api/propose` — create or replace an entry. Incomplete claims force `draft`.
- `POST /api/judge` — the same rules, no write.

## Rule

Verified means every stored claim has an http or https URL with a real host and an ISO retrieval date that is not in the future. FTS5 is the only ranker. Pages are not scraped from Wikipedia.

Wikilinks in a body look like `[[fts5]]` or `[[nabu-god|Nabu]]`. They become edges in the graph and backlinks on the target entry.

## Docs

- [docs/RESEARCH.md](docs/RESEARCH.md) — comparable encyclopedias, local search, citation models, and why the archive is a hall rather than a game.
- [docs/HANDOFF.md](docs/HANDOFF.md) — prompt and invariants for the next editor.

## Comparable projects (not forks)

- [SQLite FTS5](https://www.sqlite.org/fts5.html)
- [Kiwix](https://kiwix.org/en/)
- [Wikidata statements](https://www.wikidata.org/wiki/Help:Statements)
- [Wayback Machine](https://web.archive.org/)
- [Scholarpedia](http://www.scholarpedia.org/)
- [Obsidian linking](https://obsidian.md/help/link-notes)
