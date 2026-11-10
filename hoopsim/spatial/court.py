"""Half-court geometry, in feet.

x runs across the court (-25 left sideline .. +25 right sideline, looking at the
basket from half court). y runs from the baseline (0) to half court (47).
"""
from __future__ import annotations

import math

WIDTH = 50.0
HALF_LENGTH = 47.0
FULL_LENGTH = 94.0                # transition: the far baseline, in the attacking frame
RIM = (0.0, 5.25)                 # centre of the rim
BACKBOARD_Y = 4.0
THREE_RADIUS = 23.75
CORNER_THREE_X = 22.0
CORNER_THREE_Y = RIM[1] + math.sqrt(THREE_RADIUS ** 2 - CORNER_THREE_X ** 2)   # ~14.2
PAINT_HALF_WIDTH = 8.0
FREE_THROW_Y = 19.0
RESTRICTED_RADIUS = 4.0

RIM_ZONE, CLOSE_ZONE, MID_ZONE, THREE_ZONE = "rim", "close", "mid", "three"


def dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def dist_to_rim(p: tuple[float, float]) -> float:
    return dist(p, RIM)


def is_three(p: tuple[float, float]) -> bool:
    x, y = p
    if abs(x) >= CORNER_THREE_X and y <= CORNER_THREE_Y:
        return True
    return dist_to_rim(p) >= THREE_RADIUS


def zone(p: tuple[float, float]) -> str:
    if is_three(p):
        return THREE_ZONE
    d = dist_to_rim(p)
    if d < RESTRICTED_RADIUS:
        return RIM_ZONE
    if d < 14:
        return CLOSE_ZONE
    return MID_ZONE


def clamp(p: tuple[float, float], full: bool = False) -> tuple[float, float]:
    """Keep a point inside the half court (and out of the area behind the backboard).
    ``full`` allows the backcourt too (players still running up in transition)."""
    x = max(-WIDTH / 2 + 0.5, min(WIDTH / 2 - 0.5, p[0]))
    y = max(0.5, min((FULL_LENGTH if full else HALF_LENGTH) - 0.5, p[1]))
    return x, y


def toward(a: tuple[float, float], b: tuple[float, float], distance: float) -> tuple[float, float]:
    """The point ``distance`` feet from a along the line to b."""
    d = dist(a, b)
    if d < 1e-9:
        return a
    t = distance / d
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def point_segment_distance(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 < 1e-9:
        return dist(p, a)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))
