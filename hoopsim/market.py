"""Free agency, AI trades and roster management."""
from __future__ import annotations

import random
from typing import Optional

from .contracts import (
    asking_salary, best_offer, contract_length, payment_route, payroll_for, player_value,
    team_strength,
)
from .lineup import auto_lineup
from .models import Contract, League, Player

TARGET_ROSTER = 14


def _roster_need(league: League, team_id: int) -> int:
    return TARGET_ROSTER - len(league.roster(team_id))


def _upgrade_margin(league: League, team_id: int, candidate_value: float) -> float:
    """How much better than our 10th man this free agent is (rotation upgrade)."""
    values = sorted((player_value(p, league, 2) for p in league.roster(team_id)), reverse=True)
    tenth = values[9] if len(values) > 9 else 0
    return candidate_value - tenth


def _choose_jersey(league: League, team_id: int, rng: random.Random) -> int:
    used = {q.jersey_number for q in league.roster(team_id)}
    return rng.choice([n for n in range(100) if n not in used])


def start_free_agency(season: int, rounds: int = 8) -> dict:
    """State of a free agency, a plain JSON-able dict so it can pause on the
    user's bids each round (see ``advance_free_agency``)."""
    return {"season": season, "rounds": rounds, "round": 0, "discount": 1.0, "log": [],
            "open": None, "done": False}


def _open_round(league: League, rng: random.Random, state: dict, user: Optional[int]) -> bool:
    """Set up this round: asks and every AI team's bid. False if nobody's left."""
    season = state["season"]
    pool = [p for p in league.free_agents() if not p.injury or p.injury.games_remaining < 20]
    if not pool:
        return False
    values = {p.id: player_value(p, league, 2) for p in pool}
    pool.sort(key=lambda p: -values[p.id])
    # Each player's preferred length and asking salary this round.
    terms: dict[int, tuple[int, int]] = {}
    for p in pool:
        years = contract_length(p, league, rng)
        terms[p.id] = (years, int(asking_salary(p, league, years) * state["discount"]))
    offers: dict[int, list[tuple[int, int, str, int]]] = {}
    teams = [t for t in league.teams if t != user]
    rng.shuffle(teams)
    for tid in teams:
        if len(league.roster(tid)) >= league.rules.max_roster:
            continue
        need = _roster_need(league, tid)
        for p in pool[:40]:
            years, ask = terms[p.id]
            if need <= 0 and _upgrade_margin(league, tid, values[p.id]) < 5:
                continue
            salary, how = best_offer(league, tid, ask, season, tid not in league.mle_used)
            if salary is None:
                continue
            offers.setdefault(p.id, []).append((tid, salary, how, years))
            break                                  # one bid per AI team per round
    state["open"] = {
        "pool": [p.id for p in pool],
        "terms": [[p.id, *terms[p.id]] for p in pool],
        "offers": [[pid, [list(b) for b in bids]] for pid, bids in offers.items()],
    }
    return True


def free_agency_round(state: dict) -> tuple[list[int], dict[int, tuple[int, int]]]:
    """The open round's pool (ids, best first) and terms {id: (years, ask)}."""
    rnd = state["open"]
    return list(rnd["pool"]), {pid: (years, ask) for pid, years, ask in rnd["terms"]}


def user_can_bid(league: League, user: Optional[int]) -> bool:
    return user is not None and len(league.roster(user)) < league.rules.max_roster


def _close_round(league: League, rng: random.Random, state: dict, user: Optional[int],
                 user_offers: list[tuple[int, int, int]]) -> None:
    """Add the user's bids, then every player takes his best offer."""
    season, log = state["season"], state["log"]
    pool_ids, terms = free_agency_round(state)
    offers: dict[int, list[tuple]] = {pid: [tuple(b) for b in bids] for pid, bids in state["open"]["offers"]}
    if user_can_bid(league, user):
        for pid, salary, years in user_offers:
            how = payment_route(league, user, salary, season, user not in league.mle_used)
            if pid in terms and how:
                offers.setdefault(pid, []).append((user, salary, how, max(1, min(5, years))))
    for pid in pool_ids:
        p = league.players[pid]
        bids = offers.get(p.id)
        if not bids or p.team_id is not None:          # (signed by the user meanwhile)
            continue
        _, ask = terms[p.id]

        def appeal(bid):
            tid, salary, _, _ = bid
            return salary * (1 + 0.006 * (team_strength(league, tid) - 100) * p.hidden.ambition / 10)

        tid, salary, how, years = max(bids, key=appeal)
        user_bid = any(b[0] == user for b in bids)
        if salary < ask * 0.85 or len(league.roster(tid)) >= league.rules.max_roster:
            if user_bid:
                log.append(f"{p.name} turns down your offer (asking ${ask / 1e6:.1f}M)")
            continue
        # The cap may have moved since the bid: re-check.
        if tid == user:
            how2 = payment_route(league, tid, salary, season, tid not in league.mle_used)
            salary2 = salary if how2 else None
        else:
            salary2, how2 = best_offer(league, tid, salary, season, tid not in league.mle_used)
        if salary2 is None:
            continue
        if how2 == "mid-level exception":
            league.mle_used.append(tid)
        p.jersey_number = _choose_jersey(league, tid, rng)
        league.sign(p.id, tid, Contract(salary2, season + years - 1))
        log.append(f"{league.teams[tid].abbreviation} sign {p.name} "
                   f"(CA {p.current_ability}, {p.age(league.current_date)}): {years}y ${salary2 / 1e6:.1f}M via {how2}")
        if user_bid and tid != user:
            log.append(f"{p.name} chose {league.teams[tid].abbreviation} over your offer")
    state["discount"] *= 0.9                           # unsigned players lower their sights
    state["round"] += 1
    state["open"] = None


def advance_free_agency(league: League, rng: random.Random, state: dict,
                        user: Optional[int]) -> bool:
    """Play rounds until the user (team ``user``) is asked for bids (returns True,
    with ``state["open"]`` describing the round) or free agency ends (False)."""
    while not state["done"]:
        if state["open"] is None:
            if state["round"] >= state["rounds"] or not _open_round(league, rng, state, user):
                state["log"] += fill_rosters(league, rng, state["season"])
                state["done"] = True
                break
        if user_can_bid(league, user):
            return True
        _close_round(league, rng, state, user, [])
    return False


def answer_free_agency(league: League, rng: random.Random, state: dict, user: Optional[int],
                       offers: list[tuple[int, int, int]]) -> None:
    """The user's bids for the open round: [(player id, salary, years)]."""
    if state["open"] is None:
        raise ValueError("no free-agency round is open")
    _close_round(league, rng, state, user, offers)


def run_free_agency(league: League, rng: random.Random, season: int, rounds: int = 8,
                    manager=None) -> list[str]:
    """Rounds of offers. Each round every AI team bids on its best affordable target
    and the user (via ``manager``) may bid on several. Each player takes his best
    offer (money first, then team quality) if it meets his falling asking price.
    Then teams fill to the minimum roster."""
    state = start_free_agency(season, rounds)
    user = league.user_team_id if manager is not None else None
    while advance_free_agency(league, rng, state, user):
        pool_ids, terms = free_agency_round(state)
        pool = [league.players[pid] for pid in pool_ids]
        answer_free_agency(league, rng, state, user,
                           manager.free_agency(league, pool, terms, state["round"] + 1))
    return state["log"]


def fill_rosters(league: League, rng: random.Random, season: int) -> list[str]:
    """Teams below the minimum sign the best remaining free agents to minimum deals."""
    log = []
    for tid in league.teams:
        while len(league.roster(tid)) < league.rules.min_roster:
            pool = [p for p in league.free_agents() if not p.injury]
            if not pool:
                raise RuntimeError("free agent pool is empty")
            p = max(pool, key=lambda q: player_value(q, league, 2))
            p.jersey_number = _choose_jersey(league, tid, rng)
            league.sign(p.id, tid, Contract(league.rules.min_salary, season))
            log.append(f"{league.teams[tid].abbreviation} sign {p.name} to a minimum deal")
    return log


def user_excess(league: League) -> int:
    """How many players the user's team must waive to get down to the maximum."""
    if league.user_team_id is None:
        return 0
    return max(0, len(league.roster(league.user_team_id)) - league.rules.max_roster)


def trim_rosters(league: League, manager=None, user_waivers: Optional[list[int]] = None) -> list[str]:
    """Teams over the maximum waive their least valuable players (cheapest first on ties).
    The user's waivers are the ``manager``'s call (or ``user_waivers``); an invalid
    choice falls back to the AI's."""
    if user_waivers is None and manager is not None and user_excess(league) > 0:
        user_waivers = manager.choose_waivers(league, user_excess(league))
    log = []
    for tid in league.teams:
        roster = league.roster(tid)
        excess = len(roster) - league.rules.max_roster
        if excess <= 0:
            continue
        keep_score = sorted(roster, key=lambda p: (player_value(p, league, 3),
                                                   p.contract.salary if p.contract else 0))
        to_waive = keep_score[:excess]
        if user_waivers is not None and tid == league.user_team_id:
            chosen = user_waivers
            ids = {p.id for p in roster}
            if len(set(chosen)) == excess and set(chosen) <= ids:
                to_waive = [league.players[pid] for pid in chosen]
        for p in to_waive:
            log.append(f"{league.teams[tid].abbreviation} waive {p.name}")
            league.release(p.id)
    return log


# --------------------------------------------------------------------------
# Trades
# --------------------------------------------------------------------------
_RANK_WEIGHTS = [1.0, 0.9, 0.8, 0.7, 0.6, 0.45, 0.35, 0.25, 0.15, 0.1]


def _team_value(league: League, players: list[Player], horizon: int) -> float:
    vals = sorted((player_value(p, league, horizon) for p in players), reverse=True)
    # Stars matter far more than depth: convex in ability, weighted by rank.
    return sum(w * max(0.0, v - 70) ** 1.5 for w, v in zip(_RANK_WEIGHTS, vals))


def _contending(league: League, team_id: int) -> bool:
    """Top 40% of the league by top-8 strength: win now. Everyone else builds."""
    ranked = sorted(league.teams, key=lambda t: team_strength(league, t), reverse=True)
    return ranked.index(team_id) < max(1, round(len(ranked) * 0.4))


def _trade_gain(league: League, team_id: int, before: list[Player], after: list[Player]) -> float:
    """Contenders value ability now; rebuilders value the next 3 years and shed salary."""
    contending = _contending(league, team_id)
    horizon = 1 if contending else 3
    gain = _team_value(league, after, horizon) - _team_value(league, before, horizon)
    salary_change = (sum(p.contract.salary for p in after) - sum(p.contract.salary for p in before)) / 1e6
    over_tax = payroll_for(league, team_id, league.season) > league.rules.luxury_tax_line
    money_weight = 0.3 if contending and not over_tax else 2.0 if over_tax else 1.2
    return gain - money_weight * salary_change


def _salaries_match(league: League, a_out: list[Player], b_out: list[Player], season: int,
                    a: int, b: int) -> bool:
    sa = sum(p.contract.salary for p in a_out)
    sb = sum(p.contract.salary for p in b_out)
    for tid, incoming, outgoing in ((a, sb, sa), (b, sa, sb)):
        over_cap = payroll_for(league, tid, season) - outgoing + incoming > league.rules.salary_cap
        if over_cap and incoming > outgoing * 1.25 + 100_000:
            return False
    return True


def ai_trades(league: League, rng: random.Random, attempts: int = 300,
              max_trades: int = 4) -> list[str]:
    """Random 1-for-1 and 2-for-1 proposals; done when both teams' value rises."""
    log = []
    season = league.season
    team_ids = [t for t in league.teams if t != league.user_team_id]   # never trade for the user
    for _ in range(attempts):
        if len(log) >= max_trades:
            break
        a, b = rng.sample(team_ids, 2)
        ra, rb = league.roster(a), league.roster(b)
        a_out = [rng.choice(ra[:10])]
        b_out = rng.sample(rb[:12], rng.choice([1, 1, 2]))
        if len(ra) - len(a_out) + len(b_out) > league.rules.max_roster or \
                len(rb) - len(b_out) + len(a_out) > league.rules.max_roster or \
                len(rb) - len(b_out) + len(a_out) < league.rules.min_roster:
            continue
        if not _salaries_match(league, a_out, b_out, season, a, b):
            continue
        out_a, out_b = {p.id for p in a_out}, {p.id for p in b_out}
        new_a = [p for p in ra if p.id not in out_a] + b_out
        new_b = [p for p in rb if p.id not in out_b] + a_out
        if _trade_gain(league, a, ra, new_a) > 5 and _trade_gain(league, b, rb, new_b) > 5:
            league.trade(a, [p.id for p in a_out], b, [p.id for p in b_out])
            auto_lineup(league, a)
            auto_lineup(league, b)
            ta, tb = league.teams[a].abbreviation, league.teams[b].abbreviation
            log.append(f"TRADE: {ta} send {', '.join(p.name for p in a_out)} to {tb} "
                       f"for {', '.join(p.name for p in b_out)}")
    return log


# --------------------------------------------------------------------------
# Trades involving the user's team
# --------------------------------------------------------------------------
def evaluate_trade(league: League, user_out: list[int], other: int,
                   other_out: list[int]) -> tuple[bool, str]:
    """Would AI team ``other`` accept? Checks ownership, roster limits and salary
    matching, then whether the deal improves the AI team by its own priorities."""
    user = league.user_team_id
    if user is None or other == user or other not in league.teams:
        return False, "pick another team"
    if not user_out and not other_out:
        return False, "the trade is empty"
    for pid in user_out:
        if pid not in league.players or league.players[pid].team_id != user:
            return False, f"player {pid} isn't on your team"
    for pid in other_out:
        if pid not in league.players or league.players[pid].team_id != other:
            return False, f"player {pid} isn't on {league.teams[other].abbreviation}"
    ru, ro = league.roster(user), league.roster(other)
    uo = [league.players[p] for p in user_out]
    oo = [league.players[p] for p in other_out]
    rules = league.rules
    for size, who in ((len(ru) - len(uo) + len(oo), "your"), (len(ro) - len(oo) + len(uo), "their")):
        if size > rules.max_roster:
            return False, f"{who} roster would be over {rules.max_roster}"
        if size < rules.min_roster - 2:
            return False, f"{who} roster would be too short"
    if not _salaries_match(league, uo, oo, league.season, user, other):
        return False, "salaries don't match (over-the-cap teams can take back at most 125% + $100k)"
    new_o = [p for p in ro if p.id not in set(other_out)] + uo
    gain = _trade_gain(league, other, ro, new_o)
    if gain <= 2:
        return False, f"{league.teams[other].abbreviation} say no: it doesn't help them"
    return True, "accepted"


def execute_user_trade(league: League, user_out: list[int], other: int, other_out: list[int]) -> None:
    league.trade(league.user_team_id, user_out, other, other_out)
    auto_lineup(league, other)
    if league.teams[league.user_team_id].auto_lineup:
        auto_lineup(league, league.user_team_id)


def start_offers(attempts: int = 150, max_offers: int = 2) -> dict:
    """State of a round of AI trade pitches to the user (JSON-able, resumable)."""
    return {"attempt": 0, "attempts": attempts, "offered": 0, "max_offers": max_offers,
            "log": [], "offer": None}


def advance_offers(league: League, rng: random.Random, state: dict) -> bool:
    """Look for the next deal an AI team likes and thinks the user might take.
    True when one is waiting in ``state["offer"]``: {team_id, you_give, you_get}."""
    user = league.user_team_id
    if user is None or state["offer"] is not None:
        return state["offer"] is not None
    others = [t for t in league.teams if t != user]
    while state["attempt"] < state["attempts"]:
        if state["offered"] >= state["max_offers"]:
            break
        state["attempt"] += 1
        b = rng.choice(others)
        ru, rb = league.roster(user), league.roster(b)
        user_out = [rng.choice(ru[:10])]
        b_out = rng.sample(rb[:12], rng.choice([1, 1, 2]))
        ok, _ = evaluate_trade(league, [p.id for p in user_out], b, [p.id for p in b_out])
        if not ok:
            continue
        # Only pitch deals the user's side could plausibly like.
        out_ids = {p.id for p in user_out}
        new_u = [p for p in ru if p.id not in out_ids] + b_out
        if _trade_gain(league, user, ru, new_u) < -5:
            continue
        state["offered"] += 1
        state["offer"] = {"team_id": b, "you_give": [p.id for p in user_out],
                          "you_get": [p.id for p in b_out]}
        return True
    return False


def answer_offer(league: League, state: dict, accept: bool) -> str:
    """Accept or turn down the waiting offer. Returns the log line."""
    offer = state["offer"]
    if offer is None:
        raise ValueError("no trade offer is waiting")
    state["offer"] = None
    b = offer["team_id"]
    names_out = ", ".join(league.players[i].name for i in offer["you_give"])
    names_in = ", ".join(league.players[i].name for i in offer["you_get"])
    if accept:
        ok, _ = evaluate_trade(league, offer["you_give"], b, offer["you_get"])   # still on?
        if ok:
            execute_user_trade(league, offer["you_give"], b, offer["you_get"])
            line = f"TRADE: you send {names_out} to {league.teams[b].abbreviation} for {names_in}"
        else:
            line = f"{league.teams[b].abbreviation}'s offer of {names_in} for {names_out} is off the table"
    else:
        line = f"You turned down {league.teams[b].abbreviation}'s offer of {names_in} for {names_out}"
    state["log"].append(line)
    return line


def ai_offers_to_user(league: League, rng: random.Random, manager, attempts: int = 150,
                      max_offers: int = 2) -> list[str]:
    """AI teams pitch trades they like and think the user might take."""
    if league.user_team_id is None or manager is None:
        return []
    state = start_offers(attempts, max_offers)
    while advance_offers(league, rng, state):
        answer_offer(league, state, bool(manager.respond_to_trade(league, dict(state["offer"]))))
    return state["log"]
