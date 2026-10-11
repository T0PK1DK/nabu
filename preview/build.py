#!/usr/bin/env python3
"""Build preview/nabu.html: the single-file Nabu that runs as a claude.ai artifact.

It inlines the scribe portrait and seed.json into template.html. The Sage in
this build is Claude, reached through the artifact's `sample` capability.
"""

import base64
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
img = "data:image/jpeg;base64," + base64.b64encode((ROOT / "static" / "scribe.jpg").read_bytes()).decode()
seed = json.dumps(json.loads((ROOT / "seed.json").read_text(encoding="utf-8")), ensure_ascii=False).replace("</", "<\\/")
page = (ROOT / "preview" / "template.html").read_text(encoding="utf-8").replace("__IMG__", img).replace("__SEED__", seed)
(ROOT / "preview" / "nabu.html").write_text(page, encoding="utf-8")
print("wrote preview/nabu.html")
