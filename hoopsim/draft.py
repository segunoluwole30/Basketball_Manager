"""Draft class generation, lottery and the draft itself."""
from __future__ import annotations

import random
from typing import Optional

from .contracts import rookie_contract
from .generate import generate_player
from .models import League, Player
from .playoffs import ranked

LOTTERY_PICKS = 4
_LOTTERY_WEIGHTS = [14, 14, 14, 12.5, 10.5, 9, 7.5, 6, 4.5, 3, 2, 1.5, 1, 0.5]


def generate_draft_class(league: League, rng: random.Random, size: int) -> list[Player]:
    """Mostly 19-22-year-olds; a few have star potential, most won't stick."""
    prospects = []
    first_id = league.next_player_id()
    for i in range(size):
        age = rng.choices([19, 20, 21, 22], [35, 30, 20, 15])[0]
        potential = int(max(45, min(195, rng.gauss(89, 27.5))))
        # Ids are final: every prospect joins the league (drafted or not).
        prospects.append(generate_player(rng, first_id + i, league.current_date,
                                         age=age, potential=potential))
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


def start_draft(league: League, rng: random.Random, order: list[int], season: int) -> dict:
    """Generate the class and the league's big board. Returns the draft's state,
    a plain JSON-able dict, so a draft can be paused on the user's pick and saved."""
    rounds = league.rules.draft_rounds
    prospects = generate_draft_class(league, rng, len(order) * rounds + len(order) // 2)
    board = [[i, _scouted_value(p, rng)] for i, p in enumerate(prospects)]
    return {
        "order": list(order),
        "season": season,
        "picks": len(order) * rounds,
        "made": 0,                                     # picks made so far
        "board": board,                                # [prospect index, AI board value], in order
        "prospects": {str(i): p.to_dict() for i, p in enumerate(prospects)},
        "log": [],
    }


def draft_on_the_clock(state: dict) -> tuple[int, int]:
    """(overall pick number, team id) for the next pick."""
    n = state["made"] + 1
    return n, state["order"][(n - 1) % len(state["order"])]


def draft_available(state: dict) -> list[Player]:
    """Prospects still on the board, in board order (not yet league players)."""
    return [Player.from_dict(state["prospects"][str(i)]) for i, _ in state["board"]]


def _make_pick(league: League, rng: random.Random, state: dict, idx: int) -> Player:
    pick, team_id = draft_on_the_clock(state)
    state["board"] = [entry for entry in state["board"] if entry[0] != idx]
    choice = Player.from_dict(state["prospects"].pop(str(idx)))
    choice.team_id = team_id
    choice.draft_year, choice.draft_pick = state["season"], pick
    used = {q.jersey_number for q in league.roster(team_id)}
    choice.jersey_number = rng.choice([n for n in range(100) if n not in used])
    choice.contract = rookie_contract(pick, league, state["season"])
    league.add_player(choice)
    state["made"] += 1
    state["log"].append(f"#{pick} {league.teams[team_id].abbreviation}: {choice.name} "
                        f"({choice.position.value}, {choice.age(league.current_date)}, CA {choice.current_ability})")
    return choice


def advance_draft(league: League, rng: random.Random, state: dict,
                  user_team_id: Optional[int]) -> bool:
    """AI teams pick until it's ``user_team_id``'s turn (returns True) or the
    draft is over (undrafted prospects become free agents; returns False)."""
    while state["made"] < state["picks"]:
        _, team_id = draft_on_the_clock(state)
        if user_team_id is not None and team_id == user_team_id:
            return True
        # Every team scouts differently: re-roll a little noise per pick.
        board = {i: v for i, v in state["board"]}
        idx = max(board, key=lambda i: board[i] + rng.gauss(0, 4))
        _make_pick(league, rng, state, idx)
    for idx, _ in state["board"]:                      # undrafted: free agents
        p = Player.from_dict(state["prospects"].pop(str(idx)))
        p.draft_year = state["season"]
        league.add_player(p)
    state["board"] = []
    return False


def user_draft_pick(league: League, rng: random.Random, state: dict, player_id: int) -> Player:
    """Make the user's pick (a prospect id from the board)."""
    for idx, _ in state["board"]:
        if state["prospects"][str(idx)]["id"] == player_id:
            return _make_pick(league, rng, state, idx)
    raise ValueError(f"player {player_id} isn't on the draft board")


def run_draft(league: League, rng: random.Random, order: list[int], season: int,
              manager=None) -> list[str]:
    """Draft ``rules.draft_rounds`` rounds; picks get rookie-scale deals for ``season``.

    The user's picks are made by ``manager.draft_pick``."""
    state = start_draft(league, rng, order, season)
    user = league.user_team_id if manager is not None else None
    while advance_draft(league, rng, state, user):
        pick, _ = draft_on_the_clock(state)
        choice = manager.draft_pick(league, pick, draft_available(state))
        user_draft_pick(league, rng, state, choice.id)
    return state["log"]
