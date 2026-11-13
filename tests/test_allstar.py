"""All-Star Weekend: voting, selections, Rising Stars, the Three-Point Contest and the game."""
import copy
import random
import tempfile
import unittest
from pathlib import Path

from hoopsim import allstar, generate_league, load_league, save_league
from hoopsim.models import Position
from hoopsim.season import play_regular_season


def through_all_star(seed=4):
    """A 30-team league played to the first game day after All-Star Weekend."""
    lg = generate_league(seed=seed, num_teams=30)
    lg.user_team_id = 1
    asg = allstar.game_day(lg)
    days = sorted({g.day for g in lg.games.values() if not g.playoff})
    play_regular_season(lg, random.Random(1), days=sum(1 for d in days if d <= asg) + 1)
    return lg


class AllStarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lg = through_all_star()
        cls.st = cls.lg.all_star

    def test_dates(self):
        lg = self.lg
        d = allstar.game_day(lg)
        days = allstar.event_days(lg)
        self.assertEqual(days["game"], d)
        self.assertEqual(days["three"], d - 1)
        self.assertEqual(days["rising"], d - 2)
        from hoopsim.season import season_start
        from datetime import timedelta
        when = season_start(lg.season) + timedelta(days=d)
        self.assertEqual((when.month, when.weekday()), (2, 6))          # a Sunday in February
        self.assertFalse(any(g.day == d for g in lg.games.values()))     # no games that day
        short = generate_league(seed=1, num_teams=4)
        short.games = {}
        short.rules.games_per_season = 12
        from hoopsim.generate import generate_schedule
        generate_schedule(short, random.Random(0))
        self.assertIsNone(allstar.game_day(short))                       # too short for a break
        self.assertEqual(allstar.catch_up(short, 999), [])

    def test_every_event_happened_in_order(self):
        self.assertEqual(self.st["done"], [k for k, _ in allstar.EVENTS])
        self.assertEqual(len(self.st["returns"]), 3)

    def test_selections(self):
        lg, st = self.lg, self.st
        everyone = []
        for conf in ("east", "west"):
            side = st[conf]
            self.assertEqual(len(side["starters"]), 5)
            self.assertEqual(len(side["reserves"]), 7)
            ids = side["starters"] + side["reserves"]
            everyone += ids
            for pid in ids:
                self.assertEqual(lg.teams[lg.players[pid].team_id].conference.lower(), conf)
            guards = [pid for pid in side["starters"] if lg.players[pid].position in allstar.GUARDS]
            replaced = {r["in"] for r in side["replaced"]}
            if not replaced:
                self.assertEqual(len(guards), 2)                         # two guards, three frontcourt
        self.assertEqual(len(everyone), len(set(everyone)))
        self.assertEqual(allstar.selected(lg), set(everyone))
        # The best players are there: the league's top scorer at least.
        from hoopsim.stats import per_game, season_totals
        totals = season_totals(lg)
        top = max((pid for pid, t in totals.items() if t["gp"] >= 30), key=lambda pid: per_game(totals[pid], "pts"))
        self.assertIn(top, everyone)

    def test_three_point_contest(self):
        c = self.st["three"]
        self.assertGreaterEqual(len(c["field"]), 6)
        self.assertEqual(len(c["finalists"]), 3)
        self.assertIn(c["winner"], c["finalists"])
        for rnd in list(c["first"].values()) + list(c["final"].values()):
            self.assertLessEqual(rnd["points"], 40)
            self.assertEqual(len(rnd["racks"]), 5)
            self.assertEqual(len(rnd["deep"]), 2)
            self.assertEqual(rnd["points"], sum(map(sum, rnd["racks"])) + sum(rnd["deep"]))
        first = c["first"]
        cut = sorted((r["points"] for r in first.values()), reverse=True)[2]
        for pid in c["finalists"]:
            self.assertGreaterEqual(first[str(pid)]["points"], cut)
        # shot by shot: the record behind the results, and a Watch link in the news
        rec = c["record"]
        self.assertEqual(rec["winner"], c["winner"])
        for s in rec["rounds"][0]["shooters"]:
            self.assertEqual(allstar.expand_round(s)["points"], first[str(s["p"])]["points"])
        item = next(m for m in self.lg.inbox if m["title"].endswith("wins the Three-Point Contest"))
        self.assertEqual(item["watch_url"], allstar.contest_url(self.lg.season))

    def test_shooting_rating_matters(self):
        lg = self.lg
        p = copy.deepcopy(next(iter(lg.players.values())))
        rng = random.Random(0)
        p.attributes.three_point = 20
        good = sum(allstar.shoot_round(p, rng)["points"] for _ in range(200)) / 200
        p.attributes.three_point = 6
        bad = sum(allstar.shoot_round(p, rng)["points"] for _ in range(200)) / 200
        self.assertGreater(good, bad + 6)
        self.assertTrue(20 <= good <= 30, good)          # contest winners score in the 20s

    def test_games(self):
        lg, st = self.lg, self.st
        g = st["game"]
        self.assertTrue(all(s >= 120 for s in g["score"]), g["score"])   # defence optional
        winner = "home" if g["score"][0] > g["score"][1] else "away"
        self.assertIn(g["mvp"], [r["id"] for r in g["box"][winner]])
        r = st["rising"]["result"]
        self.assertIn(r["mvp"], [x["id"] for x in r["box"]["home"] + r["box"]["away"]])
        self.assertIsNone(g["replay_id"])                # statistical engine: not recorded

    def test_exhibitions_leave_the_league_alone(self):
        lg = copy.deepcopy(self.lg)
        games, injuries = len(lg.games), {p.id: p.injury for p in lg.players.values()}
        attrs = {p.id: p.attributes.to_dict() for p in lg.players.values()}
        lg.rules.match_engine = "hybrid"                 # recorded, and watchable
        res = allstar._exhibition(lg, random.Random(3), ("WEST", "West All-Stars", lg.all_star["west"]["starters"]),
                                  ("EAST", "East All-Stars", lg.all_star["east"]["starters"]), relax=10,
                                  replay_key="game")
        self.assertEqual(len(lg.games), games)
        self.assertEqual({p.id: p.injury for p in lg.players.values()}, injuries)
        self.assertEqual({p.id: p.attributes.to_dict() for p in lg.players.values()}, attrs)
        self.assertEqual(res["replay_id"], allstar.REPLAY_IDS["game"])
        self.assertEqual(allstar.replay(lg, res["replay_id"])["home"]["name"], "West All-Stars")

    def test_inbox_and_history(self):
        lg = self.lg
        titles = [m["title"] for m in lg.inbox if m["kind"] == "allstar"]
        self.assertIn("All-Star starters announced", titles)
        self.assertTrue(any("Three-Point Contest" in t for t in titles))
        self.assertTrue(any(t.startswith("All-Star Game") for t in titles))
        flags = allstar.career_flags(lg)
        self.assertTrue(flags[self.st["game"]["mvp"]]["asg_mvp"])
        self.assertTrue(flags[self.st["three"]["winner"]]["three_point_champion"])
        self.assertEqual(sum(1 for f in flags.values() if f.get("all_star")), 24)
        summary = allstar.season_summary(lg)
        self.assertEqual(summary["mvp"], self.st["game"]["mvp"])

    def test_view_and_save(self):
        v = allstar.view(self.lg)
        self.assertTrue(v["available"])
        self.assertEqual(len(v["east"]["starters"]), 5)
        self.assertIn("East Guards", v["votes"])
        self.assertTrue(all(e["done"] for e in v["schedule"]))
        with tempfile.TemporaryDirectory() as d:
            save_league(self.lg, Path(d) / "x.db")
            back = load_league(Path(d) / "x.db")
        self.assertEqual(back.all_star["east"], self.st["east"])
        self.assertTrue(all(isinstance(k, int) for k in back.all_star["votes"]))
        self.assertEqual(allstar.view(back)["game"]["score"], self.st["game"]["score"])

    def test_weekend_uses_its_own_random_stream(self):
        """Voting and the exhibitions don't draw from the season's random stream: games up
        to the starters' announcement (when morale lifts begin) are the same without them."""
        import hoopsim.season as season_mod
        a = generate_league(seed=9, num_teams=30)
        b = copy.deepcopy(a)
        ev = allstar.event_days(a)
        days = sorted({g.day for g in a.games.values() if not g.playoff})
        n = sum(1 for d in days if d <= ev["starters"])
        play_regular_season(a, random.Random(5), days=n)
        real = season_mod.allstar.catch_up
        season_mod.allstar.catch_up = lambda league, day: []
        try:
            play_regular_season(b, random.Random(5), days=n)
        finally:
            season_mod.allstar.catch_up = real
        self.assertEqual(a.all_star["done"][:3], ["returns1", "returns2", "returns3"])
        ra = {g.id: (g.result.home_score, g.result.away_score) for g in a.games.values() if g.played}
        rb = {g.id: (g.result.home_score, g.result.away_score) for g in b.games.values() if g.played}
        self.assertEqual(ra, rb)

if __name__ == "__main__":
    unittest.main()
