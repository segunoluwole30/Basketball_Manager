"""Playoff seeding and best-of-N series."""
from __future__ import annotations

import random
from typing import Optional

from .engine import play_day
from .models import Game, League, Phase, Series

_BRACKET = {2: [1, 2], 4: [1, 4, 2, 3], 8: [1, 8, 4, 5, 2, 7, 3, 6], 16: [
    1, 16, 8, 9, 4, 13, 5, 12, 2, 15, 7, 10, 3, 14, 6, 11]}
_HIGH_SEED_HOME = {0, 1, 4, 6}   # 2-2-1-1-1 format


def teams_per_conference(conference_size: int) -> int:
    """Largest power of two that is at most two thirds of the conference."""
    k = 2
    while k * 2 <= max(2, conference_size * 2 // 3) and k * 2 <= 16:
        k *= 2
    return k


def team_records(league: League) -> dict[int, tuple[int, int, int]]:
    """{team id: (wins, losses, point differential)} for the regular season."""
    rec = {tid: [0, 0, 0] for tid in league.teams}
    for g in league.games.values():
        if g.season != league.season or g.playoff or not g.played:
            continue
        d = g.result.home_score - g.result.away_score
        win, lose = (g.home_team_id, g.away_team_id) if d > 0 else (g.away_team_id, g.home_team_id)
        rec[win][0] += 1
        rec[lose][1] += 1
        rec[g.home_team_id][2] += d
        rec[g.away_team_id][2] -= d
    return {tid: tuple(v) for tid, v in rec.items()}


def ranked(league: League, team_ids) -> list[int]:
    """Best record first; ties broken by point differential, then team id."""
    rec = team_records(league)
    return sorted(team_ids, key=lambda t: (-rec[t][0] / max(1, rec[t][0] + rec[t][1]),
                                           -rec[t][2], t))


def start_playoffs(league: League) -> None:
    if league.phase is not Phase.REGULAR_SEASON:
        raise ValueError(f"can't start playoffs during {league.phase.value}")
    if any(not g.played for g in league.games.values() if g.season == league.season and not g.playoff):
        raise ValueError("regular season isn't finished")
    league.playoffs = []
    conferences = sorted({t.conference for t in league.teams.values()})
    for conf in conferences:
        members = [t.id for t in league.teams.values() if t.conference == conf]
        k = teams_per_conference(len(members))
        seeds = ranked(league, members)[:k]
        order = _BRACKET[k]
        for i in range(0, k, 2):
            hi, lo = order[i], order[i + 1]
            _add_series(league, 1, conf, seeds[hi - 1], seeds[lo - 1], hi, lo)
    league.phase = Phase.PLAYOFFS


def _add_series(league: League, rnd: int, conf: Optional[str], a: int, b: int,
                seed_a: int, seed_b: int) -> None:
    if (seed_a, a) > (seed_b, b):    # keep the better seed as "high"
        a, b, seed_a, seed_b = b, a, seed_b, seed_a
    league.playoffs.append(Series(len(league.playoffs) + 1, rnd, conf, a, b, seed_a, seed_b,
                                  best_of=league.rules.playoff_series_length))


def current_round(league: League) -> int:
    return max(s.round for s in league.playoffs)


def _advance_bracket(league: League) -> bool:
    """Create the next round once the current one is over. False when the finals are done."""
    rnd = current_round(league)
    done = [s for s in league.playoffs if s.round == rnd]
    if not all(s.over for s in done):
        return True
    if len(done) == 1 and done[0].conference is None:
        return False                                  # finals finished
    by_conf: dict[str, list[Series]] = {}
    for s in done:
        by_conf.setdefault(s.conference, []).append(s)
    if all(len(v) == 1 for v in by_conf.values()):
        # Conference champions meet in the finals; better record has home court.
        champs = [v[0] for v in by_conf.values()]
        order = ranked(league, [s.winner_id for s in champs])
        if len(order) == 1:
            return False                              # single-conference league
        _add_series(league, rnd + 1, None, order[0], order[1], 1, 2)
        return True
    for conf, series in by_conf.items():
        series.sort(key=lambda s: s.id)               # bracket order
        for a, b in zip(series[::2], series[1::2]):
            wa, wb = a.winner_id, b.winner_id
            sa = a.high_seed if wa == a.high_id else a.low_seed
            sb = b.high_seed if wb == b.high_id else b.low_seed
            _add_series(league, rnd + 1, conf, wa, wb, sa, sb)
    return True


def play_playoffs(league: League, rng: Optional[random.Random] = None) -> int:
    """Play the whole bracket one day at a time. Returns the champion's team id."""
    if league.phase is not Phase.PLAYOFFS:
        start_playoffs(league)
    day = max(g.day for g in league.games.values() if g.season == league.season)
    while True:
        live = [s for s in league.playoffs if s.round == current_round(league) and not s.over]
        if not live:
            if not _advance_bracket(league):
                break
            continue
        day += 1
        for s in live:
            n = len(s.game_ids)
            home, away = (s.high_id, s.low_id) if n in _HIGH_SEED_HOME else (s.low_id, s.high_id)
            gid = league.next_game_id()
            league.add_game(Game(gid, league.season, day, home, away, playoff=True))
            s.game_ids.append(gid)
        play_day(league, day, rng)
        for s in live:
            g = league.games[s.game_ids[-1]]
            if g.winner_id() == s.high_id:
                s.high_wins += 1
            else:
                s.low_wins += 1
    return champion(league)


def champion(league: League) -> Optional[int]:
    finals = [s for s in league.playoffs if s.round == current_round(league)]
    return finals[0].winner_id if len(finals) == 1 else None
