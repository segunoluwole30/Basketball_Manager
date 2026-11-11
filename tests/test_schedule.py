"""NBA-shaped schedules: calendar, game counts, rest, back-to-backs and playoff spacing."""
import random
import statistics as st
import unittest
from collections import Counter
from datetime import timedelta

from hoopsim import generate_league
from hoopsim.engine import play_day
from hoopsim.schedule import schedule_report, season_calendar, tired_from_last_night
from hoopsim.season import season_start


class ScheduleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lg = generate_league(seed=1, num_teams=30)
        cls.rep = schedule_report(cls.lg)
        cls.base = season_start(cls.lg.season)

    def test_every_team_plays_82_about_half_at_home(self):
        for t in self.rep["teams"].values():
            self.assertEqual(t["games"], 82)
            self.assertTrue(40 <= t["home"] <= 42, t)

    def test_back_to_backs_like_the_nba_and_never_three_in_three(self):
        b2b = [t["b2b"] for t in self.rep["teams"].values()]
        self.assertTrue(12 <= st.mean(b2b) <= 18, st.mean(b2b))
        self.assertTrue(all(t["three_in_three"] == 0 for t in self.rep["teams"].values()))

    def test_calendar_dates(self):
        nights = self.rep["nights"]
        first, last = min(nights), max(nights)
        self.assertEqual((self.base + timedelta(days=first)).weekday(), 1)              # opening Tuesday
        self.assertEqual((self.base + timedelta(days=first)).month, 10)
        self.assertEqual((self.base + timedelta(days=last)).month, 4)                   # mid-April finish
        cal = season_calendar(self.lg.season, 82)
        for d in cal["blackout"]:                                                        # Thanksgiving, Xmas Eve, ASB
            self.assertNotIn(d, nights)
        self.assertEqual(len(cal["all_star"]), 6)
        self.assertLessEqual(nights.get(cal["christmas"], 0), 5)
        self.assertGreater(nights.get(cal["christmas"], 0), 0)

    def test_weekday_rhythm(self):
        per, cnt = Counter(), Counter()
        for d, k in self.rep["nights"].items():
            wd = (self.base + timedelta(days=d)).weekday()
            per[wd] += k
            cnt[wd] += 1
        avg = {wd: per[wd] / cnt[wd] for wd in cnt}
        self.assertGreater(min(avg[2], avg[4]), max(avg[0], avg[3]))       # Wed/Fri busier than Mon/Thu

    def test_conference_opponents_more_often(self):
        same = other = 0
        for g in self.lg.games.values():
            if self.lg.teams[g.home_team_id].conference == self.lg.teams[g.away_team_id].conference:
                same += 1
            else:
                other += 1
        self.assertAlmostEqual(other / (same + other), 30 / 82, delta=0.03)

    def test_small_league_and_short_season(self):
        lg = generate_league(seed=2, num_teams=8)
        rep = schedule_report(lg)
        self.assertEqual({t["games"] for t in rep["teams"].values()}, {82})
        self.assertTrue(all(t["home"] == 41 for t in rep["teams"].values()))


class RestTests(unittest.TestCase):
    def test_last_nights_players_start_tired(self):
        lg = generate_league(seed=3, num_teams=8)
        days = {}
        for g in lg.games.values():
            for t in (g.home_team_id, g.away_team_id):
                days.setdefault(t, set()).add(g.day)
        team, day = next((t, d) for t, ds in sorted(days.items()) for d in sorted(ds) if d - 1 in ds)
        self.assertEqual(tired_from_last_night(lg, team, day), set())     # last night not played yet
        play_day(lg, day - 1, random.Random(0))
        tired = tired_from_last_night(lg, team, day)
        self.assertTrue(5 <= len(tired) <= 12)
        self.assertEqual(tired_from_last_night(lg, team, day + 5), set())


class PlayoffSpacingTests(unittest.TestCase):
    def test_playoffs_start_after_a_gap_and_go_every_other_day(self):
        from hoopsim.playoffs import PLAYOFF_GAME_GAP, PLAYOFF_START_GAP, play_playoffs
        from hoopsim.engine import play_season
        lg = generate_league(seed=4, num_teams=4)
        for gid in [g.id for g in lg.games.values() if g.day > min(x.day for x in lg.games.values()) + 10]:
            del lg.games[gid]
        lg.rules.playoff_series_length = 3
        play_season(lg, random.Random(1))
        last = max(g.day for g in lg.games.values())
        play_playoffs(lg, random.Random(2))
        days = sorted({g.day for g in lg.games.values() if g.playoff})
        self.assertEqual(days[0], last + PLAYOFF_START_GAP)
        self.assertTrue(all(b - a == PLAYOFF_GAME_GAP for a, b in zip(days, days[1:])))


class DivisionTests(unittest.TestCase):
    def test_nba_divisions_and_the_nba_formula(self):
        lg = generate_league(seed=1, num_teams=30)
        divs = Counter(t.division for t in lg.teams.values())
        self.assertEqual(divs, Counter({"Atlantic": 5, "Central": 5, "Southeast": 5,
                                        "Northwest": 5, "Pacific": 5, "Southwest": 5}))
        for t in lg.teams.values():
            self.assertIn(t.division, {"East": ("Atlantic", "Central", "Southeast"),
                                       "West": ("Northwest", "Pacific", "Southwest")}[t.conference])
        T = lg.teams
        for tid in list(T)[:6]:
            vs = Counter()
            for g in lg.games.values():
                if tid in (g.home_team_id, g.away_team_id):
                    vs[g.away_team_id if g.home_team_id == tid else g.home_team_id] += 1
            div = sorted(vs[o] for o in T if o != tid and T[o].division == T[tid].division)
            conf = sorted(vs[o] for o in T if T[o].conference == T[tid].conference and T[o].division != T[tid].division)
            other = {vs[o] for o in T if T[o].conference != T[tid].conference}
            self.assertEqual(div, [4, 4, 4, 4])
            self.assertEqual(conf, [3] * 4 + [4] * 6)
            self.assertEqual(other, {2})

    def test_smaller_leagues_favour_division_rivals(self):
        for n in (12, 8):
            lg = generate_league(seed=3, num_teams=n)
            T = lg.teams
            tid = min(T)
            vs = Counter()
            for g in lg.games.values():
                if tid in (g.home_team_id, g.away_team_id):
                    vs[g.away_team_id if g.home_team_id == tid else g.home_team_id] += 1
            div = [vs[o] for o in T if o != tid and T[o].division == T[tid].division]
            other = [vs[o] for o in T if T[o].conference != T[tid].conference]
            self.assertGreater(min(div), max(other))
            self.assertEqual(sum(vs.values()), 82)


class TiebreakTests(unittest.TestCase):
    """The NBA's tie-break order, on hand-made results."""

    def league(self, results):
        from hoopsim.models import Game, GameResult
        lg = generate_league(seed=1, num_teams=8)
        lg.games = {}
        for i, (home, away, hs, as_) in enumerate(results):
            g = Game(i + 1, lg.season, i + 1, home, away)
            g.result = GameResult(hs, as_, overtimes=0)
            lg.add_game(g)
        return lg

    def ids(self, lg, division):
        return sorted(t.id for t in lg.teams.values() if t.division == division)

    def test_two_teams_head_to_head_first(self):
        from hoopsim.playoffs import ranked
        lg = generate_league(seed=1, num_teams=8)
        a, b = self.ids(lg, "Atlantic")
        c, d = self.ids(lg, "Central")
        # a and b both 2-1; b beat a; a has the better point differential.
        lg = self.league([(a, c, 140, 90), (a, d, 140, 90), (b, a, 101, 100),
                          (b, c, 101, 100), (d, b, 101, 100), (c, a, 101, 100)])
        order = ranked(lg, [a, b])
        self.assertEqual(order, [b, a])

    def test_division_winner_beats_a_non_winner(self):
        from hoopsim.playoffs import division_leaders, ranked
        lg = generate_league(seed=1, num_teams=8)
        a, b = self.ids(lg, "Atlantic")
        c, d = self.ids(lg, "Central")
        # a and c both 1-1, split nothing head to head; a leads the Atlantic (b is 0-2),
        # c doesn't lead the Central (d is 2-0).
        lg = self.league([(a, b, 100, 90), (c, a, 100, 90), (d, c, 100, 90), (d, b, 100, 90)])
        self.assertIn(a, division_leaders(lg))
        self.assertNotIn(c, division_leaders(lg))
        # a and c met once (c won) -> head-to-head decides two teams before division winner.
        self.assertEqual(ranked(lg, [a, c])[0], c)
        # Three-way: division winner goes first.
        lg2 = self.league([(a, b, 100, 90), (b, c, 100, 90), (c, d, 100, 90), (d, a, 100, 90)])
        # everyone 1-1; leaders decided within divisions by head-to-head: a over b, c over d.
        L = division_leaders(lg2)
        self.assertEqual(L & {a, b}, {a})
        self.assertEqual(L & {c, d}, {c})
        order = ranked(lg2, [a, b, c])
        self.assertEqual(set(order[:2]), {a, c})        # both leaders ahead of b
        self.assertEqual(order[2], b)
