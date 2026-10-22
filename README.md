# Nabu v1.0

A cited answer engine and encyclopedia. Ask anything: Nabu fetches live sources, writes an answer that cites them, and shows how sure it is. It blends Perplexity (source cards, cited answers, follow-ups) with Google (instant answers, a knowledge panel, web results), guided by Nabu himself, an all-seeing sage among floating books.

There are three ways to run the same page:

| Where | Answers come from | Sources come from |
| --- | --- | --- |
| Cloudflare Worker (`worker/`) | Workers AI, free daily allowance | Wikipedia, plus Brave Search with a key |
| `python3 app.py` | Workers AI over its REST API, or read straight from the sources | Wikipedia, plus Brave Search with a key, plus the verified tablet |
| claude.ai artifact (`preview/nabu.html`) | Claude | Claude's own suggestions to check |

## Deploy to Cloudflare

```bash
npm install -g wrangler
wrangler login
cd worker
wrangler secret put BRAVE_API_KEY   # optional: open-web results from Brave Search
wrangler deploy
```

The Worker serves the page at `/`, answers at `POST /api/answer`, and reports its setup at `/api/health`. Change the model with `NABU_MODEL` in `worker/wrangler.toml`.

## Run locally

```bash
export CF_ACCOUNT_ID=your-account-id          # optional: Workers AI writes the answers
export CF_API_TOKEN=token-with-workers-ai     # optional
export BRAVE_API_KEY=your-brave-key           # optional: open-web results
python3 app.py
```

Open http://127.0.0.1:8787. Without Cloudflare credentials, answers are read straight from the top sources and say so. The original tablet search lives at `/tablet`.

## Editing the page

The page has one source: `preview/template.html`. After editing it, run:

```bash
python3 preview/build.py
```

That writes `preview/nabu.html` (the artifact), `static/app.html` (for `app.py`), and `worker/src/page.js` (for the Worker).

## The answer contract

`POST /api/answer` takes `{"q": "...", "focus": "all|know|do|compare", "history": [...], "rewrite": false}` and streams plain text, one tagged line at a time. Source lines come first (`@site`, `@result`), then the answer (`@intent @title @sub @conf @tldr @point @fact @h @p @step @row @time @unsure @fresh @ask @next`). The page renders each line as it arrives. Citations like `[1]` refer to the `@site` lines in order.

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

- `POST /api/answer` — the new answer engine, described above.
- `GET /api/ask?q=` — the Sage: answer, mode (`ai` or `tablet`), numbered sources, any slugs learned, a doorway if the query names a site, and the hits.
- `GET /api/search?q=` — ranked hits: slug, title, kind, summary, status. An empty `q` lists the tablet.
- `GET /api/e/:slug` — the entry and its claims.
- `POST /api/propose` — create or replace an entry. If any claim lacks a source URL or a retrieval date, status is forced to `draft`.

## Rule

On the tablet, verified means every stored claim has an http or https URL and a retrieval date. FTS5 is the only ranker. Nabu never scrapes pages. Wikipedia text comes only from its API, one summary per lesson, with attribution, and stays a draft. The Sage answers only from what is on the tablet.

## What the page does

- **Nabu the sage:** an all-seeing eye in a turning ring of cuneiform. He greets you, listens while you type, thinks while he writes, follows your cursor, and narrates each answer.
- **Floating books** drift behind every page and stir while an answer is written.
- **Instant answers** with no waiting: math, unit conversions, time and date, coin flips, dice.
- **Answers shaped by intent:** a cited lead, key points, a facts table, sections, numbered steps, comparison tables, timelines.
- **Honesty on the page:** a confidence badge, plus "Not settled" and "May have changed" notes.
- **Google-style extras:** a knowledge panel on wide screens, ranked web results, a Links tab, search suggestions as you type.
- **Perplexity-style thread:** source cards, citation pills, related questions, follow-ups, focus modes, and a library of past searches.
- Light and dark, phone and desktop.
