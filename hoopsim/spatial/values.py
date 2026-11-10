"""Decision values: what each option is worth, learned from simulated outcomes.

Every option a ball handler can take is described by a short feature vector
(shot clock, expected points of the shot, how open the receiver is, ...). Its
value is a weighted sum of those features. The starting weights (``HEURISTIC``)
reproduce the original hand-written formulas; training (``train.py``) refits the
weights so each value predicts the points the possession actually went on to
score after taking that option.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

# Feature names per option (the first is always the constant 1).
FEATURES = {
    # ep: make chance x points with the predicted contest at release
    "shoot": ["bias", "ep", "is_three", "moving", "late_clock"],
    # clock: value of the rest of the possession (see hold_curve) after the action takes time
    "drive": ["bias", "clock", "skill_edge", "gap", "crowd", "beaten", "closeout", "dist_rim"],
    "continue": ["bias", "clock", "skill_edge", "gap", "crowd", "beaten", "closeout", "dist_rim"],
    # best: max(catch-and-shoot ep, keep-going value) discounted by interception risk
    # near_rim: receiver will catch within 8 ft of the rim; lob: thrown over the top
    "pass": ["bias", "catch_shoot", "clock", "risk", "back_pass", "best", "near_rim", "lob"],
    # backing down in the post
    "post": ["bias", "clock", "strength_edge", "skill_edge", "dist_rim", "doubled"],
    "pnr": ["bias", "clock", "handling", "screener_strength"],
    "hold": ["bias", "clock", "clock_raw"],
}

# Weights matching the original hand-written formulas.
HEURISTIC_WEIGHTS = {
    "shoot": [0.0, 1.0, 0.0, 0.0, 0.0],
    "drive": [0.01, 1.0, 0.025, 0.05, -0.03, 0.15, 0.12, 0.0],
    "continue": [-0.04, 1.0, 0.025, 0.05, -0.03, 0.25, 0.12, 0.0],
    "pass": [-0.03, 0.0, 0.0, 0.0, -0.08, 1.0, 0.0, 0.0],
    "post": [0.0, 1.0, 0.02, 0.02, -0.05, -0.10],
    "pnr": [0.06, 1.0, 0.012, 0.008],
    "hold": [0.0, 1.0, 0.0],
}

DEFAULT_PATH = Path(__file__).with_name("decision_values.json")


def hold_curve(shot_clock: float) -> float:
    """A fixed feature: how much time is left to work with, scaled 0-1.04."""
    return 1.04 * max(0.0, min(1.0, (shot_clock - 3) / 11))


class DecisionModel:
    def __init__(self, weights: dict[str, list[float]], meta: Optional[dict] = None):
        for kind, names in FEATURES.items():
            if len(weights.get(kind, [])) != len(names):
                raise ValueError(f"weights for '{kind}' need {len(names)} numbers")
        self.weights = {k: list(v) for k, v in weights.items()}
        self.meta = meta or {}

    def value(self, kind: str, features: list[float]) -> float:
        return sum(w * f for w, f in zip(self.weights[kind], features))

    def to_dict(self) -> dict:
        return {"features": FEATURES, "weights": self.weights, "meta": self.meta}

    def save(self, path: Path = DEFAULT_PATH) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=1))

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> "DecisionModel":
        d = json.loads(Path(path).read_text())
        if d.get("features") != FEATURES:
            raise ValueError("saved decision values use different features; retrain them")
        return cls(d["weights"], d.get("meta"))


HEURISTIC = DecisionModel(HEURISTIC_WEIGHTS, {"source": "hand-written formulas"})


def default_model() -> DecisionModel:
    """Learned values when they've been trained, else the hand-written ones."""
    try:
        return DecisionModel.load()
    except (FileNotFoundError, ValueError):
        return HEURISTIC


# --------------------------------------------------------------------------
# Fitting
# --------------------------------------------------------------------------
def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (small dense systems)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            raise ValueError("singular system")
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                if f:
                    m[r] = [x - f * y for x, y in zip(m[r], m[col])]
    return [m[i][n] / m[i][i] for i in range(n)]


def fit_kind(samples: list[tuple[list[float], float]], prior: list[float], ridge: float) -> list[float]:
    """Least squares pulled toward ``prior``:  min |Xw - y|^2 + ridge * |w - prior|^2."""
    k = len(prior)
    xtx = [[ridge if i == j else 0.0 for j in range(k)] for i in range(k)]
    xty = [ridge * p for p in prior]
    for x, y in samples:
        for i in range(k):
            xi = x[i]
            if xi == 0.0:
                continue
            xty[i] += xi * y
            row = xtx[i]
            for j in range(k):
                row[j] += xi * x[j]
    return _solve(xtx, xty)


def fit(samples: dict[str, list[tuple[list[float], float]]], prior: DecisionModel,
        ridge: float = 50.0, meta: Optional[dict] = None) -> DecisionModel:
    weights = {}
    for kind in FEATURES:
        data = samples.get(kind, [])
        weights[kind] = fit_kind(data, prior.weights[kind], ridge) if data else list(prior.weights[kind])
    return DecisionModel(weights, meta)
