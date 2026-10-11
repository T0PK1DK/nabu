#!/usr/bin/env python3
"""Nabu v0.2 — local cited encyclopedia and search index.

Python 3 standard library only. SQLite FTS5. No crawler, no embeddings.
"""

import html
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
DB = ROOT / "nabu.db"
SEED = ROOT / "seed.json"
STATIC = ROOT / "static"
HOST = os.environ.get("NABU_HOST", "127.0.0.1")
PORT = int(os.environ.get("NABU_PORT", "8787"))
STATUSES = ("draft", "verified", "disputed")
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


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


def esc(value):
    return html.escape(str(value), quote=True)


def render_template(name, mapping):
    text = (STATIC / name).read_text(encoding="utf-8")
    pattern = re.compile(r"\{\{(" + "|".join(re.escape(key) for key in mapping) + r")\}\}")
    return pattern.sub(lambda match: mapping[match.group(1)], text).encode("utf-8")


def render_hits(q, hits):
    if q:
        kicker = "Hits"
    else:
        kicker = "The tablet"
    parts = [f'<p class="kicker">{esc(kicker)}</p>']
    if not hits:
        parts.append('<p class="empty">Nothing on the tablet for that.</p>')
        return "".join(parts)
    for hit in hits:
        parts.append(
            f"""<a class="hit" href="/e/{esc(hit["slug"])}">
  <div class="hit-top"><h2>{esc(hit["title"])}</h2><span class="status {esc(hit["status"])}">{esc(hit["status"])}</span></div>
  <div class="kind">{esc(hit["kind"])}</div>
  <p>{esc(hit["summary"])}</p>
</a>"""
        )
    return "".join(parts)


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


def render_home(q):
    hits = search(q)
    return render_template(
        "index.html",
        {"QUERY": esc(q), "RESULTS": render_hits(q, hits)},
    )


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
        if path in ("/", "/index.html"):
            q = parse_qs(parsed.query).get("q", [""])[0]
            self.send_html(render_home(q))
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
    print(f"Nabu v0.2 on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
