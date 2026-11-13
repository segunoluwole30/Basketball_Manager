"""Regular-season awards: announced when the regular season ends, before the playoffs."""
import contextlib
import copy
import io
import random
import sys
import tempfile
import unittest
from pathlib import Path

from hoopsim import awards, generate_league, load_league, save_league
from hoopsim.generate import generate_schedule
from hoopsim.models import Phase
from hoopsim.playoffs import play_playoffs, start_playoffs
from hoopsim.season import finish_season, play_regular_season, regular_season_over

GAMES = 16


def short_league(seed=3, teams=4, games=GAMES):
    lg = generate_league(seed=seed, num_teams=teams)
    lg.games = {}
    lg.rules.games_per_season = games
    generate_schedule(lg, random.Random(0))
    return lg


def all_ids(tiers):
    return [r["id"] for tier in tiers for r in tier]


class TimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lg = lg = short_league()
        cls.rng = random.Random(5)
        days = sorted({g.day for g in lg.games.values() if not g.playoff})
        cls.before_last = None
        for d in days:
            if d == days[-1]:
                cls.before_last = copy.deepcopy(lg.awards)
            play_regular_season(lg, cls.rng, days=1)
        cls.at_end = copy.deepcopy(lg.awards)
        cls.morale = {pid: p.morale for pid, p in lg.players.items()}
        start_playoffs(lg)
        play_playoffs(lg, cls.rng)
        cls.after_playoffs = copy.deepcopy(lg.awards)
        cls.summary = finish_season(lg)

    def test_named_on_the_last_regular_season_day(self):
        self.assertIsNone(self.before_last)
        self.assertIsNotNone(self.at_end)
        self.assertEqual(self.at_end["season"], self.summary["season"])
        self.assertNotIn("finals_mvp", self.at_end)                  # only after the Finals
        self.assertFalse(self.at_end["seen"])

    def test_playoffs_change_nothing(self):
        self.assertEqual(self.after_playoffs, self.at_end)

    def test_finals_mvp_after_the_finals(self):
        h = self.lg.history[-1]
        self.assertIsNotNone(h["awards"]["finals_mvp"])
        self.assertEqual(h["season_awards"]["finals_mvp"], h["awards"]["finals_mvp"])
        self.assertEqual(awards.winners(h["season_awards"]), {k: h["awards"][k] for k, _, _ in awards.SINGLE})
        self.assertEqual(h["season_awards"]["single"], self.at_end["single"])
        self.assertEqual(h["season_awards"]["teams"], self.at_end["teams"])

    def test_honours_on_career_lines(self):
        season = self.summary["season"]
        flags = awards.career_flags(self.at_end)
        self.assertTrue(flags)
        for pid, f in flags.items():
            line = next(c for c in self.lg.players[pid].career if c["season"] == season and not c["playoffs"])
            for k, v in f.items():
                self.assertEqual(line[k], v)
        mvp = self.at_end["single"]["mvp"]["winner"]
        self.assertEqual(flags[mvp]["mvp"], True)

    def test_news_without_user_team(self):
        self.assertFalse(any(m["kind"] == "awards" for m in self.lg.inbox))   # no user team: no inbox


class AwardRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lg = lg = short_league(seed=4, teams=6)
        play_regular_season(lg, random.Random(4))
        cls.data = lg.awards

    def test_rules_scale_with_the_season(self):
        lg = copy.copy(self.lg)
        lg.rules = copy.copy(self.lg.rules)
        lg.rules.games_per_season = 82
        r = awards.rules(lg)
        self.assertEqual((r["required"], r["min_minutes"], r["near_minutes"], r["near_games"]), (65, 20, 15, 2))
        lg.rules.games_per_season = 20
        self.assertEqual(awards.rules(lg)["required"], 16)            # 65/82 of 20 = 15.9
        lg.rules.quarter_minutes = 10
        self.assertAlmostEqual(awards.rules(lg)["min_minutes"], 16.7)

    def test_teams_full_and_without_duplicates(self):
        t = self.data["teams"]
        self.assertEqual([len(x) for x in t["all_nba"]], [5, 5, 5])
        self.assertEqual([len(x) for x in t["all_defense"]], [5, 5])
        for key in ("all_nba", "all_defense", "all_rookie"):
            ids = all_ids(t[key])
            self.assertEqual(len(ids), len(set(ids)), key)
            self.assertLessEqual(len(t[key]), {"all_nba": 3}.get(key, 2))
            self.assertTrue(all(len(x) <= 5 for x in t[key]))
        for pid in all_ids(t["all_rookie"]):
            self.assertEqual(self.lg.players[pid].draft_year, self.lg.season)

    def test_finalists_ordered_by_vote(self):
        for key, _, _ in awards.SINGLE:
            e = self.data["single"][key]
            f = e["finalists"]
            self.assertLessEqual(len(f), 3)
            if not f:
                self.assertIsNone(e["winner"])
                continue
            self.assertEqual(e["winner"], f[0]["id"])
            self.assertEqual([r["points"] for r in f], sorted((r["points"] for r in f), reverse=True))
            top = awards.VOTERS * awards.BALLOT.get(key, awards.DEFAULT_BALLOT)[0]
            for r in f:
                self.assertAlmostEqual(r["share"], round(r["points"] / top, 3))
                self.assertTrue(0 < r["share"] <= 1)
            self.assertEqual(len({r["id"] for r in f}), len(f))
        self.assertIsNotNone(self.data["single"]["mvp"]["winner"])
        self.assertIsNotNone(self.data["single"]["dpoy"]["winner"])

    def test_eligibility_rule(self):
        lg = copy.deepcopy(self.lg)
        r = awards.rules(lg)
        counted = awards.counted_games(lg)
        sc = awards.scores(lg, __import__("hoopsim.stats", fromlist=["x"]).season_totals(lg))
        for key in awards.RULED:
            self.assertTrue(all(counted.get(pid, 0) >= r["required"] for pid in sc[key]), key)
        # Cut the MVP's minutes: one game short of the rule and he can't win it.
        mvp = self.data["single"]["mvp"]["winner"]
        lines = [line for g in lg.games.values() if g.box_score and not g.playoff
                 for box in (g.box_score.home, g.box_score.away) for line in box.lines
                 if line.player_id == mvp and line.seconds > 0]
        keep = r["required"] - 1
        for line in lines[keep:]:
            line.seconds = 10 * 60
        self.assertEqual(awards.counted_games(lg)[mvp], keep)
        data = awards.compute(lg)
        self.assertNotIn(mvp, [x["id"] for x in data["single"]["mvp"]["finalists"]])
        self.assertNotIn(mvp, all_ids(data["teams"]["all_nba"]))
        self.assertNotIn(mvp, all_ids(data["teams"]["all_defense"]))

    def test_most_improved_needs_last_season(self):
        self.assertIsNone(self.data["single"]["mip"]["winner"])        # a new league has no history
        lg = copy.deepcopy(self.lg)
        from hoopsim.stats import season_totals
        totals = season_totals(lg)
        ruled = [pid for pid, n in awards.counted_games(lg).items() if n >= awards.rules(lg)["required"]]
        pid = ruled[0]
        t = totals[pid]
        lg.players[pid].career.append({
            "season": lg.season - 1, "playoffs": False, "team_id": t["team_id"], "team": "?",
            "gp": 40, "gs": 0, "min": 400.0, "pts": 120, "fgm": 50, "fga": 140, "tpm": 5, "tpa": 20,
            "ftm": 15, "fta": 20, "orb": 10, "drb": 40, "ast": 20, "stl": 10, "blk": 4, "tov": 30,
            "pf": 60, "plus_minus": -80})
        data = awards.compute(lg)
        self.assertEqual(data["single"]["mip"]["winner"], pid)

    def test_vote_has_its_own_random_stream(self):
        self.assertEqual(awards.compute(self.lg)["single"], self.data["single"])

    def test_announce_is_idempotent_and_lifts_morale(self):
        lg = copy.deepcopy(self.lg)
        lg.awards = None
        for p in lg.players.values():
            p.morale = 50.0
        first = awards.announce(lg)
        mvp = first["single"]["mvp"]["winner"]
        self.assertEqual(lg.players[mvp].morale, 50 + awards.MORALE_WIN)
        flags = awards.career_flags(first)
        team_only = [pid for pid, f in flags.items() if not any(v is True for v in f.values())]
        self.assertTrue(team_only)
        self.assertEqual(lg.players[team_only[0]].morale, 50 + awards.MORALE_TEAM)
        unnamed = next(pid for pid in lg.players if pid not in flags)
        self.assertEqual(lg.players[unnamed].morale, 50.0)
        morale = {pid: p.morale for pid, p in lg.players.items()}
        self.assertIs(awards.announce(lg), first)
        self.assertEqual(morale, {pid: p.morale for pid, p in lg.players.items()})

    def test_not_before_the_season_ends(self):
        lg = short_league(seed=5)
        self.assertIsNone(awards.ensure(lg))
        with self.assertRaises(ValueError):
            awards.announce(lg)


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip(self):
        lg = short_league(seed=6)
        play_regular_season(lg, random.Random(6))
        path = Path(self.tmp.name) / "a.db"
        save_league(lg, path)
        back = load_league(path)
        self.assertEqual(back.awards, lg.awards)
        start_playoffs(back)
        play_playoffs(back, random.Random(1))
        s = finish_season(back)
        self.assertEqual(s["awards"]["mvp"], lg.awards["single"]["mvp"]["winner"])

    def test_old_save_in_the_playoffs(self):
        """A save from before awards night: the awards are named at the end of the Finals."""
        lg = short_league(seed=7)
        play_regular_season(lg, random.Random(7))
        start_playoffs(lg)
        expected = copy.deepcopy(lg.awards)
        lg.awards = None                                    # as an old save has it
        path = Path(self.tmp.name) / "old.db"
        save_league(lg, path)
        back = load_league(path)
        self.assertIsNone(back.awards)
        self.assertIs(back.phase, Phase.PLAYOFFS)
        play_playoffs(back, random.Random(1))
        s = finish_season(back)
        self.assertEqual(s["season_awards"]["single"], expected["single"])  # same regular-season data
        self.assertIsNotNone(s["awards"]["finals_mvp"])

    def test_old_save_after_the_regular_season(self):
        lg = short_league(seed=8)
        play_regular_season(lg, random.Random(8))
        lg.awards = None
        self.assertTrue(regular_season_over(lg))
        start_playoffs(lg)                                  # names them before the bracket
        self.assertIsNotNone(awards.current(lg))


class WebDayByDayTests(unittest.TestCase):
    def test_next_day_and_week_reach_awards_night(self):
        from hoopsim.web.session import GameSession
        for mode in ("day", "week"):
            s = GameSession(saves_dir=tempfile.gettempdir())
            s.new_game(seed=3, num_teams=4)
            lg = s.league
            lg.games = {}
            lg.rules.games_per_season = 8
            generate_schedule(lg, random.Random(0))
            s.choose(min(lg.teams))
            for _ in range(200):
                if regular_season_over(lg):
                    break
                self.assertIsNone(s.state()["awards"])
                s.sim(mode)
                for m in lg.inbox:
                    if m.get("kind") == "offer" and m.get("status") == "open":
                        s.answer_offer(m["id"], False)
            self.assertEqual(s.state()["stage"], "season_over")
            self.assertEqual(s.state()["awards"], {"season": lg.season, "seen": False}, mode)
            self.assertTrue(any(m["kind"] == "awards" for m in lg.inbox))
            self.assertFalse(lg.playoffs)


class CliTests(unittest.TestCase):
    def test_season_command_prints_awards_before_the_playoffs(self):
        from hoopsim.__main__ import main
        out = io.StringIO()
        argv = sys.argv
        sys.argv = ["hoopsim", "season", "--seed", "2", "--teams", "4", "--games", "12"]
        try:
            with contextlib.redirect_stdout(out):
                main()
        finally:
            sys.argv = argv
        text = out.getvalue()
        self.assertIn("Regular-season awards", text)
        self.assertIn("Most Valuable Player", text)
        self.assertIn("All-NBA First Team", text)
        self.assertLess(text.index("Regular-season awards"), text.index("Playoffs"))
        self.assertIn("Finals MVP", text[text.index("Playoffs"):])

    def test_interactive_play_announces_awards(self):
        from hoopsim.manager import choose_team
        from hoopsim.play import IO, Game
        lg = short_league(seed=9, games=12)
        uid = min(lg.teams)
        choose_team(lg, uid)
        script = iter(["end", "awards", "quit", "n"])
        out = []
        ask = lambda prompt: "n" if prompt.startswith("Accept") else next(script)   # noqa: E731 (trade offers)
        Game(lg, IO(ask=ask, say=out.append), random.Random(1)).run()
        text = "\n".join(out)
        self.assertEqual(text.count("AWARDS"), 2)                          # at the end, and on request
        self.assertLess(text.index("REGULAR SEASON IS OVER"), text.index("AWARDS"))
        self.assertIn("Most Valuable Player", text)
        self.assertNotIn("Traceback", text)
        self.assertTrue(any(m["kind"] == "awards" for m in lg.inbox))      # the user's inbox has them


if __name__ == "__main__":
    unittest.main()
