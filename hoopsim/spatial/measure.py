"""Measure the spatial prototype against NBA half-court numbers.

    python -m hoopsim.spatial.measure [--possessions 4000] [--seed 0]

Targets are one consistent set of NBA *half-court possession* figures (2023-24
and 2024-25), counted the way this engine counts: a possession includes its
offensive rebounds, putbacks and free throws. Sources (via cached snippets; the
live tables couldn't be opened from this environment, so treat these as
approximate):
* Cleaning the Glass league context: 98.4-98.7 points per 100 half-court
  *plays* (a play restarts after an offensive rebound); 115.6 overall; about 15%
  transition at ~127. Per half-court possession that is ~1.10.
* Cleaning the Glass shot zones 2024-25: rim (0-4 ft) 30.9% at 66.6%, short
  mid-range (4-14 ft) 23.3% at 44.9%, long mid-range 7.3% at 42.3%, threes ~42%
  (league 3PA rate) at ~36%. Rim share and accuracy are trimmed slightly for
  the half court (transition adds easy layups).
* NBA.com tracking: 3P% by closest defender 27/30/35/39% for very tight (0-2 ft) /
  tight (2-4) / open (4-6) / wide open (6+); most threes are open or wide open.
  Catch-and-shoot 37.4% vs pull-up 32.9%.
* Estimates: turnovers ~13%, FTA/FGA ~0.24, ~15.5 s per half-court possession,
  ~3 passes per possession.
"""
from __future__ import annotations

import argparse
import random
import time
from collections import Counter
from typing import Optional

from ..generate import generate_league
from .params import SpatialParams
from .sim import DEFAULT_TUNING, HalfCourtGame, SpatialTuning
from .values import HEURISTIC, DecisionModel, default_model

ZONES = ("rim", "close", "mid", "three")

# stat: (target, tolerance)
TARGETS = {
    "points_per_possession": (1.10, 0.04),
    "efg": (0.527, 0.02),
    "seconds_per_possession": (15.5, 1.5),
    "passes_per_possession": (3.2, 0.8),
    "turnover_pct": (0.13, 0.025),
    "orb_pct": (0.27, 0.03),
    "fta_per_fga": (0.24, 0.05),
    "ast_per_fgm": (0.60, 0.08),
    "share_rim": (0.28, 0.05),
    "share_close": (0.23, 0.05),
    "share_mid": (0.07, 0.03),
    "share_three": (0.42, 0.05),
    "fg_rim": (0.64, 0.04),
    "fg_close": (0.44, 0.04),
    "fg_mid": (0.415, 0.04),
    "fg_three": (0.362, 0.025),
    # How contested threes are (closest defender at release): NBA.com tracking.
    "three_very_tight_share": (0.02, 0.04),    # 0-2 ft
    "three_wide_open_share": (0.44, 0.10),     # 6+ ft
}


def play(possessions: int, seeds, tuning: SpatialTuning = DEFAULT_TUNING,
         model: DecisionModel = None, params: Optional[SpatialParams] = None,
         rng_seed: Optional[int] = None) -> tuple[Counter, float]:
    """Play ``possessions`` spread over every team pairing in each league seed.
    Returns the summed stats and the seconds it took. The game RNG is seeded with
    ``rng_seed`` (default: the first league seed)."""
    seeds = list(seeds)
    rng = random.Random(seeds[0] if rng_seed is None else rng_seed)
    total = Counter()
    start = time.time()
    per_league = possessions // len(seeds)
    for seed in seeds:
        league = generate_league(seed=seed)
        teams = sorted(league.teams)
        pairs = [(teams[i], teams[(i + 1) % len(teams)]) for i in range(len(teams))]
        per_pair = max(2, per_league // len(pairs))
        for home, away in pairs:
            game = HalfCourtGame(league, home, away, rng, tuning, record=False, model=model,
                                 params=params)
            game.play(per_pair)
            total += game.stats
    return total, time.time() - start


def collect(possessions: int, seed: int, tuning: SpatialTuning = DEFAULT_TUNING,
            model: DecisionModel = None, params: Optional[SpatialParams] = None) -> dict:
    stats, elapsed = play(possessions, [seed], tuning, model, params)
    return summarize(stats, elapsed)


def summarize(s: Counter, elapsed: float = 0.0) -> dict:
    """Turn summed possession stats into the measured rates (see ``TARGETS``)."""
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
        "efg": (fgm + 0.5 * s["fgm_three"]) / max(1, fga),
        "passes_per_possession": s["passes"] / n,
    }
    threes = sum(s[f"contest_three_{b}"] for b in ("tight", "close", "open", "wide")) or 1
    out["three_very_tight_share"] = s["contest_three_tight"] / threes
    out["three_wide_open_share"] = s["contest_three_wide"] / threes
    for z in ZONES:
        out[f"share_{z}"] = s[f"fga_{z}"] / max(1, fga)
        out[f"fg_{z}"] = s[f"fgm_{z}"] / max(1, s[f"fga_{z}"])
    out["_possessions"] = n
    out["_ms_per_possession"] = 1000 * elapsed / max(1, n)
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
    ap.add_argument("--params", help="JSON file of SpatialParams to use instead of the defaults")
    args = ap.parse_args()
    model = HEURISTIC if args.values == "heuristic" else default_model()
    params = SpatialParams.load(args.params) if args.params else None
    print(f"Decision values: {model.meta.get('source', '?')}\n")
    report(collect(args.possessions, args.seed, model=model, params=params))


if __name__ == "__main__":
    main()
