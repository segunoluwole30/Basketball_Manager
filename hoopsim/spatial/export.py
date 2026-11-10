"""Turn a recorded HalfCourtGame into a self-contained viewer page."""
from __future__ import annotations

import json
from pathlib import Path

TEMPLATE = Path(__file__).with_name("viewer.html")
PLACEHOLDER = "/*GAME_DATA*/null"


def build_html(game: dict, standalone: bool = True) -> str:
    """``game`` is HalfCourtGame.to_dict(). Standalone pages get a full HTML document;
    otherwise just the page content (for hosts that add their own skeleton)."""
    template = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in template:
        raise RuntimeError("viewer template is missing its data placeholder")
    data = json.dumps(game, separators=(",", ":")).replace("</", "<\\/")   # safe inside <script>
    page = template.replace(PLACEHOLDER, data)
    if not standalone:
        return page
    head, body = page.split('<div class="app">', 1)
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
            f"{head}</head>\n<body>\n<div class=\"app\">{body}</body>\n</html>\n")


def write_html(game: dict, path: str | Path, standalone: bool = True) -> Path:
    path = Path(path)
    path.write_text(build_html(game, standalone), encoding="utf-8")
    return path
