"""The web app's day-by-day controls: Next day moves exactly one calendar day, "Go to
this day" stops at the start of the chosen day (your game that day still to play), and
Play live / Watch offer that game after such a stop. Short seasons keep these quick."""
import random
import tempfile
import unittest
from datetime import timedelta
from unittest import mock

from hoopsim.generate import generate_schedule
from hoopsim.models import Phase
from hoopsim.season import season_start, trade_deadline_day
from hoopsim.web.calendar import calendar, sim_to
from hoopsim.web.session import GameSession, UserError


def new_session(tmp, teams=4, games=12, engine="statistical", seed=7) -> GameSession:
    s = GameSession(saves_dir=tmp)
    choices = s.new_game(seed=seed, num_teams=teams, match_engine=engine)["teams"]
    lg = s.league
    lg.games = {}
    lg.rules.games_per_season = games
    generate_schedule(lg, random.Random(0))
    s.choose(choices[0]["id"])
    return s


def day_of(lg) -> int:
    return (lg.current_date - season_start(lg.season)).days


def season_games(lg, playoff=False):
    return [g for g in lg.games.values() if g.season == lg.season and g.playoff == playoff]


def mine(lg, g) -> bool:
    return lg.user_team_id in (g.home_team_id, g.away_team_id)


def finish_regular_season(s):
    for _ in range(40):                                # trade offers pause the sim
        if s.state().get("season_over"):
            return
        s.sim("end")
    raise AssertionError("the regular season didn't finish")


class NextDayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_next_day_is_one_calendar_day(self):
        s = new_session(self.tmp.name)
        lg = s.league
        days = {g.day for g in season_games(lg)}
        my_days = {g.day for g in season_games(lg) if mine(lg, g)}
        kinds = set()
        while not s.state().get("season_over"):
            today = s._today()
            before = {g.id for g in lg.games.values() if g.played}
            r = s.sim("day")                           # (an offer pauses nothing: it's one day)
            self.assertEqual(r["days"], 1)
            self.assertEqual(s._today(), today + 1)
            self.assertEqual(day_of(lg), today + 1)
            self.assertEqual(r["state"]["day"], today + 1)
            self.assertEqual(r["date"], (season_start(lg.season) + timedelta(days=today + 1)).isoformat())
            self.assertEqual(r["state"]["date"], r["date"])
            self.assertEqual(calendar(s)["today"], today + 1)
            new = [g for g in lg.games.values() if g.played and g.id not in before]
            # exactly that day's games, all of them, and nothing else
            self.assertTrue(all(g.day == today + 1 for g in new))
            self.assertTrue(all(g.played for g in season_games(lg) if g.day <= today + 1))
            self.assertFalse(any(g.played for g in season_games(lg) if g.day > today + 1))
            self.assertEqual(r["league_games"], len(new))
            self.assertEqual([v["id"] for v in r["games"]], [g.id for g in new if mine(lg, g)])
            if today + 1 not in days:
                kinds.add("off day")
                self.assertEqual(new, [])
            elif today + 1 not in my_days:
                kinds.add("idle")
                self.assertTrue(new)
                self.assertEqual(r["games"], [])
            else:
                kinds.add("my game")
        self.assertEqual(kinds, {"off day", "idle", "my game"})
        self.assertEqual(s._today(), max(days))

    def test_day_by_day_plays_the_same_season_as_one_sim(self):
        """Off days draw no random numbers: stepping through them changes nothing."""
        a, b = new_session(self.tmp.name), new_session(self.tmp.name)
        finish_regular_season(a)
        while not b.state().get("season_over"):
            b.sim("day")
        score = lambda s: sorted((g.id, g.result.home_score, g.result.away_score) for g in season_games(s.league))
        self.assertEqual(score(a), score(b))

    def test_state_shows_the_next_day(self):
        s = new_session(self.tmp.name)
        lg = s.league
        g = min((g for g in season_games(lg) if mine(lg, g)), key=lambda g: g.day)
        sim_to(s, g.day)
        st = s.state()
        self.assertEqual(st["day"], g.day - 1)
        self.assertEqual(st["next_day_game"]["id"], g.id)
        self.assertEqual(st["next_date"], (season_start(lg.season) + timedelta(days=g.day)).isoformat())
        cal = calendar(s)
        self.assertEqual([d["day"] for d in cal["days"] if d["next"]], [g.day])
        self.assertEqual(s.dashboard()["upcoming"][0]["id"], g.id)

    def test_old_save_with_a_date_past_an_unplayed_day(self):
        """A date at or past a day that still has games reads as the end of the day before."""
        s = new_session(self.tmp.name)
        lg = s.league
        first = min(g.day for g in season_games(lg))
        lg.current_date = season_start(lg.season) + timedelta(days=first + 3)
        self.assertEqual(s._today(), first - 1)
        r = s.sim("day")
        self.assertTrue(all(g.played == (g.day == first) for g in season_games(lg)))
        self.assertEqual(r["state"]["day"], first)
        # Half a day played (one game of it): Next day plays the rest of that day.
        day2 = min(g.day for g in season_games(lg) if not g.played)
        games2 = [g for g in season_games(lg) if g.day == day2]
        if len(games2) > 1:
            from hoopsim.engine import play_game
            play_game(lg, games2[0].id, s.rng)
            lg.current_date = season_start(lg.season) + timedelta(days=day2)
            self.assertEqual(s._today(), day2 - 1)
            s.sim("day")
            self.assertTrue(all(g.played for g in games2))
            self.assertEqual(s._today(), day2)


class GoToDayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_go_to_a_day_stops_before_my_game(self):
        s = new_session(self.tmp.name)
        lg = s.league
        target = sorted({g.day for g in season_games(lg) if mine(lg, g)})[3]
        r = sim_to(s, target)
        self.assertEqual(r["at_day"], target)
        self.assertTrue(all(g.played == (g.day < target) for g in season_games(lg)))
        self.assertEqual(s._today(), target - 1)
        self.assertEqual(r["state"]["day"], target - 1)
        my_game = next(g for g in season_games(lg) if g.day == target and mine(lg, g))
        self.assertFalse(my_game.played)
        # Already at the start of that day: nothing to play, no error.
        again = sim_to(s, target)
        self.assertEqual(again["days"], 0)
        # Next day plays it, and only that day.
        r = s.sim("day")
        self.assertEqual([g["id"] for g in r["games"]], [my_game.id])
        self.assertTrue(all(g.played == (g.day <= target) for g in season_games(lg)))
        with self.assertRaises(UserError):
            sim_to(s, target)                          # played now
        with self.assertRaises(UserError):
            sim_to(s, max(g.day for g in season_games(lg)) + 30)

    def test_go_to_an_idle_day_and_an_off_day(self):
        s = new_session(self.tmp.name)
        lg = s.league
        days = sorted({g.day for g in season_games(lg)})
        my_days = {g.day for g in season_games(lg) if mine(lg, g)}
        idle = next(d for d in days[3:] if d not in my_days)
        sim_to(s, idle)
        self.assertTrue(all(g.played == (g.day < idle) for g in season_games(lg)))
        self.assertEqual(s._today(), idle - 1)
        off = next((d for d in range(idle + 2, days[-1]) if d not in days), None)
        if off is not None:
            sim_to(s, off)
            self.assertEqual(s._today(), off - 1)
            self.assertTrue(all(g.played == (g.day < off) for g in season_games(lg)))

    def test_trade_offers_still_interrupt(self):
        s = new_session(self.tmp.name)
        lg = s.league
        deadline = trade_deadline_day(lg)
        theirs = next(t for t in lg.teams if t != lg.user_team_id)

        def pitch(league, rng, manager, **kw):
            manager.respond_to_trade(league, {"team_id": theirs, "you_give": [league.roster(league.user_team_id)[-1].id],
                                              "you_get": [league.roster(theirs)[-1].id]})
            return []

        with mock.patch("hoopsim.season.ai_offers_to_user", pitch):
            r = sim_to(s, deadline + 5)
            self.assertEqual(r["stopped_for"], "offers")
            self.assertEqual(s._today(), deadline)
            self.assertTrue(any(m["kind"] == "offer" and m["status"] == "open" for m in lg.inbox))
            self.assertFalse(any(g.played for g in season_games(lg) if g.day > deadline))
            r = s.sim("day")                           # carries on from the deadline, one day
            self.assertEqual(s._today(), deadline + 1)

    def test_week_deadline_and_end_play_through(self):
        s = new_session(self.tmp.name)
        lg = s.league
        s.sim("week")
        self.assertEqual(s._today(), 7)
        self.assertTrue(all(g.played == (g.day <= 7) for g in season_games(lg)))
        deadline = trade_deadline_day(lg)
        r = s.sim("deadline")
        if not r["stopped_for"]:
            self.assertEqual(s._today(), deadline)
            self.assertTrue(all(g.played == (g.day <= deadline) for g in season_games(lg)))
        finish_regular_season(s)
        self.assertTrue(all(g.played for g in season_games(lg)))


class PlayoffDayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_playoff_next_day_is_one_calendar_day(self):
        s = new_session(self.tmp.name, games=4)
        lg = s.league
        lg.rules.playoff_series_length = 3
        finish_regular_season(s)
        s.start_playoffs()
        last_regular = max(g.day for g in season_games(lg))
        self.assertEqual(s._today(), last_regular)
        kinds = set()
        for _ in range(200):
            today = s._today()
            before = {g.id for g in lg.games.values() if g.played}
            ahead = s.state()["next_day_games"]
            r = s.sim_playoffs("day")
            if r["finished"]:
                break
            if ahead:                                  # the state said the next day has games
                self.assertEqual(r["games_played"], ahead)
            self.assertEqual(s._today(), today + 1)
            self.assertEqual(day_of(lg), today + 1)
            self.assertEqual(r["state"]["date"], (season_start(lg.season) + timedelta(days=today + 1)).isoformat())
            new = [g for g in lg.games.values() if g.played and g.id not in before]
            self.assertTrue(all(g.day == today + 1 for g in new))
            self.assertEqual(r["games_played"], len(new))
            kinds.add("games" if new else "off day")
            self.assertEqual(calendar(s)["today"], today + 1)
        self.assertEqual(lg.phase, Phase.OFFSEASON)
        self.assertEqual(kinds, {"games", "off day"})

    def test_old_playoff_save_with_the_date_left_behind(self):
        s = new_session(self.tmp.name, games=4)
        lg = s.league
        lg.rules.playoff_series_length = 3
        finish_regular_season(s)
        stuck = lg.current_date
        s.start_playoffs()
        s.sim_playoffs("round")
        lg.current_date = stuck                        # saves from before the date moved in the playoffs
        last = max(g.day for g in season_games(lg, playoff=True) if g.played)
        self.assertEqual(s._today(), last)
        s.sim_playoffs("day")
        self.assertEqual(s._today(), last + 1)

    def test_go_to_a_playoff_day(self):
        s = new_session(self.tmp.name, games=4)
        lg = s.league
        lg.rules.playoff_series_length = 3
        finish_regular_season(s)
        s.start_playoffs()
        target = s._today() + 9
        r = sim_to(s, target)
        self.assertEqual(r["at_day"], target)
        self.assertEqual(s._today(), target - 1)
        self.assertFalse(any(g.played for g in season_games(lg, playoff=True) if g.day >= target))


class LiveAfterGoToTests(unittest.TestCase):
    """Play live and Watch offer the game on the day you stopped at."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _stopped(self):
        s = new_session(self.tmp.name, games=4, engine="spatial")
        lg = s.league
        g = sorted((g for g in season_games(lg) if mine(lg, g)), key=lambda g: g.day)[1]
        sim_to(s, g.day)
        self.assertFalse(g.played)
        return s, g

    def test_play_live_after_a_stop(self):
        s, g = self._stopped()
        lg = s.league
        r = s.live_start()
        self.assertEqual(r["game_id"], g.id)
        self.assertEqual(s._today(), g.day - 1)                 # still the start of the day
        self.assertTrue(all(x.played == (x.day < g.day) for x in season_games(lg) if x.id != g.id))
        s.live_finish()
        self.assertTrue(all(x.played == (x.day <= g.day) for x in season_games(lg)))
        self.assertEqual(s._today(), g.day)
        self.assertEqual(s.state()["date"], (season_start(lg.season) + timedelta(days=g.day)).isoformat())

    def test_watch_after_a_stop(self):
        s, g = self._stopped()
        lg = s.league
        r = s.watch_next()
        self.assertEqual(r["game_id"], g.id)
        self.assertTrue(all(x.played == (x.day <= g.day) for x in season_games(lg)))
        self.assertEqual(s._today(), g.day)


if __name__ == "__main__":
    unittest.main()
