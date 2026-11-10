"""Choosing starters and sharing out minutes."""
from __future__ import annotations

from itertools import combinations, permutations

from .models import League, Player, Position

POSITIONS = list(Position)
GAME_MINUTES = 48 * 5
MAX_MINUTES = 40

_MINUTES_SHAPE = [34, 34, 33, 32, 32, 24, 20, 16, 12, 8, 6, 4, 3, 2, 1]


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
    """Return (starter ids in PG..C order, {player id: target minutes}) summing to 240."""
    available = sorted((p for p in players if p.is_available),
                       key=lambda p: p.current_ability, reverse=True)
    starters = best_starting_five(available)
    bench = [p for p in available if p not in starters]
    rotation = (starters + bench)[:max(5, depth)]

    shape = _MINUTES_SHAPE[:len(rotation)]
    scale = GAME_MINUTES / sum(shape)
    # An injury-hit team with only 5 or 6 healthy players has to ride them longer
    # than the usual 40-minute cap (otherwise 240 minutes can't be reached).
    cap = max(MAX_MINUTES, -(-GAME_MINUTES // len(rotation)))
    minutes = [min(cap, round(m * scale)) for m in shape]
    i = 0
    while sum(minutes) != GAME_MINUTES:  # fix rounding drift, starters first
        step = 1 if sum(minutes) < GAME_MINUTES else -1
        if 1 <= minutes[i] + step <= cap:
            minutes[i] += step
        i = (i + 1) % len(minutes)
    return [p.id for p in starters], {p.id: m for p, m in zip(rotation, minutes)}


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
