"""Free agency, AI trades and roster management."""
from __future__ import annotations

import random

from .contracts import (
    asking_salary, best_offer, contract_length, payroll_for, player_value, team_strength,
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


def run_free_agency(league: League, rng: random.Random, season: int, rounds: int = 8) -> list[str]:
    """Rounds of offers. Each round every team bids on its best affordable target;
    each player takes his best offer (money first, then team quality), if it
    meets his falling asking price. Then teams fill to the minimum roster."""
    log = []
    mle_used: set[int] = set()
    ask_discount = 1.0
    for _ in range(rounds):
        pool = [p for p in league.free_agents() if not p.injury or p.injury.games_remaining < 20]
        if not pool:
            break
        values = {p.id: player_value(p, league, 2) for p in pool}
        pool.sort(key=lambda p: -values[p.id])
        offers: dict[int, list[tuple[int, int, str]]] = {}
        teams = list(league.teams)
        rng.shuffle(teams)
        for tid in teams:
            if len(league.roster(tid)) >= league.rules.max_roster:
                continue
            need = _roster_need(league, tid)
            for p in pool[:40]:
                years = contract_length(p, league, rng)
                ask = int(asking_salary(p, league, years) * ask_discount)
                if need <= 0 and _upgrade_margin(league, tid, values[p.id]) < 5:
                    continue
                salary, how = best_offer(league, tid, ask, season, tid not in mle_used)
                if salary is None:
                    continue
                offers.setdefault(p.id, []).append((tid, salary, how))
                break                                  # one bid per team per round
        for p in pool:
            bids = offers.get(p.id)
            if not bids:
                continue
            years = contract_length(p, league, rng)
            ask = int(asking_salary(p, league, years) * ask_discount)

            def appeal(bid):
                tid, salary, _ = bid
                return salary * (1 + 0.006 * (team_strength(league, tid) - 100) * p.hidden.ambition / 10)

            tid, salary, how = max(bids, key=appeal)
            if salary < ask * 0.85 or len(league.roster(tid)) >= league.rules.max_roster:
                continue
            # The cap may have moved since the bid: re-check.
            salary2, how2 = best_offer(league, tid, salary, season, tid not in mle_used)
            if salary2 is None:
                continue
            if how2 == "mid-level exception":
                mle_used.add(tid)
            p.jersey_number = _choose_jersey(league, tid, rng)
            league.sign(p.id, tid, Contract(salary2, season + years - 1))
            log.append(f"{league.teams[tid].abbreviation} sign {p.name} "
                       f"(CA {p.current_ability}, {p.age(league.current_date)}): {years}y ${salary2 / 1e6:.1f}M via {how2}")
        ask_discount *= 0.9                            # unsigned players lower their sights
    log += fill_rosters(league, rng, season)
    return log


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


def trim_rosters(league: League) -> list[str]:
    """Teams over the maximum waive their least valuable players (cheapest first on ties)."""
    log = []
    for tid in league.teams:
        roster = league.roster(tid)
        excess = len(roster) - league.rules.max_roster
        if excess <= 0:
            continue
        keep_score = sorted(roster, key=lambda p: (player_value(p, league, 3),
                                                   p.contract.salary if p.contract else 0))
        for p in keep_score[:excess]:
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
    team_ids = list(league.teams)
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
