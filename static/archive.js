(function () {
  var canvas = document.getElementById("stage");
  var voice = document.getElementById("voice");
  var ctx = canvas.getContext("2d");
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var state = {
    nodes: [],
    edges: [],
    yaw: 0.7,
    pitch: 0.28,
    zoom: 280,
    drag: false,
    lastX: 0,
    lastY: 0,
    hover: null,
    t: 0
  };

  function resize() {
    var ratio = window.devicePixelRatio || 1;
    canvas.width = Math.floor(window.innerWidth * ratio);
    canvas.height = Math.floor(window.innerHeight * ratio);
    canvas.style.width = window.innerWidth + "px";
    canvas.style.height = window.innerHeight + "px";
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  }

  function project(node) {
    var cy = Math.cos(state.yaw);
    var sy = Math.sin(state.yaw);
    var cp = Math.cos(state.pitch);
    var sp = Math.sin(state.pitch);
    var x = node.x * cy - node.z * sy;
    var z = node.x * sy + node.z * cy;
    var y = node.y * cp - z * sp;
    z = node.y * sp + z * cp;
    var depth = z + 6.2;
    var scale = state.zoom / depth;
    return {
      x: window.innerWidth / 2 + x * scale,
      y: window.innerHeight / 2 + y * scale,
      scale: scale,
      depth: depth
    };
  }

  function colorFor(status) {
    if (status === "verified") return "#c5d2b2";
    if (status === "draft") return "#e0c078";
    if (status === "disputed") return "#d4846a";
    return "#e2b07a";
  }

  function draw() {
    var w = window.innerWidth;
    var h = window.innerHeight;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#070605";
    ctx.fillRect(0, 0, w, h);
    var glow = ctx.createRadialGradient(w * 0.5, h * 0.18, 20, w * 0.5, h * 0.4, h * 0.7);
    glow.addColorStop(0, "rgba(201,133,74,0.16)");
    glow.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, w, h);

    var projected = state.nodes.map(function (node) {
      return { node: node, p: project(node) };
    });
    projected.sort(function (a, b) { return b.p.depth - a.p.depth; });

    ctx.lineWidth = 1;
    state.edges.forEach(function (edge) {
      var a = state.nodes.find(function (n) { return n.slug === edge.from; });
      var b = state.nodes.find(function (n) { return n.slug === edge.to; });
      if (!a || !b) return;
      var pa = project(a);
      var pb = project(b);
      ctx.strokeStyle = edge.kind === "wiki" ? "rgba(226,176,122,0.45)" : "rgba(201,133,74,0.18)";
      ctx.beginPath();
      ctx.moveTo(pa.x, pa.y);
      ctx.lineTo(pb.x, pb.y);
      ctx.stroke();
    });

    state.hover = null;
    projected.forEach(function (item) {
      var node = item.node;
      var p = item.p;
      var width = Math.max(86, 0.42 * p.scale);
      var height = width * 1.28;
      var x = p.x - width / 2;
      var y = p.y - height / 2;
      node._hit = { x: x, y: y, w: width, h: height };
      var hovered = state.pointer && state.pointer.x >= x && state.pointer.x <= x + width && state.pointer.y >= y && state.pointer.y <= y + height;
      if (hovered) state.hover = node;
      ctx.fillStyle = hovered ? "#3a2a1c" : "#231b16";
      ctx.strokeStyle = colorFor(node.status);
      ctx.lineWidth = hovered ? 2 : 1;
      ctx.beginPath();
      ctx.rect(x, y, width, height);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = "#f3e6d4";
      ctx.font = Math.max(10, Math.min(16, width / 7)) + "px Palatino, Georgia, serif";
      ctx.textAlign = "center";
      wrapTitle(node.title, p.x, p.y - 6, width - 16, 3);
      ctx.fillStyle = colorFor(node.status);
      ctx.font = "10px ui-monospace, monospace";
      ctx.fillText(node.status, p.x, y + height - 12);
    });
  }

  function wrapTitle(text, x, y, max, lines) {
    var words = String(text).split(/\s+/);
    var line = "";
    var used = 0;
    words.forEach(function (word) {
      var next = line ? line + " " + word : word;
      if (ctx.measureText(next).width > max && line && used < lines) {
        ctx.fillText(line, x, y + used * 16);
        line = word;
        used += 1;
      } else {
        line = next;
      }
    });
    if (used < lines) ctx.fillText(line, x, y + used * 16);
  }

  function tick() {
    state.t += 1;
    if (!state.drag && !reduced) state.yaw += 0.0016;
    draw();
    requestAnimationFrame(tick);
  }

  canvas.addEventListener("pointerdown", function (event) {
    state.drag = true;
    state.lastX = event.clientX;
    state.lastY = event.clientY;
    canvas.classList.add("drag");
  });
  window.addEventListener("pointerup", function () {
    state.drag = false;
    canvas.classList.remove("drag");
  });
  window.addEventListener("pointermove", function (event) {
    state.pointer = { x: event.clientX, y: event.clientY };
    if (!state.drag) return;
    state.yaw += (event.clientX - state.lastX) * 0.006;
    state.pitch = Math.max(-1.1, Math.min(1.1, state.pitch + (event.clientY - state.lastY) * 0.004));
    state.lastX = event.clientX;
    state.lastY = event.clientY;
  });
  canvas.addEventListener("wheel", function (event) {
    event.preventDefault();
    state.zoom = Math.max(140, Math.min(520, state.zoom - event.deltaY * 0.2));
  }, { passive: false });
  canvas.addEventListener("click", function () {
    if (state.hover) location.href = "/e/" + encodeURIComponent(state.hover.slug);
  });
  canvas.addEventListener("mousemove", function () {
    if (state.hover) voice.textContent = state.hover.title + " — " + state.hover.status;
  });

  resize();
  window.addEventListener("resize", resize);
  fetch("/api/graph")
    .then(function (res) { return res.json(); })
    .then(function (data) {
      state.nodes = data.nodes || [];
      state.edges = data.edges || [];
      voice.textContent = state.nodes.length
        ? state.nodes.length + " tablets hang in the hall."
        : "The hall is empty.";
      tick();
    })
    .catch(function () {
      voice.textContent = "The hall could not be opened.";
    });
})();
