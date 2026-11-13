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


def _pct(w: int, l: int) -> float:
    return w / (w + l) if w + l else 0.0


class _Tiebreak:
    """The NBA's tie-break procedure for teams with the same winning percentage.

    Two teams: (1) head-to-head, (2) division winner, (3) division record (same
    division only), (4) conference record, (5) point differential.
    Three or more: (1) division winner, (2) head-to-head among all the tied teams,
    (3) division record (all in one division), (4) conference record, (5) point
    differential. Once a step splits the teams into groups, each group goes back
    through the procedure from the top. The NBA's records against playoff teams are
    left out; point differential (then team id) settles anything left."""

    def __init__(self, league: League):
        self.lg = league
        self.rec = team_records(league)
        self.h2h: dict[tuple[int, int], list[int]] = {}         # (a, b) -> [a's wins, games]
        self.div = {t: [0, 0] for t in league.teams}
        self.conf = {t: [0, 0] for t in league.teams}
        T = league.teams
        for g in league.games.values():
            if g.season != league.season or g.playoff or not g.played:
                continue
            h, a = g.home_team_id, g.away_team_id
            w = g.winner_id()
            for x, y in ((h, a), (a, h)):
                r = self.h2h.setdefault((x, y), [0, 0])
                r[0] += w == x
                r[1] += 1
                if T[x].conference == T[y].conference:
                    self.conf[x][0] += w == x
                    self.conf[x][1] += w != x
                    if T[x].division == T[y].division:
                        self.div[x][0] += w == x
                        self.div[x][1] += w != x
        self.leaders: set[int] = set()
        for name in {t.division for t in T.values()}:
            members = [t for t in T if T[t].division == name]
            self.leaders.add(self.order(members)[0])

    def overall(self, t: int) -> float:
        w, l, _ = self.rec[t]
        return _pct(w, l)

    def order(self, teams) -> list[int]:
        out = []
        for _, grp in self._groups(list(teams), self.overall):
            out += self.settle(grp)
        return out

    @staticmethod
    def _groups(teams, key):
        vals = {t: key(t) for t in teams}
        levels = sorted(set(vals.values()), reverse=True)
        return [(v, [t for t in teams if vals[t] == v]) for v in levels]

    def settle(self, tied: list[int]) -> list[int]:
        if len(tied) <= 1:
            return tied
        T = self.lg.teams
        same_div = len({T[t].division for t in tied}) == 1

        def h2h(t):
            w = g = 0
            for o in tied:
                if o != t:
                    r = self.h2h.get((t, o), [0, 0])
                    w += r[0]
                    g += r[1]
            return w / g if g else 0.0

        leader = lambda t: 1.0 if t in self.leaders else 0.0
        division = lambda t: _pct(*self.div[t])
        conference = lambda t: _pct(*self.conf[t])
        diff = lambda t: float(self.rec[t][2])
        if len(tied) == 2:
            steps = [h2h] + ([] if same_div else [leader]) + ([division] if same_div else []) + [conference, diff]
        else:
            steps = ([] if same_div else [leader]) + [h2h] + ([division] if same_div else []) + [conference, diff]
        for step in steps:
            groups = self._groups(tied, step)
            if len(groups) > 1:
                out = []
                for _, grp in groups:
                    out += self.settle(grp)
                return out
        return sorted(tied)


def ranked(league: League, team_ids) -> list[int]:
    """Best record first, ties broken by the NBA's procedure (see ``_Tiebreak``)."""
    return _Tiebreak(league).order(team_ids)


def division_leaders(league: League) -> set[int]:
    """Each division's winner (after tie-breaks)."""
    return _Tiebreak(league).leaders


def has_play_in(league: League, conference_size: int) -> bool:
    """The NBA's play-in: an eight-team bracket in a conference of ten or more teams
    (``rules.play_in`` turns it off). Seeds 7-10 play for the last two places."""
    k = teams_per_conference(conference_size)
    return bool(getattr(league.rules, "play_in", True)) and k >= 8 and conference_size >= k + 2


def seeding(league: League) -> dict[str, list[int]]:
    """{conference: team ids in seed order} from the regular-season standings."""
    out = {}
    for conf in sorted({t.conference for t in league.teams.values()}):
        out[conf] = ranked(league, [t.id for t in league.teams.values() if t.conference == conf])
    return out


def start_playoffs(league: League) -> None:
    if league.phase is not Phase.REGULAR_SEASON:
        raise ValueError(f"can't start playoffs during {league.phase.value}")
    if any(not g.played for g in league.games.values() if g.season == league.season and not g.playoff):
        raise ValueError("regular season isn't finished")
    from .awards import ensure
    ensure(league)                       # the season's awards come before the playoffs
    league.playoffs = []
    seeds = seeding(league)
    play_in = [c for c, order in seeds.items() if has_play_in(league, len(order))]
    for conf, order in seeds.items():
        k = teams_per_conference(len(order))
        if conf in play_in:
            # Play-in: 7 v 8 (the winner is the 7 seed) and 9 v 10 (the loser is out);
            # round one follows once the 8 seed is decided.
            _add_series(league, 0, conf, order[k - 2], order[k - 1], k - 1, k, best_of=1, play_in="7v8")
            _add_series(league, 0, conf, order[k], order[k + 1], k + 1, k + 2, best_of=1, play_in="9v10")
        elif not play_in:
            _add_first_round(league, conf, order[:k])
    league.phase = Phase.PLAYOFFS


def _add_first_round(league: League, conf: str, seeds: list[int]) -> None:
    order = _BRACKET[len(seeds)]
    for i in range(0, len(seeds), 2):
        hi, lo = order[i], order[i + 1]
        _add_series(league, 1, conf, seeds[hi - 1], seeds[lo - 1], hi, lo)


def _add_series(league: League, rnd: int, conf: Optional[str], a: int, b: int,
                seed_a: int, seed_b: int, best_of: Optional[int] = None, play_in: str = "") -> None:
    if (seed_a, a) > (seed_b, b):    # keep the better seed as "high"
        a, b, seed_a, seed_b = b, a, seed_b, seed_a
    league.playoffs.append(Series(len(league.playoffs) + 1, rnd, conf, a, b, seed_a, seed_b,
                                  best_of=best_of or league.rules.playoff_series_length, play_in=play_in))


def _seed_of(s: Series, team_id: int) -> int:
    return s.high_seed if team_id == s.high_id else s.low_seed


def _advance_play_in(league: League, done: list[Series]) -> None:
    """After the play-in's first night: the deciders. After those: round one."""
    by_conf: dict[str, dict[str, Series]] = {}
    for s in done:
        by_conf.setdefault(s.conference, {})[s.play_in] = s
    if any("decider" not in v for v in by_conf.values()):
        for conf, v in sorted(by_conf.items()):
            a, b = v["7v8"], v["9v10"]
            _add_series(league, 0, conf, a.loser_id, b.winner_id, _seed_of(a, a.loser_id),
                        _seed_of(b, b.winner_id), best_of=1, play_in="decider")
        return
    for conf, order in seeding(league).items():
        k = teams_per_conference(len(order))
        v = by_conf.get(conf)
        seeds = order[:k]
        if v:
            seeds[k - 2], seeds[k - 1] = v["7v8"].winner_id, v["decider"].winner_id
        _add_first_round(league, conf, seeds)


def current_round(league: League) -> int:
    return max(s.round for s in league.playoffs)


def _advance_bracket(league: League) -> bool:
    """Create the next round once the current one is over. False when the finals are done."""
    rnd = current_round(league)
    done = [s for s in league.playoffs if s.round == rnd]
    if not all(s.over for s in done):
        return True
    if rnd == 0:
        _advance_play_in(league, done)
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


PLAYOFF_START_GAP = 6     # NBA: the play-in fills the week after the regular season
PLAYOFF_GAME_GAP = 2      # then a game every other day
PLAY_IN_GAP = 2           # play-in nights: two and four days after the season, round one on day six


def next_playoff_day(league: League, day: int) -> int:
    """The day after ``day`` (the last scheduled game) for the next playoff games."""
    started = any(g.playoff for g in league.games.values() if g.season == league.season)
    if started:
        return day + PLAYOFF_GAME_GAP
    return day + (PLAY_IN_GAP if any(s.play_in for s in league.playoffs) else PLAYOFF_START_GAP)


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
        day = next_playoff_day(league, day)
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


def waiting_for_play_in(league: League) -> set[int]:
    """Teams already in round one (seeds 1-6) while the play-in is still going on."""
    if not league.playoffs or current_round(league) != 0:
        return set()
    out = set()
    for conf, order in seeding(league).items():
        if has_play_in(league, len(order)):
            out.update(order[:teams_per_conference(len(order)) - 2])
    return out


def eliminated(league: League, team_id: int) -> bool:
    """Is ``team_id`` out of this postseason (or never in it)? A team between series
    (waiting for the next round, or the play-in decider) is still alive."""
    mine = [s for s in league.playoffs if team_id in (s.high_id, s.low_id)]
    if not mine:
        return team_id not in waiting_for_play_in(league)
    last = max(mine, key=lambda s: s.id)
    if not last.over:
        return False
    if last.winner_id == team_id:
        return last.conference is None and last.play_in == ""      # won the title: nothing left
    return last.play_in != "7v8"                                     # a 7v8 loser gets the decider


def champion(league: League) -> Optional[int]:
    finals = [s for s in league.playoffs if s.round == current_round(league)]
    return finals[0].winner_id if len(finals) == 1 and finals[0].round > 0 else None
