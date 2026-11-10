"""Search the spatial engine's settings so its output matches the half-court targets.

    python -m hoopsim.spatial.calibrate [--log calibration.jsonl] [--resume]

The offence's choices are learned, so a candidate setting can't be judged with
the decision values trained for another one: the offence would keep shooting the
shots that used to be good. Each candidate therefore gets a quick retrain (a few
rounds, warm-started from the saved decision values) on the training leagues
(seeds 100-102), then is measured on those leagues. The held-out leagues
``measure`` uses (seeds 0-2) are never looked at during the search.

Score: weighted mean of squared errors in tolerance units, ((sim - target) / tol)^2,
weighting points per possession, eFG, the shot mix and 3P% most.

Search, all phases resumable from the JSONL log (one line per evaluation):
1. noise: the default settings evaluated several times with different seeds, to
   know how much of a score difference is luck;
2. sample: a Latin hypercube over ``SEARCH_SPACE`` (plus the defaults);
3. refine: a (1+3) evolution strategy from the best so far. Each generation
   mutates a few parameters of the incumbent; a mutant that beats it is
   re-evaluated with a new seed and replaces it only if its mean score is still
   better. The step size grows after successes and shrinks after failures.
"""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import random
import time
import zlib
from pathlib import Path
from typing import Callable, Iterable, Optional

from .measure import FULLGAME_TARGETS, TARGETS, collect_fullgames, play, summarize
from .params import DEFAULT_PARAMS, SEARCH_SPACE, SpatialParams
from .train import TRAIN_SEEDS, train
from .values import DEFAULT_PATH, DecisionModel

# Score weights (default 1).
WEIGHTS = {
    "points_per_possession": 3.0, "efg": 3.0,
    "share_rim": 2.5, "share_close": 2.0, "share_mid": 1.5, "share_three": 2.5,
    "fg_three": 2.5,
    # Driven mostly by how patient the learned policy is, which these settings barely touch.
    "seconds_per_possession": 0.5, "passes_per_possession": 0.5,
}


def score(metrics: dict) -> float:
    total = wsum = 0.0
    for k, (target, tol) in TARGETS.items():
        w = WEIGHTS.get(k, 1.0)
        total += w * ((metrics[k] - target) / tol) ** 2
        wsum += w
    return total / wsum


def within(metrics: dict) -> int:
    return sum(abs(metrics[k] - t) <= tol for k, (t, tol) in TARGETS.items())


# Full-game objective: the 2025-26 league averages (measure.FULLGAME_TARGETS).
# Minutes barely depend on these settings; transition numbers are estimates.
FULLGAME_WEIGHTS = {
    "pace": 3.0, "points_per_100": 3.0, "efg": 3.0, "three_share": 2.5,
    "fta_per_100": 1.5, "tov_per_100": 1.5, "pf_per_100": 1.0, "orb_pct": 1.5,
    "transition_share": 0.75, "transition_ppp": 0.75,
    "starter_minutes": 0.25, "top_minutes": 0.25, "points": 0.0,   # points = pace x efficiency
}


def score_full(metrics: dict) -> float:
    total = wsum = 0.0
    for k, (target, tol) in FULLGAME_TARGETS.items():
        w = FULLGAME_WEIGHTS.get(k, 1.0)
        total += w * ((metrics[k] - target) / tol) ** 2
        wsum += w
    return total / wsum


def within_full(metrics: dict) -> int:
    return sum(abs(metrics[k] - t) <= tol for k, (t, tol) in FULLGAME_TARGETS.items())


def targets_for(objective: str) -> dict:
    return FULLGAME_TARGETS if objective == "fullgame" else TARGETS


# ------------------------------------------------------------------ parameter space
def to_unit(params: SpatialParams, space: dict = SEARCH_SPACE) -> list[float]:
    d = params.to_dict()
    return [max(0.0, min(1.0, (d[k] - lo) / (hi - lo))) for k, (lo, hi) in space.items()]


def from_unit(x: list[float], base: SpatialParams = DEFAULT_PARAMS, space: dict = SEARCH_SPACE) -> SpatialParams:
    changes = {k: round(lo + max(0.0, min(1.0, xi)) * (hi - lo), 5) for (k, (lo, hi)), xi in zip(space.items(), x)}
    return base.with_values(**changes)


def latin_hypercube(n: int, dims: int, rng: random.Random) -> list[list[float]]:
    cols = []
    for _ in range(dims):
        strata = [(i + rng.random()) / n for i in range(n)]
        rng.shuffle(strata)
        cols.append(strata)
    return [[cols[d][i] for d in range(dims)] for i in range(n)]


def mutate(x: list[float], sigma: float, rng: random.Random) -> list[float]:
    """Change 1-4 coordinates by a Gaussian step (unit-cube scale), reflecting at the bounds."""
    y = list(x)
    for i in rng.sample(range(len(x)), min(len(x), rng.choice((1, 2, 2, 3, 3, 4)))):
        v = y[i] + rng.gauss(0.0, sigma)
        while v < 0.0 or v > 1.0:
            v = -v if v < 0.0 else 2.0 - v
        y[i] = v
    return y


# ------------------------------------------------------------------ one evaluation
def evaluate(job: dict) -> dict:
    """Retrain quickly for the job's params, then measure on the training leagues."""
    t0 = time.time()
    params = SpatialParams.from_dict(job["params"])
    seed = job["seed"]
    model = DecisionModel.load(job["warm"])
    if job["rounds"] > 0:
        model = train(job["rounds"], job["train_possessions"], start=model, verbose=False,
                      params=params, seed=seed)
    if job.get("objective") == "fullgame":
        metrics = collect_fullgames(job["games"], 0, params=params, model=model,
                                    seeds=list(TRAIN_SEEDS), offset=seed)
        metrics = {k: v for k, v in metrics.items() if not k.startswith("_")}
        sc, ok = score_full(metrics), within_full(metrics)
    else:
        stats, _ = play(job["possessions"], TRAIN_SEEDS, model=model, params=params, rng_seed=seed + 7919)
        metrics = summarize(stats)
        metrics = {k: v for k, v in metrics.items() if not isinstance(v, str)}
        sc, ok = score(metrics), within(metrics)
    return {**{k: job[k] for k in ("id", "phase", "params", "seed")}, **job.get("tags", {}),
            "score": sc, "within": ok, "metrics": metrics,
            "seconds": round(time.time() - t0, 1)}


# ------------------------------------------------------------------ the search
class Search:
    def __init__(self, log: Path, budget: dict, workers: int = 3, seed: int = 0,
                 warm: Path = DEFAULT_PATH, resume: bool = False,
                 printer: Callable[[str], None] = print, objective: str = "halfcourt"):
        self.log, self.budget, self.workers, self.seed = Path(log), budget, workers, seed
        self.objective = objective
        self.targets = targets_for(objective)
        self.warm = Path(warm)
        self.say = printer
        self.done: dict[str, dict] = {}
        self.state: Optional[dict] = None
        if resume and self.log.exists():
            for line in self.log.read_text().splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("phase") == "state":
                    self.state = rec
                else:
                    self.done[rec["id"]] = rec
            self.say(f"resuming: {len(self.done)} evaluations logged")
        elif self.log.exists():
            self.log.unlink()
        self.pool = multiprocessing.Pool(workers) if workers > 1 else None

    def close(self) -> None:
        if self.pool is not None:
            self.pool.close()
            self.pool.join()

    def write(self, rec: dict) -> None:
        with self.log.open("a") as f:
            f.write(json.dumps(rec) + "\n")

    def job(self, job_id: str, phase: str, params: SpatialParams, tags: Optional[dict] = None) -> dict:
        b = self.budget
        return {"id": job_id, "phase": phase, "params": params.to_dict(), "tags": tags or {},
                "seed": zlib.crc32(f"{self.seed}:{job_id}".encode()) % 1_000_000,
                "warm": str(self.warm), "rounds": b["rounds"], "train_possessions": b["train_possessions"],
                "possessions": b["possessions"], "objective": self.objective, "games": b.get("games", 30)}

    def run(self, jobs: list[dict]) -> list[dict]:
        """Evaluate jobs (reusing logged results); results in job order."""
        todo = [j for j in jobs if j["id"] not in self.done]
        results = (self.pool.imap_unordered(evaluate, todo) if self.pool else map(evaluate, todo))
        for rec in results:
            self.done[rec["id"]] = rec
            self.write(rec)
            m = rec["metrics"]
            detail = (f"pace {m['pace']:.1f} per100 {m['points_per_100']:.1f} efg {m['efg']:.3f} "
                      f"3s {m['three_share']:.3f} orb {m['orb_pct']:.3f} tov {m['tov_per_100']:.1f}"
                      if self.objective == "fullgame" else
                      f"ppp {m['points_per_possession']:.3f} rim {m['share_rim']:.2f} "
                      f"3s {m['share_three']:.2f} 3p% {m['fg_three']:.3f}")
            self.say(f"  {rec['id']:<10} score {rec['score']:7.2f}  {rec['within']:>2}/{len(self.targets)} ok  "
                     f"{detail} ({rec['seconds']:.0f}s)")
        return [self.done[j["id"]] for j in jobs]

    # -------------------------------------------------------------- phases
    def noise(self) -> dict:
        n = self.budget["noise"]
        recs = self.run([self.job(f"noise-{i}", "noise", DEFAULT_PARAMS) for i in range(n)])
        scores = [r["score"] for r in recs]
        out = {"mean": mean(scores), "sd": sd(scores), "metrics_sd": {}}
        for k in self.targets:
            out["metrics_sd"][k] = sd([r["metrics"][k] for r in recs])
        self.say(f"noise: default params score {out['mean']:.2f} +/- {out['sd']:.2f} (sd over {n} seeds)")
        for k, (_, tol) in self.targets.items():
            self.say(f"  {k:<24} sd {out['metrics_sd'][k]:.4f}  ({out['metrics_sd'][k] / tol:.2f} tol)")
        return out

    def sample(self) -> list[dict]:
        n = self.budget["samples"]
        rng = random.Random(self.seed)
        design = latin_hypercube(n, len(SEARCH_SPACE), rng)
        jobs = [self.job(f"lhs-{i}", "sample", from_unit(x)) for i, x in enumerate(design)]
        return self.run(jobs)

    def refine(self, start: list[dict]) -> dict:
        b = self.budget
        if self.state is not None:
            st = self.state
            self.say(f"refine: resuming at generation {st['generation']}")
        else:
            # Start from the best candidate seen so far (by mean score per params).
            groups: dict[str, list[dict]] = {}
            for r in start:
                groups.setdefault(json.dumps(r["params"], sort_keys=True), []).append(r)
            best_key = min(groups, key=lambda k: mean([r["score"] for r in groups[k]]))
            best = groups[best_key]
            st = {"phase": "state", "generation": 0, "params": best[0]["params"],
                  "scores": [r["score"] for r in best], "sigma": b["sigma"], "stale": 0, "accepted": 0}
            # Confirm the starting point with a second seed if it has only one.
            if len(st["scores"]) < 2:
                rec = self.run([self.job("start-confirm", "confirm", SpatialParams.from_dict(st["params"]))])[0]
                st["scores"].append(rec["score"])
        lam = max(1, self.workers)
        while st["generation"] < b["generations"] and st["stale"] < b["patience"] and st["sigma"] >= b["min_sigma"]:
            g = st["generation"]
            rng = random.Random(f"{self.seed}:gen:{g}")
            x = to_unit(SpatialParams.from_dict(st["params"]))
            inc = mean(st["scores"])
            mutants = [from_unit(mutate(x, st["sigma"], rng)) for _ in range(lam)]
            recs = self.run([self.job(f"g{g}-m{i}", "refine", p, {"generation": g})
                             for i, p in enumerate(mutants)])
            hopefuls = sorted((r for r in recs if r["score"] < inc), key=lambda r: r["score"])
            winner = None
            if hopefuls:
                confirms = self.run([self.job(f"g{g}-c{i}", "confirm", SpatialParams.from_dict(r["params"]),
                                              {"generation": g, "of": r["id"]})
                                     for i, r in enumerate(hopefuls)])
                means = [(mean([h["score"], c["score"]]), h, c) for h, c in zip(hopefuls, confirms)]
                m, h, c = min(means, key=lambda t: t[0])
                if m < inc:
                    winner = (h, c)
            if winner:
                h, c = winner
                st["params"], st["scores"] = h["params"], [h["score"], c["score"]]
                st["sigma"] = min(0.5, st["sigma"] * 1.25)
                st["stale"], st["accepted"] = 0, st["accepted"] + 1
                self.say(f"gen {g}: accepted {h['id']} (mean {mean(st['scores']):.2f} < {inc:.2f}), "
                         f"sigma {st['sigma']:.3f}")
            else:
                st["sigma"] *= 0.9
                st["stale"] += 1
                self.say(f"gen {g}: no improvement on {inc:.2f}, sigma {st['sigma']:.3f}")
            st["generation"] = g + 1
            self.write(st)
            self.state = st
        return st

    def search(self) -> dict:
        t0 = time.time()
        noise = self.noise()
        noise_recs = [self.done[f"noise-{i}"] for i in range(self.budget["noise"])]
        samples = self.sample() if self.budget["samples"] else []
        final = self.refine(noise_recs + samples)
        best = SpatialParams.from_dict(final["params"])
        self.say(f"\nbest: mean score {mean(final['scores']):.2f} (default {noise['mean']:.2f} "
                 f"+/- {noise['sd']:.2f}); {final['accepted']} accepted steps; "
                 f"{(time.time() - t0) / 60:.1f} min")
        return {"best": best, "best_scores": final["scores"], "noise": noise, "state": final}


def mean(xs: Iterable[float]) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else math.nan


def sd(xs: Iterable[float]) -> float:
    xs = list(xs)
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def at_bounds(params: SpatialParams, margin: float = 0.03) -> list[str]:
    """Searched parameters within ``margin`` (unit-cube scale) of a bound."""
    out = []
    for k, x in zip(SEARCH_SPACE, to_unit(params)):
        if x <= margin:
            out.append(f"{k} at low bound {SEARCH_SPACE[k][0]}")
        elif x >= 1 - margin:
            out.append(f"{k} at high bound {SEARCH_SPACE[k][1]}")
    return out


def main(argv: Optional[list[str]] = None) -> dict:
    ap = argparse.ArgumentParser(prog="hoopsim.spatial.calibrate")
    ap.add_argument("--log", default="calibration.jsonl", help="JSONL log of every evaluation")
    ap.add_argument("--resume", action="store_true", help="continue from the log")
    ap.add_argument("--out", default="calibrated_params.json", help="where to write the best params")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--warm", default=str(DEFAULT_PATH), help="decision values to warm-start each retrain from")
    ap.add_argument("--rounds", type=int, default=3, help="retrain rounds per candidate")
    ap.add_argument("--train-possessions", type=int, default=6000, help="possessions per retrain round")
    ap.add_argument("--possessions", type=int, default=9000, help="possessions measured per candidate")
    ap.add_argument("--noise", type=int, default=6, help="noise evaluations of the defaults")
    ap.add_argument("--samples", type=int, default=90, help="Latin-hypercube samples")
    ap.add_argument("--generations", type=int, default=60, help="refinement generations")
    ap.add_argument("--patience", type=int, default=15, help="stop after this many generations without a gain")
    ap.add_argument("--sigma", type=float, default=0.15, help="initial mutation step (unit-cube scale)")
    ap.add_argument("--min-sigma", type=float, default=0.02)
    ap.add_argument("--objective", choices=["halfcourt", "fullgame"], default="halfcourt",
                    help="score candidates on half-court possessions or on full games (2025-26 averages)")
    ap.add_argument("--games", type=int, default=30, help="full games per candidate (--objective fullgame)")
    args = ap.parse_args(argv)
    workers = max(1, min(3, args.workers))
    budget = {"rounds": args.rounds, "train_possessions": args.train_possessions,
              "possessions": args.possessions, "noise": max(1, args.noise), "samples": args.samples,
              "generations": args.generations, "patience": args.patience, "sigma": args.sigma,
              "min_sigma": args.min_sigma, "games": args.games}
    s = Search(Path(args.log), budget, workers=workers, seed=args.seed, warm=Path(args.warm),
               resume=args.resume, objective=args.objective)
    try:
        result = s.search()
    finally:
        s.close()
    result["best"].save(args.out)
    print(f"wrote {args.out}")
    for line in at_bounds(result["best"]):
        print(f"  {line}")
    return result


if __name__ == "__main__":
    main()
