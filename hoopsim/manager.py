"""Managing your own team.

Two halves:

* **Decisions the season asks you for** (``Manager``): team options, re-signing,
  draft picks, free-agent bids, trade offers, waivers. ``AIManager`` answers
  them the way the AI would, so an assistant can run your team on autopilot.
  The interactive CLI subclasses it and asks you instead.
* **Actions you can take any time** (functions below): lineup, tactics,
  signings, releases, trade proposals and scouting. Each checks the rules and
  returns ``(ok, message)``.
"""
from __future__ import annotations

import random
from typing import Optional

from .contracts import (
    asking_salary, cap_room, max_contract, payment_route, player_value, team_strength,
    team_wants_to_keep,
)
from .lineup import POSITIONS, auto_lineup
from .market import (
    _choose_jersey, _trade_gain, _upgrade_margin, evaluate_trade, execute_user_trade,
)
from .models import (
    ATTR_MAX, ATTR_MIN, Contract, DefensiveScheme, League, OffensiveFocus, Phase, Player,
)


# --------------------------------------------------------------------------
# Decisions
# --------------------------------------------------------------------------
class AIManager:
    """Makes the user team's decisions like an AI front office would (autopilot)."""

    def exercise_team_option(self, league: League, player: Player, fair_salary: int) -> bool:
        return player.contract.salary <= fair_salary * 1.25

    def resign(self, league: League, expiring: list[tuple[Player, int, int]]) -> dict[int, tuple[int, int]]:
        """``expiring`` is [(player, asking salary, preferred years)]. Return {id: (salary, years)}."""
        return {p.id: (ask, years) for p, ask, years in expiring
                if team_wants_to_keep(p, league, ask, league.season)}

    def draft_pick(self, league: League, pick: int, available: list[Player]) -> Player:
        return max(available, key=lambda p: 0.35 * p.current_ability + 0.65 * scouted_potential(league, p)[0])

    def free_agency(self, league: League, pool: list[Player], terms: dict[int, tuple[int, int]],
                    round_no: int) -> list[tuple[int, int, int]]:
        """Return offers as [(player id, salary, years)]."""
        tid = league.user_team_id
        rules = league.rules
        need = 14 - len(league.roster(tid))
        budget = cap_room(league, tid, league.season)
        mle = tid not in league.mle_used
        # Weaker teams must outbid stronger ones to land free agents.
        weak = team_strength(league, tid) < sorted(team_strength(league, t) for t in league.teams)[len(league.teams) // 2]
        premium = 1.15 if weak else 1.05
        offers = []
        for p in pool[:40]:
            if len(offers) >= max(1, need):
                break
            years, ask = terms[p.id]
            if need <= 0 and _upgrade_margin(league, tid, player_value(p, league, 2)) < 5:
                continue
            bid = min(int(ask * premium), max_contract(league))
            if budget >= bid:
                offers.append((p.id, bid, years))
                budget -= bid
            elif mle and ask <= rules.mid_level_exception:
                offers.append((p.id, min(bid, rules.mid_level_exception), years))
                mle = False                            # one MLE target at a time
            elif ask <= rules.min_salary * 1.2 and need > 0:
                offers.append((p.id, rules.min_salary, years))
        return offers

    def respond_to_trade(self, league: League, offer: dict) -> bool:
        user = league.user_team_id
        roster = league.roster(user)
        give = set(offer["you_give"])
        after = [p for p in roster if p.id not in give] + [league.players[i] for i in offer["you_get"]]
        return _trade_gain(league, user, roster, after) > 5

    def choose_waivers(self, league: League, count: int) -> list[int]:
        roster = sorted(league.roster(league.user_team_id),
                        key=lambda p: (player_value(p, league, 3), p.contract.salary if p.contract else 0))
        return [p.id for p in roster[:count]]


# --------------------------------------------------------------------------
# Lineup and tactics
# --------------------------------------------------------------------------
def _user_team(league: League):
    if league.user_team_id is None:
        raise ValueError("no user team: call choose_team first")
    return league.teams[league.user_team_id]


def choose_team(league: League, team_id: int) -> None:
    if team_id not in league.teams:
        raise ValueError(f"unknown team {team_id}")
    league.user_team_id = team_id


def set_starters(league: League, starter_ids: list[int]) -> tuple[bool, str]:
    """Starters in PG, SG, SF, PF, C order. Turns the coach's auto-lineup off."""
    team = _user_team(league)
    roster = {p.id: p for p in league.roster(team.id)}
    if len(starter_ids) != 5 or len(set(starter_ids)) != 5:
        return False, "give exactly 5 different players (PG SG SF PF C)"
    for pid in starter_ids:
        if pid not in roster:
            return False, f"player {pid} isn't on your roster"
        if not roster[pid].is_available:
            return False, f"{roster[pid].name} is injured"
    t = team.tactics
    t.starters = list(starter_ids)
    for pid in starter_ids:                          # starters need minutes
        t.minutes_targets.setdefault(pid, 30)
    _rebalance_minutes(t.minutes_targets, keep=set(starter_ids))
    team.auto_lineup = False
    names = ", ".join(f"{pos.value} {roster[pid].name}" for pos, pid in zip(POSITIONS, starter_ids))
    return True, f"Starting: {names}"


def set_minutes(league: League, minutes: dict[int, int]) -> tuple[bool, str]:
    """Set target minutes for some players (others are scaled to fill 240)."""
    team = _user_team(league)
    roster = {p.id: p for p in league.roster(team.id)}
    for pid, m in minutes.items():
        if pid not in roster:
            return False, f"player {pid} isn't on your roster"
        if not 0 <= m <= 48:
            return False, "minutes must be 0-48"
    if sum(minutes.values()) > 240:
        return False, "that's more than 240 minutes"
    t = team.tactics
    for pid, m in minutes.items():
        if m == 0 and pid in t.starters:
            return False, f"{roster[pid].name} is a starter: change the starters first"
    for pid, m in minutes.items():
        if m == 0:
            t.minutes_targets.pop(pid, None)
        else:
            t.minutes_targets[pid] = m
    _rebalance_minutes(t.minutes_targets, keep=set(minutes))
    team.auto_lineup = False
    return True, "Minutes: " + ", ".join(f"{roster[p].name} {m}" for p, m in
                                          sorted(t.minutes_targets.items(), key=lambda kv: -kv[1]))


def _rebalance_minutes(targets: dict[int, int], keep: set[int]) -> None:
    """Scale the players not in ``keep`` so the total is 240."""
    fixed = sum(m for p, m in targets.items() if p in keep)
    flexible = {p: m for p, m in targets.items() if p not in keep}
    room = max(0, 240 - fixed)
    if flexible:
        scale = room / max(1, sum(flexible.values()))
        for p, m in flexible.items():
            targets[p] = max(1, min(48, round(m * scale)))
    # Fix rounding drift on the flexible players (or everyone if none are flexible).
    adjustable = list(flexible) or list(targets)
    i = 0
    while sum(targets.values()) != 240 and adjustable:
        p = adjustable[i % len(adjustable)]
        step = 1 if sum(targets.values()) < 240 else -1
        if 1 <= targets[p] + step <= 48:
            targets[p] += step
        i += 1
        if i > 10_000:
            break


def set_auto_lineup(league: League, on: bool) -> str:
    team = _user_team(league)
    team.auto_lineup = on
    if on:
        auto_lineup(league, team.id)
        return "Your assistant coach will pick the lineup and minutes."
    return "You pick the lineup and minutes."


TACTIC_KEYS = {
    "pace": "pace", "three": "three_point_rate", "crash": "crash_offensive_glass",
    "depth": "rotation_depth", "focus": "offensive_focus", "defense": "defensive_scheme",
}


def set_tactics(league: League, **changes) -> tuple[bool, str]:
    """pace/three/crash 1-20, depth 5-15, focus inside|balanced|perimeter,
    defense man|switch|zone|press."""
    t = _user_team(league).tactics
    for key, value in changes.items():
        attr = TACTIC_KEYS.get(key)
        if attr is None:
            return False, f"unknown tactic '{key}' (try: {', '.join(TACTIC_KEYS)})"
        try:
            if attr == "offensive_focus":
                value = OffensiveFocus(str(value))
            elif attr == "defensive_scheme":
                value = DefensiveScheme(str(value))
            else:
                value = int(value)
                lo, hi = (5, 15) if attr == "rotation_depth" else (ATTR_MIN, ATTR_MAX)
                if not lo <= value <= hi:
                    return False, f"{key} must be {lo}-{hi}"
        except ValueError:
            return False, f"bad value for {key}: {value}"
        setattr(t, attr, value)
    return True, describe_tactics(league)


def describe_tactics(league: League) -> str:
    t = _user_team(league).tactics
    return (f"pace {t.pace}, three {t.three_point_rate}, focus {t.offensive_focus.value}, "
            f"defense {t.defensive_scheme.value}, crash {t.crash_offensive_glass}, depth {t.rotation_depth}")


# --------------------------------------------------------------------------
# Roster moves
# --------------------------------------------------------------------------
def free_agent_ask(league: League, player: Player) -> tuple[int, int]:
    """(preferred years, asking salary) for a free agent right now. During the
    season unsigned players are cheaper."""
    years = 1 if league.phase is not Phase.OFFSEASON else 2
    ask = asking_salary(player, league, years)
    if league.phase is not Phase.OFFSEASON:
        ask = max(league.rules.min_salary, int(ask * 0.7))
    return years, ask


def offer_contract(league: League, player_id: int, salary: int, years: int,
                   rng: Optional[random.Random] = None) -> tuple[bool, str]:
    """Sign a free agent now (in season). He takes it if it meets his ask and the
    cap rules allow it."""
    team = _user_team(league)
    p = league.players.get(player_id)
    if p is None or p.retired:
        return False, "no such player"
    if p.team_id is not None:
        return False, f"{p.name} isn't a free agent"
    if len(league.roster(team.id)) >= league.rules.max_roster:
        return False, f"your roster is full ({league.rules.max_roster}): release someone first"
    if not 1 <= years <= 5:
        return False, "contracts are 1-5 years"
    _, ask = free_agent_ask(league, p)
    route = payment_route(league, team.id, salary, league.season, team.id not in league.mle_used)
    if route is None:
        return False, ("you can't pay that: over the cap you can only use the mid-level "
                       f"exception (up to ${league.rules.mid_level_exception / 1e6:.1f}M, once a year) "
                       f"or the minimum (${league.rules.min_salary / 1e6:.2f}M)")
    if salary < ask:
        return False, f"{p.name} wants at least ${ask / 1e6:.2f}M"
    if route == "mid-level exception":
        league.mle_used.append(team.id)
    p.jersey_number = _choose_jersey(league, team.id, rng or random.Random(player_id))
    league.sign(p.id, team.id, Contract(salary, league.season + years - 1))
    if team.auto_lineup:
        auto_lineup(league, team.id)
    return True, f"Signed {p.name}: {years}y ${salary / 1e6:.2f}M via {route}"


def release_player(league: League, player_id: int) -> tuple[bool, str]:
    team = _user_team(league)
    p = league.players.get(player_id)
    if p is None or p.team_id != team.id:
        return False, "he isn't on your team"
    if len(league.roster(team.id)) <= league.rules.min_roster and league.phase is Phase.REGULAR_SEASON:
        return False, f"you need at least {league.rules.min_roster} players: sign someone first"
    league.release(p.id)
    if team.auto_lineup:
        auto_lineup(league, team.id)
    return True, f"Released {p.name} (his salary comes off your books)"


def propose_trade(league: League, give: list[int], team_id: int, get: list[int]) -> tuple[bool, str]:
    ok, reason = evaluate_trade(league, give, team_id, get)
    if not ok:
        return False, reason
    execute_user_trade(league, give, team_id, get)
    names_out = ", ".join(league.players[i].name for i in give) or "nothing"
    names_in = ", ".join(league.players[i].name for i in get) or "nothing"
    return True, f"Done! You send {names_out} to {league.teams[team_id].abbreviation} for {names_in}"


# --------------------------------------------------------------------------
# Scouting: you never see potential or hidden attributes exactly
# --------------------------------------------------------------------------
def scouting_error(league: League, player: Player) -> float:
    """How unsure your scouts are about his potential (wider for young unknowns).
    Your own players are known better."""
    seasons = sum(1 for c in player.career if not c["playoffs"])
    age = player.age(league.current_date)
    # Track record and age both reveal a player: by 30 there are few surprises left.
    error = max(3.0, 18.0 - 4.0 * seasons - 1.5 * max(0, age - 21))
    if player.team_id == league.user_team_id:
        error *= 0.6
    return error


def scouted_potential(league: League, player: Player) -> tuple[int, int, int]:
    """(estimate, low, high). The estimate's error is fixed per player, so it
    doesn't change every time you look."""
    err = scouting_error(league, player)
    bias = random.Random(player.id * 7919 + 17).gauss(0, 1) * err * 0.6
    est = player.potential_ability + bias
    est = max(player.current_ability, est)          # nobody's ceiling is below what he is now
    return round(est), round(max(player.current_ability, est - err)), round(est + err)


def hidden_traits(player: Player) -> list[str]:
    """Hints about hidden attributes, the way a scouting report would put them."""
    h = player.hidden
    notes = []
    for value, high, low in (
        (h.consistency, "consistent", "inconsistent"),
        (h.clutch, "big-game player", "shrinks in big moments"),
        (h.work_ethic, "hard worker", "lazy in practice"),
        (h.professionalism, "model professional", "unprofessional"),
        (h.loyalty, "loyal", "will chase the money"),
        (h.ambition, "hungry to win", "content"),
    ):
        if value >= 15:
            notes.append(high)
        elif value <= 5:
            notes.append(low)
    if h.injury_proneness >= 15:
        notes.append("injury prone")
    elif h.injury_proneness <= 5:
        notes.append("durable")
    return notes
