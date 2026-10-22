#!/usr/bin/env python3
"""Nabu v0.3 — local cited encyclopedia, search index, and tablet archive.

Python 3 standard library only. SQLite FTS5. No crawler, no embeddings.
"""

from __future__ import annotations

import html
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from nabu import __version__
from nabu.core import (
    STATIC,
    export_tablet,
    get_entry,
    graph,
    init_db,
    propose,
    revisions_for,
    search,
    seed,
    stats,
    today_iso,
)

HOST = os.environ.get("NABU_HOST", "127.0.0.1")
PORT = int(os.environ.get("NABU_PORT", "8787"))
ASSET_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def mark_snippet(snippet: str) -> str:
    safe = esc(snippet or "")
    return safe.replace("\x1e", "<mark>").replace("\x1f", "</mark>")


def render_template(name: str, mapping: dict) -> bytes:
    text = (STATIC / name).read_text(encoding="utf-8")
    pattern = re.compile(r"\{\{(" + "|".join(re.escape(key) for key in mapping) + r")\}\}")
    return pattern.sub(lambda match: mapping[match.group(1)], text).encode("utf-8")


def voice_for(q: str, count: int, kind: str = "", status: str = "") -> str:
    if not q and count == 0 and (kind or status):
        return "Those filters left the tablet blank."
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


def render_empty(q: str, kind: str = "", status: str = "") -> str:
    asked = q or "that line"
    bits = []
    if kind:
        bits.append(f"kind {esc(kind)}")
    if status:
        bits.append(f"status {esc(status)}")
    among = (" among " + " and ".join(bits)) if bits else ""
    actions = []
    if kind or status:
        actions.append(f'<a class="chip" href="{esc(search_href(q, "", ""))}">Clear filters</a>')
    actions.append('<a class="chip" href="/">Open the tablet</a>')
    return (
        '<div class="empty-state" role="status">'
        f'<p class="empty">Nothing on the tablet for “{esc(asked)}”{among}.</p>'
        '<p class="empty-hint">The clay only answers what is already cut. '
        "Try a shorter stem, or open the whole tablet.</p>"
        f'<div class="chips">{"".join(actions)}</div>'
        "</div>"
    )


def render_hits(hits: list[dict], q: str = "", kind: str = "", status: str = "") -> str:
    if not hits:
        return render_empty(q, kind, status)
    parts = []
    for i, hit in enumerate(hits, 1):
        status = hit["status"]
        snippet = hit.get("snippet")
        extra = (
            f'<p class="snip">{mark_snippet(snippet)}</p>'
            if snippet and hit.get("rank") is not None
            else f'<p>{esc(hit.get("summary") or "")}</p>'
        )
        parts.append(
            f"""<a class="hit" href="/e/{esc(hit["slug"])}">
  <span class="num">{i:02d}</span>
  <span class="entry">
    <span class="line"><h2>{esc(hit["title"])}</h2><span class="status {esc(status)}"><i></i>{esc(status)}</span></span>
    <span class="kind">{esc(hit["kind"])}</span>
    {extra}
  </span>
</a>"""
        )
    return "".join(parts)


def search_href(q: str = "", kind: str = "", status: str = "") -> str:
    params = []
    if q:
        params.append("q=" + quote_plus(q))
    if kind:
        params.append("kind=" + quote_plus(kind))
    if status:
        params.append("status=" + quote_plus(status))
    return "/?" + "&".join(params) if params else "/"


def render_chips(field: str, selected: str, values: list[str], *, q: str, kind: str, status: str) -> str:
    facet = "kind" if field.startswith("kind") else "status"
    parts = []
    blank_href = search_href(q, "" if facet == "kind" else kind, "" if facet == "status" else status)
    parts.append(
        f'<a class="chip" href="{esc(blank_href)}" data-facet="{facet}" data-value="" '
        f'aria-current="{"true" if not selected else "false"}">all {esc(field)}</a>'
    )
    for value in values:
        href = search_href(
            q,
            value if facet == "kind" else kind,
            value if facet == "status" else status,
        )
        current = "true" if value == selected else "false"
        parts.append(
            f'<a class="chip" href="{esc(href)}" data-facet="{facet}" data-value="{esc(value)}" '
            f'aria-current="{current}">{esc(value)}</a>'
        )
    return "".join(parts)


def render_home(q: str, kind: str = "", status: str = "") -> bytes:
    q = (q or "").strip()
    kind = (kind or "").strip()
    status = (status or "").strip()
    hits = search(q, kind=kind or None, status=status or None)
    tablet = stats()
    kinds = [row["kind"] for row in tablet["by_kind"]]
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
            "RESULTS": render_hits(hits, q, kind, status),
            "LABEL": esc(label),
            "COUNT": f"{len(hits):02d}",
            "VOICE": esc(voice_for(q, len(hits), kind, status)),
            "KIND_CHIPS": render_chips("kinds", kind, kinds, q=q, kind=kind, status=status),
            "STATUS_CHIPS": render_chips(
                "statuses", status, ["draft", "verified", "disputed"], q=q, kind=kind, status=status
            ),
            "KIND": esc(kind),
            "STATUS": esc(status),
            "LEDGER": (
                f'{tablet["entries"]} entries · {tablet["sourced_claims"]}/'
                f'{tablet["claims"]} sourced claims · '
                f'{tablet["by_status"].get("verified", 0)} verified'
            ),
        },
    )


def render_wiki_body(body: str) -> str:
    escaped = esc(body)

    def replace(match: re.Match) -> str:
        slug = match.group(1)
        label = match.group(2) or slug.replace("-", " ")
        return f'<a class="wiki" href="/e/{esc(slug)}">{label}</a>'

    return re.sub(
        r"\[\[([a-z0-9]+(?:-[a-z0-9]+)*)(?:\|([^\]]+))?\]\]",
        replace,
        escaped,
    )


def render_claim_item(claim: dict) -> str:
    url = claim["url"]
    if claim.get("sourced"):
        src = f'<a class="src" href="{esc(url)}">{esc(url)}</a>'
    elif url:
        src = f'<span class="src">{esc(url)}</span>'
    else:
        src = '<span class="src missing">No source URL</span>'
    when = claim["retrieved"] or "no retrieval date"
    archive = ""
    if claim.get("archive"):
        archive = (
            f'<a class="archive" href="{esc(claim["archive"])}">'
            f"Wayback near {esc(claim['retrieved'])}</a>"
        )
    return (
        f"<li><p>{esc(claim['text'])}</p>{src}{archive}"
        f'<span class="when">retrieved {esc(when)}</span></li>'
    )


def render_claims(claims: list[dict]) -> str:
    if not claims:
        return (
            '<section class="claim-group">'
            "<h3>Claims</h3>"
            "<ol><li>No claims. This entry cannot be verified.</li></ol>"
            "</section>"
        )
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for claim in claims:
        key = claim.get("host") or "Unsourced"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(claim)
    parts = []
    for host in order:
        items = "".join(render_claim_item(claim) for claim in groups[host])
        extra = " unsourced" if host == "Unsourced" else ""
        parts.append(
            f'<section class="claim-group{extra}">'
            f"<h3>{esc(host)}</h3><ol>{items}</ol></section>"
        )
    return "".join(parts)


def render_related(items: list[dict], empty: str) -> str:
    if not items:
        return f'<p class="empty">{esc(empty)}</p>'
    parts = []
    for item in items:
        flag = " linked" if item.get("linked") else ""
        parts.append(
            f"""<a class="related{flag}" href="/e/{esc(item["slug"])}">
  <span class="kind">{esc(item["kind"])}</span>
  <strong>{esc(item["title"])}</strong>
  <span class="status {esc(item["status"])}">{esc(item["status"])}</span>
</a>"""
        )
    return "".join(parts)


def render_revisions(items: list[dict]) -> str:
    if not items:
        return "<li>No earlier cuts of this tablet.</li>"
    parts = []
    for item in items:
        when = (item.get("updated_at") or "").replace("T", " ").replace("Z", " UTC")
        parts.append(
            f'<li><span class="status {esc(item["status"])}">{esc(item["status"])}</span> '
            f"{esc(item.get('title') or '')} · {esc(when)}</li>"
        )
    return "".join(parts)


def meter(ratio: float) -> str:
    pct = int(round(max(0.0, min(1.0, ratio)) * 100))
    return (
        f'<div class="meter" role="img" aria-label="Citation completeness {pct} percent">'
        f'<span class="bar"><i style="width:{pct}%"></i></span>'
        f"<span>{pct}% sourced</span></div>"
    )


def missing_entry_map(slug: str = "", summary: str = "This slug is not in the index.") -> dict:
    return {
        "TITLE": "Not on the tablet",
        "KIND": "",
        "STATUS": "",
        "SUMMARY": esc(summary),
        "BODY": "",
        "CLAIMS": (
            '<section class="claim-group">'
            "<h3>Claims</h3><ol><li>No claims.</li></ol></section>"
        ),
        "UPDATED": "",
        "VOICE": "That page is not cut into the clay.",
        "RELATED": '<p class="empty">Nothing nearby.</p>',
        "BACKLINKS": '<p class="empty">No backlinks.</p>',
        "REVISIONS": "<li>No earlier cuts of this tablet.</li>",
        "METER": meter(0),
        "SLUG": esc(slug),
        "GRAPH": "[]",
        "REVISE": "",
    }


def render_entry(slug: str):
    entry = get_entry(slug)
    if not entry:
        return (render_template("entry.html", missing_entry_map(slug)), 404)
    updated = entry["updated_at"].replace("T", " ").replace("Z", " UTC")
    neighborhood = {
        "center": entry["slug"],
        "related": entry.get("related") or [],
        "backlinks": entry.get("backlinks") or [],
        "links": entry.get("links") or [],
    }
    return (
        render_template(
            "entry.html",
            {
                "TITLE": esc(entry["title"]),
                "KIND": esc(entry["kind"]),
                "STATUS": esc(entry["status"]),
                "SUMMARY": esc(entry["summary"]),
                "BODY": render_wiki_body(entry["body"]),
                "CLAIMS": render_claims(entry["claims"]),
                "UPDATED": esc("Revised " + updated),
                "VOICE": "This is what the clay says.",
                "RELATED": render_related(entry.get("related") or [], "No nearby tablets yet."),
                "BACKLINKS": render_related(
                    [
                        {
                            "slug": b["slug"],
                            "title": b["title"],
                            "kind": b["kind"],
                            "status": b["status"],
                            "linked": True,
                        }
                        for b in entry.get("backlinks") or []
                    ],
                    "No other tablet points here.",
                ),
                "REVISIONS": render_revisions(entry.get("revisions") or []),
                "METER": meter((entry.get("completeness") or {}).get("ratio") or 0),
                "SLUG": esc(entry["slug"]),
                "GRAPH": json.dumps(neighborhood, ensure_ascii=False).replace("<", "\\u003c"),
                "REVISE": (
                    f'<a class="revise" href="/propose?slug={esc(entry["slug"])}">'
                    "Revise this tablet</a>"
                ),
            },
        ),
        200,
    )


def render_ledger() -> bytes:
    tablet = stats()
    kinds = "".join(
        f'<li><a href="/?kind={esc(row["kind"])}">{esc(row["kind"])}</a> {row["n"]:02d}</li>'
        for row in tablet["by_kind"]
    )
    hosts = "".join(
        f"<li>{esc(row['host'])} {row['n']:02d}</li>" for row in tablet["hosts"]
    ) or "<li>No sourced hosts yet.</li>"
    statuses = "".join(
        f'<li><a href="/?status={esc(name)}">{esc(name)}</a> {tablet["by_status"].get(name, 0):02d}</li>'
        for name in ("verified", "draft", "disputed")
    )
    return render_template(
        "ledger.html",
        {
            "ENTRIES": f"{tablet['entries']:02d}",
            "CLAIMS": f"{tablet['sourced_claims']:02d}/{tablet['claims']:02d}",
            "REVISIONS": f"{tablet['revisions']:02d}",
            "KINDS": kinds,
            "HOSTS": hosts,
            "STATUSES": statuses,
            "VOICE": "A count of what is already on the tablet.",
        },
    )


def query_value(parsed, name: str) -> str:
    return parse_qs(parsed.query).get(name, [""])[0]


def status_options(selected: str) -> str:
    parts = []
    for name in ("verified", "draft", "disputed"):
        flag = " selected" if name == selected else ""
        parts.append(f'<option value="{name}"{flag}>{name}</option>')
    return "".join(parts)


def render_propose_claims(claims: list[dict], today: str) -> str:
    if not claims:
        return ""
    parts = []
    for i, claim in enumerate(claims, 1):
        parts.append(
            f"""<div class="claim">
  <div class="claim-head"><span>Claim {i}</span>
    <button type="button" class="ghost remove-claim">Remove</button></div>
  <label>Text
    <textarea name="text" placeholder="A single claim, one sentence.">{esc(claim.get("text") or "")}</textarea>
  </label>
  <label>Source URL
    <input name="url" type="url" placeholder="https://…" value="{esc(claim.get("url") or "")}">
  </label>
  <label>Retrieved
    <input name="retrieved" type="date" placeholder="YYYY-MM-DD" value="{esc(claim.get("retrieved") or today)}">
  </label>
</div>"""
        )
    return "".join(parts)


def render_propose(slug: str = "") -> bytes:
    slug = (slug or "").strip().lower()
    today = today_iso()
    entry = get_entry(slug) if slug else None
    if entry:
        voice = "This tablet is already on the clay. Revise it. I will still refuse unsourced verification."
        requested = entry.get("status") or "verified"
        ratio = (entry.get("completeness") or {}).get("ratio") or 0
        banner = (
            f'<p class="banner">Editing <a href="/e/{esc(entry["slug"])}">{esc(entry["title"])}</a>. '
            "A replace writes a revision snapshot. "
            f'<a href="/e/{esc(entry["slug"])}">Back to this tablet</a>.</p>'
        )
        return render_template(
            "propose.html",
            {
                "VOICE": esc(voice),
                "TODAY": esc(today),
                "SLUG": esc(entry["slug"]),
                "ORIGINAL_SLUG": esc(entry["slug"]),
                "SLUG_ATTRS": "readonly",
                "KIND": esc(entry["kind"]),
                "TITLE": esc(entry["title"]),
                "SUMMARY": esc(entry["summary"]),
                "BODY": esc(entry["body"]),
                "STATUS_OPTIONS": status_options(requested),
                "CLAIMS": render_propose_claims(entry.get("claims") or [], today),
                "METER": meter(ratio),
                "BANNER": banner,
                "PREFILLED": "true",
                "SUBMIT": "Replace this tablet",
                "CUT_NEW_HIDDEN": "",
                "SLUG_NOTE": "This slug is locked to the tablet you are revising. Unlock it only to cut a new line.",
            },
        )
    if slug:
        voice = "That slug is not on the tablet yet. Cut a new line."
    else:
        voice = "Write a line. I will not verify what has no source."
    return render_template(
        "propose.html",
        {
            "VOICE": esc(voice),
            "TODAY": esc(today),
            "SLUG": esc(slug),
            "ORIGINAL_SLUG": "",
            "SLUG_ATTRS": "",
            "KIND": "note",
            "TITLE": "",
            "SUMMARY": "",
            "BODY": "",
            "STATUS_OPTIONS": status_options("verified"),
            "CLAIMS": "",
            "METER": meter(0),
            "BANNER": "",
            "PREFILLED": "false",
            "SUBMIT": "Cut into the tablet",
            "CUT_NEW_HIDDEN": "hidden",
            "SLUG_NOTE": "",
        },
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[nabu]", self.address_string(), fmt % args)

    def send_json(self, code, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
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

    def send_page(self, name, mapping, code=200):
        self.send_html(render_template(name, mapping), code)

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
        if path.startswith("/static/"):
            name = path[len("/static/") :]
            if "/" in name or name.startswith("."):
                self.send_json(404, {"error": "not found"})
                return
            ext = os.path.splitext(name)[1].lower()
            self.send_asset(name, ASSET_TYPES.get(ext, "application/octet-stream"))
            return
        if path in ("/", "/index.html"):
            self.send_html(
                render_home(query_value(parsed, "q"), query_value(parsed, "kind"), query_value(parsed, "status"))
            )
            return
        if path == "/propose":
            self.send_html(render_propose(query_value(parsed, "slug")))
            return
        if path == "/archive":
            self.send_page(
                "archive.html",
                {"VOICE": "The tablets hang in the hall. Turn them."},
            )
            return
        if path == "/ledger":
            self.send_html(render_ledger())
            return
        if path == "/api/health":
            tablet = stats()
            self.send_json(
                200,
                {"ok": True, "name": "nabu", "version": __version__, "entries": tablet["entries"]},
            )
            return
        if path == "/api/stats":
            self.send_json(200, stats())
            return
        if path == "/api/graph":
            self.send_json(200, graph())
            return
        if path == "/api/export":
            self.send_json(200, export_tablet())
            return
        if path == "/api/search":
            q = query_value(parsed, "q")
            kind = query_value(parsed, "kind").strip() or None
            status = query_value(parsed, "status").strip() or None
            try:
                limit = int(query_value(parsed, "limit") or "20")
            except ValueError:
                limit = 20
            hits = search(q, kind=kind, status=status, limit=limit)
            self.send_json(200, {"query": q, "kind": kind, "status": status, "hits": hits})
            return
        if path.startswith("/api/e/") and path.endswith("/revisions"):
            slug = unquote(path[len("/api/e/") : -len("/revisions")]).strip("/")
            if not slug or "/" in slug:
                self.send_json(404, {"error": "not found"})
                return
            if not get_entry(slug):
                self.send_json(404, {"error": "not found"})
                return
            self.send_json(200, {"slug": slug, "revisions": revisions_for(slug)})
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
        if path.startswith("/api/"):
            self.send_json(404, {"error": "not found"})
            return
        self.send_html(
            render_template("entry.html", missing_entry_map(summary="This path is not in the index.")),
            404,
        )

    def do_POST(self):
        try:
            self.route_post()
        except Exception as exc:
            print("[nabu] error", exc)
            self.send_json(500, {"error": "server error"})

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return 400, {"ok": False, "error": "invalid length"}
        if length > 256_000:
            return 413, {"ok": False, "error": "body too large"}
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return 400, {"ok": False, "error": "invalid json"}
        return None, payload

    def route_post(self):
        path = urlparse(self.path).path
        if path not in ("/api/propose", "/api/judge"):
            self.send_json(404, {"error": "not found"})
            return
        code, payload = self.read_json()
        if code:
            self.send_json(code, payload)
            return
        persist = path == "/api/propose"
        code, body = propose(payload, persist=persist)
        self.send_json(code, body)


class Server(ThreadingHTTPServer):
    allow_reuse_address = True


def main():
    init_db()
    seed()
    server = Server((HOST, PORT), Handler)
    print(f"Nabu v{__version__} on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
