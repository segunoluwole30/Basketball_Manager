"""Compare simulated league averages against real pro basketball numbers.

    python -m hoopsim.calibrate [--seasons 2] [--seed 0]

The targets are approximate 2023-24 NBA per-team-game league averages
(Basketball-Reference). The match is never exact (our players are random,
not NBA rosters), but the averages should land close.
"""
from __future__ import annotations

import argparse
import random
import statistics
import time
from collections import defaultdict

from .engine import DEFAULT_TUNING, Tuning, simulate_game
from .generate import generate_league

# stat: (target, tolerance for "OK")
TARGETS = {
    "pace": (98.5, 3.0),
    "pts": (114.2, 3.0),
    "fga": (88.9, 3.0),
    "fg_pct": (0.474, 0.010),
    "tpa": (35.1, 2.5),
    "tp_pct": (0.366, 0.010),
    "fta": (21.7, 2.0),
    "ft_pct": (0.784, 0.015),
    "orb": (10.6, 1.0),
    "drb": (33.0, 1.5),
    "orb_pct": (0.243, 0.015),
    "ast": (26.7, 2.0),
    "stl": (7.5, 0.8),
    "blk": (5.1, 0.8),
    "tov": (13.6, 1.0),
    "pf": (18.6, 1.5),
    "home_win_pct": (0.545, 0.03),
}


def collect(seasons: int, seed: int, tuning: Tuning = DEFAULT_TUNING) -> dict:
    sums = defaultdict(float)
    team_games = games = home_wins = overtimes = 0
    margins = []
    minutes_err = []
    max_minutes = []
    for s in range(seasons):
        league = generate_league(seed=seed + s)
        rng = random.Random(seed * 1000 + s)
        for game in league.games.values():
            sim = simulate_game(league, game, rng, tuning=tuning)
            box, res = sim.box_score, sim.result
            games += 1
            home_wins += res.home_score > res.away_score
            overtimes += res.overtimes > 0
            margins.append(abs(res.home_score - res.away_score))
            for side, opp in ((box.home, box.away), (box.away, box.home)):
                t, o = side.totals(), opp.totals()
                team_games += 1
                game_minutes = t.seconds / 60 / 5
                sums["pace"] += (side.possessions + opp.possessions) / 2 * 48 / game_minutes
                sums["pts"] += t.pts
                for f in ("fga", "fgm", "tpa", "tpm", "fta", "ftm", "orb", "drb",
                          "ast", "stl", "blk", "tov", "pf"):
                    sums[f] += getattr(t, f)
                sums["opp_drb"] += o.drb
                targets = league.teams[side.team_id].tactics.minutes_targets
                for line in side.lines:
                    if res.overtimes == 0 and line.player_id in targets:
                        minutes_err.append(line.minutes - targets[line.player_id])
                max_minutes.append(max(l.minutes for l in side.lines))

    n = team_games
    out = {k: sums[k] / n for k in ("pace", "pts", "fga", "tpa", "fta", "orb", "drb",
                                     "ast", "stl", "blk", "tov", "pf")}
    out["fg_pct"] = sums["fgm"] / sums["fga"]
    out["tp_pct"] = sums["tpm"] / sums["tpa"]
    out["ft_pct"] = sums["ftm"] / sums["fta"]
    out["orb_pct"] = sums["orb"] / (sums["orb"] + sums["opp_drb"])
    out["home_win_pct"] = home_wins / games
    out["_games"] = games
    out["_ot_rate"] = overtimes / games
    out["_avg_margin"] = statistics.mean(margins)
    out["_minutes_mae"] = statistics.mean(abs(e) for e in minutes_err)
    out["_minutes_bias"] = statistics.mean(minutes_err)
    out["_max_minutes_p95"] = sorted(max_minutes)[int(len(max_minutes) * 0.95)]
    return out


def report(stats: dict) -> int:
    print(f"{'stat':<14}{'sim':>9}{'target':>9}{'diff':>9}")
    bad = 0
    for k, (target, tol) in TARGETS.items():
        v = stats[k]
        fmt = ".3f" if target < 1 else ".1f"
        ok = abs(v - target) <= tol
        bad += not ok
        print(f"{k:<14}{v:>9{fmt}}{target:>9{fmt}}{v - target:>+9{fmt}}  {'ok' if ok else 'OFF'}")
    print()
    print(f"games simulated     {stats['_games']}")
    print(f"overtime rate       {stats['_ot_rate']:.3f}  (NBA ~0.06)")
    print(f"avg winning margin  {stats['_avg_margin']:.1f}  (NBA ~12)")
    print(f"minutes vs targets  mean abs error {stats['_minutes_mae']:.1f}, bias {stats['_minutes_bias']:+.1f}")
    print(f"95th pct max mpg    {stats['_max_minutes_p95']:.1f}")
    print(f"\n{len(TARGETS) - bad}/{len(TARGETS)} stats within tolerance")
    return bad


def main() -> None:
    ap = argparse.ArgumentParser(prog="hoopsim.calibrate")
    ap.add_argument("--seasons", type=int, default=2)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    start = time.time()
    stats = collect(args.seasons, args.seed)
    report(stats)
    print(f"({time.time() - start:.1f}s)")


if __name__ == "__main__":
    main()
