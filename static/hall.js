(function () {
  var canvas = document.getElementById("hall");
  var raw = document.getElementById("graph-data");
  if (!canvas || !raw) return;
  var data;
  try { data = JSON.parse(raw.textContent || "{}"); } catch (err) { return; }
  var ctx = canvas.getContext("2d");
  var nodes = [{ slug: data.center || "", title: "here", kind: "", status: "verified", x: 0, y: 0, z: 0 }];
  var related = (data.related || []).slice(0, 8);
  related.forEach(function (item, i) {
    var angle = (i / Math.max(related.length, 1)) * Math.PI * 2;
    nodes.push({
      slug: item.slug,
      title: item.title,
      status: item.status,
      x: Math.cos(angle) * 1.6,
      y: Math.sin(angle) * 0.4,
      z: Math.sin(angle) * 1.6
    });
  });
  var yaw = 0.4;
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function project(node) {
    var x = node.x * Math.cos(yaw) - node.z * Math.sin(yaw);
    var z = node.x * Math.sin(yaw) + node.z * Math.cos(yaw);
    var scale = 90 / (z + 3.4);
    return { x: canvas.width / 2 + x * scale, y: canvas.height / 2 + node.y * scale, scale: scale };
  }

  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#0c0a08";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    nodes.slice(1).forEach(function (node) {
      var a = project(nodes[0]);
      var b = project(node);
      ctx.strokeStyle = "rgba(201,133,74,0.35)";
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    });
    nodes.forEach(function (node, index) {
      var p = project(node);
      var w = index === 0 ? 72 : 52;
      var h = w * 1.25;
      node._hit = { x: p.x - w / 2, y: p.y - h / 2, w: w, h: h, slug: node.slug };
      ctx.fillStyle = index === 0 ? "#4a3424" : "#2b211a";
      ctx.strokeStyle = "#e2b07a";
      ctx.strokeRect(p.x - w / 2, p.y - h / 2, w, h);
      ctx.fillRect(p.x - w / 2, p.y - h / 2, w, h);
      ctx.fillStyle = "#f3e6d4";
      ctx.font = "12px Palatino, Georgia, serif";
      ctx.textAlign = "center";
      ctx.fillText((node.title || "").slice(0, 16), p.x, p.y);
    });
    if (!reduced) yaw += 0.008;
    requestAnimationFrame(draw);
  }

  canvas.addEventListener("click", function (event) {
    var box = canvas.getBoundingClientRect();
    var x = (event.clientX - box.left) * (canvas.width / box.width);
    var y = (event.clientY - box.top) * (canvas.height / box.height);
    nodes.forEach(function (node) {
      var hit = node._hit;
      if (hit && x >= hit.x && x <= hit.x + hit.w && y >= hit.y && y <= hit.y + hit.h && node.slug) {
        location.href = "/e/" + encodeURIComponent(node.slug);
      }
    });
  });
  draw();
})();
