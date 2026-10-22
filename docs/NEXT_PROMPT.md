# Prompt: take Nabu live and make it better

Paste everything below the line into Claude Code (or another coding agent) opened on this repository.

---

You are working on **Nabu**, a cited answer engine and encyclopedia in the repository `T0PK1DK/nabu`, branch `claude/sage-floating-library`. Read `README.md` first. It explains the three ways the same page runs (Cloudflare Worker, `python3 app.py`, claude.ai artifact) and the answer contract.

## What Nabu is

A search engine that feels magical. Think of the floating books in the Hogwarts library, the grimoire towers in Black Clover, Wong's library in Doctor Strange, and Rimuru's Great Sage in *That Time I Got Reincarnated as a Slime*. When you ask for something, the knowledge comes to you.

- **It works like Perplexity:** source cards, an answer with citation pills, related questions, follow-ups, focus modes (All, Encyclopedia, How-to, Compare), and a library of past searches.
- **It works like Google:** instant answers (math, units, time), a knowledge panel, ranked web results, and suggestions as you type.
- **Nabu himself** is a living sage: an all-seeing eye in a turning ring of cuneiform. He greets, listens, thinks, follows the cursor, and narrates each answer.
- **Floating books** drift behind every page.
- **It never hides uncertainty.** Every answer shows a confidence badge, "Not settled" notes, and "May have changed" notes.

## Where things are

- `preview/template.html` is the only source of the page: HTML, CSS and JavaScript in one file. Run `python3 preview/build.py` after every edit. That writes `preview/nabu.html`, `static/app.html` and `worker/src/page.js`. Never edit those three by hand.
- `worker/src/index.js` is the Cloudflare Worker. It serves the page, `POST /api/answer` (Wikipedia, plus Brave Search when `BRAVE_API_KEY` is set, then Workers AI streaming a tagged answer) and `/api/health`.
- `app.py` is the Python 3 standard-library server with the same `/api/answer`, plus the original SQLite tablet at `/tablet`.
- **The answer format:** plain text, one tagged line at a time. Sources come first (`@site Name | url | type | why`, `@result Title | url | snippet`), then `@intent @title @sub @conf @tldr @point @fact @h @p @step @row @time @unsure @fresh @ask @next`. `[n]` cites the nth `@site`. The page's `Turn.prototype.line` renders each tag.

## Do these, in order

Stop after each step, check it works, and commit.

1. **Deploy.** Run `cd worker && wrangler deploy` and confirm `/api/health` returns `ai: true`. Ask me to run `wrangler login` and `wrangler secret put BRAVE_API_KEY` myself; never ask me to paste keys into chat. Report the live URL.
2. **Make it fast and cheap.**
   - Cache `/api/answer` source lookups in Workers KV or the Cache API for 6 hours, keyed by the lowercased query.
   - Add a per-IP rate limit (about 30 answers per 10 minutes) that returns 429. The page already shows a friendly message for 429.
3. **Better sources.**
   - Fetch the top two non-Wikipedia results' pages (HTML to text, first 3,000 characters) so the model reads more than snippets.
   - Drop any `@site` the model invents. Only the server's own source lines may appear.
   - Show favicons from `https://icons.duckduckgo.com/ip3/{host}.ico`, with the letter as a fallback.
4. **Images tab.** Next to Answer and Links, add an Images tab from Wikimedia Commons search (no key needed), shown in a masonry grid that floats in like summoned pages.
5. **Voice.** Add a microphone button in the ask box (Web Speech API, where supported). Nabu's eye glows while listening.
6. **Learning library.** When an answer has high confidence and at least two Wikipedia sources, offer "Save to the tablet". It posts to `/api/propose` with the claims and their source URLs and dates, so it can become a verified tablet entry. Keep the existing rule: unsourced text stays a draft.
7. **Polish.**
   - Keyboard shortcuts: `/` focuses search, `Esc` closes, `j`/`k` move between answers.
   - Shareable links: `?q=` runs a search on load.
   - An accessibility pass: focus order, labels, contrast in both themes.
   - Lighthouse 90+ on mobile.

## Rules

- **Keep the look:** lapis accent, Geist for the interface, Source Serif 4 for answers, the cuneiform sage, the floating books, light and dark themes. Respect `prefers-reduced-motion`.
- Stay on the free tiers (Workers AI, Wikipedia, Wikimedia), with Brave optional.
- Never present a guessed URL as a real source. Never hide uncertainty.
- **Test before you push:** `node --check worker/src/index.js`, a headless browser run of a search on phone and desktop widths, and `python3 app.py` still serving `/`, `/tablet` and `/api/answer`.
- Plain, friendly copy. Nabu speaks briefly and warmly, a little old.
