#!/usr/bin/env python3
"""Nabu v1.0 — local cited encyclopedia, search index, and a sage that reads it.

Python 3 standard library only. SQLite FTS5. No crawler, no embeddings.
The sage answers from the tablet through Cloudflare Workers AI when a token is
set. When the tablet is thin, Nabu reads one Wikipedia summary through the
official API and keeps it as a draft with its URL.
"""

import html
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

ROOT = Path(__file__).resolve().parent
DB = ROOT / "nabu.db"
SEED = ROOT / "seed.json"
STATIC = ROOT / "static"
HOST = os.environ.get("NABU_HOST", "127.0.0.1")
PORT = int(os.environ.get("NABU_PORT", "8787"))
STATUSES = ("draft", "verified", "disputed")
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
USER_AGENT = "Nabu/0.3 (local cited encyclopedia; https://github.com/T0PK1DK/nabu)"
LEARN = os.environ.get("NABU_LEARN", "1") != "0"
LEARN_PER_HOUR = int(os.environ.get("NABU_LEARN_PER_HOUR", "12"))
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "").strip()
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "").strip()
CF_MODEL = os.environ.get("NABU_CF_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast").strip()
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "").strip()
DOMAIN_RE = re.compile(r"(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})(/\S*)?", re.I)
SOURCE_TTL = 6 * 3600
RATE_LIMIT = int(os.environ.get("NABU_RATE_LIMIT", "30"))
RATE_WINDOW = int(os.environ.get("NABU_RATE_WINDOW", "600"))
_SOURCE_CACHE = {}
_RATE = {}


def connect():
    conn = sqlite3.connect(DB, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db():
    conn = connect()
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS entries (
            slug TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            kind TEXT NOT NULL,
            summary TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('draft', 'verified', 'disputed')),
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS claims (
            id INTEGER PRIMARY KEY,
            slug TEXT NOT NULL REFERENCES entries(slug) ON DELETE CASCADE,
            text TEXT NOT NULL,
            url TEXT NOT NULL,
            retrieved TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
            slug UNINDEXED,
            title,
            summary,
            body,
            tokenize = 'porter unicode61'
        );
        CREATE TABLE IF NOT EXISTS lessons (
            query TEXT PRIMARY KEY,
            slug TEXT,
            tried_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


def now_stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_http_url(url):
    lowered = url.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def judge(claims, requested):
    """Verified only when every claim has an http(s) URL and a retrieval date.

    Anything unsourced is stored as draft. requested is None, draft, verified,
    or disputed. None means verified when the claims are complete.
    """
    problems = []
    if not claims:
        problems.append("the entry has no claims")
    for i, claim in enumerate(claims, 1):
        if not claim["url"]:
            problems.append(f"claim {i} has no source URL")
        elif not is_http_url(claim["url"]):
            problems.append(f"claim {i} has no http(s) source URL")
        if not claim["retrieved"]:
            problems.append(f"claim {i} has no retrieval date")
    if problems:
        why = "; ".join(problems)
        return (
            "draft",
            f"Stored as draft because {why}. Unsourced text cannot be marked verified.",
        )
    if requested == "draft":
        return (
            "draft",
            "Stored as draft as requested. Every claim has a source URL and a retrieval date.",
        )
    if requested == "disputed":
        return (
            "disputed",
            "Stored as disputed. Every claim has a source URL and a retrieval date.",
        )
    return (
        "verified",
        "Verified. Every claim has a source URL and a retrieval date.",
    )


def save_entry(conn, slug, title, kind, summary, body, status, claims, updated_at):
    conn.execute(
        """
        INSERT INTO entries (slug, title, kind, summary, body, status, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            title = excluded.title,
            kind = excluded.kind,
            summary = excluded.summary,
            body = excluded.body,
            status = excluded.status,
            updated_at = excluded.updated_at
        """,
        (slug, title, kind, summary, body, status, updated_at),
    )
    conn.execute("DELETE FROM claims WHERE slug = ?", (slug,))
    for claim in claims:
        conn.execute(
            "INSERT INTO claims (slug, text, url, retrieved) VALUES (?, ?, ?, ?)",
            (slug, claim["text"], claim["url"], claim["retrieved"]),
        )
    conn.execute("DELETE FROM entries_fts WHERE slug = ?", (slug,))
    conn.execute(
        "INSERT INTO entries_fts (slug, title, summary, body) VALUES (?, ?, ?, ?)",
        (slug, title, summary, body),
    )


def normalize_seed_claims(raw):
    claims = []
    for claim in raw or []:
        claims.append(
            {
                "text": str(claim.get("text") or "").strip(),
                "url": str(claim.get("url") or "").strip(),
                "retrieved": str(claim.get("retrieved") or "").strip(),
            }
        )
    return [c for c in claims if c["text"]]


def seed():
    conn = connect()
    count = conn.execute("SELECT COUNT(*) AS n FROM entries").fetchone()["n"]
    if count:
        conn.close()
        return
    data = json.loads(SEED.read_text(encoding="utf-8"))
    updated_at = now_stamp()
    for entry in data["entries"]:
        claims = normalize_seed_claims(entry.get("claims"))
        requested = entry.get("status") or "verified"
        status, reason = judge(claims, requested)
        if status != requested:
            print(f"[nabu] seed {entry['slug']} forced to {status}: {reason}")
        save_entry(
            conn,
            entry["slug"],
            entry["title"],
            entry["kind"],
            entry["summary"],
            entry["body"],
            status,
            claims,
            updated_at,
        )
    conn.commit()
    conn.close()


def entry_out(row, claims):
    return {
        "slug": row["slug"],
        "title": row["title"],
        "kind": row["kind"],
        "summary": row["summary"],
        "body": row["body"],
        "status": row["status"],
        "updated_at": row["updated_at"],
        "claims": [
            {"text": c["text"], "url": c["url"], "retrieved": c["retrieved"]}
            for c in claims
        ],
    }


def get_entry(slug):
    conn = connect()
    row = conn.execute("SELECT * FROM entries WHERE slug = ?", (slug,)).fetchone()
    if not row:
        conn.close()
        return None
    claims = conn.execute(
        "SELECT text, url, retrieved FROM claims WHERE slug = ? ORDER BY id",
        (slug,),
    ).fetchall()
    conn.close()
    return entry_out(row, claims)


def fts_match(q):
    """FTS5 MATCH: keep stemming, prefix every bare token of two or more letters.

    Folded from PR #3. Quoted phrases stay exact. An empty AND query is retried
    as OR in search(), so a partial first word still finds the line.
    """
    terms = []
    for part in (q or "").split():
        cleaned = part.replace('"', "")
        if cleaned:
            terms.append(cleaned + "*" if len(cleaned) >= 2 else cleaned)
    return " AND ".join(terms)


def _match_rows(conn, match):
    try:
        return conn.execute(
            """
            SELECT e.slug, e.title, e.kind, e.summary, e.status
            FROM entries_fts
            JOIN entries e ON e.slug = entries_fts.slug
            WHERE entries_fts MATCH ?
            ORDER BY bm25(entries_fts)
            LIMIT 20
            """,
            (match,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def search(q):
    q = (q or "").strip()
    conn = connect()
    if not q:
        rows = conn.execute(
            "SELECT slug, title, kind, summary, status FROM entries ORDER BY title"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    match = fts_match(q)
    if not match:
        conn.close()
        return []
    rows = _match_rows(conn, match)
    if not rows and " AND " in match:
        rows = _match_rows(conn, match.replace(" AND ", " OR "))
    conn.close()
    return [dict(r) for r in rows]


def propose(payload):
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "json object required"}
    title = str(payload.get("title") or "").strip()
    summary = str(payload.get("summary") or "").strip()
    body = str(payload.get("body") or "").strip()
    slug = str(payload.get("slug") or "").strip().lower()
    slug = re.sub(r"\s+", "-", slug)
    kind = str(payload.get("kind") or "note").strip() or "note"
    if not title or not summary or not body or not slug:
        return 400, {"ok": False, "error": "slug, title, summary, and body are required"}
    if not SLUG_RE.fullmatch(slug):
        return 400, {"ok": False, "error": "slug must be lowercase letters, numbers, and hyphens"}
    raw_claims = payload.get("claims") if "claims" in payload else []
    if not isinstance(raw_claims, list):
        return 400, {"ok": False, "error": "claims must be a list"}
    claims = []
    for claim in raw_claims:
        if not isinstance(claim, dict):
            return 400, {"ok": False, "error": "each claim must be an object"}
        text = str(claim.get("text") or "").strip()
        url = str(claim.get("url") or "").strip()
        retrieved = str(claim.get("retrieved") or "").strip()
        if not text and not url and not retrieved:
            continue
        if not text:
            return 400, {"ok": False, "error": "a claim needs text"}
        claims.append({"text": text, "url": url, "retrieved": retrieved})
    requested = payload.get("status", None)
    if isinstance(requested, str):
        requested = requested.strip().lower() or None
    elif requested is not None:
        return 400, {"ok": False, "error": "status must be draft, verified, or disputed"}
    if requested is not None and requested not in STATUSES:
        return 400, {"ok": False, "error": "status must be draft, verified, or disputed"}
    status, reason = judge(claims, requested)
    updated_at = now_stamp()
    conn = connect()
    try:
        save_entry(conn, slug, title, kind, summary, body, status, claims, updated_at)
        conn.commit()
    finally:
        conn.close()
    out = {"ok": True, "slug": slug, "status": status, "reason": reason}
    if requested and requested != status:
        out["requested_status"] = requested
    return 200, out


def fetch_json(url, data=None, headers=None, timeout=8):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:80].strip("-") or "entry"


def first_sentences(text, limit=240):
    out = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        if out and len(out) + len(sentence) + 1 > limit:
            break
        out = (out + " " + sentence).strip()
    return out[:limit].rstrip() if len(out) > limit else out


def wikipedia_summary(q):
    """One summary from the official Wikipedia API, or None."""
    found = fetch_json(
        "https://en.wikipedia.org/w/api.php?"
        + urlencode({"action": "query", "list": "search", "srsearch": q, "srlimit": 1, "format": "json"})
    )
    results = found.get("query", {}).get("search", [])
    if not results:
        return None
    title = results[0]["title"]
    page = fetch_json(
        "https://en.wikipedia.org/api/rest_v1/page/summary/" + quote(title.replace(" ", "_"), safe="")
    )
    if page.get("type") == "disambiguation" or not page.get("extract"):
        return None
    return {
        "title": page.get("title") or title,
        "description": page.get("description") or "",
        "extract": page["extract"].strip(),
        "url": page.get("content_urls", {}).get("desktop", {}).get("page")
        or "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_")),
    }


def learn(q, hits):
    """Read one Wikipedia summary when the tablet is thin. Returns a new slug or None.

    Learned text is stored as draft, never verified: a summary read by a
    machine is a lead, not a line on the record. Each query is tried once,
    and only LEARN_PER_HOUR lessons are taken in any hour.
    """
    key = " ".join(q.lower().split())
    if not LEARN or len(hits) >= 2 or len(key) < 3 or DOMAIN_RE.fullmatch(key):
        return None
    conn = connect()
    try:
        if conn.execute("SELECT 1 FROM lessons WHERE query = ?", (key,)).fetchone():
            return None
        since = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        taken = conn.execute("SELECT COUNT(*) AS n FROM lessons WHERE tried_at > ?", (since,)).fetchone()["n"]
        if taken >= LEARN_PER_HOUR:
            return None
        conn.execute("INSERT INTO lessons (query, slug, tried_at) VALUES (?, NULL, ?)", (key, now_stamp()))
        conn.commit()
    finally:
        conn.close()
    try:
        page = wikipedia_summary(q)
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        print(f"[nabu] could not read Wikipedia for {key!r}: {exc}")
        conn = connect()
        conn.execute("DELETE FROM lessons WHERE query = ?", (key,))
        conn.commit()
        conn.close()
        return None
    if not page:
        return None
    slug = slugify(page["title"])
    if get_entry(slug) or any(hit["slug"] == slug for hit in hits):
        return None
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    summary = first_sentences(page["extract"])
    body = (
        page["extract"]
        + f"\n\nNabu read this summary from Wikipedia on {today}. "
        + "It is kept as a draft until someone checks it against the source. "
        + "Wikipedia text is available under CC BY-SA 4.0."
    )
    kind = (page["description"] or "learned")[:60].lower()
    claims = [{"text": summary, "url": page["url"], "retrieved": today}]
    status, _ = judge(claims, "draft")
    conn = connect()
    try:
        save_entry(conn, slug, page["title"], kind, summary, body, status, claims, now_stamp())
        conn.execute("UPDATE lessons SET slug = ? WHERE query = ?", (slug, key))
        conn.commit()
    finally:
        conn.close()
    print(f"[nabu] learned {slug} from {page['url']}")
    return slug


def portal_for(q):
    """A doorway when the query names a site, like github.com."""
    match = DOMAIN_RE.fullmatch(q.strip())
    if not match:
        return None
    host = match.group(1).lower()
    return {"host": host, "url": "https://" + host + (match.group(2) or "")}


SAGE_PROMPT = (
    "You are Nabu, scribe of the tablet. Answer the question using only the numbered "
    "entries below. Cite entries in square brackets like [1] after the sentence they "
    "support. If the entries do not answer it, say plainly that it is not written on "
    "the tablet. Do not invent facts. Three to five short sentences, plain text, no "
    "lists, no headings."
)


def workers_ai(question, entries):
    """Ask Cloudflare Workers AI to answer from the given entries. Returns text or None."""
    if not (CF_ACCOUNT_ID and CF_API_TOKEN):
        return None
    context = "\n\n".join(
        f"[{i}] {e['title']} ({e['status']})\n{e['summary']}\n{e['body'][:1200]}"
        for i, e in enumerate(entries, 1)
    )
    payload = {
        "messages": [
            {"role": "system", "content": SAGE_PROMPT},
            {"role": "user", "content": f"Entries:\n\n{context}\n\nQuestion: {question}"},
        ],
        "max_tokens": 360,
        "temperature": 0.2,
    }
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_MODEL}"
    try:
        data = fetch_json(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"},
            timeout=20,
        )
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"[nabu] Workers AI failed: {exc}")
        return None
    text = (data.get("result") or {}).get("response") or ""
    return text.strip() or None


def ask(q):
    """The sage: search, learn if the tablet is thin, then answer from what is written."""
    q = (q or "").strip()
    if not q:
        return {"query": q, "answer": "", "mode": "silent", "sources": [], "learned": [], "portal": None}
    hits = search(q)
    learned = []
    slug = learn(q, hits)
    if slug:
        learned.append(slug)
        hits = search(q)
        if not any(h["slug"] == slug for h in hits):
            entry = get_entry(slug)
            hits = [{k: entry[k] for k in ("slug", "title", "kind", "summary", "status")}] + hits
    entries = [get_entry(h["slug"]) for h in hits[:4]]
    entries = [e for e in entries if e]
    sources = [
        {
            "n": i,
            "slug": e["slug"],
            "title": e["title"],
            "status": e["status"],
            "url": next((c["url"] for c in e["claims"] if is_http_url(c["url"])), ""),
        }
        for i, e in enumerate(entries, 1)
    ]
    portal = portal_for(q)
    if not entries:
        answer = "I turned the tablet. That is not written, and I found nothing I could set down."
        if portal:
            answer = f"The tablet has nothing on {portal['host']}, but the doorway is open."
        return {"query": q, "answer": answer, "mode": "tablet", "sources": [], "learned": learned, "portal": portal, "hits": hits}
    answer = workers_ai(q, entries)
    mode = "ai"
    if not answer:
        mode = "tablet"
        answer = " ".join(f"{e['summary']} [{i}]" for i, e in enumerate(entries[:2], 1))
    return {"query": q, "answer": answer, "mode": mode, "sources": sources, "learned": learned, "portal": portal, "hits": hits}



# The answer engine behind the new page: live sources, then a cited answer.
# Same contract as worker/src/index.js, so the page works against either.

ANSWER_SPEC = """Output only lines that start with a tag. No markdown, no blank lines, nothing else.
@intent go|know|do|compare|quick
@title the proper name of the subject or a short task title
@sub one line saying what it is
@conf high|medium|low   (how well the sources and your knowledge support the answer)
@tldr the direct answer in one or two sentences, citing sources as [1], [2]
@point a key takeaway in one specific sentence   (three to five)
@fact Label | value   (know: four to eight key facts, like an infobox)
@h a section heading
@p a paragraph of two to four sentences, with [n] citations where a source supports it
@step one instruction   (do: four to eight, after an @h)
@row cell | cell | cell   (compare: first row is the header, then three to six rows)
@time when | what happened   (history, people, stories, products: four to eight, oldest first)
@unsure something disputed, unknown, or not covered by the sources   (zero to two)
@fresh something that may have changed recently   (zero to two)
@ask a natural follow-up question the reader might ask next   (four)
@next a related topic, two to four words   (three)
Order: @intent @title @sub @conf @tldr, @point lines, @fact lines, then depth, then @unsure @fresh @ask @next.
Do not write @site or @result lines; the sources are already listed."""

ANSWER_FOCUS = {
    "know": "The reader chose Encyclopedia focus: treat it as know and go deep.",
    "do": "The reader chose How-to focus: treat it as do, with clear numbered steps.",
    "compare": "The reader chose Compare focus: treat it as compare, with a table and a verdict.",
}


def plain(text):
    text = html.unescape(re.sub(r"<[^>]+>", "", str(text or "")))
    return re.sub(r"\s+", " ", text).strip()


def one_line(text, limit=220):
    text = plain(text).replace("|", "/")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def wikipedia_sources(q):
    found = fetch_json(
        "https://en.wikipedia.org/w/api.php?"
        + urlencode({"action": "query", "list": "search", "srsearch": q, "srlimit": 5, "format": "json"})
    )
    hits = found.get("query", {}).get("search", [])
    out, seen = [], set()
    for hit in hits[:3]:
        try:
            page = fetch_json(
                "https://en.wikipedia.org/api/rest_v1/page/summary/" + quote(hit["title"].replace(" ", "_"), safe="")
            )
        except (urllib.error.URLError, OSError, ValueError):
            continue
        if page.get("type") == "disambiguation" or not page.get("extract"):
            continue
        url = page.get("content_urls", {}).get("desktop", {}).get("page")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({"name": page.get("title") or hit["title"], "url": url, "type": "reference",
                    "text": page["extract"], "snippet": page.get("description") or one_line(page["extract"], 160)})
    for hit in hits:
        url = "https://en.wikipedia.org/wiki/" + quote(hit["title"].replace(" ", "_"))
        if url in seen:
            continue
        seen.add(url)
        out.append({"name": hit["title"], "url": url, "type": "reference",
                    "text": plain(hit.get("snippet")), "snippet": one_line(hit.get("snippet"), 160)})
    return out


def brave_sources(q):
    data = fetch_json(
        "https://api.search.brave.com/res/v1/web/search?" + urlencode({"q": q, "count": 8, "safesearch": "moderate"}),
        headers={"X-Subscription-Token": BRAVE_API_KEY, "Accept": "application/json"},
    )
    out = []
    for r in data.get("web", {}).get("results", []):
        host = urlparse(r.get("url", "")).hostname or ""
        if not is_http_url(r.get("url", "")):
            continue
        kind = ("video" if re.search(r"youtube\.com|vimeo", host) else
                "community" if re.search(r"reddit\.com|fandom\.com|stackexchange|stackoverflow|quora", host) else
                "reference" if re.search(r"wikipedia\.org|britannica", host) else "official")
        out.append({"name": (r.get("profile") or {}).get("name") or host.removeprefix("www."), "title": plain(r.get("title")),
                    "url": r["url"], "type": kind, "text": plain(r.get("description")), "snippet": one_line(r.get("description"), 200)})
    return out


def tablet_sources(q):
    """A verified tablet entry leads when it matches: it is the one source Nabu has checked itself."""
    out = []
    for hit in search(q)[:1]:
        entry = get_entry(hit["slug"])
        if entry and entry["status"] == "verified" and entry["claims"]:
            out.append({"name": "The Tablet: " + entry["title"], "title": entry["title"] + " (Nabu's tablet)",
                        "url": entry["claims"][0]["url"], "type": "reference",
                        "text": entry["summary"] + " " + entry["body"], "snippet": entry["summary"]})
    return out


def cache_get(q):
    hit = _SOURCE_CACHE.get(q.lower())
    if hit and time.time() - hit[0] < SOURCE_TTL:
        return hit[1]
    return None


def cache_put(q, sources):
    _SOURCE_CACHE[q.lower()] = (time.time(), sources)


def client_ip(handler):
    forwarded = handler.headers.get("X-Forwarded-For") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()
    return handler.client_address[0]


def allow_answer(ip):
    now = time.time()
    stamps = [t for t in _RATE.get(ip, []) if now - t < RATE_WINDOW]
    if len(stamps) >= RATE_LIMIT:
        _RATE[ip] = stamps
        return False
    stamps.append(now)
    _RATE[ip] = stamps
    return True


def html_to_text(raw):
    text = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?>.*?</\1>", " ", raw or "")
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    return plain(text)[:3000]


def is_wiki_host(url):
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return host.endswith("wikipedia.org")


def read_page(url):
    """First 3,000 characters of a non-Wikipedia result page, or empty."""
    if not is_http_url(url) or is_wiki_host(url):
        return ""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        )
        with urllib.request.urlopen(req, timeout=5) as res:
            ctype = (res.headers.get("Content-Type") or "").lower()
            if ctype and not re.search(r"text/html|application/xhtml|text/plain", ctype):
                return ""
            raw = res.read(80_000).decode("utf-8", "replace")
        return html_to_text(raw)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return ""


def enrich_pages(sources):
    extra = [s for s in sources if s.get("url") and not is_wiki_host(s["url"])][:2]
    for src in extra:
        text = read_page(src["url"])
        if text and len(text) > len(src.get("text") or ""):
            src["text"] = text
    return sources


def keep_known_sources(line, urls):
    m = re.match(r"@(site|result)\s+(.+)$", (line or "").strip(), re.I)
    if not m:
        return True
    parts = [p.strip() for p in m.group(2).split("|")]
    url = parts[1] if len(parts) > 1 else ""
    return url in urls


def gather_sources(q):
    cached = cache_get(q)
    if cached is not None:
        return cached
    tablet = tablet_sources(q)
    try:
        wiki = wikipedia_sources(q)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"[nabu] Wikipedia unavailable: {exc}")
        wiki = []
    web = []
    if BRAVE_API_KEY:
        try:
            web = brave_sources(q)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"[nabu] Brave unavailable: {exc}")
    out, seen = list(tablet), {s["url"] for s in tablet}
    for i in range(max(len(wiki), len(web))):
        for src in (web[i] if i < len(web) else None, wiki[i] if i < len(wiki) else None):
            if src and src["url"] not in seen:
                seen.add(src["url"])
                out.append(src)
    out = out[:8]
    enrich_pages(out)
    cache_put(q, out)
    return out


def header_lines(sources):
    lines = [f"@site {one_line(s['name'], 80)} | {s['url']} | {s['type']} | {one_line(s['snippet'], 120)}" for s in sources[:5]]
    lines += [f"@result {one_line(s.get('title') or s['name'], 120)} | {s['url']} | {one_line(s['snippet'] or s['text'], 220)}" for s in sources]
    return lines


def answer_messages(q, sources, payload):
    ctx = "\n\n".join(f"[{i}] {s.get('title') or s['name']} ({s['url']})\n{s['text'][:3000]}" for i, s in enumerate(sources[:5], 1))
    system = (
        "You are Nabu, a search engine and encyclopedia. You answer from the numbered sources below, citing them as [n]. "
        "If the sources do not cover something, you may add well-established general knowledge without a citation, and lower @conf when you rely on it. "
        "Be specific: names, numbers, dates. No filler.\n\n" + ANSWER_SPEC + "\n\nSources:\n"
        + (ctx or "(no sources were found; answer from general knowledge, set @conf low, and say so in @unsure)")
    )
    history = []
    for m in (payload.get("history") or [])[-10:]:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
            history.append({"role": m["role"], "content": m["content"][:6000]})
    ask_text = ("Follow-up question: " if history else "Question: ") + q
    if ANSWER_FOCUS.get(payload.get("focus")):
        ask_text += "\n" + ANSWER_FOCUS[payload["focus"]]
    if payload.get("rewrite"):
        ask_text += "\nWrite a fresh version of this answer."
    return [{"role": "system", "content": system}] + history + [{"role": "user", "content": ask_text}]


def extractive_answer(q, sources):
    lines = ["@intent know", "@title " + one_line(sources[0]["name"] if sources else q, 80), "@conf medium"]
    if not sources:
        return lines + ["@tldr Nothing was found for this question. Try different words, or a live search below."]
    sentences = re.split(r"(?<=[.!?])\s+", plain(sources[0]["text"]))
    lines.append("@tldr " + " ".join(sentences[:2]) + " [1]")
    if len(sentences) > 2:
        lines.append("@p " + " ".join(sentences[2:6]) + " [1]")
    for i, s in enumerate(sources[1:4], 2):
        if s["text"]:
            lines.append(f"@point {one_line(s['text'], 200)} [{i}]")
    lines.append("@unsure This answer was read straight from the sources; the writing model was not configured or did not respond.")
    return lines


def answer(payload):
    """Text in Nabu's tag format: the source lines first, then the written answer."""
    q = str(payload.get("q") or "").strip()[:500]
    if not q:
        return None
    sources = gather_sources(q)
    head = header_lines(sources)
    text = None
    if CF_ACCOUNT_ID and CF_API_TOKEN:
        try:
            data = fetch_json(
                f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_MODEL}",
                data=json.dumps({"messages": answer_messages(q, sources, payload), "max_tokens": 1400, "temperature": 0.3}).encode("utf-8"),
                headers={"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"},
                timeout=45,
            )
            text = ((data.get("result") or {}).get("response") or "").strip() or None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"[nabu] Workers AI failed: {exc}")
    urls = {s["url"] for s in sources}
    body = text.splitlines() if text else extractive_answer(q, sources)
    kept = [line for line in body if line.strip() and keep_known_sources(line, urls)]
    return "\n".join(head + kept)


def esc(value):
    return html.escape(str(value), quote=True)


def render_template(name, mapping):
    text = (STATIC / name).read_text(encoding="utf-8")
    pattern = re.compile(r"\{\{(" + "|".join(re.escape(key) for key in mapping) + r")\}\}")
    return pattern.sub(lambda match: mapping[match.group(1)], text).encode("utf-8")


def voice_for(q, count):
    if not q:
        return "The tablet is open. Ask, and I will find the line."
    if count == 0:
        return "I turned the tablet. That line is not written."
    if count == 1:
        return "One line was already there."
    words = {
        2: "Two",
        3: "Three",
        4: "Four",
        5: "Five",
        6: "Six",
        7: "Seven",
        8: "Eight",
        9: "Nine",
        10: "Ten",
        11: "Eleven",
        12: "Twelve",
    }
    return f"{words.get(count, str(count))} lines were already written."


def render_empty(q):
    asked = q or "that line"
    return (
        '<div class="empty-state" role="status">'
        f'<p class="empty">Nothing on the tablet for “{esc(asked)}”.</p>'
        '<p class="empty-hint">The clay only answers what is already cut. '
        "Try a shorter stem, or open the whole tablet.</p>"
        '<a class="chip" href="/tablet">Open the tablet</a>'
        "</div>"
    )


def render_hits(hits, q=""):
    if not hits:
        return render_empty(q)
    parts = []
    for i, hit in enumerate(hits, 1):
        status = hit["status"]
        parts.append(
            f"""<a class="hit" href="/e/{esc(hit["slug"])}">
  <span class="num">{i:02d}</span>
  <span class="entry">
    <span class="line"><h2>{esc(hit["title"])}</h2><span class="status {esc(status)}"><i></i>{esc(status)}</span></span>
    <span class="kind">{esc(hit["kind"])}</span>
    <p>{esc(hit["summary"])}</p>
  </span>
</a>"""
        )
    return "".join(parts)


def render_home(q):
    q = (q or "").strip()
    hits = search(q)
    if not hits:
        label = "No hits"
    elif q:
        label = "Hits"
    else:
        label = "The tablet"
    return render_template(
        "index.html",
        {
            "QUERY": esc(q),
            "RESULTS": render_hits(hits, q),
            "LABEL": esc(label),
            "COUNT": f"{len(hits):02d}",
            "VOICE": esc(voice_for(q, len(hits))),
        },
    )


def render_claims(claims):
    if not claims:
        return "<li>No claims. This entry cannot be verified.</li>"
    parts = []
    for claim in claims:
        url = claim["url"]
        if is_http_url(url):
            src = f'<a class="src" href="{esc(url)}">{esc(url)}</a>'
        elif url:
            src = f'<span class="src">{esc(url)}</span>'
        else:
            src = '<span class="src missing">No source URL</span>'
        when = claim["retrieved"] or "no retrieval date"
        parts.append(
            f"<li><p>{esc(claim['text'])}</p>{src}<span class=\"when\">retrieved {esc(when)}</span></li>"
        )
    return "".join(parts)


def render_entry(slug):
    entry = get_entry(slug)
    if not entry:
        return (
            render_template(
                "entry.html",
                {
                    "TITLE": "Not on the tablet",
                    "KIND": "",
                    "STATUS": "",
                    "SUMMARY": "This slug is not in the index.",
                    "BODY": "",
                    "CLAIMS": "<li>No claims.</li>",
                    "UPDATED": "",
                    "VOICE": "That page is not cut into the clay.",
                },
            ),
            404,
        )
    updated = entry["updated_at"].replace("T", " ").replace("Z", " UTC")
    return (
        render_template(
            "entry.html",
            {
                "TITLE": esc(entry["title"]),
                "KIND": esc(entry["kind"]),
                "STATUS": esc(entry["status"]),
                "SUMMARY": esc(entry["summary"]),
                "BODY": esc(entry["body"]),
                "CLAIMS": render_claims(entry["claims"]),
                "UPDATED": esc("Revised " + updated),
                "VOICE": "This is what the clay says.",
            },
        ),
        200,
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[nabu]", self.address_string(), fmt % args)

    def send_json(self, code, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_asset(self, name, content_type):
        path = (STATIC / name).resolve()
        root = STATIC.resolve()
        if path.parent != root or not path.is_file():
            self.send_json(404, {"error": "not found"})
            return
        raw = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(raw)

    def send_html(self, raw, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        try:
            self.route_get()
        except Exception as exc:
            print("[nabu] error", exc)
            self.send_json(500, {"error": "server error"})

    def route_get(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if path == "/static/scribe.jpg":
            self.send_asset("scribe.jpg", "image/jpeg")
            return
        if path in ("/", "/index.html"):
            self.send_html((STATIC / "app.html").read_bytes())
            return
        if path == "/tablet":
            q = parse_qs(parsed.query).get("q", [""])[0]
            self.send_html(render_home(q))
            return
        if path == "/api/ask":
            q = parse_qs(parsed.query).get("q", [""])[0]
            self.send_json(200, ask(q))
            return
        if path == "/api/search":
            q = parse_qs(parsed.query).get("q", [""])[0]
            self.send_json(200, {"query": q, "hits": search(q)})
            return
        if path.startswith("/api/e/"):
            slug = unquote(path[len("/api/e/") :]).strip("/")
            if not slug or "/" in slug:
                self.send_json(404, {"error": "not found"})
                return
            entry = get_entry(slug)
            if not entry:
                self.send_json(404, {"error": "not found"})
                return
            self.send_json(200, entry)
            return
        if path.startswith("/e/"):
            slug = unquote(path[len("/e/") :]).strip("/")
            if not slug or "/" in slug:
                self.send_json(404, {"error": "not found"})
                return
            raw, code = render_entry(slug)
            self.send_html(raw, code)
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        try:
            self.route_post()
        except Exception as exc:
            print("[nabu] error", exc)
            self.send_json(500, {"error": "server error"})

    def route_post(self):
        route = urlparse(self.path).path
        if route not in ("/api/propose", "/api/answer"):
            self.send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.send_json(400, {"ok": False, "error": "invalid length"})
            return
        if length > 256_000:
            self.send_json(413, {"ok": False, "error": "body too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.send_json(400, {"ok": False, "error": "invalid json"})
            return
        if route == "/api/answer":
            if not allow_answer(client_ip(self)):
                raw = b"Too many questions. Give the shelves a few minutes."
                self.send_response(429)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Retry-After", "60")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            if not isinstance(payload, dict):
                self.send_json(400, {"error": "json object required"})
                return
            text = answer(payload)
            if text is None:
                self.send_json(400, {"error": "q is required"})
                return
            raw = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        code, body = propose(payload)
        self.send_json(code, body)


class Server(ThreadingHTTPServer):
    allow_reuse_address = True


def main():
    init_db()
    seed()
    server = Server((HOST, PORT), Handler)
    print(f"Nabu v1.0 on http://{HOST}:{PORT}  (the tablet: /tablet)")
    print("[nabu] sage:", "Workers AI " + CF_MODEL if CF_ACCOUNT_ID and CF_API_TOKEN else "reading the tablet (set CF_ACCOUNT_ID and CF_API_TOKEN for Workers AI)")
    print("[nabu] web results:", "Wikipedia + Brave Search" if BRAVE_API_KEY else "Wikipedia (set BRAVE_API_KEY for the open web)")
    print("[nabu] learning:", f"Wikipedia, up to {LEARN_PER_HOUR} lessons an hour" if LEARN else "off")
    server.serve_forever()


if __name__ == "__main__":
    main()
