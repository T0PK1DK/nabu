#!/usr/bin/env python3
"""Nabu v0.3 — local cited encyclopedia, search index, and a sage that reads it.

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
CF_MODEL = os.environ.get("NABU_CF_MODEL", "@cf/meta/llama-3.1-8b-instruct").strip()
DOMAIN_RE = re.compile(r"(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})(/\S*)?", re.I)


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
    parts = []
    for part in q.split():
        cleaned = part.replace('"', "")
        if cleaned:
            parts.append('"' + cleaned + '"')
    return " ".join(parts)


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
    try:
        rows = conn.execute(
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
        rows = []
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
        return "I turned the tablet. That is not written."
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


def render_hits(hits):
    if not hits:
        return '<p class="empty">Nothing on the tablet for that.</p>'
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
    if q and not hits:
        label = "No hits"
    elif q:
        label = "Hits"
    else:
        label = "The tablet"
    return render_template(
        "index.html",
        {
            "QUERY": esc(q),
            "RESULTS": render_hits(hits),
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
        if urlparse(self.path).path != "/api/propose":
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
        code, body = propose(payload)
        self.send_json(code, body)


class Server(ThreadingHTTPServer):
    allow_reuse_address = True


def main():
    init_db()
    seed()
    server = Server((HOST, PORT), Handler)
    print(f"Nabu v0.3 on http://{HOST}:{PORT}")
    print("[nabu] sage:", "Workers AI " + CF_MODEL if CF_ACCOUNT_ID and CF_API_TOKEN else "reading the tablet (set CF_ACCOUNT_ID and CF_API_TOKEN for Workers AI)")
    print("[nabu] learning:", f"Wikipedia, up to {LEARN_PER_HOUR} lessons an hour" if LEARN else "off")
    server.serve_forever()


if __name__ == "__main__":
    main()
