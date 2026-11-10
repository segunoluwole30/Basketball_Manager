"""Injury types and durations."""
from __future__ import annotations

import random

from .models import Injury

# (share of injuries, min games out, max games out, descriptions)
INJURY_TABLE = [
    (0.45, 0, 2, ["sore knee", "mild ankle sprain", "back spasms", "bruised hip", "jammed finger"]),
    (0.30, 3, 10, ["sprained ankle", "hamstring strain", "groin strain", "sprained wrist"]),
    (0.18, 11, 30, ["knee sprain", "broken hand", "calf strain", "high ankle sprain"]),
    (0.07, 31, 82, ["torn meniscus", "broken foot", "torn knee ligament", "ruptured tendon"]),
]


def draw_injury(rng: random.Random) -> Injury:
    shares = [row[0] for row in INJURY_TABLE]
    _, lo, hi, names = rng.choices(INJURY_TABLE, shares)[0]
    # Skew towards the short end of each band.
    games = lo + int((hi - lo + 1) * rng.random() ** 1.3)
    return Injury(rng.choice(names), min(games, hi))
