"""Measure the spatial prototype against NBA half-court numbers.

    python -m hoopsim.spatial.measure [--possessions 4000] [--seed 0]

Targets are approximate NBA half-court figures (no transition, no putback-heavy
fast breaks): points per possession is lower than a full game's ~1.15.
"""
from __future__ import annotations

import argparse
import random
import time

from ..generate import generate_league
from .sim import DEFAULT_TUNING, HalfCourtGame, SpatialTuning
from .values import HEURISTIC, DecisionModel, default_model

ZONES = ("rim", "close", "mid", "three")

# stat: (target, tolerance)
TARGETS = {
    "points_per_possession": (0.98, 0.06),
    "seconds_per_possession": (14.5, 2.0),
    "turnover_pct": (0.13, 0.025),
    "orb_pct": (0.25, 0.04),
    "fta_per_fga": (0.24, 0.06),
    "ast_per_fgm": (0.60, 0.10),
    "share_rim": (0.30, 0.06),
    "share_close": (0.16, 0.05),
    "share_mid": (0.13, 0.05),
    "share_three": (0.41, 0.06),
    "fg_rim": (0.65, 0.05),
    "fg_close": (0.44, 0.05),
    "fg_mid": (0.42, 0.05),
    "fg_three": (0.36, 0.03),
}


def collect(possessions: int, seed: int, tuning: SpatialTuning = DEFAULT_TUNING,
            model: DecisionModel = None) -> dict:
    league = generate_league(seed=seed)
    rng = random.Random(seed)
    teams = sorted(league.teams)
    pairs = [(teams[i], teams[(i + 1) % len(teams)]) for i in range(len(teams))]
    per_pair = max(2, possessions // len(pairs))
    total = None
    start = time.time()
    for home, away in pairs:
        game = HalfCourtGame(league, home, away, rng, tuning, record=False, model=model)
        game.play(per_pair)
        total = game.stats if total is None else total + game.stats
    elapsed = time.time() - start
    s = total
    n = s["possessions"]
    fga = sum(s[f"fga_{z}"] for z in ZONES)
    fgm = sum(s[f"fgm_{z}"] for z in ZONES)
    misses = fga - fgm
    out = {
        "points_per_possession": s["points"] / n,
        # time to bring it up (shot clock started 3.5-6s down) + half-court time
        "seconds_per_possession": s["seconds"] / n + 4.75,
        "turnover_pct": s["tov"] / n,
        "orb_pct": s["orb"] / max(1, s["orb"] + s["drb"]),
        "fta_per_fga": s["fta"] / max(1, fga),
        "ast_per_fgm": s["ast"] / max(1, fgm),
    }
    for z in ZONES:
        out[f"share_{z}"] = s[f"fga_{z}"] / max(1, fga)
        out[f"fg_{z}"] = s[f"fgm_{z}"] / max(1, s[f"fga_{z}"])
    out["_possessions"] = n
    out["_ms_per_possession"] = 1000 * elapsed / n
    for k in ("passes", "drives", "pnr", "blocks", "steals"):
        out[f"_{k}_per_possession"] = s[k] / n
    out["_stalled"] = s["end_stalled"]
    for k in ("cuts", "lobs", "post_ups", "doubles"):
        out[f"_{k}_per_possession"] = s[k] / n
    inside = {k[len("src_rim_"):]: v for k, v in s.items() if k.startswith("src_rim_")}
    total_rim = sum(inside.values()) or 1
    out["_rim_shots_from"] = ", ".join(f"{k} {v / total_rim:.0%}" for k, v in
                                       sorted(inside.items(), key=lambda kv: -kv[1]))
    out["_misses"] = misses
    return out


def report(stats: dict) -> int:
    print(f"{'stat':<24}{'sim':>8}{'target':>8}")
    bad = 0
    for k, (target, tol) in TARGETS.items():
        v = stats[k]
        ok = abs(v - target) <= tol
        bad += not ok
        fmt = ".3f" if target < 2 else ".1f"
        print(f"{k:<24}{v:>8{fmt}}{target:>8{fmt}}  {'ok' if ok else 'OFF'}")
    print()
    for k, v in stats.items():
        if k.startswith("_"):
            print(f"{k[1:]:<24}{v:>8.2f}" if isinstance(v, float) else f"{k[1:]:<24}  {v}")
    print(f"\n{len(TARGETS) - bad}/{len(TARGETS)} within tolerance")
    return bad


def main() -> None:
    ap = argparse.ArgumentParser(prog="hoopsim.spatial.measure")
    ap.add_argument("--possessions", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--values", choices=["learned", "heuristic"], default="learned",
                    help="decision values to use (learned falls back to heuristic if untrained)")
    args = ap.parse_args()
    model = HEURISTIC if args.values == "heuristic" else default_model()
    print(f"Decision values: {model.meta.get('source', '?')}\n")
    report(collect(args.possessions, args.seed, model=model))


if __name__ == "__main__":
    main()
