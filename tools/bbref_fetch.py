"""Polite page fetcher for the NBA roster builder (personal, non-commercial use).

Every page is cached under ``leagues/cache/`` so each one is fetched once; requests
are spaced out (Basketball-Reference asks for no more than about 20 a minute) and
identify themselves honestly.
"""
from __future__ import annotations

import os
import sys
import time
import urllib.request
from pathlib import Path

BASE = "https://www.basketball-reference.com"
USER_AGENT = "hoopsim-roster-builder/1.0 (personal, non-commercial use)"
DELAY = 3.5
CACHE = Path(__file__).resolve().parent.parent / "leagues" / "cache"

TEAMS = ["ATL", "BOS", "BRK", "CHO", "CHI", "CLE", "DAL", "DEN", "DET", "GSW", "HOU", "IND",
         "LAC", "LAL", "MEM", "MIA", "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHO",
         "POR", "SAC", "SAS", "TOR", "UTA", "WAS"]

_last = 0.0


def pages(year: int) -> dict[str, str]:
    """Cache file name -> path on the site, for the season ending in ``year``."""
    prev = year - 1
    out = {f"NBA_{y}_{kind}.html": f"/leagues/NBA_{y}_{kind}.html"
           for y in (year, prev, prev - 1) for kind in ("per_game", "advanced", "shooting")}
    out["contracts_players.html"] = "/contracts/players.html"
    out.update({f"team_{t}_{year}.html": f"/teams/{t}/{year}.html" for t in TEAMS})
    return out


def get(name: str, path: str, refresh: bool = False) -> str:
    global _last
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / name
    if f.exists() and not refresh:
        return f.read_text(encoding="utf-8")
    wait = DELAY - (time.monotonic() - _last)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(BASE + path, headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                html = r.read().decode("utf-8", errors="replace")
            break
        except Exception as e:          # noqa: BLE001 - retry slowly, then give up
            if attempt == 2:
                raise
            print(f"  {path}: {e}; retrying", file=sys.stderr)
            time.sleep(30 * (attempt + 1))
    _last = time.monotonic()
    f.write_text(html, encoding="utf-8")
    return html


def fetch_all(year: int, refresh: bool = False) -> None:
    todo = pages(year)
    for i, (name, path) in enumerate(todo.items(), 1):
        cached = (CACHE / name).exists() and not refresh
        get(name, path, refresh)
        print(f"[{i}/{len(todo)}] {'cached ' if cached else 'fetched'} {path}", flush=True)


if __name__ == "__main__":
    if "SSL_CERT_FILE" not in os.environ and Path("/root/.ccr/ca-bundle.crt").exists():
        os.environ["SSL_CERT_FILE"] = "/root/.ccr/ca-bundle.crt"
    fetch_all(int(sys.argv[1]) if len(sys.argv) > 1 else 2027)
