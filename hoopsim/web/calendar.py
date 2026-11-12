"""The season calendar for the web app: every day of the season, your games on it,
the NBA's key dates, and "sim to this day"."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from ..models import Phase
from ..schedule import season_calendar
from ..season import regular_season_over, season_start, trade_deadline_day


def _label_days(lg) -> dict[int, list[str]]:
    games = [g for g in lg.games.values() if g.season == lg.season]
    regular = sorted({g.day for g in games if not g.playoff})
    if not regular:
        return {}
    cal = season_calendar(lg.season, lg.rules.games_per_season, last_day=regular[-1])
    base = season_start(lg.season)
    marks: dict[int, list[str]] = {}

    def mark(day, text):
        if day is not None:
            marks.setdefault(day, []).append(text)

    mark(regular[0], "Opening night")
    for d in cal["blackout"]:
        when = base + timedelta(days=d)
        if (when.month, when.day) == (12, 24):
            mark(d, "Christmas Eve: no games")
        elif when.month == 11:
            mark(d, "Thanksgiving: no games")
    mark(cal["christmas"], "Christmas Day")
    for d in cal["all_star"]:
        mark(d, "All-Star Game" if d == cal["all_star_game"] else "All-Star break")
    mark(trade_deadline_day(lg), "Trade deadline")
    from ..allstar import event_days
    ev = event_days(lg)
    for key, label in (("starters", "All-Star starters"), ("reserves", "All-Star reserves"),
                       ("rising", "Rising Stars"), ("three", "Three-Point Contest")):
        if key in ev:
            mark(ev[key], label)
    mark(regular[-1], "Final day of the regular season")
    playoff_days = sorted({g.day for g in games if g.playoff})
    if playoff_days:
        mark(playoff_days[0], "Playoffs begin")
    return marks


def calendar(session, team_id: Optional[int] = None) -> dict:
    lg = session._me()
    tid = lg.user_team_id if team_id is None else int(team_id)
    if tid not in lg.teams:
        from .session import UserError
        raise UserError(f"no team {tid}", 404)
    games = [g for g in lg.games.values() if g.season == lg.season]
    if not games:
        return {"team_id": tid, "days": [], "today": None}
    base = season_start(lg.season)
    by_day: dict[int, list] = {}
    for g in games:
        by_day.setdefault(g.day, []).append(g)
    mine = {g.day: g for g in games if tid in (g.home_team_id, g.away_team_id)}
    marks = _label_days(lg)
    first = min(by_day)
    last = max(max(by_day), *(marks or {0: 0}).keys())
    played = [g.day for g in games if g.played]
    today = max(played) if played else first - 1
    days = []
    for d in range(first - 3, last + 1):
        g = mine.get(d)
        tonight = by_day.get(d, [])
        days.append({
            "day": d, "date": (base + timedelta(days=d)).isoformat(),
            "games": len(tonight), "played": bool(tonight) and all(x.played for x in tonight),
            "playoff": any(x.playoff for x in tonight),
            "game": session._game_view(g) if g else None,
            "b2b": bool(g) and (d - 1) in mine,                   # the second night of a back-to-back
            "marks": marks.get(d, []),
        })
    my_days = sorted(mine)
    gaps = [b - a for a, b in zip(my_days, my_days[1:])]
    return {
        "team_id": tid, "today": today, "today_date": (base + timedelta(days=today)).isoformat(),
        "phase": lg.phase.value, "days": days,
        "summary": {"games": len(my_days), "home": sum(1 for g in mine.values() if g.home_team_id == tid),
                    "back_to_backs": sum(1 for x in gaps if x == 1),
                    "longest_road_trip": _longest_trip(lg, tid)},
    }


def _longest_trip(lg, tid: int) -> int:
    best = run = 0
    for g in sorted((g for g in lg.games.values() if g.season == lg.season and not g.playoff
                     and tid in (g.home_team_id, g.away_team_id)), key=lambda g: g.day):
        run = run + 1 if g.away_team_id == tid else 0
        best = max(best, run)
    return best


def calendar_day(session, day: int) -> dict:
    lg = session._lg()
    games = sorted((g for g in lg.games.values() if g.season == lg.season and g.day == int(day)),
                   key=lambda g: g.id)
    base = season_start(lg.season)
    return {"day": int(day), "date": (base + timedelta(days=int(day))).isoformat(),
            "marks": _label_days(lg).get(int(day), []),
            "games": [session._game_view(g) for g in games]}


def sim_to(session, day: int) -> dict:
    """Play every day up to and including ``day`` (regular season or playoffs)."""
    from .session import UserError
    lg = session._me()
    day = int(day)
    if lg.phase is Phase.REGULAR_SEASON and not regular_season_over(lg):
        days = len({g.day for g in lg.games.values() if g.season == lg.season and not g.playoff
                    and not g.played and g.day <= day})
        if days <= 0:
            raise UserError("that day has already been played")
        return session.sim("days", min(days, 200))
    if lg.phase is Phase.PLAYOFFS:
        r = None
        for _ in range(120):
            last = max((g.day for g in lg.games.values() if g.season == lg.season and g.played and g.playoff),
                       default=-1)
            if last >= day:
                break
            r = session.sim_playoffs("day")
            if r.get("finished"):
                break
        if r is None:
            raise UserError("that day has already been played")
        return r
    raise UserError("there are no games to play right now (start the playoffs first?)")
