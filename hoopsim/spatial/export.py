"""Turn a recorded HalfCourtGame into a self-contained viewer page."""
from __future__ import annotations

import functools
import json
from pathlib import Path

TEMPLATE = Path(__file__).with_name("viewer.html")
PLACEHOLDER = "/*GAME_DATA*/null"
# The 3D view's library: three.js r160 (MIT, licence header kept), vendored so pages work
# offline. It is inlined once, as inert text the viewer only evaluates when 3D is switched on.
THREE_JS = Path(__file__).with_name("vendor") / "three.module.min.js"
THREE_VERSION = "0.160.0"
THREE_PLACEHOLDER = "/*THREE_JS*/"


@functools.lru_cache(maxsize=1)
def _three_source() -> str:
    src = THREE_JS.read_text(encoding="utf-8")
    # It sits inside a <script type="text/plain"> block: keep the HTML parser out of it.
    return src.replace("</", "<\\/").replace("<!--", "<\\!--")


def build_html(game: dict, standalone: bool = True, three: bool = True) -> str:
    """``game`` is HalfCourtGame.to_dict(). Standalone pages get a full HTML document;
    otherwise just the page content (for hosts that add their own skeleton). ``three``:
    include the 3D view (adds ~650 KB); without it the viewer is 2D only."""
    template = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in template or THREE_PLACEHOLDER not in template:
        raise RuntimeError("viewer template is missing its data placeholders")
    data = json.dumps(game, separators=(",", ":")).replace("</", "<\\/")   # safe inside <script>
    page = template.replace(THREE_PLACEHOLDER, _three_source() if three else "", 1)
    page = page.replace(PLACEHOLDER, data, 1)
    if not standalone:
        return page
    head, body = page.split('<div class="app">', 1)
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
            f"{head}</head>\n<body>\n<div class=\"app\">{body}</body>\n</html>\n")


def write_html(game: dict, path: str | Path, standalone: bool = True, three: bool = True) -> Path:
    path = Path(path)
    path.write_text(build_html(game, standalone, three), encoding="utf-8")
    return path
