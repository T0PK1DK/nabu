(function () {
  var form = document.getElementById("propose");
  var box = document.getElementById("claims");
  var reason = document.getElementById("reason");
  var voice = document.getElementById("voice");
  var today = window.NABU_TODAY || "";

  function field(name, type, placeholder, value) {
    var input = document.createElement("input");
    input.name = name;
    input.type = type || "text";
    input.placeholder = placeholder || "";
    input.value = value || "";
    return input;
  }

  function addClaim(preset) {
    var wrap = document.createElement("div");
    wrap.className = "claim";
    var text = document.createElement("textarea");
    text.name = "text";
    text.placeholder = "A single claim, one sentence.";
    text.value = (preset && preset.text) || "";
    wrap.appendChild(text);
    wrap.appendChild(field("url", "url", "https://…", preset && preset.url));
    wrap.appendChild(field("retrieved", "date", "YYYY-MM-DD", (preset && preset.retrieved) || today));
    box.appendChild(wrap);
  }

  function payload() {
    var claims = Array.prototype.map.call(box.querySelectorAll(".claim"), function (node) {
      return {
        text: node.querySelector("[name=text]").value.trim(),
        url: node.querySelector("[name=url]").value.trim(),
        retrieved: node.querySelector("[name=retrieved]").value.trim()
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

  function send(path) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload())
    }).then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); });
  }

  document.getElementById("add-claim").addEventListener("click", function () { addClaim(); });
  document.getElementById("judge").addEventListener("click", function () {
    send("/api/judge").then(function (result) {
      reason.textContent = result.data.reason || result.data.error || "No judgment.";
      if (result.data.status) voice.textContent = "Judged as " + result.data.status + ".";
    });
  });
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    send("/api/propose").then(function (result) {
      reason.textContent = result.data.reason || result.data.error || "No reply.";
      if (result.ok && result.data.slug) {
        voice.textContent = "Cut. Status is " + result.data.status + ".";
        setTimeout(function () { location.href = "/e/" + encodeURIComponent(result.data.slug); }, 700);
      }
    });
  });
  addClaim();
})();
