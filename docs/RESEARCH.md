# Research: cited encyclopedias, local search, and spatial archives

Nabu is a local cited encyclopedia: one SQLite file, FTS5 search, and a hard rule that unsourced claims cannot be marked verified. This note records comparable products and the features borrowed from them. No code, copy, or assets were taken from the sources below.

Roblox is only weakly relevant. Nabu is not a game. Spatial museum and library experiences are treated as a navigation metaphor for the tablet archive, not as gameplay.

## Comparable encyclopedias

### Wikipedia

Wikipedia’s public model is verifiability: nontrivial claims should point at reliable sources. Article pages pair a long body with inline citations, a references list, categories, “See also,” and talk/dispute surfaces.

**Borrow:** a dedicated claims list with source URL + retrieval date; related entries; a disputed status that is not the same as a missing source.

**Do not borrow:** scraping Wikipedia, copying article text, or treating Wikipedia as a source of truth for this tablet.

- [Wikipedia:Verifiability](https://en.wikipedia.org/wiki/Wikipedia:Verifiability)
- [Help:Footnotes](https://en.wikipedia.org/wiki/Help:Footnotes)

### Encyclopædia Britannica

Britannica is expert-authored and professionally edited. Coverage is narrower than a wiki, and the editorial model is the product.

**Borrow:** status as a first-class property (draft / verified / disputed), not an afterthought badge.

- [Britannica](https://www.britannica.com/)
- [ReputationX, “15 Best Wikipedia Alternatives in 2026”](https://www.reputationx.com/blog/wikipedia-alternatives)

### Scholarpedia

Scholarpedia is a peer-reviewed encyclopedia. Named experts curate articles; revisions go through approval. Academic citation studies treat it differently from Wikipedia.

**Borrow:** verification is a process with a reason string, not a checkbox. The API must explain why a proposal stayed a draft.

- [Scholarpedia](http://www.scholarpedia.org/)
- [Li, Thelwall, Mohammadi, “How are encyclopedias cited in academic research?”](https://revista.profesionaldelainformacion.com/index.php/EPI/article/download/81073/63027/289131)

### Citizendium

Citizendium splits draft work from approved, citable versions and asks for accountable authorship.

**Borrow:** keep drafts readable and searchable. A draft is stored, not discarded. Approval is not implied by existence.

- [Citizendium](https://en.citizendium.org/)
- [Grokipedia page on Citizendium](https://grokipedia.com/page/Citizendium) (secondary summary; not used as a source of facts on this tablet)

### Grokipedia (xAI)

Grokipedia launched in October 2025 as a machine-generated encyclopedia. Launch coverage emphasized that visitors could not edit pages the way Wikipedia allows, and that some pages carried Wikipedia-adaptation notices. Later reporting described a large generated corpus and weaker editorial transparency than expert-reviewed encyclopedias.

**Borrow:** Nabu stays the opposite product. Humans and agents write claims; the store refuses to call unsourced text verified. No crawler, no generated encyclopedia dump.

- [The Verge on the Grokipedia launch](https://www.theverge.com/news/807686/elon-musk-grokipedia-launch-wikipedia-xai-copied)
- [Lawfare on Grokipedia editorial process](https://www.lawfaremedia.org/article/grokipedia-stopped-reviewing-edits-in-april.-it-didn-t-tell-anyone)

### Kiwix

Kiwix is an offline reader for ZIM archives (Wikipedia and other sites). Features that matter here: local full-text search, title suggestions, bookmarks, a LAN server, and no account requirement.

**Borrow:** stay local-first and account-free. Search must work without the network. Do not import ZIM dumps or Wikipedia text.

- [Kiwix](https://kiwix.org/en/)
- [Kiwix on SourceForge (feature list)](https://sourceforge.net/projects/kiwix/)
- [Simple English Wikipedia, Kiwix](https://simple.wikipedia.org/wiki/Kiwix)

## Structured claims and citations

### Wikidata

Wikidata stores statements as property–value pairs with optional qualifiers, ranks, and references. It does not claim a statement is “true”; it claims the statement appears in a source. Unreferenced statements are weaker.

**Borrow:** a claim is text + http(s) URL + retrieval date. Verification is completeness of that triple, not a model’s confidence. Revisions keep superseded text instead of overwriting history.

- [Wikidata: Help:Statements](https://www.wikidata.org/wiki/Help:Statements)
- [Wikidata: Help:Sources](https://www.wikidata.org/wiki/Help:Sources)
- [Wikidata:Glossary](https://www.wikidata.org/wiki/Wikidata:Glossary)

### Internet Archive / Wayback Machine

A retrieval date is only half a citation. The Wayback URL `https://web.archive.org/web/YYYYMMDD/https://example.com/page` lets a later reader try the page as it stood near that day. Citation guides treat the timestamped archive URL as the stable locator.

**Borrow:** every sourced claim gets a constructed Wayback link from its retrieval date. Nabu does not fetch or scrape archive.org.

- [Wayback Machine](https://web.archive.org/)
- [Tacoma Community College: citing the Wayback Machine](https://tacomacc.libguides.com/Citing_Sources/citing_internetarchive_waybackmachine)
- [Columbia Libraries: citing archived web pages](https://library.cumc.columbia.edu/kb/finding-archived-web-content-government-websites)

### Perplexity and other cited-answer search

Cited-answer engines put sources next to the sentence they support, and they show a short excerpt so the reader can judge the match.

**Borrow:** search hits include an FTS5 snippet with highlighted terms, not only title and summary.

- [Perplexity](https://www.perplexity.ai/)

## Local knowledge graphs

### Obsidian

Obsidian is a local Markdown vault with `[[wikilinks]]`, automatic backlinks, unlinked mentions, a local graph, and a global graph. Notes are nodes; links are edges. The local graph is more useful on an article page than the global view.

**Borrow:** `[[slug]]` / `[[slug|label]]` in entry bodies; backlinks on the entry page; a global archive view and a local neighborhood on each entry. Local files / one SQLite file remain the store.

**Do not borrow:** plugin ecosystems, sync products, or any Obsidian UI chrome.

- [Obsidian: link notes](https://obsidian.md/help/link-notes)
- [Obsidian graph view (help mirror)](https://mintlify.wiki/obsidianmd/obsidian-help/plugins/graph-view)
- [SitePoint guide to Obsidian linking and graph view](https://www.sitepoint.com/obsidian-beginner-guide/)

## Search quality (FTS5)

SQLite FTS5 is the ranker. Official and practical guides agree on the same levers Nabu was underusing:

- Do not wrap every token in quotes if Porter stemming should fire.
- Prefix the last token (`scrib*`) for typeahead-style recall.
- Weight columns with `bm25(table, title, summary, body, claims)`.
- Use `snippet()` / `highlight()` for result excerpts.
- Escape `* " ( ) : ^` so a typed query cannot break MATCH.

Nabu v0.2 quoted every token, which disabled stemming, and it did not index claims. Both are defects relative to the FTS5 documentation.

- [SQLite FTS5](https://www.sqlite.org/fts5.html)
- [Coddy: FTS5 MATCH, bm25 weights, snippet/highlight](https://coddy.tech/docs/sqlite/full-text-search)
- [Mako: FTS5 query syntax and ranking](https://mako.ai/guides/sqlite/full-text-search)

## UI references (Mobbin)

Patterns used as layout ideas only (clay visual language stays Nabu’s):

- Article pages with a related-reading column and an “on this page” rail — [Codecademy related articles](https://mobbin.com/screens/05c28138-b587-4500-99da-7b7019463cf4), [GetYourGuide article + related](https://mobbin.com/screens/7771ec75-2458-4381-9cbe-f0cb87d4b027), [Uxcel related topics](https://mobbin.com/screens/604e0ab3-dfc1-4bc5-abd7-95ea26eb724d).
- Knowledge-base article with status and version metadata in a side panel — [Intercom public article](https://mobbin.com/screens/11dfdc37-cff0-423e-a34b-e2e8eefb8aaa).
- Search with filter chips (kind / status / category) above a result list — [Relevance AI results + filters](https://mobbin.com/screens/32b88167-1a97-4fbd-8707-340cf80defb8), [Remote job filters](https://mobbin.com/screens/c6e7a2e6-1d5f-4f2d-bfe9-8d20cae7e5c3), [Codecademy filtered cards](https://mobbin.com/screens/461acbe4-35f1-4610-b561-bcb46c47b9b7).

## Roblox and 3D (why a hall, not a game)

Searches for Roblox library / museum / encyclopedia experiences turn up:

- [Roblox Museum](https://pinewood.fandom.com/wiki/Roblox_Museum) — a 2008 place that exhibits old objects in a walkable hall.
- [Write a Book! Library of Aletheia](https://www.roblox.com/games/15881940396/Write-a-Book-Library-of-Aletheia) — a persistent library of player-written books.
- [Archived](https://bloxinformer.com/wikis/archived/) — an RPG whose “Library” is a combat dungeon, not a reference work.

Those are spatial metaphors (walk a hall, pull a volume) or combat games. Nabu is a cited index. The upgrade adds a **3D tablet archive**: clay tablets laid out from the knowledge graph, orbitable, clickable. There is no score, inventory, combat, or passcode. 3D is a graph view, not a game loop.

## Features chosen for this upgrade

| Idea | From | Nabu implementation |
| --- | --- | --- |
| Verified only if every claim is sourced | Wikipedia / Wikidata / Nabu v0.2 | Kept and tightened (URL parse + ISO date) |
| Draft vs approved | Citizendium / Scholarpedia | `draft` / `verified` / `disputed` with a reason |
| Claims as structured statements | Wikidata | text + URL + retrieved; completeness score |
| Dated archive locator | Wayback Machine | constructed `web.archive.org` link per claim |
| Stemmed, weighted FTS + snippets | FTS5 docs / Kiwix-style local search | Porter + prefix + bm25 column weights + snippet |
| Wikilinks and backlinks | Obsidian | `[[slug]]` in the body; backlinks on the entry |
| Related reading | Wikipedia See also / Codecademy | IDF cosine + link/domain bonuses |
| Filter chips | Mobbin search UIs | kind and status facets |
| Spatial archive | Museum / library places | 3D tablet hall from a force-directed layout |
| Human propose door | Wikipedia edit, minus accounts | `/propose` form, same judge as the API |
| History | Wikidata ranks / encyclopedia revisions | revision snapshots on replace |

## Out of scope

- No Wikipedia / Grokipedia scrape or dump.
- No embeddings, crawler, or accounts.
- No PIN, passcode, or age-gate.
- No deployment in this change.
- No copied UI assets or third-party fonts/scripts.
