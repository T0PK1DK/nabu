# Handoff prompt

Copy everything below the line into a new Claude (or Cursor) session that already has this repository.

---

You are continuing work on **Nabu**, a local cited encyclopedia.

## What this project is

Nabu is one SQLite file plus a stdlib HTTP server (`app.py`). Humans search at `/`. Agents use `/api/*`. There is no crawler, no embeddings, no accounts, no PIN/passcode gate, and no Wikipedia scrape.

**Verified** means every claim has:

1. claim text
2. an `http` or `https` URL with a real hostname (see `nabu.core.is_http_url`)
3. an ISO retrieval date `YYYY-MM-DD` that is not in the future (see `nabu.core.is_iso_date`)

If any of that is missing, the entry is stored as `draft` and the response `reason` says why. That rule is the product. Do not weaken it.

## Current version (0.3.1)

Already shipped in this tree:

- Package `nabu/core.py`: judge, FTS query, search, related (IDF cosine + wiki/domain bonuses), 3D force layout, revisions, stats.
- Server `app.py`: HTML doors `/`, `/e/:slug`, `/archive`, `/propose`, `/ledger` and the JSON API.
- FTS5 indexes title, summary, body, **and claims**. Porter stemming is on. The last query token is prefixed. `bm25` column weights are title-heavy. Hits include snippets from title (col 1), body (col 3), and claims (col 4). A highlighted match wins over a bare summary.
- `[[slug]]` / `[[slug|label]]` wikilinks, backlinks, related entries, revision snapshots on replace.
- Constructed Wayback URLs on sourced claims only (not fetched). Unsourced claims never get `archive`.
- 3D tablet archive at `/archive` (orbitable hall, not a game). Pinch zooms. A drag is not a click.
- Propose UX: `/propose?slug=` prefills from `GET /api/e/:slug`. The form judges as you type and shows the completeness meter. Entry pages link **Revise this tablet**.
- Kind/status filter chips on `/`. Claims on an entry are grouped by source host.
- Tests in `tests/test_nabu.py`. Run `python3 -m unittest discover -s tests -v`.
- Research notes in `docs/RESEARCH.md`. Do not copy third-party code or assets.

Read `README.md` and `docs/RESEARCH.md` before editing.

## Hard invariants

- Python 3 standard library only. No pip dependencies.
- No personal names in public text (README, seed, UI copy, commit messages, PR body).
- Do not deploy.
- Do not add a passcode, PIN, login, or age gate.
- Do not scrape or import Wikipedia / Grokipedia / ZIM dumps.
- Do not turn the archive hall into a score-driven game.
- Prefer the better algorithm even if it is slower (weighted BM25, IDF cosine, more force-layout steps).
- Keep `python3 app.py` as the start command. `NABU_HOST`, `NABU_PORT`, `NABU_DB` already override bind and database path.

## Likely next work (pick one lane, finish it, add tests)

1. **Search quality.** Tune `BM25_WEIGHTS` and `fts_query` against the seed tablet. Add phrase-boost tests. Consider FTS5 `NEAR` for two-term queries. Keep snippets XSS-safe (`\x1e`/`\x1f` markers, then escape). Snippet columns are already title/body/claims.
2. **Citation hygiene.** Optional “archive exists?” is out of scope (no network). You may add a claim `note` field. Host grouping and “no Wayback on unsourced claims” are done.
3. **Graph.** Persist computed related edges if the tablet grows; today they are computed per request. Keep the 3D renderer dependency-free.
4. **Propose UX.** Done in 0.3.1: `/propose?slug=`, load button, live meter via `/api/judge`, revise link on the entry. Further work could be a side-by-side revision diff. Still no accounts.
5. **Seed.** Only add original entries with real source URLs and retrieval dates. Never paste encyclopedia article text.
6. **Tests.** Any new judge/search/API behavior needs a unittest. HTTP tests should bind port `0`.

## Done in 0.3.1

- Fixed FTS5 `snippet()` column indexes (they were reading summary/body and calling them body/claims).
- Pushed kind/status filters into the MATCH query instead of filtering after `LIMIT`.
- Propose form prefills an existing tablet and shows a live completeness meter.
- Entry page: revise link, claims grouped by host, status moved out of the cramped header.
- Search chips, larger mobile tap targets, archive drag-vs-click, pinch zoom, HiDPI local hall.

## How to run

```bash
python3 -m unittest discover -s tests -v
python3 app.py
```

Open http://127.0.0.1:8787 — search, filter by a chip, open an entry, use **Revise this tablet**, orbit `/archive`, submit an unsourced propose and confirm it stays `draft`.

When you change search, relatedness, or the judge, update `docs/RESEARCH.md` only if you borrowed a new external idea, and keep `README.md` API tables truthful.

## Done means

Tests pass, the clay UI still works without a network, unsourced text cannot be verified, and the PR description has no personal names.
