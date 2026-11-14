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

Full games (``--games N``, see ``fullgame.py``) are measured per team-game against
the 2025-26 regular-season league averages on Basketball-Reference
(https://www.basketball-reference.com/leagues/NBA_2026.html, "Per Game Stats" and
"Advanced Stats" League Average rows; 2024-25 in brackets as a check,
https://www.basketball-reference.com/leagues/NBA_2025.html):
* pace 99.4 [98.8] possessions per 48 minutes; 115.6 [113.8] points per team;
  offensive rating 115.8 [114.5] per 100 possessions.
* Per team-game: 23.5 [21.7] FTA, 19.9 [18.6] personal fouls, 14.5 [14.3]
  turnovers (12.7% [12.6%] of possessions). Per 100 possessions that is 23.6 FTA,
  20.0 fouls and 14.6 turnovers.
* Also: eFG .546, 3PA rate .415, FTA/FGA .264, offensive rebound rate 26.0%
  (all possessions, transition included).
* Transition: Cleaning the Glass puts ~15% of plays in transition (after steals,
  defensive rebounds and some makes) at ~1.25-1.27 points per play, against
  ~0.98 per half-court play (~1.10 per half-court possession). Synergy's
  transition frequency is similar (~15-16% of possessions at ~1.12-1.15 points
  per play, which excludes free throws after the play). We count a possession
  as transition if it started with a pushed open-court phase and its first shot,
  foul or turnover came within 8 seconds of gaining the ball.
* Minutes: starters average ~30-36 minutes; about 6% of games go to overtime.
Because the half-court engine's possessions are shorter than the NBA's (about
11 s against ~15.5 s), pace runs high; per-100-possession rates are reported
alongside per-game totals so the full-game mechanics can be judged on their own.
"""
from __future__ import annotations

import argparse
import random
import time
from collections import Counter
from typing import Optional

from collections import Counter

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
        # time to bring it up (shot clock started 3-4.5s down) + half-court time
        "seconds_per_possession": s["seconds"] / n + 3.75,
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


# Full games, per team-game unless noted: stat: (target, tolerance)
FULLGAME_TARGETS = {
    "pace": (99.4, 3.0),
    "points": (115.6, 4.0),
    "points_per_100": (115.8, 3.0),
    "transition_share": (0.15, 0.02),
    "transition_ppp": (1.26, 0.06),
    "fta_per_100": (23.6, 3.0),
    "pf_per_100": (20.0, 2.0),
    "tov_per_100": (14.6, 1.5),
    # 2025-26 per-game: starters average 28.4 mpg over all their games, about 31 in
    # the games they start (was an estimate of 33).
    "starter_minutes": (31.0, 3.0),
    "top_minutes": (36.0, 3.0),
    # 2025-26 Advanced Stats, League Average: 3PA rate, eFG%, ORB%.
    "three_share": (0.415, 0.025),
    "efg": (0.546, 0.015),
    "orb_pct": (0.260, 0.025),
}


def _fullgame_batch(job: tuple) -> tuple[Counter, list]:
    """Play ``games`` full games in one league (a worker for --jobs)."""
    from .fullgame import FullGame
    seed, start, games = job[:3]
    params, model = (job[3], job[4]) if len(job) > 3 else (None, None)
    offset = job[5] if len(job) > 5 else 0
    league = generate_league(seed=seed)
    teams = sorted(league.teams)
    total, starters = Counter(), []
    for i in range(start, start + games):
        home, away = teams[i % len(teams)], teams[(i + 1 + i // len(teams)) % len(teams)]
        if home == away:
            away = teams[(i + 2) % len(teams)]
        game = FullGame(league, home, away, random.Random(seed * 100_003 + i + offset), params=params,
                        model=model)
        sim = game.run()
        total += game.stats
        total["open_court_steals"] += game.hc.stats["open_court_steals"]
        total["games"] += 1
        total["overtime_games"] += sim.result.overtimes > 0
        total["seconds"] += 2880 + 300 * sim.result.overtimes
        for box in (sim.box_score.home, sim.box_score.away):
            t = box.totals()
            total["team_games"] += 1
            total["possessions_total"] += box.possessions
            total["pts"] += t.pts
            for f in ("fgm", "fga", "tpm", "tpa", "fta", "orb", "drb", "ast", "stl", "blk", "tov", "pf"):
                total[f] += getattr(t, f)
            mins = sorted((l.minutes for l in box.lines), reverse=True)
            starters.append(sorted((l.minutes for l in box.lines if l.starter), reverse=True))
            total["top_scorer_pts"] += max(l.pts for l in box.lines)
            total["players_used"] += sum(1 for m in mins if m > 0)
            total["fouled_out"] += sum(1 for l in box.lines if l.pf >= league.rules.personal_foul_limit)
    return total, starters


def collect_fullgames(games: int, seed: int, jobs: int = 1, params=None, model=None,
                      seeds: Optional[list[int]] = None, offset: int = 0) -> dict:
    """``seeds``: spread the games over these leagues (default: league ``seed`` only).
    ``params``/``model`` are passed to the worker processes as well."""
    if seeds:
        n = len(seeds)
        work = [(sd, 0, games // n + (1 if k < games % n else 0), params, model, offset)
                for k, sd in enumerate(seeds)]
        work = [w for w in work if w[2] > 0]
        jobs = 1
    else:
        jobs = max(1, min(jobs, 2, games))             # this machine is shared: at most two processes
        per = -(-games // jobs)
        work = [(seed, k * per, min(per, games - k * per), params, model, offset)
                for k in range(jobs) if games - k * per > 0]
    start = time.time()
    if jobs > 1:
        from multiprocessing import Pool
        with Pool(jobs) as pool:
            parts = pool.map(_fullgame_batch, work)
    else:
        parts = [_fullgame_batch(w) for w in work]
    s, starters = Counter(), []
    for c, st in parts:
        s += c
        starters += st
    tg = s["team_games"]
    poss = s["possessions_total"]
    per100 = lambda k: 100 * s[k] / poss
    out = {
        # NBA pace is per 48 minutes; overtime games count their extra minutes.
        "pace": poss / tg * 2880 / (s["seconds"] / s["games"]),
        "points": s["pts"] / tg,
        "points_per_100": per100("pts"),
        "transition_share": s["transition"] / poss,
        "transition_ppp": s["transition_points"] / max(1, s["transition"]),
        "fta_per_100": per100("fta"),
        "pf_per_100": per100("pf"),
        "tov_per_100": per100("tov"),
        "starter_minutes": sum(sum(m) for m in starters) / max(1, sum(len(m) for m in starters)),
        "top_minutes": sum(m[0] for m in starters if m) / max(1, len(starters)),
        "three_share": s["tpa"] / max(1, s["fga"]),
        "efg": (s["fgm"] + 0.5 * s["tpm"]) / max(1, s["fga"]),
        "orb_pct": s["orb"] / max(1, s["orb"] + s["drb"]),
    }
    out["_games"] = s["games"]
    # Basketball-Reference doesn't count possessions, it estimates them from the box
    # score (FGA + 0.4 FTA - 1.07 ORB% x misses + TOV). The same estimate here, for an
    # apples-to-apples look at the targets (counted possessions run ~2% higher:
    # end-of-period possessions with no shot, and the formula's FT-trip weight).
    orb_share = s["orb"] / max(1, s["orb"] + s["drb"])
    est = (s["fga"] + 0.4 * s["fta"] - 1.07 * orb_share * (s["fga"] - s["fgm"]) + s["tov"]) / tg
    out["_pace_bref_formula"] = est * 2880 / (s["seconds"] / s["games"])
    out["_points_per_100_bref_formula"] = 100 * (s["pts"] / tg) / max(1e-9, est)
    out["_three_pct"] = s["tpm"] / max(1, s["tpa"])
    out["_seconds_per_game"] = (time.time() - start) / s["games"] * jobs
    out["_halfcourt_ppp"] = s["halfcourt_points"] / max(1, s["halfcourt"])
    for k in ("fta", "pf", "tov", "fga", "tpa", "orb", "drb", "ast", "stl", "blk"):
        out[f"_{k}_per_game"] = s[k] / tg
    for how in ("steal", "drb", "score", "ft"):
        n = s[f"transition_after_{how}"]
        out[f"_transition_after_{how}"] = f"{n / tg:.1f}/game at {s[f'transition_after_{how}_pts'] / max(1, n):.2f}"
    for adv in ("breakaway", "numbers", "none", "early", "walk"):
        n = s[f"adv_{adv}_n"]
        out[f"_open_court_{adv}"] = f"{n / tg:.1f}/game at {s[f'adv_{adv}_pts'] / max(1, n):.2f}"
    out["_open_court_steals_per_game"] = s["open_court_steals"] / tg
    out["_seconds_per_possession"] = (s["seconds_open_court"] + s["seconds_bring_up"]
                                      + s["seconds_half_court"]) / poss
    out["_half_court_seconds_per_possession"] = s["seconds_half_court"] / poss
    out["_starter_minutes_by_rank"] = ", ".join(
        f"{sum(m[i] for m in starters if len(m) > i) / len(starters):.1f}" for i in range(5))
    out["_players_used"] = s["players_used"] / tg
    out["_top_scorer_points"] = s["top_scorer_pts"] / tg
    out["_fouled_out_per_game"] = s["fouled_out"] / tg
    out["_injuries_per_game"] = s["injuries"] / tg
    out["_injury_risk_per_game"] = s["injury_risk"] / tg        # expected injuries (smooth)
    out["_substitutions_per_game"] = s["substitutions"] / s["games"]
    out["_overtime_share"] = s["overtime_games"] / s["games"]
    out["_non_bonus_fouls_per_game"] = s["fouls_no_bonus"] / tg
    out["_intentional_fouls_per_game"] = s["intentional_fouls"] / s["games"]
    return out


def report_fullgames(stats: dict) -> int:
    print(f"{'stat (per team-game)':<24}{'sim':>8}{'target':>8}")
    bad = 0
    for k, (target, tol) in FULLGAME_TARGETS.items():
        v = stats[k]
        ok = abs(v - target) <= tol
        bad += not ok
        fmt = ".3f" if target < 2 else ".1f"
        print(f"{k:<24}{v:>8{fmt}}{target:>8{fmt}}  {'ok' if ok else 'OFF'}")
    print()
    for k, v in stats.items():
        if k.startswith("_"):
            print(f"{k[1:]:<34}{v:>8.2f}" if isinstance(v, float) else f"{k[1:]:<34}  {v}")
    print(f"\n{len(FULLGAME_TARGETS) - bad}/{len(FULLGAME_TARGETS)} within tolerance")
    return bad


def main() -> None:
    ap = argparse.ArgumentParser(prog="hoopsim.spatial.measure")
    ap.add_argument("--possessions", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--values", choices=["learned", "heuristic"], default="learned",
                    help="decision values to use (learned falls back to heuristic if untrained)")
    ap.add_argument("--games", type=int, default=0,
                    help="measure this many full games (pace, scoring, transition, minutes, fouls)")
    ap.add_argument("--jobs", type=int, default=1, help="processes for --games (at most 2)")
    ap.add_argument("--params", help="JSON file of SpatialParams to use instead of the defaults")
    ap.add_argument("--values-file", help="decision values JSON to use instead of the saved ones")
    args = ap.parse_args()
    model = HEURISTIC if args.values == "heuristic" else default_model()
    if args.values_file:
        model = DecisionModel.load(args.values_file)
    params = SpatialParams.load(args.params) if args.params else None
    if args.games:
        explicit = args.values_file or args.values == "heuristic"
        report_fullgames(collect_fullgames(args.games, args.seed, args.jobs, params=params,
                                           model=model if explicit else None))
        return
    print(f"Decision values: {model.meta.get('source', '?')}\n")
    report(collect(args.possessions, args.seed, model=model, params=params))


if __name__ == "__main__":
    main()
