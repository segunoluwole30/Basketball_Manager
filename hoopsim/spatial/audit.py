"""Realism audit of the spatial engine: what a 3D view would show, against the NBA.

    python -m hoopsim.spatial.audit [--games 30] [--seed 0]

Box-score calibration lives in ``measure.py``. This looks at the movement itself,
from recorded full games (ten updates a second):

* Bodies: running speeds and distance covered, accelerations, "teleports" (moves no
  human could make in a tenth of a second), players standing inside each other, and
  players off the floor.
* Floor: offensive spacing in the half court and defenders between their man and the
  basket.
* Ball: pass speeds and shot arcs.
* Shots: where they come from (zones, corner threes, distance), how open they are
  (closest defender) and when in the shot clock; passes, assists and possession
  length.

NBA reference values are approximate league-wide figures from public player-tracking
data (NBA.com / Second Spectrum, 2023-25 seasons) and basketball physics; each line
says how far off is still believable. The verdict is "ok", "watch" (noticeable to an
expert eye) or "off" (a viewer would notice).
"""
from __future__ import annotations

import argparse
import math
import random
import statistics
from collections import Counter

from ..generate import generate_league
from . import court as C
from .fullgame import FullGame
from .season_game import _model
from .sim import HalfCourtGame

DT = 0.1
FT_PER_MILE = 5280.0
MPH = 3600 / FT_PER_MILE          # ft/s -> mph


# ----------------------------------------------------------------------------- collection
def play(games: int, seed: int) -> tuple[list[dict], list[dict], Counter, float]:
    """Record ``games`` full games; returns (possessions, shots, engine stats, minutes)."""
    shots: list[dict] = []
    original = HalfCourtGame.count_attempt

    def logged(self, h, z):                     # every field-goal attempt, where and how open
        _, d = self.nearest_defender(h.pos)
        x, y = h.pos
        shots.append({"zone": z, "x": x, "y": y, "dist": C.dist_to_rim(h.pos), "closest": d,
                      "shot_clock": self.shot_clock,
                      "corner": z == C.THREE_ZONE and abs(x) >= C.CORNER_THREE_X - 0.5 and y <= C.CORNER_THREE_Y})
        return original(self, h, z)

    HalfCourtGame.count_attempt = logged
    possessions, stats, seconds = [], Counter(), 0.0
    try:
        for i in range(games):
            league = generate_league(seed=seed + i // 6, num_teams=12)
            ids = sorted(league.teams)
            home, away = ids[(2 * i) % len(ids)], ids[(2 * i + 1) % len(ids)]
            fg = FullGame(league, home, away, random.Random(seed * 1000 + i), record=True, model=_model())
            fg.run()
            possessions += fg.to_dict()["possessions"]
            stats.update(fg.hc.stats)
            seconds += fg.elapsed if hasattr(fg, "elapsed") else 0.0
    finally:
        HalfCourtGame.count_attempt = original
    return possessions, shots, stats, seconds


# ----------------------------------------------------------------------------- analysis
def _players(f):
    return [(f[6 + 2 * n] / 10, f[7 + 2 * n] / 10) for n in range(10)]


def bodies(possessions: list[dict]) -> dict:
    speeds, accels, teleports, overlaps, offcourt, frames = [], [], 0, 0, 0, 0
    dist = 0.0
    for p in possessions:
        fr = p["frames"]
        for a, b in zip(fr, fr[1:]):
            dt = (b[0] - a[0]) or DT
            if dt <= 0 or dt > 0.3:               # a cut (possession boundary, replay join)
                continue
            pa, pb = _players(a), _players(b)
            v = [math.dist(u, w) / dt for u, w in zip(pa, pb)]
            speeds += v
            dist += sum(math.dist(u, w) for u, w in zip(pa, pb))
            teleports += sum(1 for s in v if s > 32.0)            # faster than any human sprint
        # Acceleration from velocities averaged over 0.3 s (positions are stored to 0.1 ft,
        # which alone would fake +-15 ft/s² frame to frame).
        for k in range(len(fr) - 6):
            f0, f3, f6 = fr[k], fr[k + 3], fr[k + 6]
            t1, t2 = f3[0] - f0[0], f6[0] - f3[0]
            if not (0.25 <= t1 <= 0.35 and 0.25 <= t2 <= 0.35):
                continue
            p0, p3, p6 = _players(f0), _players(f3), _players(f6)
            for n in range(10):
                v1 = ((p3[n][0] - p0[n][0]) / t1, (p3[n][1] - p0[n][1]) / t1)
                v2 = ((p6[n][0] - p3[n][0]) / t2, (p6[n][1] - p3[n][1]) / t2)
                if math.hypot(*v1) < 32 and math.hypot(*v2) < 32:
                    accels.append(math.hypot(v2[0] - v1[0], v2[1] - v1[1]) / ((t1 + t2) / 2))
        for f in fr:
            frames += 1
            pts = _players(f)
            for i in range(10):
                x, y = pts[i]
                if abs(x) > C.WIDTH / 2 + 2.0 or y < -2.0 or y > C.FULL_LENGTH + 2.0:
                    offcourt += 1
                for j in range(i + 1, 10):
                    if math.dist(pts[i], pts[j]) < 1.2:          # two bodies in one place
                        overlaps += 1
    moving = [s for s in speeds if s < 32]
    return {"avg_speed_mph": statistics.fmean(moving) * MPH,
            "p99_speed_mph": sorted(moving)[int(0.99 * len(moving))] * MPH,
            "max_speed_mph": max(moving) * MPH,
            "teleports_per_game": teleports, "accel_p99": sorted(accels)[int(0.99 * len(accels))],
            "overlap_share": overlaps / max(1, frames), "offcourt_share": offcourt / max(1, frames * 10),
            "distance_ft": dist, "player_seconds": len(speeds) * DT}


def floor(possessions: list[dict]) -> dict:
    """Half-court frames only (everyone in the frontcourt, ball held)."""
    spacing, between, crowd, n = [], 0, 0, 0
    for p in possessions:
        off = range(5) if p["offense"] == "home" else range(5, 10)
        dfn = range(5, 10) if p["offense"] == "home" else range(5)
        for f in p["frames"][::5]:
            pts = _players(f)
            if f[5] < 0 or any(pts[i][1] > C.HALF_LENGTH for i in range(10)):
                continue
            o = [pts[i] for i in off]
            d = [pts[i] for i in dfn]
            pair = [math.dist(o[i], o[j]) for i in range(5) for j in range(i + 1, 5)]
            spacing.append(statistics.fmean(pair))
            crowd += any(x < 6.0 for x in pair)
            # each attacker: is his nearest defender (within 15 ft: weak-side help sags off)
            # nearer the rim than he is?
            for u in o:
                v = min(d, key=lambda q: math.dist(u, q))
                between += math.dist(u, v) < 15.0 and C.dist_to_rim(v) < C.dist_to_rim(u) + 1.0
            n += 1
    return {"spacing_ft": statistics.fmean(spacing), "crowded_share": crowd / max(1, n),
            "guarded_share": between / max(1, 5 * n)}


def ball(possessions: list[dict]) -> dict:
    """Airborne stretches (no holder): low ones are passes, high ones shots/lobs."""
    pass_speeds, apexes = [], []
    for p in possessions:
        seg = []
        for f in p["frames"] + [None]:
            if f is not None and f[5] < 0:
                seg.append(f)
                continue
            if len(seg) >= 3:
                zs = [g[4] / 10 for g in seg]
                start, end = (seg[0][2] / 10, seg[0][3] / 10), (seg[-1][2] / 10, seg[-1][3] / 10)
                t = seg[-1][0] - seg[0][0]
                if max(zs) <= 9.0 and t > 0:
                    pass_speeds.append(math.dist(start, end) / t)
                elif max(zs) > 10.0 and math.dist(end, C.RIM) < 3.0:
                    apexes.append(max(zs))
            seg = []
    return {"pass_speed_fts": statistics.median(pass_speeds) if pass_speeds else 0.0,
            "pass_speed_p90": sorted(pass_speeds)[int(0.9 * len(pass_speeds))] if pass_speeds else 0.0,
            "shot_apex_ft": statistics.median(apexes) if apexes else 0.0,
            "n_passes": len(pass_speeds), "n_shots": len(apexes)}


def shots_summary(shots: list[dict], stats: Counter) -> dict:
    n = len(shots)
    z = Counter(s["zone"] for s in shots)
    threes = [s for s in shots if s["zone"] == C.THREE_ZONE]
    cd = Counter("0-2" if s["closest"] < 2 else "2-4" if s["closest"] < 4 else "4-6" if s["closest"] < 6
                 else "6+" for s in shots)
    sc = Counter("24-18" if s["shot_clock"] > 18 else "18-15" if s["shot_clock"] > 15 else "15-7"
                 if s["shot_clock"] > 7 else "7-4" if s["shot_clock"] > 4 else "4-0" for s in shots)
    return {
        "rim_share": z[C.RIM_ZONE] / n, "close_share": z[C.CLOSE_ZONE] / n, "mid_share": z[C.MID_ZONE] / n,
        "three_share": z[C.THREE_ZONE] / n,
        "corner_of_threes": sum(s["corner"] for s in threes) / max(1, len(threes)),
        "avg_shot_ft": statistics.fmean(s["dist"] for s in shots),
        "closest": {k: cd[k] / n for k in ("0-2", "2-4", "4-6", "6+")},
        "clock": {k: sc[k] / n for k in ("24-18", "18-15", "15-7", "7-4", "4-0")},
        "passes_per_poss": stats["passes"] / max(1, stats["possessions"]),
        "seconds_per_halfcourt_poss": stats["seconds"] / max(1, stats["possessions"]),
        "assisted": stats["ast"] / max(1, stats["points"] and (stats.get("fgm") or 0) or 1),
    }


# ----------------------------------------------------------------------------- report
def verdict(value: float, lo: float, hi: float, watch: float) -> str:
    """ok inside [lo, hi]; watch within ``watch`` (relative) of the range; off beyond."""
    if lo <= value <= hi:
        return "ok"
    edge = lo if value < lo else hi
    return "watch" if abs(value - edge) <= watch * max(abs(edge), 1e-9) else "off"


def report(games: int = 30, seed: int = 0) -> list[tuple]:
    poss, shots, stats, _ = play(games, seed)
    b, fl, bl, s = bodies(poss), floor(poss), ball(poss), shots_summary(shots, stats)
    # distance per player per 48 minutes on court (player-seconds counted from frames)
    per48 = b["distance_ft"] / max(1.0, b["player_seconds"]) * 48 * 60 / FT_PER_MILE
    rows = [
        # (group, what, value, NBA range lo-hi, unit, watch tolerance)
        ("Bodies", "average speed while on court", b["avg_speed_mph"], 3.9, 4.8, "mph", 0.12),
        ("Bodies", "distance per player per 48 min", per48, 3.1, 3.6, "mi", 0.1),
        ("Bodies", "fast running (99th percentile)", b["p99_speed_mph"], 12.0, 17.0, "mph", 0.15),
        ("Bodies", "top speed seen", b["max_speed_mph"], 15.0, 21.5, "mph", 0.1),
        ("Bodies", "acceleration (99th percentile, 0.3 s)", b["accel_p99"], 8.0, 22.0, "ft/s²", 0.2),
        ("Bodies", "teleports (>32 ft/s) per game", b["teleports_per_game"] / games, 0.0, 0.0, "", 0.0),
        ("Bodies", "frames with two players inside each other", b["overlap_share"] * 100, 0.0, 1.0, "%", 1.0),
        ("Bodies", "player positions off the floor", b["offcourt_share"] * 100, 0.0, 0.3, "%", 1.0),
        ("Floor", "offensive spacing (avg distance between the five)", fl["spacing_ft"], 16.0, 22.0, "ft", 0.1),
        ("Floor", "half-court frames with two attackers within 6 ft", fl["crowded_share"] * 100, 15.0, 45.0, "%", 0.3),
        ("Floor", "attackers with a defender between them and the rim", fl["guarded_share"] * 100, 80.0, 97.0, "%", 0.05),
        ("Ball", "pass speed (median)", bl["pass_speed_fts"], 25.0, 45.0, "ft/s", 0.15),
        ("Ball", "jump-shot arc apex (median)", bl["shot_apex_ft"], 13.0, 17.0, "ft", 0.1),
        ("Shots", "at the rim (restricted area)", s["rim_share"] * 100, 28.0, 36.0, "% FGA", 0.12),
        ("Shots", "short (paint, outside the restricted area)", s["close_share"] * 100, 12.0, 20.0, "% FGA", 0.15),
        ("Shots", "mid-range", s["mid_share"] * 100, 9.0, 16.0, "% FGA", 0.2),
        ("Shots", "threes", s["three_share"] * 100, 38.0, 44.0, "% FGA", 0.08),
        ("Shots", "corner threes, share of threes", s["corner_of_threes"] * 100, 20.0, 28.0, "%", 0.2),
        ("Shots", "average shot distance", s["avg_shot_ft"], 13.0, 15.5, "ft", 0.08),
        ("Shots", "closest defender 0-2 ft (very tight)", s["closest"]["0-2"] * 100, 5.0, 11.0, "% FGA", 0.3),
        ("Shots", "closest defender 2-4 ft (tight)", s["closest"]["2-4"] * 100, 28.0, 38.0, "% FGA", 0.15),
        ("Shots", "closest defender 4-6 ft (open)", s["closest"]["4-6"] * 100, 28.0, 36.0, "% FGA", 0.15),
        ("Shots", "closest defender 6+ ft (wide open)", s["closest"]["6+"] * 100, 22.0, 32.0, "% FGA", 0.15),
        ("Shots", "shot clock 24-18 (early)", s["clock"]["24-18"] * 100, 14.0, 24.0, "% FGA", 0.2),
        ("Shots", "shot clock 7-0 (late)", (s["clock"]["7-4"] + s["clock"]["4-0"]) * 100, 16.0, 26.0, "% FGA", 0.2),
        ("Ball movement", "passes per half-court possession", s["passes_per_poss"], 2.5, 3.6, "", 0.12),
    ]
    return [(g, w, v, lo, hi, unit, verdict(v, lo, hi, tol)) for g, w, v, lo, hi, unit, tol in rows]


def main() -> None:
    ap = argparse.ArgumentParser(prog="hoopsim.spatial.audit")
    ap.add_argument("--games", type=int, default=30)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    rows = report(args.games, args.seed)
    group = None
    for g, what, v, lo, hi, unit, ok in rows:
        if g != group:
            print(f"\n{g}")
            group = g
        rng = f"{lo:g}" if lo == hi else f"{lo:g}-{hi:g}"
        print(f"  {what:<52}{v:>8.2f} {unit:<6} NBA {rng:<10} {ok}")
    c = Counter(r[-1] for r in rows)
    print(f"\n{c['ok']} ok, {c['watch']} watch, {c['off']} off ({args.games} games)")


if __name__ == "__main__":
    main()
