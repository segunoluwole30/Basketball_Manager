"""The season loop: regular season -> playoffs -> awards -> offseason -> next season.

    league = generate_league(seed=1)
    for _ in range(10):
        summary = play_full_season(league, rng)   # regular season + playoffs + awards
        report = run_offseason(league, rng)        # development, draft, free agency...
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Optional

from .contracts import (
    resign_ai_phase, resign_chance, resign_user_phase, resolve_options, team_option_questions,
)
from .development import develop, retirement_chance
from .draft import (
    advance_draft, draft_available, draft_on_the_clock, draft_order, start_draft, user_draft_pick,
)
from .engine import play_day
from .generate import generate_schedule
from .lineup import auto_lineup, lineup_is_usable
from .market import (
    advance_free_agency, advance_offers, ai_offers_to_user, ai_trades, answer_free_agency, answer_offer,
    fill_rosters, free_agency_round, start_free_agency, start_offers, trim_rosters, user_excess,
)
from .models import League, Phase
from .playoffs import play_playoffs, start_playoffs, team_records
from .stats import awards, career_line, game_score, season_totals, update_morale

SEASON_START = (10, 20)     # month, day
FREE_AGENT_POOL_PER_TEAM = 3


def season_start(season: int) -> date:
    return date(season, *SEASON_START)


# --------------------------------------------------------------------------
# Regular season
# --------------------------------------------------------------------------
def _needs_new_lineup(league: League, team_id: int) -> bool:
    if not lineup_is_usable(league, team_id):
        return True
    # A healthy player better than someone in the rotation isn't in it
    # (e.g. back from injury): re-plan.
    tactics = league.teams[team_id].tactics
    rotation = [league.players[pid].current_ability for pid in tactics.minutes_targets]
    worst = min(rotation, default=0)
    return any(p.is_available and p.id not in tactics.minutes_targets and p.current_ability > worst
               for p in league.roster(team_id))


def refresh_lineups(league: League) -> None:
    """AI coaches re-plan when needed. A user team with auto-lineup off keeps its
    choices; if they become unusable (injury) the engine covers that game only."""
    for tid, team in league.teams.items():
        if not team.auto_lineup:
            continue
        if _needs_new_lineup(league, tid):
            auto_lineup(league, tid)


def play_regular_season(league: League, rng: random.Random,
                        days: Optional[int] = None, manager=None) -> list[str]:
    """Play the remaining regular season (or just the next ``days`` days).

    Returns notable events (trades at the deadline). ``manager`` answers trade
    offers made to the user's team."""
    if league.phase is not Phase.REGULAR_SEASON:
        raise ValueError(f"not in the regular season ({league.phase.value})")
    all_days = sorted({g.day for g in league.games.values()
                       if g.season == league.season and not g.playoff})
    remaining = [d for d in all_days if any(not g.played for g in league.games.values()
                                             if g.season == league.season and g.day == d)]
    deadline = all_days[int(len(all_days) * 0.6)] if all_days else None
    events = []
    for day in remaining[:days] if days else remaining:
        refresh_lineups(league)
        play_day(league, day, rng)
        league.current_date = season_start(league.season) + timedelta(days=day)
        if day % 7 == 0:
            update_morale(league, season_totals(league))
        if day == deadline:
            events += [f"Deadline {line}" for line in ai_trades(league, rng, attempts=200, max_trades=3)]
            events += [f"Deadline {line}" for line in ai_offers_to_user(league, rng, manager)]
    return events


# --------------------------------------------------------------------------
# End of season
# --------------------------------------------------------------------------
def finish_season(league: League) -> dict:
    """Awards, career stat lines and a history entry. Moves the league to the offseason."""
    if league.phase is not Phase.PLAYOFFS or not league.playoffs:
        raise ValueError("play the playoffs first")
    finals = max(league.playoffs, key=lambda s: s.round)
    if not finals.over:
        raise ValueError("the playoffs aren't finished")
    reg = season_totals(league)
    po = season_totals(league, playoff=True)
    award_ids = awards(league, reg)

    finals_ids = set(finals.game_ids)
    finals_totals: dict[int, dict] = {}
    for gid in finals_ids:
        box = league.games[gid].box_score
        for team_box in (box.home, box.away):
            if team_box.team_id != finals.winner_id:
                continue
            for line in team_box.lines:
                t = finals_totals.setdefault(line.player_id, {k: 0 for k in (
                    "gp", "pts", "fgm", "fga", "fta", "ftm", "orb", "drb", "stl", "ast", "blk", "pf", "tov")})
                if line.seconds > 0:
                    t["gp"] += 1
                    t["pts"] += line.pts
                    for k in ("fgm", "fga", "fta", "ftm", "orb", "drb", "stl", "ast", "blk", "pf", "tov"):
                        t[k] += getattr(line, k)
    finals_mvp = max(finals_totals, key=lambda pid: game_score(finals_totals[pid]), default=None)

    for playoff, totals in ((False, reg), (True, po)):
        for pid, t in totals.items():
            p = league.players[pid]
            line = career_line(t, league.season, playoff, league)
            line["age"], line["ca"] = p.age(league.current_date), p.current_ability
            p.career.append(line)

    rec = team_records(league)
    summary = {
        "season": league.season,
        "champion": finals.winner_id,
        "runner_up": finals.loser_id,
        "finals": f"{finals.high_wins}-{finals.low_wins}" if finals.winner_id == finals.high_id
                  else f"{finals.low_wins}-{finals.high_wins}",
        "awards": {**award_ids, "finals_mvp": finals_mvp},
        "award_names": {k: league.players[v].name if v is not None else None
                        for k, v in {**award_ids, "finals_mvp": finals_mvp}.items()},
        "standings": [[tid, w, l] for tid, (w, l, _) in sorted(rec.items(), key=lambda kv: -kv[1][0])],
        "playoffs": [s.to_dict() for s in league.playoffs],
    }
    league.history.append(summary)
    # Box scores are summarised now; keep only results to keep save files small.
    for g in league.games.values():
        if g.season == league.season:
            g.box_score = None
    league.phase = Phase.OFFSEASON
    return summary


def trade_deadline_day(league: League) -> Optional[int]:
    days = sorted({g.day for g in league.games.values() if g.season == league.season and not g.playoff})
    return days[int(len(days) * 0.6)] if days else None


def regular_season_over(league: League) -> bool:
    return all(g.played for g in league.games.values() if g.season == league.season and not g.playoff)


def play_full_season(league: League, rng: random.Random, manager=None) -> dict:
    events = play_regular_season(league, rng, manager=manager)
    start_playoffs(league)
    play_playoffs(league, rng)
    summary = finish_season(league)
    summary["deadline_trades"] = events
    league.history[-1]["deadline_trades"] = events
    return summary


# --------------------------------------------------------------------------
# Offseason, as resumable steps
# --------------------------------------------------------------------------
# The offseason is a fixed sequence of steps. Steps that need a decision from
# the user's team pause and leave a question in ``league.offseason["pending"]``;
# ``answer_offseason`` takes the answer and carries on to the next question.
# The whole state is a plain JSON-able dict on the League (so a save file can
# hold a half-finished offseason), including the random generator's state at
# the pause, so the AI makes exactly the same moves however the offseason is
# driven: all at once by ``run_offseason`` with a Manager, or a step at a time
# by a web client.
#
#   start -> options -> resign -> draft (pick by pick) -> free_agency (round by
#   round) -> trades (AI trades, then offers to you one by one) -> cuts -> done
OFFSEASON_STEPS = ["options", "resign", "draft", "free_agency", "trades", "cuts"]
STEP_TITLES = {
    "options": "Team options", "resign": "Re-signing", "draft": "Draft",
    "free_agency": "Free agency", "trades": "Trade offers", "cuts": "Roster cuts",
}


def start_offseason(league: League, rng: random.Random, user_controlled: bool = True) -> Optional[dict]:
    """Retirements and development, then on to the first decision. Returns the
    pending question (None if the whole offseason ran without one: it's over).

    With ``user_controlled`` False the AI runs the user's team too."""
    if league.phase is not Phase.OFFSEASON:
        raise ValueError("finish the season first")
    if league.offseason is not None:
        raise ValueError("the offseason is already under way")
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

    league.offseason = {
        "season": new,
        "user": bool(user_controlled and league.user_team_id is not None),
        "step": "options",
        "order": order,
        "report": report,
        "pending": None,
        "rng": None,
    }
    return _advance(league, rng)


def offseason_pending(league: League) -> Optional[dict]:
    """The question waiting for the user, or None. A dict with ``kind`` (the step),
    ``title``, ``prompt`` and the step's data (ids; see ``pending_players``)."""
    return league.offseason["pending"] if league.offseason else None


def offseason_report(league: League) -> Optional[dict]:
    """What has happened so far this offseason (same shape as run_offseason's report)."""
    return league.offseason["report"] if league.offseason else None


def answer_offseason(league: League, rng: random.Random, answer) -> Optional[dict]:
    """Answer the pending question and run on to the next one.

    Answers by kind: options {id: exercise?}; resign {id: (salary, years)};
    draft a prospect id; free_agency [(id, salary, years)]; trade_offer accept?;
    cuts [ids to waive]. Returns the next question, or None when the offseason
    is over (``league.phase`` is then the regular season and the report is in
    ``league.last_offseason``)."""
    state = league.offseason
    if state is None or state["pending"] is None:
        raise ValueError("no offseason decision is waiting")
    if state["rng"] is not None:                          # pick up where the pause left off
        rng.setstate(_rng_state(state["rng"]))
    kind = state["pending"]["kind"]
    report, new = state["report"], state["season"]
    if kind == "options":
        report["options"] = resolve_options(league, new, decisions={int(k): bool(v) for k, v in answer.items()})
        state["step"] = "resign"
    elif kind == "resign":
        r = state["resign"]
        offers = {int(k): (int(v[0]), int(v[1])) for k, v in answer.items()}
        log, walked = resign_user_phase(league, new, rng, [tuple(e) for e in r["expiring"]], offers)
        report["resigned"] = r["log"] + log
        del state["resign"]
        state["step"] = "draft"
    elif kind == "draft":
        user_draft_pick(league, rng, state["draft"], int(answer))
    elif kind == "free_agency":
        answer_free_agency(league, rng, state["fa"], league.user_team_id,
                           [(int(a), int(b), int(c)) for a, b, c in answer])
    elif kind == "trade_offer":
        answer_offer(league, state["offers"], bool(answer))
    elif kind == "cuts":
        report["waived"] = trim_rosters(league, user_waivers=[int(x) for x in answer])
        state["step"] = "finish"
    else:                                                  # pragma: no cover
        raise ValueError(f"unknown decision {kind}")
    state["pending"] = None
    return _advance(league, rng)


def _rng_state(saved) -> tuple:
    version, internal, gauss = saved
    return version, tuple(internal), gauss


def _pause(league: League, rng: random.Random, pending: dict) -> dict:
    state = league.offseason
    version, internal, gauss = rng.getstate()
    state["rng"] = [version, list(internal), gauss]
    pending.setdefault("title", STEP_TITLES.get(pending["kind"], pending["kind"]))
    state["pending"] = pending
    return pending


def _advance(league: League, rng: random.Random) -> Optional[dict]:
    """Run steps until one needs the user (returns its question) or the offseason ends."""
    state = league.offseason
    report, new, user = state["report"], state["season"], state["user"]
    user_id = league.user_team_id if user else None
    while True:
        step = state["step"]
        if step == "options":
            questions = team_option_questions(league, new) if user else []
            if questions:
                return _pause(league, rng, {
                    "kind": "options",
                    "prompt": "These players have team options for next season. Exercise or decline each.",
                    "options": [{"player_id": p.id, "salary": p.contract.salary, "fair_salary": fair}
                                for p, fair in questions],
                })
            report["options"] = resolve_options(league, new)
            state["step"] = "resign"
        elif step == "resign":
            log, _, expiring = resign_ai_phase(league, new, rng, user)
            if expiring:
                state["resign"] = {"log": log, "expiring": [list(e) for e in expiring]}
                return _pause(league, rng, {
                    "kind": "resign",
                    "prompt": "These contracts have ended. Make offers or let them walk. "
                              "Paying over the ask makes a player likelier to stay; "
                              "below 90% of it he says no.",
                    "expiring": [{"player_id": pid, "ask": ask, "years": years,
                                  "chance": round(resign_chance(league.players[pid], league), 2)}
                                 for pid, ask, years in expiring],
                })
            report["resigned"] = log
            state["step"] = "draft"
        elif step == "draft":
            if "draft" not in state:
                state["draft"] = start_draft(league, rng, state["order"], new)
            d = state["draft"]
            if advance_draft(league, rng, d, user_id):
                pick, _ = draft_on_the_clock(d)
                return _pause(league, rng, {
                    "kind": "draft",
                    "prompt": f"You're on the clock with pick #{pick}.",
                    "pick": pick,
                    "round": (pick - 1) // len(d["order"]) + 1,
                    "available": [p.id for p in draft_available(d)],
                    "log": list(d["log"]),
                })
            report["draft"] = d["log"]
            del state["draft"]
            state["step"] = "free_agency"
        elif step == "free_agency":
            if "fa" not in state:
                state["fa"] = start_free_agency(new)
            fa = state["fa"]
            if advance_free_agency(league, rng, fa, user_id):
                pool, terms = free_agency_round(fa)
                return _pause(league, rng, {
                    "kind": "free_agency",
                    "prompt": f"Free agency round {fa['round'] + 1} of {fa['rounds']}. "
                              "Bid on as many players as you like; each takes his best offer "
                              "(money first, then team quality) if it's near his ask.",
                    "round": fa["round"] + 1,
                    "rounds": fa["rounds"],
                    "pool": [{"player_id": pid, "years": terms[pid][0], "ask": terms[pid][1]}
                             for pid in pool],
                    "log": list(fa["log"]),
                })
            report["free_agency"] = fa["log"]
            del state["fa"]
            state["step"] = "trades"
        elif step == "trades":
            if "offers" not in state:
                report["trades"] = ai_trades(league, rng, attempts=300, max_trades=4)
                state["offers"] = start_offers()
            offers = state["offers"]
            if user and advance_offers(league, rng, offers):
                return _pause(league, rng, {
                    "kind": "trade_offer",
                    "prompt": f"Trade offer from the {league.teams[offers['offer']['team_id']].full_name}.",
                    "offer": dict(offers["offer"]),
                })
            report["trades"] += offers["log"]
            del state["offers"]
            state["step"] = "cuts"
        elif step == "cuts":
            excess = user_excess(league) if user else 0
            if excess:
                return _pause(league, rng, {
                    "kind": "cuts",
                    "prompt": f"You have {excess} too many players "
                              f"(maximum {league.rules.max_roster}). Choose who to waive.",
                    "count": excess,
                })
            report["waived"] = trim_rosters(league)
            state["step"] = "finish"
        elif step == "finish":
            _finish_offseason(league, rng)
            return None
        else:                                              # pragma: no cover
            raise ValueError(f"unknown offseason step {step}")


def _finish_offseason(league: League, rng: random.Random) -> None:
    report, new = league.offseason["report"], league.offseason["season"]
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
    league.offseason = None
    league.last_offseason = report


def pending_players(league: League) -> dict:
    """Every player the pending question mentions, by id, including draft
    prospects (who aren't league players until they're picked)."""
    state = league.offseason
    out = {}
    if state and "draft" in state:
        for p in draft_available(state["draft"]):
            out[p.id] = p
    return out


def manager_answer(league: League, manager, pending: dict):
    """Ask a Manager (AIManager, the terminal's InteractiveManager...) the pending
    question and return its answer in ``answer_offseason``'s format."""
    kind = pending["kind"]
    players = league.players
    if kind == "options":
        return {o["player_id"]: bool(manager.exercise_team_option(league, players[o["player_id"]], o["fair_salary"]))
                for o in pending["options"]}
    if kind == "resign":
        expiring = [(players[e["player_id"]], e["ask"], e["years"]) for e in pending["expiring"]]
        return manager.resign(league, expiring)
    if kind == "draft":
        available = draft_available(league.offseason["draft"])
        return manager.draft_pick(league, pending["pick"], available).id
    if kind == "free_agency":
        pool = [players[e["player_id"]] for e in pending["pool"]]
        terms = {e["player_id"]: (e["years"], e["ask"]) for e in pending["pool"]}
        return manager.free_agency(league, pool, terms, pending["round"])
    if kind == "trade_offer":
        return bool(manager.respond_to_trade(league, dict(pending["offer"])))
    if kind == "cuts":
        return manager.choose_waivers(league, pending["count"])
    raise ValueError(f"unknown decision {kind}")


def run_offseason(league: League, rng: random.Random, manager=None) -> dict:
    """Retirements, development, options, re-signing, draft, free agency, trades and a
    new schedule. Returns a report of everything that happened.

    Decisions for the user's team go to ``manager`` (see manager.py); without one,
    the AI runs every team. (This drives the same steps a web client does.)"""
    pending = start_offseason(league, rng, user_controlled=manager is not None)
    while pending is not None:
        pending = answer_offseason(league, rng, manager_answer(league, manager, pending))
    return league.last_offseason


def _prune_free_agents(league: League) -> None:
    """Players nobody signed drift out of the league; keep the pool a sensible size."""
    pool = league.free_agents()
    limit = FREE_AGENT_POOL_PER_TEAM * len(league.teams)
    for p in pool[limit:]:                             # pool is sorted best first
        p.retired = True
