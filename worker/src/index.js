// Nabu on Cloudflare Workers: serves the page and answers at POST /api/answer.
// Sources are fetched live for each question (Wikipedia always, Brave Search when
// BRAVE_API_KEY is set), and Workers AI writes a cited answer from them.

import PAGE from "./page.js";

const UA = "Nabu/1.0 (cited answer engine; https://github.com/T0PK1DK/nabu)";
const MAX_Q = 500;
const SOURCE_TTL = 6 * 60 * 60;
const RATE_LIMIT = 30;
const RATE_WINDOW = 10 * 60;
const RATE_MEMORY = new Map();

const SPEC = `Output only lines that start with a tag. No markdown, no blank lines, nothing else.
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
Do not write @site or @result lines; the sources are already listed.`;

const FOCUS = {
  know: "The reader chose Encyclopedia focus: treat it as know and go deep.",
  do: "The reader chose How-to focus: treat it as do, with clear numbered steps.",
  compare: "The reader chose Compare focus: treat it as compare, with a table and a verdict.",
};

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method === "GET" && (url.pathname === "/" || url.pathname === "/index.html")) {
      return new Response(PAGE, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "public, max-age=300" } });
    }
    if (url.pathname === "/api/health") {
      return Response.json({ ok: true, ai: Boolean(env.AI), brave: Boolean(env.BRAVE_API_KEY), kv: Boolean(env && env.NABU), model: model(env) });
    }
    if (request.method === "POST" && url.pathname === "/api/answer") {
      return answer(request, env, ctx);
    }
    return new Response("Not found", { status: 404 });
  },
};

function model(env) {
  return env.NABU_MODEL || "@cf/meta/llama-3.3-70b-instruct-fp8-fast";
}

function clean(text) {
  return String(text || "").replace(/<[^>]+>/g, "").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
}

function oneLine(text, max = 220) {
  const t = clean(text).replace(/\|/g, "/");
  return t.length > max ? t.slice(0, max - 1).trimEnd() + "…" : t;
}

async function getJSON(url, headers = {}) {
  const res = await fetch(url, { headers: { "user-agent": UA, accept: "application/json", ...headers } });
  if (!res.ok) throw new Error(`${res.status} from ${new URL(url).hostname}`);
  return res.json();
}

async function wikipedia(q) {
  const found = await getJSON("https://en.wikipedia.org/w/api.php?" + new URLSearchParams({ action: "query", list: "search", srsearch: q, srlimit: "5", format: "json", origin: "*" }));
  const hits = (found.query && found.query.search) || [];
  const pages = await Promise.all(hits.slice(0, 3).map(async (hit) => {
    try {
      const page = await getJSON("https://en.wikipedia.org/api/rest_v1/page/summary/" + encodeURIComponent(hit.title.replace(/ /g, "_")));
      if (page.type === "disambiguation" || !page.extract) return null;
      return { name: page.title, url: page.content_urls.desktop.page, type: "reference", text: page.extract, snippet: page.description || oneLine(page.extract, 160) };
    } catch {
      return null;
    }
  }));
  const rich = pages.filter(Boolean);
  const seen = new Set(rich.map((p) => p.name));
  for (const hit of hits) {
    if (seen.has(hit.title)) continue;
    rich.push({ name: hit.title, url: "https://en.wikipedia.org/wiki/" + encodeURIComponent(hit.title.replace(/ /g, "_")), type: "reference", text: clean(hit.snippet), snippet: oneLine(hit.snippet, 160) });
  }
  return rich;
}

async function brave(q, key) {
  const data = await getJSON("https://api.search.brave.com/res/v1/web/search?" + new URLSearchParams({ q, count: "8", safesearch: "moderate" }), { "x-subscription-token": key });
  return ((data.web && data.web.results) || []).map((r) => {
    const host = new URL(r.url).hostname;
    const type = /youtube\.com|vimeo/.test(host) ? "video" : /reddit\.com|fandom\.com|stackexchange|stackoverflow|quora/.test(host) ? "community" : /wikipedia\.org|britannica/.test(host) ? "reference" : "official";
    return { name: (r.profile && r.profile.name) || host.replace(/^www\./, ""), title: clean(r.title), url: r.url, type, text: clean(r.description), snippet: oneLine(r.description, 200) };
  });
}

function kv(env) {
  return env && env.NABU;
}

function cacheKey(q) {
  return new Request("https://nabu.cache/sources?q=" + encodeURIComponent(q));
}

export async function readSourceCache(q, env) {
  const key = String(q || "").toLowerCase();
  if (!key) return null;
  const store = kv(env);
  if (store) {
    try {
      const hit = await store.get("src:" + key, "json");
      if (Array.isArray(hit)) return hit;
    } catch (err) {
      console.log("nabu: kv read skipped:", err && err.message);
    }
  }
  try {
    const cached = await caches.default.match(cacheKey(key));
    if (cached) {
      const data = await cached.json();
      if (Array.isArray(data)) return data;
    }
  } catch (err) {
    console.log("nabu: cache read skipped:", err && err.message);
  }
  return null;
}

export async function writeSourceCache(q, sources, env) {
  const key = String(q || "").toLowerCase();
  if (!key) return;
  const body = JSON.stringify(sources);
  const store = kv(env);
  if (store) {
    try {
      await store.put("src:" + key, body, { expirationTtl: SOURCE_TTL });
    } catch (err) {
      console.log("nabu: kv write skipped:", err && err.message);
    }
  }
  try {
    await caches.default.put(cacheKey(key), new Response(body, {
      headers: { "content-type": "application/json", "cache-control": "public, max-age=" + SOURCE_TTL },
    }));
  } catch (err) {
    console.log("nabu: cache write skipped:", err && err.message);
  }
}

export async function cachedGather(q, env, ctx) {
  const hit = await readSourceCache(q, env);
  if (hit) return hit;
  const sources = await gather(q, env);
  const save = writeSourceCache(q, sources, env);
  if (ctx && typeof ctx.waitUntil === "function") ctx.waitUntil(save);
  else await save;
  return sources;
}

function clientIP(request) {
  const cf = request.headers.get("cf-connecting-ip");
  if (cf) return cf;
  const forwarded = request.headers.get("x-forwarded-for");
  if (forwarded) return forwarded.split(",")[0].trim();
  return "local";
}

export async function allowAnswer(request, env) {
  const ip = clientIP(request);
  const now = Math.floor(Date.now() / 1000);
  const window = Math.floor(now / RATE_WINDOW);
  const store = kv(env);
  if (store) {
    try {
      const key = "rl:" + ip + ":" + window;
      const n = Number(await store.get(key)) || 0;
      if (n >= RATE_LIMIT) return false;
      await store.put(key, String(n + 1), { expirationTtl: RATE_WINDOW + 30 });
      return true;
    } catch (err) {
      console.log("nabu: kv rate limit skipped:", err && err.message);
    }
  }
  const bucket = RATE_MEMORY.get(ip);
  const stamps = bucket ? bucket.filter((t) => now - t < RATE_WINDOW) : [];
  if (stamps.length >= RATE_LIMIT) {
    RATE_MEMORY.set(ip, stamps);
    return false;
  }
  stamps.push(now);
  RATE_MEMORY.set(ip, stamps);
  return true;
}

export async function gather(q, env) {
  const jobs = [wikipedia(q).catch(() => [])];
  if (env.BRAVE_API_KEY) jobs.push(brave(q, env.BRAVE_API_KEY).catch(() => []));
  const [wiki, web = []] = await Promise.all(jobs);
  // Interleave so the strongest page of each kind leads.
  const out = [], seen = new Set();
  for (let i = 0; out.length < 8 && (i < wiki.length || i < web.length); i++) {
    for (const s of [web[i], wiki[i]]) {
      if (s && !seen.has(s.url)) { seen.add(s.url); out.push(s); }
    }
  }
  return out.slice(0, 8);
}

export function headerLines(sources) {
  const lines = [];
  sources.slice(0, 5).forEach((s) => lines.push(`@site ${oneLine(s.name, 80)} | ${s.url} | ${s.type} | ${oneLine(s.snippet, 120)}`));
  sources.forEach((s) => lines.push(`@result ${oneLine(s.title || s.name, 120)} | ${s.url} | ${oneLine(s.snippet || s.text, 220)}`));
  return lines;
}

export function buildMessages(q, sources, body) {
  const ctx = sources.slice(0, 5).map((s, i) => `[${i + 1}] ${s.title || s.name} (${s.url})\n${String(s.text || "").slice(0, 1500)}`).join("\n\n");
  const system = `You are Nabu, a search engine and encyclopedia. You answer from the numbered sources below, citing them as [n]. If the sources do not cover something, you may add well-established general knowledge without a citation, and lower @conf when you rely on it. Be specific: names, numbers, dates. No filler.\n\n${SPEC}\n\nSources:\n${ctx || "(no sources were found; answer from general knowledge, set @conf low, and say so in @unsure)"}`;
  const history = Array.isArray(body.history) ? body.history.slice(-10).filter((m) => m && (m.role === "user" || m.role === "assistant") && typeof m.content === "string").map((m) => ({ role: m.role, content: m.content.slice(0, 6000) })) : [];
  const ask = (history.length ? "Follow-up question: " : "Question: ") + q + (FOCUS[body.focus] ? "\n" + FOCUS[body.focus] : "") + (body.rewrite ? "\nWrite a fresh version of this answer." : "");
  return [{ role: "system", content: system }, ...history, { role: "user", content: ask }];
}

// Without a model, Nabu still answers: the lead of the best source, cited.
export function extractive(q, sources) {
  const lines = ["@intent know", "@title " + oneLine((sources[0] && sources[0].name) || q, 80), "@conf medium"];
  if (!sources.length) return lines.concat(["@tldr Nothing was found for this question. Try different words, or a live search below.", "@ask " + q + " explained"]);
  const sentences = clean(sources[0].text).split(/(?<=[.!?])\s+/);
  lines.push("@tldr " + sentences.slice(0, 2).join(" ") + " [1]");
  if (sentences.length > 2) lines.push("@p " + sentences.slice(2, 6).join(" ") + " [1]");
  sources.slice(1, 4).forEach((s, i) => { if (s.text) lines.push("@point " + oneLine(s.text, 200) + " [" + (i + 2) + "]"); });
  lines.push("@unsure This answer was read straight from the sources; the writing model was unavailable.");
  return lines;
}

function sseToText() {
  let buf = "";
  return new TransformStream({
    transform(chunk, controller) {
      buf += typeof chunk === "string" ? chunk : new TextDecoder().decode(chunk, { stream: true });
      const parts = buf.split("\n");
      buf = parts.pop();
      for (const line of parts) {
        const m = /^data:\s*(.*)$/.exec(line.trim());
        if (!m || m[1] === "[DONE]") continue;
        try { const j = JSON.parse(m[1]); if (j.response) controller.enqueue(j.response); } catch { /* partial frame */ }
      }
    },
  });
}

async function answer(request, env, ctx) {
  if (!(await allowAnswer(request, env))) {
    return new Response("Too many questions. Give the shelves a few minutes.", {
      status: 429,
      headers: { "retry-after": "60", "content-type": "text/plain; charset=utf-8" },
    });
  }
  let body;
  try { body = await request.json(); } catch { return new Response("invalid json", { status: 400 }); }
  const q = String((body && body.q) || "").trim().slice(0, MAX_Q);
  if (!q) return new Response("q is required", { status: 400 });

  const sources = await cachedGather(q, env, ctx);
  const head = headerLines(sources).join("\n") + "\n";
  const enc = new TextEncoder();
  const { readable, writable } = new TransformStream();
  const writer = writable.getWriter();

  (async () => {
    await writer.write(enc.encode(head));
    try {
      if (!env.AI) throw new Error("no AI binding");
      const stream = await env.AI.run(model(env), { messages: buildMessages(q, sources, body), stream: true, max_tokens: 1400, temperature: 0.3 });
      const reader = stream.pipeThrough(sseToText()).getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        await writer.write(enc.encode(value));
      }
    } catch (err) {
      console.log("nabu: model failed:", err && err.message);
      await writer.write(enc.encode(extractive(q, sources).join("\n")));
    }
    await writer.close();
  })();

  return new Response(readable, { headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store", "x-nabu-sources": String(sources.length) } });
}
