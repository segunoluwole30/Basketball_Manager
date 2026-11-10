"""Draft class generation, lottery and the draft itself."""
from __future__ import annotations

import random

from .contracts import rookie_contract
from .generate import generate_player
from .models import League, Player
from .playoffs import ranked

LOTTERY_PICKS = 4
_LOTTERY_WEIGHTS = [14, 14, 14, 12.5, 10.5, 9, 7.5, 6, 4.5, 3, 2, 1.5, 1, 0.5]


def generate_draft_class(league: League, rng: random.Random, size: int) -> list[Player]:
    """Mostly 19-22-year-olds; a few have star potential, most won't stick."""
    prospects = []
    for _ in range(size):
        age = rng.choices([19, 20, 21, 22], [35, 30, 20, 15])[0]
        potential = int(max(45, min(195, rng.gauss(86, 27))))
        p = generate_player(rng, league.next_player_id() + len(prospects), league.current_date,
                            age=age, potential=potential)
        prospects.append(p)
    return prospects


def draft_order(league: League, rng: random.Random, playoff_team_ids: set[int]) -> list[int]:
    """First-round order: lottery among non-playoff teams for the top picks, then
    reverse standings. Later rounds are straight reverse standings."""
    worst_first = list(reversed(ranked(league, league.teams)))
    lottery = [t for t in worst_first if t not in playoff_team_ids]
    rest = [t for t in worst_first if t in playoff_team_ids]
    weights = {t: _LOTTERY_WEIGHTS[i] if i < len(_LOTTERY_WEIGHTS) else 0.5
               for i, t in enumerate(lottery)}
    winners = []
    pool = list(lottery)
    for _ in range(min(LOTTERY_PICKS, len(pool))):
        pick = rng.choices(pool, [weights[t] for t in pool])[0]
        winners.append(pick)
        pool.remove(pick)
    return winners + [t for t in lottery if t not in winners] + rest


def _scouted_value(p: Player, rng: random.Random) -> float:
    """What a front office thinks a prospect is worth: mostly potential, seen through noise."""
    return 0.35 * p.current_ability + 0.65 * (p.potential_ability + rng.gauss(0, 15))


def run_draft(league: League, rng: random.Random, order: list[int], season: int) -> list[str]:
    """Draft ``rules.draft_rounds`` rounds; picks get rookie-scale deals for ``season``."""
    rounds = league.rules.draft_rounds
    prospects = generate_draft_class(league, rng, len(order) * rounds + len(order) // 2)
    board = {i: _scouted_value(p, rng) for i, p in enumerate(prospects)}
    log = []
    pick = 0
    for rnd in range(rounds):
        for team_id in order:
            pick += 1
            # Every team scouts differently: re-roll a little noise per pick.
            idx = max(board, key=lambda i: board[i] + rng.gauss(0, 4))
            del board[idx]
            choice = prospects[idx]
            choice.id = league.next_player_id()
            choice.team_id = team_id
            choice.draft_year, choice.draft_pick = season, pick
            used = {q.jersey_number for q in league.roster(team_id)}
            choice.jersey_number = rng.choice([n for n in range(100) if n not in used])
            choice.contract = rookie_contract(pick, league, season)
            league.add_player(choice)
            log.append(f"#{pick} {league.teams[team_id].abbreviation}: {choice.name} "
                       f"({choice.position.value}, {choice.age(league.current_date)}, CA {choice.current_ability})")
    # Undrafted prospects become free agents.
    for idx in board:
        p = prospects[idx]
        p.id = league.next_player_id()
        p.draft_year = season
        league.add_player(p)
    return log
