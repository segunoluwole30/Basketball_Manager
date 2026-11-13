"""Do the decision values match the engine?

    python -m hoopsim.spatial.check_values [--values FILE] [--games 36] [--measure 64]

Two checks (README "Values that match the engine"):

* **Converged.** Full games on leagues the values were not trained on, with no
  exploring. Every decision is recorded with the points its possession went on to
  score, and each option's mean value is compared with what it really scored. Values
  trained for the current engine are within ``MARGIN`` for every option (the fit
  makes them match on the training leagues, and nothing about the measure leagues is
  special). Stale values aren't: after "Shots with room" and "Steadier leagues" the
  values trained before them overrated post-ups by 0.30 and holding the ball by 0.15.
* **Calibrated** (``--measure N``): ``measure --games N`` on each seed, listing every
  full-game target out of tolerance.

``train --check`` runs both after training.
"""
from __future__ import annotations

import argparse
from typing import Optional

from .params import SpatialParams
from .values import FEATURES, DecisionModel, default_model

# Leagues to check on: the measure leagues, not the training ones (100-102).
CHECK_SEEDS = (0, 1, 2)
# How far an option's mean value may be from what it scored (points per decision).
MARGIN = 0.10
# Options with fewer decisions than this in the sample aren't judged (too noisy: post-ups,
# a few hundred a sample, move by 0.1-0.15 from one sample to the next).
MIN_DECISIONS = 600


def record(model: DecisionModel, games: int = 36, seeds=CHECK_SEEDS, jobs: int = 2,
           params: Optional[SpatialParams] = None, seed: int = 7) -> dict:
    """Every decision of ``games`` full games (no exploring), with the points its
    possession scored from there: {option: [(features, points)]}."""
    from .train import play_round_full
    samples, _ = play_round_full(model, games, 0.0, seed=seed, params=params, jobs=jobs, leagues=seeds)
    return samples


def gap_table(model: DecisionModel, samples: dict) -> dict:
    """{option: (decisions, mean value, mean points scored)}. Shots are also split into
    twos (``shoot_2``) and threes (``shoot_3``)."""
    out = {}
    groups = {k: samples.get(k, []) for k in FEATURES}
    shots = samples.get("shoot", [])
    groups["shoot_2"] = [r for r in shots if not r[0][2]]
    groups["shoot_3"] = [r for r in shots if r[0][2]]
    for name, rows in groups.items():
        if not rows:
            continue
        kind = "shoot" if name.startswith("shoot") else name
        n = len(rows)
        out[name] = (n, sum(model.value(kind, f) for f, _ in rows) / n, sum(y for _, y in rows) / n)
    return out


def value_gaps(model: DecisionModel, games: int = 36, seeds=CHECK_SEEDS, jobs: int = 2,
               params: Optional[SpatialParams] = None, seed: int = 7) -> dict:
    """``gap_table`` over ``games`` full games played with ``model``."""
    return gap_table(model, record(model, games, seeds, jobs, params, seed))


def off_options(gaps: dict, margin: float = MARGIN, min_decisions: int = MIN_DECISIONS) -> list[str]:
    """The options whose mean value is more than ``margin`` from what they scored."""
    return [k for k, (n, v, y) in gaps.items() if n >= min_decisions and abs(v - y) > margin]


def report_gaps(gaps: dict, margin: float = MARGIN) -> list[str]:
    print(f"{'option':<10}{'decisions':>10}{'value':>8}{'scored':>8}{'gap':>8}")
    for k, (n, v, y) in gaps.items():
        flag = "" if n < MIN_DECISIONS else "  ok" if abs(v - y) <= margin else "  OFF"
        print(f"{k:<10}{n:>10}{v:>8.3f}{y:>8.3f}{v - y:>+8.3f}{flag}")
    off = off_options(gaps, margin)
    print("\nvalues match the engine" if not off else
          f"\nvalues don't match the engine ({', '.join(off)} off by more than {margin}): retrain them")
    return off


def measure_seeds(model: Optional[DecisionModel], seeds, games: int = 64, jobs: int = 2,
                  params: Optional[SpatialParams] = None) -> dict:
    """{seed: (stats, [targets out of tolerance])} from ``measure --games``."""
    from .measure import FULLGAME_TARGETS, collect_fullgames
    out = {}
    for s in seeds:
        stats = collect_fullgames(games, s, jobs, params=params, model=model)
        off = [k for k, (t, tol) in FULLGAME_TARGETS.items() if abs(stats[k] - t) > tol]
        out[s] = (stats, off)
    return out


def report_measure(results: dict) -> int:
    """Print each seed's misses and the seed means; returns the number of seeds missing
    more than two targets or with any mean target out."""
    from .measure import FULLGAME_TARGETS
    bad = 0
    total = len(FULLGAME_TARGETS)
    for s, (stats, off) in results.items():
        detail = ", ".join(f"{k} {stats[k]:.3f}" if stats[k] < 2 else f"{k} {stats[k]:.1f}" for k in off)
        print(f"seed {s}: {total - len(off)}/{total} within tolerance" + (f" (out: {detail})" if off else ""))
        bad += len(off) > 2
    means = {k: sum(r[0][k] for r in results.values()) / len(results) for k in FULLGAME_TARGETS}
    off = [k for k, (t, tol) in FULLGAME_TARGETS.items() if abs(means[k] - t) > tol]
    print(f"seed means: {total - len(off)}/{total} within tolerance"
          + (f" (out: {', '.join(off)})" if off else ""))
    return bad + len(off)


def main() -> None:
    ap = argparse.ArgumentParser(prog="hoopsim.spatial.check_values")
    ap.add_argument("--values", help="decision values JSON (default: the saved ones)")
    ap.add_argument("--params", help="JSON file of SpatialParams (default: the defaults)")
    ap.add_argument("--games", type=int, default=36, help="full games for the value check")
    ap.add_argument("--measure", type=int, default=0, help="also run measure with this many games per seed")
    ap.add_argument("--seeds", default="0,1,2", help="measure seeds")
    ap.add_argument("--jobs", type=int, default=2)
    args = ap.parse_args()
    model = DecisionModel.load(args.values) if args.values else default_model()
    params = SpatialParams.load(args.params) if args.params else None
    bad = len(report_gaps(value_gaps(model, args.games, jobs=args.jobs, params=params)))
    if args.measure:
        print()
        bad += report_measure(measure_seeds(model, [int(s) for s in args.seeds.split(",")],
                                            args.measure, args.jobs, params))
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
