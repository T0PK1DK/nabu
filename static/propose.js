(function () {
  var form = document.getElementById("propose");
  var box = document.getElementById("claims");
  var reason = document.getElementById("reason");
  var voice = document.getElementById("voice");
  var meterWrap = document.getElementById("meter-wrap");
  var slugNote = document.getElementById("slug-note");
  var cut = document.getElementById("cut");
  var cutNew = document.getElementById("cut-new");
  var loadBtn = document.getElementById("load-slug");
  var today = window.NABU_TODAY || "";
  var timer = 0;
  var originalSlug = (form.slug.getAttribute("data-original") || "").trim();

  function setNote(text) {
    if (slugNote) slugNote.textContent = text || "";
  }

  function setRevising(slug, title) {
    originalSlug = slug || "";
    form.slug.setAttribute("data-original", originalSlug);
    form.slug.readOnly = !!originalSlug;
    if (cut) cut.textContent = originalSlug ? "Replace this tablet" : "Cut into the tablet";
    if (cutNew) cutNew.hidden = !originalSlug;
    setNote(
      originalSlug
        ? "This slug is locked to the tablet you are revising. Unlock it only to cut a new line."
        : ""
    );
    if (title) {
      voice.textContent = "This tablet is already on the clay. Revise it.";
    }
  }

  function setNewCut(message) {
    originalSlug = "";
    form.slug.removeAttribute("data-original");
    form.slug.readOnly = false;
    if (cut) cut.textContent = "Cut into the tablet";
    if (cutNew) cutNew.hidden = true;
    setNote(message || "");
  }

  function field(name, type, placeholder, value) {
    var input = document.createElement("input");
    input.name = name;
    input.type = type || "text";
    input.placeholder = placeholder || "";
    input.value = value || "";
    return input;
  }

  function labeled(title, node) {
    var label = document.createElement("label");
    label.appendChild(document.createTextNode(title));
    label.appendChild(node);
    return label;
  }

  function addClaim(preset) {
    var wrap = document.createElement("div");
    wrap.className = "claim";
    var head = document.createElement("div");
    head.className = "claim-head";
    head.appendChild(document.createElement("span")).textContent = "Claim";
    var remove = document.createElement("button");
    remove.type = "button";
    remove.className = "ghost remove-claim";
    remove.textContent = "Remove";
    head.appendChild(remove);
    wrap.appendChild(head);
    var text = document.createElement("textarea");
    text.name = "text";
    text.placeholder = "A single claim, one sentence.";
    text.value = (preset && preset.text) || "";
    wrap.appendChild(labeled("Text", text));
    wrap.appendChild(labeled("Source URL", field("url", "url", "https://…", preset && preset.url)));
    wrap.appendChild(labeled("Retrieved", field("retrieved", "date", "YYYY-MM-DD", (preset && preset.retrieved) || today)));
    box.appendChild(wrap);
    numberClaims();
    scheduleJudge();
  }

  function numberClaims() {
    Array.prototype.forEach.call(box.querySelectorAll(".claim-head span"), function (node, i) {
      node.textContent = "Claim " + (i + 1);
    });
  }

  function payload() {
    var claims = Array.prototype.map.call(box.querySelectorAll(".claim"), function (node) {
      var text = node.querySelector("[name=text]");
      var url = node.querySelector("[name=url]");
      var retrieved = node.querySelector("[name=retrieved]");
      return {
        text: text ? text.value.trim() : "",
        url: url ? url.value.trim() : "",
        retrieved: retrieved ? retrieved.value.trim() : ""
      };
    }).filter(function (claim) {
      return claim.text || claim.url || claim.retrieved;
    });
    return {
      slug: form.slug.value.trim(),
      title: form.title.value.trim(),
      kind: form.kind.value.trim(),
      summary: form.summary.value.trim(),
      body: form.body.value.trim(),
      status: form.status.value,
      claims: claims
    };
  }

  function paintMeter(ratio) {
    var pct = Math.round(Math.max(0, Math.min(1, Number(ratio) || 0)) * 100);
    meterWrap.innerHTML =
      '<div class="meter" role="img" aria-label="Citation completeness ' + pct + ' percent">' +
      '<span class="bar"><i style="width:' + pct + '%"></i></span>' +
      "<span>" + pct + "% sourced</span></div>";
  }

  function send(path) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload())
    }).then(function (res) {
      return res.json().then(function (data) { return { ok: res.ok, data: data }; });
    });
  }

  function scheduleJudge() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () {
      if (!form.slug.value.trim() && !form.title.value.trim() && !payload().claims.length) {
        paintMeter(0);
        return;
      }
      send("/api/judge").then(function (result) {
        var data = result.data || {};
        if (data.completeness) paintMeter(data.completeness.ratio);
        if (data.reason) reason.textContent = data.reason;
        else if (data.error) reason.textContent = data.error;
      }).catch(function () {
        reason.textContent = "The judge could not be reached.";
      });
    }, 280);
  }

  function fill(entry) {
    form.slug.value = entry.slug || "";
    form.title.value = entry.title || "";
    form.kind.value = entry.kind || "note";
    form.summary.value = entry.summary || "";
    form.body.value = entry.body || "";
    if (entry.status) form.status.value = entry.status;
    box.replaceChildren();
    (entry.claims || []).forEach(function (claim) { addClaim(claim); });
    if (!box.children.length) addClaim();
    paintMeter(entry.completeness && entry.completeness.ratio);
    setRevising(entry.slug, entry.title);
    reason.textContent = "Loaded from the tablet. The judge still applies.";
    if (form.title && form.title.focus) form.title.focus();
  }

  function loadSlug(slug, replaceUrl) {
    slug = (slug || "").trim().toLowerCase();
    if (!slug) return;
    if (loadBtn) {
      loadBtn.disabled = true;
      loadBtn.textContent = "Reading…";
    }
    fetch("/api/e/" + encodeURIComponent(slug))
      .then(function (res) {
        if (res.status === 404) {
          voice.textContent = "That slug is not on the tablet yet. Cut a new line.";
          reason.textContent = "";
          setNewCut("That slug is not on the tablet yet. The form still holds what you typed.");
          return null;
        }
        return res.json();
      })
      .then(function (entry) {
        if (!entry) return;
        fill(entry);
        if (replaceUrl) history.replaceState({}, "", "/propose?slug=" + encodeURIComponent(entry.slug));
      })
      .catch(function () {
        reason.textContent = "The tablet could not be read.";
      })
      .then(function () {
        if (loadBtn) {
          loadBtn.disabled = false;
          loadBtn.textContent = "Load tablet";
        }
      });
  }

  document.getElementById("add-claim").addEventListener("click", function () { addClaim(); });
  loadBtn.addEventListener("click", function () {
    loadSlug(form.slug.value, true);
  });
  form.slug.addEventListener("keydown", function (event) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    if (form.slug.readOnly) return;
    loadSlug(form.slug.value, true);
  });
  if (cutNew) {
    cutNew.addEventListener("click", function () {
      setNewCut("Slug unlocked. Submit will cut a new tablet instead of replacing this one.");
      form.slug.focus();
    });
  }
  box.addEventListener("click", function (event) {
    var button = event.target.closest(".remove-claim");
    if (!button) return;
    var claim = button.closest(".claim");
    if (claim) claim.remove();
    numberClaims();
    scheduleJudge();
  });
  document.getElementById("judge").addEventListener("click", function () {
    send("/api/judge").then(function (result) {
      var data = result.data || {};
      reason.textContent = data.reason || data.error || "No judgment.";
      if (data.completeness) paintMeter(data.completeness.ratio);
      if (data.status) voice.textContent = "Judged as " + data.status + ".";
    }).catch(function () {
      reason.textContent = "The judge could not be reached.";
    });
  });
  form.addEventListener("input", scheduleJudge);
  form.addEventListener("change", scheduleJudge);
  var cutting = false;
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (cutting) return;
    cutting = true;
    if (cut) cut.disabled = true;
    send("/api/propose").then(function (result) {
      var data = result.data || {};
      reason.textContent = data.reason || data.error || "No reply.";
      if (data.completeness) paintMeter(data.completeness.ratio);
      if (result.ok && data.slug) {
        voice.textContent = "Cut. Status is " + data.status + ".";
        setTimeout(function () { location.href = "/e/" + encodeURIComponent(data.slug); }, 420);
        return;
      }
      cutting = false;
      if (cut) cut.disabled = false;
    }).catch(function () {
      cutting = false;
      if (cut) cut.disabled = false;
      reason.textContent = "The cut could not be written.";
    });
  });

  if (!box.children.length) addClaim();
  else {
    numberClaims();
    if (window.NABU_PREFILLED) scheduleJudge();
  }
})();
