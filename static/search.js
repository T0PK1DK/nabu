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

  function answer(n, q) {
    var words = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve"];
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

  function paint(q, hits, fresh) {
    registers.replaceChildren();
    if (!hits.length) {
      var empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "Nothing on the tablet for that.";
      registers.appendChild(empty);
      count.textContent = "No hits 00";
    } else {
      hits.forEach(function (h, i) { registers.appendChild(hit(h, i)); });
      count.textContent = (q ? "Hits " : "The tablet ") + String(hits.length).padStart(2, "0");
    }
    registers.classList.toggle("fresh", !!(fresh && !reduced));
    voice.textContent = answer(hits.length, q);
  }

  function ask(q, ritual) {
    if (timer) clearInterval(timer);
    button.disabled = true;
    slab.classList.add("searching");
    var step = 0;
    if (ritual && !reduced) {
      voice.textContent = seeking[0];
      timer = setInterval(function () {
        step = (step + 1) % seeking.length;
        voice.textContent = seeking[step];
      }, 380);
    }
    var started = performance.now();
    var url = href("/api/search", { q: q, kind: kind.value, status: status.value });
    return fetch(url)
      .then(function (res) { return res.json(); })
      .then(function (data) {
        var wait = ritual && !reduced ? Math.max(0, 700 - (performance.now() - started)) : 0;
        return new Promise(function (resolve) {
          setTimeout(function () { resolve(data); }, wait);
        });
      })
      .then(function (data) {
        clearInterval(timer);
        timer = 0;
        button.disabled = false;
        slab.classList.remove("searching");
        paint(q, data.hits || [], ritual);
      })
      .catch(function () {
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
    var state = current();
    history.pushState(state, "", href("/", state));
    ask(state.q, true);
  });

  kind.addEventListener("change", function () { form.requestSubmit(); });
  status.addEventListener("change", function () { form.requestSubmit(); });

  window.addEventListener("popstate", function () {
    var params = new URLSearchParams(location.search);
    input.value = params.get("q") || "";
    kind.value = params.get("kind") || "";
    status.value = params.get("status") || "";
    ask(input.value.trim(), false);
  });
})();
