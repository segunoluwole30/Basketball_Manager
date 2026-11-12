"""Choosing starters and sharing out minutes."""
from __future__ import annotations

import math
from itertools import combinations, permutations

from .models import League, Player, Position

POSITIONS = list(Position)
GAME_MINUTES = 48 * 5
MAX_MINUTES = 40
STAR_CAP = 38          # the most a coach plans for anyone (a healthy roster)

# Minutes by rank of ability in the rotation (not by starting spot), from the 2025-26
# NBA: a team's best player averages about 34, then 31.5, 29.5, 28, 26, 24, 21.5, 19.5,
# 17.5 and 14.5 (players with 40+ games). The shape is scaled to 240 for the rotation.
_MINUTES_SHAPE = [31.0, 29.5, 28.2, 27.0, 25.6, 24.2, 22.2, 20.6, 18.8, 15.5, 6, 4, 3, 2, 1]
# A player far better (or worse) than his rotation's average plays more (or less):
# minutes scale by exp(TALENT_PULL * (ability - average) / 10). Stars on thin teams
# reach 37-38, as in the NBA.
TALENT_PULL = 0.035
# Coaches rank a starter as if he were this much better (ability points) when sharing
# out minutes: a strong sixth man still out-plays a weak starter, but a starter picked
# for his position isn't buried behind similar bench players.
STARTER_EDGE = 12


def best_starting_five(players: list[Player]) -> list[Player]:
    """Players for PG, SG, SF, PF, C (in that order) maximising total effective ability.

    Brute force over the top 10 candidates: C(10,5) * 5! = 30k checks, instant.
    """
    candidates = sorted(players, key=lambda p: p.current_ability, reverse=True)[:10]
    if len(candidates) < 5:
        raise ValueError("need at least 5 players")
    rating = {(p.id, pos): p.ability_at(pos) for p in candidates for pos in POSITIONS}
    best, best_score = None, -1
    for group in combinations(candidates, 5):
        for order in permutations(group):
            score = sum(rating[p.id, pos] for p, pos in zip(order, POSITIONS))
            if score > best_score:
                best, best_score = list(order), score
    return best


def plan_rotation(players: list[Player], depth: int) -> tuple[list[int], dict[int, int]]:
    """Return (starter ids in PG..C order, {player id: target minutes}) summing to 240.

    Minutes follow ability, not the starting spot: the best players play the most,
    a strong sixth man can out-play the weakest starter, and a star well above his
    teammates gets star minutes (up to ``STAR_CAP``)."""
    available = sorted((p for p in players if p.is_available),
                       key=lambda p: p.current_ability, reverse=True)
    starters = best_starting_five(available)
    bench = [p for p in available if p not in starters]
    def rank(p: Player) -> float:
        return p.current_ability + (STARTER_EDGE if p in starters else 0)

    rotation = sorted((starters + bench)[:max(5, depth)], key=rank, reverse=True)
    avg = sum(rank(p) for p in rotation) / len(rotation)
    want = [m * math.exp(TALENT_PULL * (rank(p) - avg) / 10)
            for m, p in zip(_MINUTES_SHAPE, rotation)]
    # An injury-hit team with only 5 or 6 healthy players has to ride them longer
    # than usual (otherwise 240 minutes can't be reached).
    cap = max(STAR_CAP, -(-GAME_MINUTES // len(rotation)))
    minutes = _share(want, cap)
    i = 0
    while sum(minutes) != GAME_MINUTES:  # fix rounding drift, best players first
        step = 1 if sum(minutes) < GAME_MINUTES else -1
        if 1 <= minutes[i] + step <= cap:
            minutes[i] += step
        i = (i + 1) % len(minutes)
    return [p.id for p in starters], {p.id: m for p, m in zip(rotation, minutes)}


def _share(want: list[float], cap: int) -> list[int]:
    """Scale ``want`` to 240 minutes, no one above ``cap`` (the excess goes to the rest)."""
    fixed: dict[int, float] = {}
    while True:
        free = [i for i in range(len(want)) if i not in fixed]
        left = GAME_MINUTES - sum(fixed.values())
        scale = left / sum(want[i] for i in free)
        over = [i for i in free if want[i] * scale > cap]
        if not over:
            break
        for i in over:
            fixed[i] = cap
    return [round(fixed.get(i, want[i] * scale)) for i in range(len(want))]


def auto_lineup(league: League, team_id: int) -> None:
    """Have the coach set the team's starters and minutes."""
    tactics = league.teams[team_id].tactics
    roster = [p for p in league.roster(team_id) if p.is_available]
    if len(roster) < 5:
        tactics.starters, tactics.minutes_targets = [], {}
        return
    tactics.starters, tactics.minutes_targets = plan_rotation(roster, tactics.rotation_depth)


def lineup_is_usable(league: League, team_id: int) -> bool:
    """True if the saved starters and rotation are all on the roster and healthy."""
    tactics = league.teams[team_id].tactics
    healthy = {p.id for p in league.roster(team_id) if p.is_available}
    return (len(tactics.starters) == 5
            and set(tactics.starters) <= healthy
            and set(tactics.minutes_targets) <= healthy)
