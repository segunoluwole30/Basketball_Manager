"""Learn decision values from simulated possessions.

    python -m hoopsim.spatial.train [--rounds 5] [--possessions 20000]

Each round plays possessions with the current values, trying a random option a
fraction of the time so every option gets tested. Every decision is recorded
with the points its possession went on to score; then each option's weights are
refit (least squares, pulled gently toward the previous round's) to predict
those points. Better decisions change what the options are worth, so a few
rounds are run. Training leagues (seeds 100+) are separate from the leagues
``measure`` uses, so the result is checked on players it never trained on.
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


def train(rounds: int = 5, possessions: int = 20000, ridge: float = 50.0,
          start: DecisionModel = HEURISTIC, verbose: bool = True,
          params: Optional[SpatialParams] = None, seed: int = 0) -> DecisionModel:
    """``params``: the engine settings to learn values for (default: ``DEFAULT_PARAMS``);
    ``seed`` offsets the per-round game seeds."""
    model = start
    history: list[DecisionModel] = []
    resumed = start is not HEURISTIC
    schedule = [0.1] * 8 if resumed else [0.25, 0.2, 0.15, 0.12, 0.1, 0.1, 0.08, 0.08]
    for r in range(rounds):
        t0 = time.time()
        explore = schedule[min(r, len(schedule) - 1)]
        samples, stats = play_round(model, possessions, explore, seed=seed + r, params=params)
        model = fit(samples, prior=model, ridge=ridge)
        history.append(model)
        if verbose:
            n = stats["possessions"]
            fga = sum(stats[f"fga_{z}"] for z in ("rim", "close", "mid", "three"))
            print(f"round {r + 1}: {n} possessions (exploring {explore:.0%}), "
                  f"{sum(len(v) for v in samples.values())} decisions, {stats['points'] / n:.3f} pts/poss, "
                  f"threes {stats['fga_three'] / max(1, fga):.0%} of shots, {time.time() - t0:.0f}s")
    # Average the last rounds: where options are nearly equal in value, single
    # rounds wander between equally good policies; the average is stable.
    tail = history[-min(3, len(history)):]
    model = DecisionModel({k: [sum(m.weights[k][i] for m in tail) / len(tail)
                               for i in range(len(FEATURES[k]))] for k in FEATURES})
    model.meta = {"source": "learned", "rounds": rounds, "possessions_per_round": possessions,
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
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--possessions", type=int, default=20000)
    ap.add_argument("--ridge", type=float, default=50.0)
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--resume", action="store_true", help="start from the saved values")
    ap.add_argument("--params", help="JSON file of SpatialParams to train for (default: the defaults)")
    args = ap.parse_args()
    start = DecisionModel.load() if args.resume else HEURISTIC
    params = SpatialParams.load(args.params) if args.params else None
    model = train(args.rounds, args.possessions, args.ridge, start=start, params=params)
    print("\nLearned weights:\n" + describe(model))
    if not args.no_save:
        model.save()
        print(f"\nSaved to {DEFAULT_PATH}")


if __name__ == "__main__":
    main()
