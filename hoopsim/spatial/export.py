"""Turn a recorded HalfCourtGame into a self-contained viewer page."""
from __future__ import annotations

import base64
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
# The animated players of the 3D view: three.js r160's glTF loader and SkeletonUtils (MIT,
# unmodified) and one model with its motion-capture clips (see assets/LICENSES.md). The web
# app serves them as files under PLAYER_ASSETS_URL, fetched the first time 3D is shown; an
# exported page only carries them if asked to (animation="embed", about 0.9 MB more).
_VENDOR = Path(__file__).with_name("vendor")
PLAYER_ASSETS = {
    "BufferGeometryUtils.js": _VENDOR / "BufferGeometryUtils.js",
    "GLTFLoader.js": _VENDOR / "GLTFLoader.js",
    "SkeletonUtils.js": _VENDOR / "SkeletonUtils.js",
    "player.glb": Path(__file__).with_name("assets") / "player.glb",
}
PLAYER_ASSETS_URL = "/viewer-assets/"
PLAYER_ASSETS_PLACEHOLDER = "/*PLAYER_ASSETS*/"


@functools.lru_cache(maxsize=1)
def _three_source() -> str:
    src = THREE_JS.read_text(encoding="utf-8")
    # It sits inside a <script type="text/plain"> block: keep the HTML parser out of it.
    return src.replace("</", "<\\/").replace("<!--", "<\\!--")


@functools.lru_cache(maxsize=1)
def _embedded_players() -> str:
    files = {}
    for name, path in PLAYER_ASSETS.items():
        raw = path.read_bytes()
        files[name] = base64.b64encode(raw).decode("ascii") if name.endswith(".glb") else raw.decode("utf-8")
    return json.dumps({"embed": files}, separators=(",", ":")).replace("</", "<\\/").replace("<!--", "<\\!--")


def _player_assets(animation: str | None) -> str:
    """What the page is told about the animated players: nothing (simple bodies), the files
    inside the page ("embed"), or the address they are served from (anything else)."""
    if not animation:
        return ""
    if animation == "embed":
        return _embedded_players()
    return json.dumps({"url": animation if animation.endswith("/") else animation + "/"}).replace("</", "<\\/")


def build_html(game: dict, standalone: bool = True, three: bool = True, animation: str | None = None) -> str:
    """``game`` is HalfCourtGame.to_dict(). Standalone pages get a full HTML document;
    otherwise just the page content (for hosts that add their own skeleton). ``three``:
    include the 3D view (adds ~650 KB); without it the viewer is 2D only. ``animation``:
    the 3D view's animated players. None (the default): simple bodies only; "embed": the
    model, clips and loader travel in the page; a URL prefix (the web app passes
    PLAYER_ASSETS_URL): fetched from there the first time 3D is shown."""
    template = TEMPLATE.read_text(encoding="utf-8")
    if any(ph not in template for ph in (PLACEHOLDER, THREE_PLACEHOLDER, PLAYER_ASSETS_PLACEHOLDER)):
        raise RuntimeError("viewer template is missing its data placeholders")
    data = json.dumps(game, separators=(",", ":")).replace("</", "<\\/")   # safe inside <script>
    page = template.replace(THREE_PLACEHOLDER, _three_source() if three else "", 1)
    page = page.replace(PLAYER_ASSETS_PLACEHOLDER, _player_assets(animation) if three else "", 1)
    page = page.replace(PLACEHOLDER, data, 1)
    if not standalone:
        return page
    head, body = page.split('<div class="app">', 1)
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
            f"{head}</head>\n<body>\n<div class=\"app\">{body}</body>\n</html>\n")


def write_html(game: dict, path: str | Path, standalone: bool = True, three: bool = True,
               animation: str | None = None) -> Path:
    path = Path(path)
    path.write_text(build_html(game, standalone, three, animation), encoding="utf-8")
    return path
