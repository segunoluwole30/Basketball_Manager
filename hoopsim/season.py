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

from .contracts import resign_or_release, resolve_options
from .development import develop, retirement_chance
from .draft import draft_order, run_draft
from .engine import play_day
from .generate import generate_schedule
from .lineup import auto_lineup, lineup_is_usable
from .market import ai_trades, fill_rosters, run_free_agency, trim_rosters
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
    for tid in league.teams:
        if _needs_new_lineup(league, tid):
            auto_lineup(league, tid)


def play_regular_season(league: League, rng: random.Random,
                        days: Optional[int] = None) -> list[str]:
    """Play the remaining regular season (or just the next ``days`` days).

    Returns notable events (trades at the deadline)."""
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


def play_full_season(league: League, rng: random.Random) -> dict:
    events = play_regular_season(league, rng)
    start_playoffs(league)
    play_playoffs(league, rng)
    summary = finish_season(league)
    summary["deadline_trades"] = events
    league.history[-1]["deadline_trades"] = events
    return summary


# --------------------------------------------------------------------------
# Offseason
# --------------------------------------------------------------------------
def run_offseason(league: League, rng: random.Random) -> dict:
    """Retirements, development, options, re-signing, draft, free agency, trades and a
    new schedule. Returns a report of everything that happened."""
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
    report: dict = {"season": new, "retired": [], "development": {}}

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

    report["options"] = resolve_options(league, new)
    report["resigned"], walked = resign_or_release(league, new, rng)
    report["draft"] = run_draft(league, rng, order, new)
    report["free_agency"] = run_free_agency(league, rng, new)
    report["trades"] = ai_trades(league, rng, attempts=300, max_trades=4)
    report["waived"] = trim_rosters(league)
    report["free_agency"] += fill_rosters(league, rng, new)
    _prune_free_agents(league)

    # Fresh season
    league.games = {gid: g for gid, g in league.games.items() if g.season >= new}
    league.playoffs = []
    league.current_date = season_start(new)
    generate_schedule(league, rng)
    for tid in league.teams:
        auto_lineup(league, tid)
    league.phase = Phase.REGULAR_SEASON
    report["problems"] = league.validate()
    return report


def _prune_free_agents(league: League) -> None:
    """Players nobody signed drift out of the league; keep the pool a sensible size."""
    pool = league.free_agents()
    limit = FREE_AGENT_POOL_PER_TEAM * len(league.teams)
    for p in pool[limit:]:                             # pool is sorted best first
        p.retired = True
