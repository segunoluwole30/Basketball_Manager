"""A frozen copy of the offseason code as it was before it became resumable steps.

Used only by tests to prove the step-by-step offseason makes exactly the same
decisions (same random draws, same results) as the original synchronous one.
"""
from __future__ import annotations

import random
from datetime import date

from hoopsim.contracts import (
    asking_salary, best_offer, contract_length, max_contract, payment_route, resign_chance,
    rookie_contract, team_strength, team_wants_to_keep, willing_to_resign,
)
from hoopsim.contracts import player_value
from hoopsim.development import develop, retirement_chance
from hoopsim.draft import _scouted_value, draft_order, generate_draft_class
from hoopsim.generate import generate_schedule
from hoopsim.lineup import auto_lineup, lineup_is_usable
from hoopsim.market import (
    _choose_jersey, _roster_need, _trade_gain, _upgrade_margin, ai_trades, evaluate_trade,
    execute_user_trade, fill_rosters,
)
from hoopsim.models import Contract, ContractOption, League, Phase
from hoopsim.season import _prune_free_agents, season_start


def resolve_options(league: League, season: int, manager=None) -> list[str]:
    """Decide options on contracts whose final (option) year is ``season``.

    Team options on the user's team are the ``manager``'s call."""
    log = []
    for p in list(league.players.values()):
        c = p.contract
        if not c or p.team_id is None or c.option is None or c.expires_season != season:
            continue
        fair = asking_salary(p, league, 1)
        team = league.teams[p.team_id].abbreviation
        if (c.option is ContractOption.TEAM and manager is not None
                and p.team_id == league.user_team_id):
            if manager.exercise_team_option(league, p, fair):
                c.option = None
                log.append(f"{team} exercise team option on {p.name}")
            else:
                log.append(f"{team} decline team option on {p.name}")
                league.release(p.id)
            continue
        if c.option is ContractOption.TEAM and c.salary > fair * 1.25:
            log.append(f"{team} decline team option on {p.name}")
            league.release(p.id)
        elif c.option is ContractOption.PLAYER and fair > c.salary * 1.15:
            log.append(f"{p.name} opts out of {team} deal")
            league.release(p.id)
        else:
            c.option = None                            # option exercised: final year is guaranteed
    return log


def resign_or_release(league: League, season: int, rng: random.Random,
                      manager=None) -> tuple[list[str], list[int]]:
    """For contracts that ended before ``season``: re-sign or let walk.

    Returns (log lines, ids of players who became free agents).
    """
    log, walked = [], []
    user_expiring = []
    for p in sorted(league.players.values(), key=lambda q: -q.current_ability):
        c = p.contract
        if p.team_id is None or not c or c.expires_season >= season:
            continue
        team = league.teams[p.team_id].abbreviation
        years = contract_length(p, league, rng)
        salary = asking_salary(p, league, years)
        if manager is not None and p.team_id == league.user_team_id:
            user_expiring.append((p, salary, years))
            continue
        if team_wants_to_keep(p, league, salary, season) and willing_to_resign(p, league, rng):
            p.contract = Contract(salary, season + years - 1)
            log.append(f"{team} re-sign {p.name}: {years}y ${salary / 1e6:.1f}M")
        else:
            league.release(p.id)
            walked.append(p.id)
    if user_expiring:
        offers = manager.resign(league, user_expiring)
        for p, ask, _ in user_expiring:
            team = league.teams[p.team_id].abbreviation
            offer = offers.get(p.id)
            if offer is None:
                log.append(f"{team} let {p.name} walk")
            else:
                salary, years = offer
                salary = min(salary, max_contract(league))
                if salary >= ask * 0.9 and rng.random() < resign_chance(p, league, salary, ask):
                    p.contract = Contract(salary, season + max(1, min(5, years)) - 1)
                    log.append(f"{team} re-sign {p.name}: {years}y ${salary / 1e6:.1f}M")
                    continue
                log.append(f"{p.name} turns down {team}'s offer (asked ${ask / 1e6:.1f}M)")
            league.release(p.id)
            walked.append(p.id)
    return log, walked


def run_draft(league: League, rng: random.Random, order: list[int], season: int,
              manager=None) -> list[str]:
    """Draft ``rules.draft_rounds`` rounds; picks get rookie-scale deals for ``season``.

    The user's picks are made by ``manager.draft_pick``."""
    rounds = league.rules.draft_rounds
    prospects = generate_draft_class(league, rng, len(order) * rounds + len(order) // 2)
    board = {i: _scouted_value(p, rng) for i, p in enumerate(prospects)}
    log = []
    pick = 0
    for rnd in range(rounds):
        for team_id in order:
            pick += 1
            if manager is not None and team_id == league.user_team_id:
                available = [prospects[i] for i in board]
                picked = manager.draft_pick(league, pick, available)
                idx = next(i for i in board if prospects[i] is picked)
            else:
                # Every team scouts differently: re-roll a little noise per pick.
                idx = max(board, key=lambda i: board[i] + rng.gauss(0, 4))
            del board[idx]
            choice = prospects[idx]
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
        p.draft_year = season
        league.add_player(p)
    return log


def run_free_agency(league: League, rng: random.Random, season: int, rounds: int = 8,
                    manager=None) -> list[str]:
    """Rounds of offers. Each round every AI team bids on its best affordable target
    and the user (via ``manager``) may bid on several. Each player takes his best
    offer (money first, then team quality) if it meets his falling asking price.
    Then teams fill to the minimum roster."""
    log = []
    user = league.user_team_id if manager is not None else None
    ask_discount = 1.0
    for rnd in range(rounds):
        pool = [p for p in league.free_agents() if not p.injury or p.injury.games_remaining < 20]
        if not pool:
            break
        values = {p.id: player_value(p, league, 2) for p in pool}
        pool.sort(key=lambda p: -values[p.id])
        # Each player's preferred length and asking salary this round.
        terms: dict[int, tuple[int, int]] = {}
        for p in pool:
            years = contract_length(p, league, rng)
            terms[p.id] = (years, int(asking_salary(p, league, years) * ask_discount))
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
        if user is not None and len(league.roster(user)) < league.rules.max_roster:
            for pid, salary, years in manager.free_agency(league, pool, terms, rnd + 1):
                how = payment_route(league, user, salary, season, user not in league.mle_used)
                if pid in terms and how:
                    offers.setdefault(pid, []).append((user, salary, how, max(1, min(5, years))))
        for p in pool:
            bids = offers.get(p.id)
            if not bids:
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
        ask_discount *= 0.9                            # unsigned players lower their sights
    log += fill_rosters(league, rng, season)
    return log


def trim_rosters(league: League, manager=None) -> list[str]:
    """Teams over the maximum waive their least valuable players (cheapest first on ties).
    The user's waivers are the ``manager``'s call."""
    log = []
    for tid in league.teams:
        roster = league.roster(tid)
        excess = len(roster) - league.rules.max_roster
        if excess <= 0:
            continue
        keep_score = sorted(roster, key=lambda p: (player_value(p, league, 3),
                                                   p.contract.salary if p.contract else 0))
        to_waive = keep_score[:excess]
        if manager is not None and tid == league.user_team_id:
            chosen = manager.choose_waivers(league, excess)
            ids = {p.id for p in roster}
            if len(set(chosen)) == excess and set(chosen) <= ids:
                to_waive = [league.players[pid] for pid in chosen]
        for p in to_waive:
            log.append(f"{league.teams[tid].abbreviation} waive {p.name}")
            league.release(p.id)
    return log


def ai_offers_to_user(league: League, rng: random.Random, manager, attempts: int = 150,
                      max_offers: int = 2) -> list[str]:
    """AI teams pitch trades they like and think the user might take."""
    user = league.user_team_id
    if user is None or manager is None:
        return []
    log, offered = [], 0
    others = [t for t in league.teams if t != user]
    for _ in range(attempts):
        if offered >= max_offers:
            break
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
        offered += 1
        offer = {"team_id": b, "you_give": [p.id for p in user_out], "you_get": [p.id for p in b_out]}
        names_out = ", ".join(p.name for p in user_out)
        names_in = ", ".join(p.name for p in b_out)
        if manager.respond_to_trade(league, offer):
            execute_user_trade(league, offer["you_give"], b, offer["you_get"])
            log.append(f"TRADE: you send {names_out} to {league.teams[b].abbreviation} for {names_in}")
        else:
            log.append(f"You turned down {league.teams[b].abbreviation}'s offer of {names_in} for {names_out}")
    return log


def run_offseason(league: League, rng: random.Random, manager=None) -> dict:
    """Retirements, development, options, re-signing, draft, free agency, trades and a
    new schedule. Returns a report of everything that happened.

    Decisions for the user's team go to ``manager`` (see manager.py); without one,
    the AI runs every team."""
    if league.phase is not Phase.OFFSEASON:
        raise ValueError("finish the season first")
    old = league.season
    playoff_teams = {t for s in league.playoffs for t in (s.high_id, s.low_id)}
    order = draft_order(league, rng, playoff_teams)       # uses this season's records
    mpg = {}
    for p in league.players.values():
        line = next((c for c in reversed(p.career) if c["season"] == old and not c["playoffs"]), None)
        mpg[p.id] = line["min"] / max(1, line["gp"]) if line else 0.0

    new = old + 1
    league.season = new
    league.current_date = date(new, 7, 1)
    report: dict = {"season": new, "retired": [], "development": {}, "notes": []}
    league.mle_used = []                                   # new league year

    # Retirements and summer development
    for p in list(league.players.values()):
        if p.retired:
            continue
        age = p.age(league.current_date)
        if rng.random() < retirement_chance(p, age, signed=p.team_id is not None):
            report["retired"].append(f"{p.name} ({age}, CA {p.current_ability})")
            if p.team_id is not None:
                league.release(p.id)
            p.retired = True
            continue
        report["development"][p.id] = develop(p, age, mpg.get(p.id, 0.0), rng)
        if p.injury:                                   # the summer heals most things
            p.injury.games_remaining -= 40
            if p.injury.games_remaining <= 0:
                p.injury = None
        p.morale = 50 + (p.morale - 50) * 0.5

    report["options"] = resolve_options(league, new, manager)
    report["resigned"], walked = resign_or_release(league, new, rng, manager)
    report["draft"] = run_draft(league, rng, order, new, manager)
    report["free_agency"] = run_free_agency(league, rng, new, manager=manager)
    report["trades"] = ai_trades(league, rng, attempts=300, max_trades=4)
    report["trades"] += ai_offers_to_user(league, rng, manager)
    report["waived"] = trim_rosters(league, manager)
    report["free_agency"] += fill_rosters(league, rng, new)
    _prune_free_agents(league)

    # Fresh season
    league.games = {gid: g for gid, g in league.games.items() if g.season >= new}
    league.playoffs = []
    league.current_date = season_start(new)
    generate_schedule(league, rng)
    for tid, team in league.teams.items():
        if team.auto_lineup:
            auto_lineup(league, tid)
        elif not lineup_is_usable(league, tid):
            auto_lineup(league, tid)
            report["notes"].append("Your old lineup no longer works (players left), so your "
                                   "assistant set a new one. Change it any time.")
    league.phase = Phase.REGULAR_SEASON
    report["problems"] = league.validate()
    return report
