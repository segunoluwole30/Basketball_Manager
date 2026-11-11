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
