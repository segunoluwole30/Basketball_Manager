"""Summer development: young players grow toward potential, veterans decline, some retire.

Growth is expressed in attribute points per season (1-20 scale). One point on
every attribute is roughly +10 CA.
"""
from __future__ import annotations

import math
import random

from .models import ABILITY_ATTRIBUTES, ATTR_MAX, ATTR_MIN, Attributes, HiddenAttributes, Player

PHYSICAL = ("speed", "strength", "vertical", "stamina")
MENTAL = ("basketball_iq", "composure")

# Average change per attribute over the summer after a season at this age.
_GROWTH = {19: 1.0, 20: 0.9, 21: 0.8, 22: 0.65, 23: 0.5, 24: 0.4, 25: 0.3, 26: 0.2,
           27: 0.1, 28: 0.0, 29: -0.1, 30: -0.2, 31: -0.35, 32: -0.5, 33: -0.7,
           34: -0.9, 35: -1.1}


def growth_at(age: int) -> float:
    if age <= 19:
        return _GROWTH[19]
    return _GROWTH.get(age, -1.3)


def _round_random(x: float, rng: random.Random) -> int:
    """Round so that the expected value is x (0.3 -> 1 with 30% chance)."""
    base = math.floor(x)
    return base + (rng.random() < x - base)


def develop(player: Player, age: int, minutes_per_game: float, rng: random.Random) -> int:
    """Apply one summer of development. Returns the change in current ability."""
    before = player.current_ability
    h = player.hidden
    base = growth_at(age)
    work = 0.6 + h.work_ethic / 25                    # 0.64 .. 1.4
    pro = 1.25 - h.professionalism / 40               # 1.22 .. 0.75 (slows decline)
    playing = 0.8 + 0.4 * min(1.0, minutes_per_game / 28)
    headroom = max(0.0, min(1.0, (player.potential_ability - before) / 25))
    breakout = rng.gauss(0, 0.3)                       # some summers just click

    values = player.attributes.to_dict()
    original = dict(values)
    for name in ABILITY_ATTRIBUTES:
        delta = base
        if name in PHYSICAL and age >= 29:
            delta -= 0.3                               # athleticism goes first
        if name in MENTAL:
            delta = max(delta, 0.0) + (0.15 if age <= 33 else 0.0)  # experience
        delta += breakout + rng.gauss(0, 0.5)
        delta *= work * playing * headroom if delta > 0 else pro
        values[name] = max(ATTR_MIN, min(ATTR_MAX, values[name] + _round_random(delta, rng)))
    player.attributes = Attributes(**values)

    # Never grow past potential: take back this summer's gains until under it.
    while player.current_ability > player.potential_ability:
        gained = [n for n in ABILITY_ATTRIBUTES if values[n] > original[n]]
        if not gained:
            break
        n = rng.choice(gained)
        values[n] -= 1
        player.attributes = Attributes(**values)

    # Foul drawing (not part of ability) follows the same curve, from its own random
    # numbers, so the other ratings develop exactly as they did before it existed.
    side = random.Random(f"drawing_fouls|{player.id}|{age}|{original['drawing_fouls']}")
    delta = base + breakout + side.gauss(0, 0.5)
    delta *= work * playing * headroom if delta > 0 else pro
    values["drawing_fouls"] = max(ATTR_MIN, min(ATTR_MAX, values["drawing_fouls"] + _round_random(delta, side)))
    player.attributes = Attributes(**values)

    if age >= 30:   # older bodies break down more
        hidden = player.hidden.to_dict()
        hidden["injury_proneness"] = min(ATTR_MAX, hidden["injury_proneness"] + (rng.random() < 0.3))
        player.hidden = HiddenAttributes(**hidden)
    return player.current_ability - before


def retirement_chance(player: Player, age: int, signed: bool) -> float:
    """Chance of retiring this summer: old and/or no longer good enough."""
    if age >= 41:
        return 1.0
    if age < 31:
        return 0.0
    ca = player.current_ability
    x = (age - 37) * 0.9 + (90 - ca) / 15
    if not signed:
        x += 1.5                                      # nobody wants him
    return 1 / (1 + math.exp(-x))
