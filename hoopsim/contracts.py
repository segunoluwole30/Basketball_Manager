"""Player value, salary asks, options and re-signing.

Simplified NBA-style soft cap:
* under the cap, a team can sign anyone it can fit;
* over the cap it gets one mid-level exception per offseason plus minimum deals;
* it can always re-sign its own players ("Bird rights") and sign draft picks.
"""
from __future__ import annotations

import random
from typing import Optional

from .development import growth_at
from .generate import market_salary
from .models import Contract, ContractOption, League, Player

CA_PER_ATTRIBUTE_POINT = 10.5


def projected_ability(p: Player, age: int, years_ahead: int) -> float:
    """Expected CA ``years_ahead`` summers from now (ignores luck)."""
    ca = float(p.current_ability)
    for y in range(years_ahead):
        g = growth_at(age + y) * CA_PER_ATTRIBUTE_POINT
        if g > 0:
            g *= max(0.0, min(1.0, (p.potential_ability - ca) / 25))
        ca = min(float(p.potential_ability), ca + g) if g > 0 else ca + g
    return ca


def player_value(p: Player, league: League, horizon: int = 1) -> float:
    """Average expected ability over the next ``horizon`` seasons (this one included)."""
    age = p.age(league.current_date)
    return sum(projected_ability(p, age, y) for y in range(horizon)) / horizon


def contract_length(p: Player, league: League, rng: random.Random) -> int:
    age = p.age(league.current_date)
    if age >= 33:
        return 1
    if age >= 30:
        return rng.randint(1, 2)
    return rng.randint(2, 4)


def asking_salary(p: Player, league: League, years: int) -> int:
    """What the player wants per season over a ``years``-year deal."""
    ask = market_salary(player_value(p, league, years), league.rules)
    ask *= 1 + (p.hidden.ambition - 10) * 0.01        # ambitious players ask more
    return max(league.rules.min_salary, min(int(ask), max_contract(league)))


def max_contract(league: League) -> int:
    return int(league.rules.salary_cap * league.rules.max_salary_cap_fraction)


def payroll_for(league: League, team_id: int, season: int) -> int:
    """Committed salary for ``season``."""
    return sum(p.contract.salary for p in league.roster(team_id)
               if p.contract and p.contract.expires_season >= season)


def team_strength(league: League, team_id: int) -> float:
    top = sorted((p.current_ability for p in league.roster(team_id)), reverse=True)[:8]
    return sum(top) / max(1, len(top))


# --------------------------------------------------------------------------
# Options
# --------------------------------------------------------------------------
def resolve_options(league: League, season: int) -> list[str]:
    """Decide options on contracts whose final (option) year is ``season``."""
    log = []
    for p in list(league.players.values()):
        c = p.contract
        if not c or p.team_id is None or c.option is None or c.expires_season != season:
            continue
        fair = asking_salary(p, league, 1)
        team = league.teams[p.team_id].abbreviation
        if c.option is ContractOption.TEAM and c.salary > fair * 1.25:
            log.append(f"{team} decline team option on {p.name}")
            league.release(p.id)
        elif c.option is ContractOption.PLAYER and fair > c.salary * 1.15:
            log.append(f"{p.name} opts out of {team} deal")
            league.release(p.id)
        else:
            c.option = None                            # option exercised: final year is guaranteed
    return log


# --------------------------------------------------------------------------
# Re-signing
# --------------------------------------------------------------------------
def willing_to_resign(p: Player, league: League, rng: random.Random) -> bool:
    h = p.hidden
    win = team_strength(league, p.team_id) - 105       # roughly: is this a good team?
    chance = (0.55 + 0.02 * (h.loyalty - 10) + 0.008 * (p.morale - 50)
              + 0.01 * win * (h.ambition / 10))
    return rng.random() < max(0.05, min(0.95, chance))


def team_wants_to_keep(p: Player, league: League, salary: int, season: int) -> bool:
    """Keep if he's a rotation-level player for us and we can live with the bill."""
    roster = league.roster(p.team_id)
    values = sorted((player_value(q, league, 2) for q in roster if q.id != p.id), reverse=True)
    mine = player_value(p, league, 2)
    rotation_cut = values[8] if len(values) > 8 else 0
    if mine < rotation_cut:
        return False
    payroll = payroll_for(league, p.team_id, season) + salary
    # Contenders will pay some tax; everyone else stays under the line.
    allowance = league.rules.luxury_tax_line * (1.08 if team_strength(league, p.team_id) > 112 else 1.0)
    return payroll <= allowance


def resign_or_release(league: League, season: int, rng: random.Random) -> tuple[list[str], list[int]]:
    """For contracts that ended before ``season``: re-sign or let walk.

    Returns (log lines, ids of players who became free agents).
    """
    log, walked = [], []
    for p in sorted(league.players.values(), key=lambda q: -q.current_ability):
        c = p.contract
        if p.team_id is None or not c or c.expires_season >= season:
            continue
        team = league.teams[p.team_id].abbreviation
        years = contract_length(p, league, rng)
        salary = asking_salary(p, league, years)
        if team_wants_to_keep(p, league, salary, season) and willing_to_resign(p, league, rng):
            p.contract = Contract(salary, season + years - 1)
            log.append(f"{team} re-sign {p.name}: {years}y ${salary / 1e6:.1f}M")
        else:
            league.release(p.id)
            walked.append(p.id)
    return log, walked


def rookie_contract(pick: int, league: League, season: int) -> Contract:
    """Rookie scale: #1 pick earns ~7% of the cap, sliding down; second round gets the minimum."""
    rules = league.rules
    first_round = len(league.teams)
    if pick <= first_round:
        salary = max(rules.min_salary, int(round(rules.salary_cap * 0.07 * 0.9 ** (pick - 1), -4)))
        return Contract(salary, season + 3, ContractOption.TEAM, rookie_scale=True)
    return Contract(rules.min_salary, season + 1, rookie_scale=True)


def cap_room(league: League, team_id: int, season: int) -> int:
    return league.rules.salary_cap - payroll_for(league, team_id, season)


def best_offer(league: League, team_id: int, ask: int, season: int,
               mle_available: bool) -> tuple[Optional[int], str]:
    """The most a team can pay a free agent asking ``ask``: (salary or None, how)."""
    rules = league.rules
    room = cap_room(league, team_id, season)
    if room >= ask:
        return ask, "cap space"
    if room > rules.min_salary and room >= ask * 0.8:
        return room, "cap space"
    if mle_available and ask <= rules.mid_level_exception * 1.1:
        return min(ask, rules.mid_level_exception), "mid-level exception"
    if ask <= rules.min_salary * 1.5:
        return rules.min_salary, "minimum"
    return None, ""
