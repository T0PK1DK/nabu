"""Nabu core: store, judge, search, related entries, and the tablet graph.

Python 3 standard library only. SQLite FTS5. No crawler, no embeddings.
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "nabu.db"
SEED = ROOT / "seed.json"
STATIC = ROOT / "static"

STATUSES = ("draft", "verified", "disputed")
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
KIND_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+){0,6}")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
WIKI_LINK_RE = re.compile(r"\[\[([a-z0-9]+(?:-[a-z0-9]+)*)(?:\|[^\]]+)?\]\]")
TOKEN_RE = re.compile(r"[a-z0-9]{3,}")
FTS_UNSAFE = re.compile(r'["*()^:{}[\]\\]')
SCHEMA_VERSION = 3

STOPWORDS = frozenset(
    """
    a an the and or but if in on at to for of as is was were be been being
    this that these those it its with from by into over after before about
    not no nor so than then too very can may will just also only other into
    they them their there here what when where which who whom how why
    have has had do does did done
    """.split()
)

# Porter stemming stays on. Quoting every token, as v0.2 did, disabled it.
BM25_WEIGHTS = (12.0, 4.0, 1.0, 6.0)  # title, summary, body, claims


def db_path() -> Path:
    override = os.environ.get("NABU_DB")
    return Path(override) if override else DEFAULT_DB


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or db_path(), timeout=8, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def is_http_url(url: str) -> bool:
    """Accept only http(s) URLs with a real host. Reject javascript: and friends."""
    if not url or not isinstance(url, str):
        return False
    raw = url.strip()
    if any(ch.isspace() for ch in raw):
        return False
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host or host == "localhost" or "." not in host:
        return False
    if parsed.username or parsed.password:
        return False
    return True


def is_iso_date(value: str, *, today: str | None = None) -> bool:
    if not value or not DATE_RE.fullmatch(value):
        return False
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    if parsed.year < 1991:
        return False
    ceiling = date.fromisoformat(today or today_iso())
    return parsed <= ceiling


def wayback_url(url: str, retrieved: str) -> str | None:
    if not is_http_url(url) or not DATE_RE.fullmatch(retrieved or ""):
        return None
    stamp = retrieved.replace("-", "")
    return f"https://web.archive.org/web/{stamp}/{url}"


def source_host(url: str) -> str:
    if not is_http_url(url):
        return ""
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def normalize_kind(kind: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(kind or "note").strip().lower()).strip("-")
    return cleaned or "note"


def claim_text_blob(claims: list[dict]) -> str:
    return "\n".join(c.get("text") or "" for c in claims)


def extract_wikilinks(text: str) -> list[str]:
    seen: list[str] = []
    for match in WIKI_LINK_RE.finditer(text or ""):
        slug = match.group(1)
        if slug not in seen:
            seen.append(slug)
    return seen


def completeness(claims: list[dict]) -> dict:
    total = len(claims)
    sourced = 0
    dated = 0
    complete = 0
    for claim in claims:
        has_url = is_http_url(claim.get("url") or "")
        has_date = is_iso_date(claim.get("retrieved") or "")
        if has_url:
            sourced += 1
        if has_date:
            dated += 1
        if has_url and has_date:
            complete += 1
    ratio = (complete / total) if total else 0.0
    return {
        "claims": total,
        "sourced": sourced,
        "dated": dated,
        "complete": complete,
        "ratio": round(ratio, 4),
    }


def judge(claims: list[dict], requested) -> tuple[str, str]:
    """Verified only when every claim has an http(s) URL and an ISO retrieval date.

    Anything incomplete is stored as draft. requested is None, draft, verified,
    or disputed. None means verified when the claims are complete.
    """
    problems = []
    if not claims:
        problems.append("the entry has no claims")
    for i, claim in enumerate(claims, 1):
        url = claim.get("url") or ""
        retrieved = claim.get("retrieved") or ""
        if not url:
            problems.append(f"claim {i} has no source URL")
        elif not is_http_url(url):
            problems.append(f"claim {i} has no http(s) source URL")
        if not retrieved:
            problems.append(f"claim {i} has no retrieval date")
        elif not is_iso_date(retrieved):
            problems.append(f"claim {i} has no valid retrieval date (use YYYY-MM-DD, not in the future)")
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


def fts_query(q: str) -> str:
    """Build an FTS5 MATCH string that keeps stemming and last-token prefix.

    v0.2 wrapped every token in double quotes, which turned off the Porter
    stemmer. Quoted phrases typed by the user are still honored.
    """
    q = (q or "").strip()
    if not q:
        return ""
    terms: list[str] = []
    pieces = re.findall(r'"([^"]+)"|(\S+)', q)
    bare: list[str] = []
    for quoted, raw in pieces:
        if quoted:
            cleaned = FTS_UNSAFE.sub(" ", quoted).strip()
            words = [w for w in cleaned.split() if w]
            if words:
                terms.append('"' + " ".join(words) + '"')
            continue
        token = FTS_UNSAFE.sub("", raw).strip().lower()
        token = re.sub(r"[^a-z0-9]+", "", token)
        if token:
            bare.append(token)
    if bare:
        *head, last = bare
        terms.extend(head)
        terms.append(last + "*" if len(last) >= 2 else last)
    return " AND ".join(terms)


def tokenize(text: str) -> list[str]:
    return [tok for tok in TOKEN_RE.findall((text or "").lower()) if tok not in STOPWORDS]


def _fts_columns(conn: sqlite3.Connection) -> set[str]:
    try:
        rows = conn.execute("PRAGMA table_info(entries_fts)").fetchall()
    except sqlite3.OperationalError:
        return set()
    return {row["name"] for row in rows}


def init_db(path: Path | None = None) -> None:
    conn = connect(path)
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
        CREATE TABLE IF NOT EXISTS revisions (
            id INTEGER PRIMARY KEY,
            slug TEXT NOT NULL,
            title TEXT NOT NULL,
            kind TEXT NOT NULL,
            summary TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL,
            claims_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            superseded_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS links (
            from_slug TEXT NOT NULL,
            to_slug TEXT NOT NULL,
            kind TEXT NOT NULL,
            PRIMARY KEY (from_slug, to_slug, kind)
        );
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS claims_slug_idx ON claims(slug);
        CREATE INDEX IF NOT EXISTS revisions_slug_idx ON revisions(slug, id);
        CREATE INDEX IF NOT EXISTS links_to_idx ON links(to_slug);
        """
    )
    columns = _fts_columns(conn)
    needed = {"slug", "title", "summary", "body", "claims"}
    if columns != needed:
        conn.execute("DROP TABLE IF EXISTS entries_fts")
        conn.execute(
            """
            CREATE VIRTUAL TABLE entries_fts USING fts5(
                slug UNINDEXED,
                title,
                summary,
                body,
                claims,
                tokenize = 'porter unicode61'
            )
            """
        )
        _rebuild_fts(conn)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    conn.close()


def _rebuild_fts(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM entries_fts")
    rows = conn.execute("SELECT slug, title, summary, body FROM entries").fetchall()
    for row in rows:
        claims = conn.execute(
            "SELECT text FROM claims WHERE slug = ? ORDER BY id", (row["slug"],)
        ).fetchall()
        blob = "\n".join(c["text"] for c in claims)
        conn.execute(
            "INSERT INTO entries_fts (slug, title, summary, body, claims) VALUES (?, ?, ?, ?, ?)",
            (row["slug"], row["title"], row["summary"], row["body"], blob),
        )


def normalize_seed_claims(raw) -> list[dict]:
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


def _decorate_claims(claims) -> list[dict]:
    out = []
    for claim in claims:
        url = claim["url"] if isinstance(claim, dict) else claim["url"]
        retrieved = claim["retrieved"] if isinstance(claim, dict) else claim["retrieved"]
        text = claim["text"] if isinstance(claim, dict) else claim["text"]
        item = {
            "text": text,
            "url": url,
            "retrieved": retrieved,
            "host": source_host(url),
            "archive": wayback_url(url, retrieved),
            "sourced": is_http_url(url),
            "dated": is_iso_date(retrieved),
        }
        out.append(item)
    return out


def entry_out(row, claims, extras: dict | None = None) -> dict:
    decorated = _decorate_claims(claims)
    payload = {
        "slug": row["slug"],
        "title": row["title"],
        "kind": row["kind"],
        "summary": row["summary"],
        "body": row["body"],
        "status": row["status"],
        "updated_at": row["updated_at"],
        "claims": decorated,
        "completeness": completeness(decorated),
        "links": extract_wikilinks(row["body"]),
    }
    if extras:
        payload.update(extras)
    return payload


def _snapshot_row(conn: sqlite3.Connection, slug: str) -> None:
    row = conn.execute("SELECT * FROM entries WHERE slug = ?", (slug,)).fetchone()
    if not row:
        return
    claims = conn.execute(
        "SELECT text, url, retrieved FROM claims WHERE slug = ? ORDER BY id",
        (slug,),
    ).fetchall()
    conn.execute(
        """
        INSERT INTO revisions (slug, title, kind, summary, body, status, claims_json, updated_at, superseded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["slug"],
            row["title"],
            row["kind"],
            row["summary"],
            row["body"],
            row["status"],
            json.dumps([dict(c) for c in claims], ensure_ascii=False),
            row["updated_at"],
            now_stamp(),
        ),
    )


def _rewrite_links(conn: sqlite3.Connection, slug: str, body: str) -> None:
    conn.execute("DELETE FROM links WHERE from_slug = ?", (slug,))
    for target in extract_wikilinks(body):
        if target == slug:
            continue
        exists = conn.execute("SELECT 1 FROM entries WHERE slug = ?", (target,)).fetchone()
        if exists:
            conn.execute(
                "INSERT OR IGNORE INTO links (from_slug, to_slug, kind) VALUES (?, ?, 'wiki')",
                (slug, target),
            )


def save_entry(conn, slug, title, kind, summary, body, status, claims, updated_at, *, snapshot=True):
    existing = conn.execute("SELECT slug FROM entries WHERE slug = ?", (slug,)).fetchone()
    if existing and snapshot:
        _snapshot_row(conn, slug)
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
        "INSERT INTO entries_fts (slug, title, summary, body, claims) VALUES (?, ?, ?, ?, ?)",
        (slug, title, summary, body, claim_text_blob(claims)),
    )
    _rewrite_links(conn, slug, body)
    # A newly inserted entry may be the target of earlier wiki links.
    for row in conn.execute("SELECT slug, body FROM entries").fetchall():
        if slug in extract_wikilinks(row["body"]) and row["slug"] != slug:
            conn.execute(
                "INSERT OR IGNORE INTO links (from_slug, to_slug, kind) VALUES (?, ?, 'wiki')",
                (row["slug"], slug),
            )


def seed(path: Path | None = None, seed_path: Path | None = None) -> None:
    conn = connect(path)
    count = conn.execute("SELECT COUNT(*) AS n FROM entries").fetchone()["n"]
    if count:
        conn.close()
        return
    data = json.loads((seed_path or SEED).read_text(encoding="utf-8"))
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
            normalize_kind(entry.get("kind") or "note"),
            entry["summary"],
            entry["body"],
            status,
            claims,
            updated_at,
            snapshot=False,
        )
    conn.commit()
    conn.close()


def get_entry(slug: str, path: Path | None = None) -> dict | None:
    conn = connect(path)
    row = conn.execute("SELECT * FROM entries WHERE slug = ?", (slug,)).fetchone()
    if not row:
        conn.close()
        return None
    claims = conn.execute(
        "SELECT text, url, retrieved FROM claims WHERE slug = ? ORDER BY id",
        (slug,),
    ).fetchall()
    backlinks = [
        dict(r)
        for r in conn.execute(
            """
            SELECT e.slug, e.title, e.kind, e.status
            FROM links
            JOIN entries e ON e.slug = links.from_slug
            WHERE links.to_slug = ?
            ORDER BY e.title
            """,
            (slug,),
        ).fetchall()
    ]
    revisions = [
        {
            "id": r["id"],
            "status": r["status"],
            "updated_at": r["updated_at"],
            "superseded_at": r["superseded_at"],
            "title": r["title"],
        }
        for r in conn.execute(
            """
            SELECT id, status, updated_at, superseded_at, title
            FROM revisions WHERE slug = ? ORDER BY id DESC LIMIT 12
            """,
            (slug,),
        ).fetchall()
    ]
    conn.close()
    extras = {
        "backlinks": backlinks,
        "revisions": revisions,
        "related": related_entries(slug, path=path),
    }
    return entry_out(row, claims, extras)


def list_entries(
    kind: str | None = None,
    status: str | None = None,
    path: Path | None = None,
) -> list[dict]:
    conn = connect(path)
    clauses = []
    args: list[str] = []
    if kind:
        clauses.append("kind = ?")
        args.append(kind)
    if status:
        clauses.append("status = ?")
        args.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT slug, title, kind, summary, status, updated_at FROM entries {where} ORDER BY title",
        args,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _apply_filters(rows: list[dict], kind: str | None, status: str | None) -> list[dict]:
    out = rows
    if kind:
        out = [r for r in out if r.get("kind") == kind]
    if status:
        out = [r for r in out if r.get("status") == status]
    return out


def search(
    q: str,
    kind: str | None = None,
    status: str | None = None,
    limit: int = 20,
    path: Path | None = None,
) -> list[dict]:
    q = (q or "").strip()
    limit = max(1, min(int(limit or 20), 100))
    conn = connect(path)
    if not q:
        rows = list_entries(kind=kind, status=status, path=path)
        conn.close()
        return rows[:limit]
    match = fts_query(q)
    if not match:
        conn.close()
        return []
    weights = BM25_WEIGHTS
    try:
        rows = conn.execute(
            """
            SELECT
                e.slug, e.title, e.kind, e.summary, e.status, e.updated_at,
                bm25(entries_fts, ?, ?, ?, ?) AS rank,
                snippet(entries_fts, 2, x'1e', x'1f', '…', 28) AS body_snip,
                snippet(entries_fts, 3, x'1e', x'1f', '…', 22) AS claim_snip
            FROM entries_fts
            JOIN entries e ON e.slug = entries_fts.slug
            WHERE entries_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (weights[0], weights[1], weights[2], weights[3], match, limit * 3),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    conn.close()
    hits = []
    for row in rows:
        snippet = row["body_snip"] or row["claim_snip"] or row["summary"]
        hits.append(
            {
                "slug": row["slug"],
                "title": row["title"],
                "kind": row["kind"],
                "summary": row["summary"],
                "status": row["status"],
                "updated_at": row["updated_at"],
                "rank": float(row["rank"]) if row["rank"] is not None else 0.0,
                "snippet": snippet,
            }
        )
    return _apply_filters(hits, kind, status)[:limit]


def _corpus(path: Path | None = None) -> list[dict]:
    conn = connect(path)
    rows = conn.execute("SELECT slug, title, kind, summary, body, status FROM entries").fetchall()
    claims_by: dict[str, list[str]] = defaultdict(list)
    hosts_by: dict[str, set[str]] = defaultdict(set)
    for claim in conn.execute("SELECT slug, text, url FROM claims").fetchall():
        claims_by[claim["slug"]].append(claim["text"])
        host = source_host(claim["url"])
        if host:
            hosts_by[claim["slug"]].add(host)
    wiki: dict[str, set[str]] = defaultdict(set)
    for link in conn.execute("SELECT from_slug, to_slug FROM links").fetchall():
        wiki[link["from_slug"]].add(link["to_slug"])
    conn.close()
    docs = []
    for row in rows:
        text = " ".join(
            [
                row["title"],
                row["title"],
                row["summary"],
                row["body"],
                " ".join(claims_by[row["slug"]]),
            ]
        )
        docs.append(
            {
                "slug": row["slug"],
                "title": row["title"],
                "kind": row["kind"],
                "status": row["status"],
                "summary": row["summary"],
                "tokens": tokenize(text),
                "hosts": hosts_by[row["slug"]],
                "wiki": wiki[row["slug"]],
            }
        )
    return docs


def _tfidf_vectors(docs: list[dict]) -> dict[str, dict[str, float]]:
    """IDF-weighted L2-normalised vectors. Slower than Jaccard; better ranking."""
    n = len(docs)
    df: Counter[str] = Counter()
    for doc in docs:
        df.update(set(doc["tokens"]))
    vectors: dict[str, dict[str, float]] = {}
    for doc in docs:
        tf = Counter(doc["tokens"])
        vec: dict[str, float] = {}
        for term, count in tf.items():
            idf = math.log((n + 1) / (df[term] + 1)) + 1.0
            # log-tf keeps long bodies from drowning titles we already doubled.
            vec[term] = (1.0 + math.log(count)) * idf
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vectors[doc["slug"]] = {term: value / norm for term, value in vec.items()}
    return vectors


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(value * b.get(term, 0.0) for term, value in a.items())


def related_entries(slug: str, limit: int = 6, path: Path | None = None) -> list[dict]:
    docs = _corpus(path)
    by_slug = {doc["slug"]: doc for doc in docs}
    if slug not in by_slug:
        return []
    vectors = _tfidf_vectors(docs)
    origin = by_slug[slug]
    origin_vec = vectors[slug]
    scored: list[tuple[float, dict]] = []
    for doc in docs:
        if doc["slug"] == slug:
            continue
        score = _cosine(origin_vec, vectors[doc["slug"]])
        if slug in doc["wiki"] or doc["slug"] in origin["wiki"]:
            score += 0.38
        if origin["title"].lower() in " ".join(doc["tokens"]) or doc["title"].lower() in " ".join(
            origin["tokens"]
        ):
            score += 0.12
        shared = origin["hosts"] & doc["hosts"]
        union = origin["hosts"] | doc["hosts"]
        if union:
            score += 0.18 * (len(shared) / len(union))
        if origin["kind"] == doc["kind"]:
            score += 0.04
        if score > 0.04:
            item = {
                "slug": doc["slug"],
                "title": doc["title"],
                "kind": doc["kind"],
                "status": doc["status"],
                "summary": doc["summary"],
                "score": round(score, 4),
                "linked": slug in doc["wiki"] or doc["slug"] in origin["wiki"],
            }
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:limit]]


def _force_layout(nodes: list[str], edges: list[tuple[str, str]], steps: int = 220) -> dict[str, dict]:
    """3D Fruchterman–Reingold. More steps than a cheap ring; better separation."""
    if not nodes:
        return {}
    rng = random.Random(7)
    n = len(nodes)
    area = 36.0
    k = math.sqrt(area / max(n, 1)) * 1.35
    pos = {
        slug: [
            rng.uniform(-2.2, 2.2),
            rng.uniform(-1.4, 1.4),
            rng.uniform(-2.2, 2.2),
        ]
        for slug in nodes
    }
    adj = {slug: set() for slug in nodes}
    for a, b in edges:
        if a in adj and b in adj and a != b:
            adj[a].add(b)
            adj[b].add(a)
    temperature = 1.6
    for _ in range(steps):
        disp = {slug: [0.0, 0.0, 0.0] for slug in nodes}
        slugs = list(nodes)
        for i, a in enumerate(slugs):
            pa = pos[a]
            for b in slugs[i + 1 :]:
                pb = pos[b]
                dx, dy, dz = pa[0] - pb[0], pa[1] - pb[1], pa[2] - pb[2]
                dist = math.sqrt(dx * dx + dy * dy + dz * dz) + 1e-6
                force = (k * k) / dist
                ux, uy, uz = dx / dist, dy / dist, dz / dist
                disp[a][0] += ux * force
                disp[a][1] += uy * force
                disp[a][2] += uz * force
                disp[b][0] -= ux * force
                disp[b][1] -= uy * force
                disp[b][2] -= uz * force
        for a, neighbours in adj.items():
            for b in neighbours:
                if a >= b:
                    continue
                pa, pb = pos[a], pos[b]
                dx, dy, dz = pa[0] - pb[0], pa[1] - pb[1], pa[2] - pb[2]
                dist = math.sqrt(dx * dx + dy * dy + dz * dz) + 1e-6
                force = (dist * dist) / k
                ux, uy, uz = dx / dist, dy / dist, dz / dist
                disp[a][0] -= ux * force
                disp[a][1] -= uy * force
                disp[a][2] -= uz * force
                disp[b][0] += ux * force
                disp[b][1] += uy * force
                disp[b][2] += uz * force
        for slug in slugs:
            dx, dy, dz = disp[slug]
            mag = math.sqrt(dx * dx + dy * dy + dz * dz) + 1e-6
            scale = min(mag, temperature) / mag
            pos[slug][0] += dx * scale
            pos[slug][1] += dy * scale
            pos[slug][2] += dz * scale
        temperature *= 0.97
    return {slug: {"x": p[0], "y": p[1], "z": p[2]} for slug, p in pos.items()}


def graph(path: Path | None = None) -> dict:
    docs = _corpus(path)
    vectors = _tfidf_vectors(docs) if docs else {}
    nodes = []
    edges = []
    seen_edge: set[tuple[str, str]] = set()

    def add_edge(a: str, b: str, kind: str, weight: float) -> None:
        pair = (a, b) if a < b else (b, a)
        key = (pair[0], pair[1], kind)
        if pair[0] == pair[1] or key in seen_edge:
            return
        seen_edge.add(key)
        edges.append({"from": a, "to": b, "kind": kind, "weight": round(weight, 4)})

    for doc in docs:
        nodes.append(
            {
                "slug": doc["slug"],
                "title": doc["title"],
                "kind": doc["kind"],
                "status": doc["status"],
                "summary": doc["summary"],
                "degree": 0,
            }
        )
        for target in doc["wiki"]:
            add_edge(doc["slug"], target, "wiki", 1.0)
    # Keep only the strongest lexical neighbours. A near-complete graph
    # collapses the force layout into one pile.
    related_pairs: list[tuple[float, str, str]] = []
    for i, a in enumerate(docs):
        for b in docs[i + 1 :]:
            score = _cosine(vectors[a["slug"]], vectors[b["slug"]])
            shared = a["hosts"] & b["hosts"]
            if shared:
                score += 0.12
            if score >= 0.28:
                related_pairs.append((score, a["slug"], b["slug"]))
    related_pairs.sort(reverse=True)
    kept = Counter()
    for score, a, b in related_pairs:
        if kept[a] >= 2 or kept[b] >= 2:
            continue
        add_edge(a, b, "related", score)
        kept[a] += 1
        kept[b] += 1
    layout = _force_layout(
        [n["slug"] for n in nodes],
        [(e["from"], e["to"]) for e in edges],
    )
    degree = Counter()
    for edge in edges:
        degree[edge["from"]] += 1
        degree[edge["to"]] += 1
    for node in nodes:
        node["degree"] = degree[node["slug"]]
        node.update(layout.get(node["slug"], {"x": 0.0, "y": 0.0, "z": 0.0}))
    return {"nodes": nodes, "edges": edges}


def stats(path: Path | None = None) -> dict:
    conn = connect(path)
    entries = conn.execute("SELECT COUNT(*) AS n FROM entries").fetchone()["n"]
    claims = conn.execute("SELECT COUNT(*) AS n FROM claims").fetchone()["n"]
    revisions = conn.execute("SELECT COUNT(*) AS n FROM revisions").fetchone()["n"]
    by_status = {
        row["status"]: row["n"]
        for row in conn.execute("SELECT status, COUNT(*) AS n FROM entries GROUP BY status")
    }
    by_kind = [
        {"kind": row["kind"], "n": row["n"]}
        for row in conn.execute("SELECT kind, COUNT(*) AS n FROM entries GROUP BY kind ORDER BY n DESC, kind")
    ]
    collapsed: Counter[str] = Counter()
    sourced = 0
    for claim in conn.execute("SELECT url, retrieved FROM claims").fetchall():
        host = source_host(claim["url"])
        if host:
            collapsed[host] += 1
        if is_http_url(claim["url"]) and is_iso_date(claim["retrieved"]):
            sourced += 1
    conn.close()
    return {
        "entries": entries,
        "claims": claims,
        "sourced_claims": sourced,
        "revisions": revisions,
        "by_status": {key: by_status.get(key, 0) for key in STATUSES},
        "by_kind": by_kind,
        "hosts": [{"host": host, "n": n} for host, n in collapsed.most_common(16)],
        "schema": SCHEMA_VERSION,
    }


def revisions_for(slug: str, path: Path | None = None) -> list[dict]:
    conn = connect(path)
    rows = conn.execute(
        """
        SELECT id, slug, title, kind, summary, body, status, claims_json, updated_at, superseded_at
        FROM revisions WHERE slug = ? ORDER BY id DESC
        """,
        (slug,),
    ).fetchall()
    conn.close()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["claims"] = json.loads(item.pop("claims_json"))
        except json.JSONDecodeError:
            item["claims"] = []
            item.pop("claims_json", None)
        out.append(item)
    return out


def export_tablet(path: Path | None = None) -> dict:
    conn = connect(path)
    entries = []
    for row in conn.execute("SELECT * FROM entries ORDER BY title").fetchall():
        claims = conn.execute(
            "SELECT text, url, retrieved FROM claims WHERE slug = ? ORDER BY id",
            (row["slug"],),
        ).fetchall()
        entries.append(entry_out(row, claims))
    conn.close()
    return {"entries": entries}


def parse_claims(raw_claims) -> tuple[list[dict] | None, str | None]:
    if not isinstance(raw_claims, list):
        return None, "claims must be a list"
    claims = []
    for claim in raw_claims:
        if not isinstance(claim, dict):
            return None, "each claim must be an object"
        text = str(claim.get("text") or "").strip()
        url = str(claim.get("url") or "").strip()
        retrieved = str(claim.get("retrieved") or "").strip()
        if not text and not url and not retrieved:
            continue
        if not text:
            return None, "a claim needs text"
        claims.append({"text": text, "url": url, "retrieved": retrieved})
    return claims, None


def propose(payload, path: Path | None = None, *, persist: bool = True):
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "json object required"}
    title = str(payload.get("title") or "").strip()
    summary = str(payload.get("summary") or "").strip()
    body = str(payload.get("body") or "").strip()
    slug = str(payload.get("slug") or "").strip().lower()
    slug = re.sub(r"\s+", "-", slug)
    kind = normalize_kind(payload.get("kind") or "note")
    if not KIND_RE.fullmatch(kind):
        return 400, {"ok": False, "error": "kind must be lowercase letters, numbers, and hyphens"}
    if not title or not summary or not body or not slug:
        return 400, {"ok": False, "error": "slug, title, summary, and body are required"}
    if not SLUG_RE.fullmatch(slug):
        return 400, {"ok": False, "error": "slug must be lowercase letters, numbers, and hyphens"}
    raw_claims = payload.get("claims") if "claims" in payload else []
    claims, claim_error = parse_claims(raw_claims)
    if claim_error:
        return 400, {"ok": False, "error": claim_error}
    requested = payload.get("status", None)
    if isinstance(requested, str):
        requested = requested.strip().lower() or None
    elif requested is not None:
        return 400, {"ok": False, "error": "status must be draft, verified, or disputed"}
    if requested is not None and requested not in STATUSES:
        return 400, {"ok": False, "error": "status must be draft, verified, or disputed"}
    status, reason = judge(claims or [], requested)
    out = {
        "ok": True,
        "slug": slug,
        "status": status,
        "reason": reason,
        "completeness": completeness(claims or []),
        "links": extract_wikilinks(body),
    }
    if requested and requested != status:
        out["requested_status"] = requested
    if not persist:
        return 200, out
    updated_at = now_stamp()
    conn = connect(path)
    try:
        save_entry(conn, slug, title, kind, summary, body, status, claims or [], updated_at)
        conn.commit()
    finally:
        conn.close()
    return 200, out
