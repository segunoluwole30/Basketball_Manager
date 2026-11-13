"""Learn decision values from simulated possessions.

    python -m hoopsim.spatial.train --resume [--rounds 12] [--check 64]

Each round plays full games (120 by default) with the current values, trying a
random option a tenth of the time so every option gets tested. Every decision is
recorded with the points its possession went on to score from there; then each
option's weights are refit (least squares) to predict those points, and the values
move ``step`` (0.4) of the way to the fit. Better decisions change what the options
are worth, so a few rounds are run, and the last ``average`` (6) are averaged.
Training leagues (seeds 100+) are separate from the leagues ``measure`` uses, so the
result is checked on players it never trained on (``--check``, ``check_values.py``).

Three things keep retraining safe (README "Values that match the engine"):
* Full games, not stand-alone half-court possessions (``--full-games 0`` for the old
  way): the values are used in games, with transition flows, early offence, inbounds,
  fatigue, the bench and the ends of quarters, which the old training never saw.
* Only on-policy returns: a decision followed later in its possession by a random
  (exploring) choice isn't recorded (``HalfCourtGame.on_policy_decisions``).
* Damped steps: jumping to each fit made the values swing from round to round (holding
  the ball worth 1.10 one round and 0.87 the next, and growing).
"""
from __future__ import annotations

import argparse
import random
import time
from collections import Counter
from datetime import date
from typing import Optional

from ..generate import generate_league
from .params import SpatialParams
from .sim import HalfCourtGame
from .values import DEFAULT_PATH, FEATURES, HEURISTIC, DecisionModel, fit

TRAIN_SEEDS = (100, 101, 102)


def play_round(model: DecisionModel, possessions: int, explore: float, seed: int,
               params: Optional[SpatialParams] = None) -> tuple[dict, Counter]:
    samples: dict = {}
    stats = Counter()
    rng = random.Random(seed)
    per_league = possessions // len(TRAIN_SEEDS)
    for league_seed in TRAIN_SEEDS:
        league = generate_league(seed=league_seed)
        teams = sorted(league.teams)
        pairs = [(teams[i], teams[(i + 1) % len(teams)]) for i in range(len(teams))]
        for home, away in pairs:
            game = HalfCourtGame(league, home, away, rng, record=False, model=model,
                                 explore=explore, recorder=samples, params=params)
            game.play(max(2, per_league // len(pairs)))
            stats += game.stats
    return samples, stats


def _full_batch(job: tuple) -> tuple[dict, Counter]:
    """Full games in one training league (a worker for ``play_round_full``)."""
    from .fullgame import FullGame
    league_seed, first, games, model, explore, seed, params = job
    league = generate_league(seed=league_seed)
    teams = sorted(league.teams)
    samples: dict = {}
    stats = Counter()
    for i in range(first, first + games):
        home, away = teams[i % len(teams)], teams[(i + 1 + i // len(teams)) % len(teams)]
        if home == away:
            away = teams[(i + 2) % len(teams)]
        rng = random.Random((seed * 1_000_003 + league_seed) * 10_007 + i)
        game = FullGame(league, home, away, rng, model=model, params=params, explore=explore,
                        recorder=samples)
        game.run()
        stats += game.hc.stats
        stats["full_points"] += game.stats["points"]
        stats["full_possessions"] += sum(s.possessions for s in game.sides.values())
    return samples, stats


def play_round_full(model: DecisionModel, games: int, explore: float, seed: int,
                    params: Optional[SpatialParams] = None, jobs: int = 2,
                    leagues=TRAIN_SEEDS) -> tuple[dict, Counter]:
    """Like ``play_round`` but in full games (``fullgame.py``): the possessions the
    values are used in, with transition flows, early offence, inbounds, fatigue, the
    bench and the end of games. ``games`` are spread over the ``leagues`` (seeds)."""
    leagues = list(leagues)
    per = [games // len(leagues) + (1 if k < games % len(leagues) else 0) for k in range(len(leagues))]
    work = []
    for league_seed, n in zip(leagues, per):
        halves = [n // 2, n - n // 2] if jobs > 1 else [n]
        first = 0
        for h in halves:
            if h:
                work.append((league_seed, first, h, model, explore, seed, params))
            first += h
    jobs = max(1, min(jobs, 2))                        # the machine is shared: at most two processes
    if jobs > 1:
        from multiprocessing import Pool
        with Pool(jobs) as pool:
            parts = pool.map(_full_batch, work)
    else:
        parts = [_full_batch(w) for w in work]
    samples: dict = {}
    stats = Counter()
    for smp, st in parts:
        for k, v in smp.items():
            samples.setdefault(k, []).extend(v)
        stats += st
    return samples, stats


def gaps(model: DecisionModel, samples: dict) -> dict[str, float]:
    """Per option: mean value minus mean points scored, over ``samples``. Near 0 for
    every option when the values have converged for the engine that produced them."""
    out = {}
    for kind, rows in samples.items():
        if rows:
            out[kind] = sum(model.value(kind, f) - y for f, y in rows) / len(rows)
    return out


def train(rounds: int = 5, possessions: int = 20000, ridge: float = 50.0,
          start: DecisionModel = HEURISTIC, verbose: bool = True,
          params: Optional[SpatialParams] = None, seed: int = 0,
          full_games: int = 0, jobs: int = 2, step: float = 1.0, average: int = 3) -> DecisionModel:
    """``params``: the engine settings to learn values for (default: ``DEFAULT_PARAMS``);
    ``seed`` offsets the per-round game seeds. ``full_games``: play this many full games
    per round instead of ``possessions`` half-court possessions (``jobs`` processes).
    ``step``: move this share of the way to each round's fit. ``average``: the result is
    the mean of the last this many rounds."""
    model = start
    history: list[DecisionModel] = []
    resumed = start is not HEURISTIC
    schedule = [0.1] * 8 if resumed else [0.25, 0.2, 0.15, 0.12, 0.1, 0.1, 0.08, 0.08]
    for r in range(rounds):
        t0 = time.time()
        explore = schedule[min(r, len(schedule) - 1)]
        if full_games:
            samples, stats = play_round_full(model, full_games, explore, seed=seed + r, params=params,
                                             jobs=jobs)
        else:
            samples, stats = play_round(model, possessions, explore, seed=seed + r, params=params)
        before = gaps(model, samples)
        model = fit(samples, prior=model, ridge=ridge, step=step)
        history.append(model)
        if verbose:
            n = stats["full_possessions"] or stats["possessions"]
            pts = stats["full_points"] or stats["points"]
            fga = sum(stats[f"fga_{z}"] for z in ("rim", "close", "mid", "three"))
            games = f"{full_games} games, " if full_games else ""
            print(f"round {r + 1}: {games}{n} possessions (exploring {explore:.0%}), "
                  f"{sum(len(v) for v in samples.values())} decisions, {pts / n:.3f} pts/poss, "
                  f"threes {stats['fga_three'] / max(1, fga):.0%} of shots, {time.time() - t0:.0f}s")
            print("  value - scored: " + "  ".join(f"{k} {g:+.3f}" for k, g in before.items()), flush=True)
    # Average the last rounds: where options are nearly equal in value, single
    # rounds wander between equally good policies; the average is stable.
    tail = history[-min(average, len(history)):]
    model = DecisionModel({k: [sum(m.weights[k][i] for m in tail) / len(tail)
                               for i in range(len(FEATURES[k]))] for k in FEATURES})
    per_round = {"full_games_per_round": full_games} if full_games else {"possessions_per_round": possessions}
    model.meta = {"source": "learned", "rounds": rounds, **per_round, "step": step,
                  "averaged_rounds": len(tail),
                  "ridge": ridge, "trained": date.today().isoformat(), "train_seeds": list(TRAIN_SEEDS)}
    return model


def describe(model: DecisionModel) -> str:
    lines = []
    for kind, names in FEATURES.items():
        pairs = ", ".join(f"{n} {w:+.3f}" for n, w in zip(names, model.weights[kind]))
        lines.append(f"  {kind:<9}{pairs}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(prog="hoopsim.spatial.train")
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--full-games", type=int, default=120,
                    help="full games per round (0: half-court possessions, see --possessions)")
    ap.add_argument("--possessions", type=int, default=20000,
                    help="half-court possessions per round with --full-games 0 (the old way)")
    ap.add_argument("--step", type=float, default=0.4,
                    help="move this share of the way to each round's fit (1: jump to it)")
    ap.add_argument("--average", type=int, default=6, help="average the last this many rounds")
    ap.add_argument("--ridge", type=float, default=50.0)
    ap.add_argument("--jobs", type=int, default=2, help="processes for full games (at most 2)")
    ap.add_argument("--seed", type=int, default=0, help="offsets the per-round game seeds")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--resume", action="store_true", help="start from the saved values")
    ap.add_argument("--start", help="decision values JSON to start from (instead of --resume)")
    ap.add_argument("--out", help="where to save (default: the saved values)")
    ap.add_argument("--params", help="JSON file of SpatialParams to train for (default: the defaults)")
    ap.add_argument("--check", type=int, default=0, metavar="GAMES",
                    help="afterwards, check the values (check_values) and run measure with this "
                         "many games on seeds 0-2, listing targets out of tolerance")
    args = ap.parse_args()
    start = DecisionModel.load(args.start) if args.start else DecisionModel.load() if args.resume else HEURISTIC
    params = SpatialParams.load(args.params) if args.params else None
    model = train(args.rounds, args.possessions, args.ridge, start=start, params=params,
                  full_games=args.full_games, jobs=args.jobs, seed=args.seed, step=args.step,
                  average=args.average)
    print("\nLearned weights:\n" + describe(model))
    if not args.no_save:
        out = args.out or DEFAULT_PATH
        model.save(out)
        print(f"\nSaved to {out}")
    if args.check:
        from . import check_values as cv
        print("\nCheck on the measure leagues:")
        bad = len(cv.report_gaps(cv.value_gaps(model, jobs=args.jobs, params=params)))
        print()
        bad += cv.report_measure(cv.measure_seeds(model, cv.CHECK_SEEDS, args.check, args.jobs, params))
        if bad:
            print("\nThe new values move the engine out of calibration: see above.")


if __name__ == "__main__":
    main()
