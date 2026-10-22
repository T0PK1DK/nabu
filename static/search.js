(function () {
  var form = document.getElementById("ask");
  var input = document.getElementById("q");
  var kind = document.getElementById("kind");
  var status = document.getElementById("status");
  var voice = document.getElementById("voice");
  var registers = document.getElementById("registers");
  var slab = document.getElementById("slab");
  var count = document.getElementById("count");
  var button = form.querySelector("button");
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var seeking = ["I lift the stylus.", "I search the clay.", "I read only what is written."];
  var timer = 0;
  var liveTimer = 0;
  var seq = 0;

  function answer(n, q) {
    var words = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve"];
    if (!q && !n && (kind.value || status.value)) return "Those filters left the tablet blank.";
    if (!q) return "The tablet is open. Ask, and I will find the line.";
    if (!n) return "I turned the tablet. That is not written.";
    if (n === 1) return "One line was already there.";
    return (words[n] || String(n)) + " lines were already written.";
  }

  function href(path, params) {
    var url = new URL(path, location.origin);
    Object.keys(params).forEach(function (key) {
      if (params[key]) url.searchParams.set(key, params[key]);
    });
    return url.pathname + url.search;
  }

  function paintSnippet(text) {
    var p = document.createElement("p");
    p.className = "snip";
    var parts = String(text || "").split(/[\u001e\u001f]/);
    var marked = false;
    parts.forEach(function (part) {
      if (!part) {
        marked = !marked;
        return;
      }
      if (marked) {
        var mark = document.createElement("mark");
        mark.textContent = part;
        p.appendChild(mark);
      } else {
        p.appendChild(document.createTextNode(part));
      }
      marked = !marked;
    });
    return p;
  }

  function hit(h, i) {
    var a = document.createElement("a");
    a.className = "hit";
    a.href = "/e/" + encodeURIComponent(h.slug);
    var num = document.createElement("span");
    num.className = "num";
    num.textContent = String(i + 1).padStart(2, "0");
    var entry = document.createElement("span");
    entry.className = "entry";
    var line = document.createElement("span");
    line.className = "line";
    var title = document.createElement("h2");
    title.textContent = h.title;
    var badge = document.createElement("span");
    badge.className = "status " + (h.status || "");
    badge.appendChild(document.createElement("i"));
    badge.appendChild(document.createTextNode(h.status || ""));
    line.appendChild(title);
    line.appendChild(badge);
    var kindEl = document.createElement("span");
    kindEl.className = "kind";
    kindEl.textContent = h.kind || "";
    entry.appendChild(line);
    entry.appendChild(kindEl);
    if (h.snippet) entry.appendChild(paintSnippet(h.snippet));
    else {
      var summary = document.createElement("p");
      summary.textContent = h.summary || "";
      entry.appendChild(summary);
    }
    a.appendChild(num);
    a.appendChild(entry);
    return a;
  }

  function paintEmpty(q) {
    var wrap = document.createElement("div");
    wrap.className = "empty-state";
    wrap.setAttribute("role", "status");
    var empty = document.createElement("p");
    empty.className = "empty";
    var asked = q || "that line";
    var among = [];
    if (kind.value) among.push("kind " + kind.value);
    if (status.value) among.push("status " + status.value);
    empty.textContent =
      "Nothing on the tablet for “" + asked + "”" +
      (among.length ? " among " + among.join(" and ") : "") + ".";
    var hint = document.createElement("p");
    hint.className = "empty-hint";
    hint.textContent = "The clay only answers what is already cut. Try a shorter stem, or open the whole tablet.";
    var chips = document.createElement("div");
    chips.className = "chips";
    if (kind.value || status.value) {
      var clear = document.createElement("a");
      clear.className = "chip";
      clear.href = href("/", { q: q });
      clear.textContent = "Clear filters";
      chips.appendChild(clear);
    }
    var open = document.createElement("a");
    open.className = "chip";
    open.href = "/";
    open.textContent = "Open the tablet";
    chips.appendChild(open);
    wrap.appendChild(empty);
    wrap.appendChild(hint);
    wrap.appendChild(chips);
    return wrap;
  }

  function paint(q, hits, fresh) {
    registers.replaceChildren();
    if (!hits.length) {
      registers.appendChild(paintEmpty(q));
      count.textContent = "No hits 00";
    } else {
      hits.forEach(function (h, i) { registers.appendChild(hit(h, i)); });
      count.textContent = (q ? "Hits " : "The tablet ") + String(hits.length).padStart(2, "0");
    }
    registers.classList.toggle("fresh", !!(fresh && !reduced));
    voice.textContent = answer(hits.length, q);
  }

  function ask(q, ritual) {
    var mine = ++seq;
    if (timer) clearInterval(timer);
    button.disabled = true;
    slab.classList.add("searching");
    var step = 0;
    if (ritual && !reduced) {
      voice.textContent = seeking[0];
      timer = setInterval(function () {
        step = (step + 1) % seeking.length;
        voice.textContent = seeking[step];
      }, 280);
    }
    var started = performance.now();
    var url = href("/api/search", { q: q, kind: kind.value, status: status.value });
    return fetch(url)
      .then(function (res) { return res.json(); })
      .then(function (data) {
        var wait = ritual && !reduced ? Math.max(0, 220 - (performance.now() - started)) : 0;
        return new Promise(function (resolve) {
          setTimeout(function () { resolve(data); }, wait);
        });
      })
      .then(function (data) {
        if (mine !== seq) return;
        clearInterval(timer);
        timer = 0;
        button.disabled = false;
        slab.classList.remove("searching");
        paint(q, data.hits || [], ritual);
      })
      .catch(function () {
        if (mine !== seq) return;
        location.href = href("/", { q: q, kind: kind.value, status: status.value });
      });
  }

  function current() {
    return {
      q: input.value.trim(),
      kind: kind.value,
      status: status.value
    };
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (liveTimer) clearTimeout(liveTimer);
    var state = current();
    history.pushState(state, "", href("/", state));
    ask(state.q, true);
  });

  input.addEventListener("input", function () {
    if (liveTimer) clearTimeout(liveTimer);
    liveTimer = setTimeout(function () {
      var state = current();
      history.replaceState(state, "", href("/", state));
      syncChips();
      ask(state.q, false);
    }, 160);
  });

  function syncChips() {
    document.querySelectorAll("a.chip").forEach(function (link) {
      var facet = link.getAttribute("data-facet");
      var value = link.getAttribute("data-value") || "";
      var current = facet === "status" ? status.value : kind.value;
      link.setAttribute("aria-current", value === current ? "true" : "false");
    });
  }

  function onChipClick(event) {
    var link = event.target.closest("a.chip");
    if (!link) return;
    event.preventDefault();
    var facet = link.getAttribute("data-facet");
    var value = link.getAttribute("data-value") || "";
    if (facet === "status") status.value = value;
    else if (facet === "kind") kind.value = value;
    syncChips();
    if (liveTimer) clearTimeout(liveTimer);
    var state = current();
    history.pushState(state, "", href("/", state));
    ask(state.q, false);
  }

  var kindChips = document.getElementById("kind-chips");
  var statusChips = document.getElementById("status-chips");
  if (kindChips) kindChips.addEventListener("click", onChipClick);
  if (statusChips) statusChips.addEventListener("click", onChipClick);
  if (window.matchMedia("(max-width: 430px)").matches) {
    input.removeAttribute("autofocus");
    input.blur();
  }

  window.addEventListener("popstate", function () {
    var params = new URLSearchParams(location.search);
    input.value = params.get("q") || "";
    kind.value = params.get("kind") || "";
    status.value = params.get("status") || "";
    syncChips();
    ask(input.value.trim(), false);
  });
})();
