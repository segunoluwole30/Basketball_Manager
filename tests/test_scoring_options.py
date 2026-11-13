"""1st, 2nd and 3rd scoring options (Tactics.scoring_options)."""
import random
import unittest
from collections import Counter

from hoopsim import generate_league
from hoopsim.engine import simulate_game
from hoopsim.manager import set_tactics
from hoopsim.models import Game, Tactics
from hoopsim.systems import option_ranks


def fga_share(league, me, pid, n, engine="statistical", seed0=0):
    """His share of the team's shots, counting a trip to the line as a shot (FGA + 0.44 FTA):
    a go-to scorer draws more fouls."""
    league.rules.match_engine = engine
    opps = [t for t in league.teams if t != me]
    mine = tot = 0
    for i in range(n):
        g = Game(id=10**6 + i, season=league.season, day=1, home_team_id=me, away_team_id=opps[i % len(opps)])
        if engine == "statistical":
            sim = simulate_game(league, g, random.Random(seed0 + i))
        else:
            from hoopsim.spatial.season_game import simulate_spatial
            sim = simulate_spatial(league, g, random.Random(seed0 + i), record=False)
        b = sim.box_score.home
        mine += sum(ln.fga + 0.44 * ln.fta for ln in b.lines if ln.player_id == pid)
        tot += b.totals().fga + 0.44 * b.totals().fta
    return mine / tot


class ScoringOptionTests(unittest.TestCase):
    def setUp(self):
        self.lg = generate_league(seed=11, num_teams=12, systems=False)
        self.lg.user_team_id = 1
        starters = self.lg.teams[1].tactics.starters
        self.order = sorted(starters, key=lambda pid: -self.lg.players[pid].current_ability)

    def test_saved_and_defaults(self):
        t = Tactics(scoring_options=[5, 9])
        self.assertEqual(Tactics.from_dict(t.to_dict()).scoring_options, [5, 9])
        d = Tactics().to_dict()
        del d["scoring_options"]
        self.assertEqual(Tactics.from_dict(d).scoring_options, [])           # old saves: none

    def test_setting_and_validation(self):
        lg, o = self.lg, self.order
        ok, msg = set_tactics(lg, options=[o[2], o[0]])
        self.assertTrue(ok, msg)
        self.assertEqual(lg.teams[1].tactics.scoring_options, [o[2], o[0]])
        self.assertIn("options", msg)
        self.assertTrue(set_tactics(lg, options=f"{o[1]},{o[0]},{o[2]}")[0])  # the CLI's form
        self.assertEqual(lg.teams[1].tactics.scoring_options, [o[1], o[0], o[2]])
        stranger = next(p.id for p in lg.players.values() if p.team_id == 2)
        for bad in ([o[0], o[0]], o[:4] if len(o) > 3 else [1, 2, 3, 4], [stranger]):
            ok, msg = set_tactics(lg, options=bad)
            self.assertFalse(ok, bad)
        self.assertTrue(set_tactics(lg, options="")[0])                       # cleared: back to normal
        self.assertEqual(lg.teams[1].tactics.scoring_options, [])

    def test_options_move_up_when_one_sits(self):
        t = Tactics(scoring_options=[10, 20, 30])
        self.assertEqual(option_ranks(t, [10, 20, 30, 4, 5]), {10: 0, 20: 1, 30: 2})
        self.assertEqual(option_ranks(t, [20, 30, 4, 5, 6]), {20: 0, 30: 1})    # the 1st option sits
        self.assertEqual(option_ranks(Tactics(), [1, 2, 3]), {})

    def test_more_shots_for_the_option_statistical(self):
        p = self.order[3]                                   # the 4th-best starter
        base = fga_share(self.lg, 1, p, 300)
        self.lg.teams[1].tactics.scoring_options = [p]
        boosted = fga_share(self.lg, 1, p, 300)
        self.assertGreater(boosted, base * 1.2)
        self.lg.teams[1].tactics.scoring_options = [self.order[4], self.order[3], self.order[2]]
        shares = [fga_share(self.lg, 1, pid, 300) for pid in (self.order[4], self.order[3])]
        self.assertGreater(shares[0], base * 0.5)            # the weakest starter now shoots plenty

    def test_more_shots_for_the_option_spatial(self):
        p = self.order[4]                                    # the weakest starter as the 1st option
        base = fga_share(self.lg, 1, p, 10, "spatial")
        self.lg.teams[1].tactics.scoring_options = [p]
        boosted = fga_share(self.lg, 1, p, 10, "spatial")
        self.assertGreater(boosted, base * 1.2)

    def test_web_api(self):
        try:
            from fastapi.testclient import TestClient
            from hoopsim.web.app import create_app
            from hoopsim.web.session import GameSession
        except ImportError:                                  # pragma: no cover
            self.skipTest("web extras not installed")
        s = GameSession()
        s.league = self.lg
        c = TestClient(create_app(s))
        t = c.get("/api/tactics").json()
        self.assertEqual(t["options"], [])
        self.assertEqual(len(t["roster"]), len(self.lg.roster(1)))
        r = c.post("/api/tactics", json={"options": [self.order[1]]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["tactics"]["options"], [self.order[1]])
        self.assertEqual(c.post("/api/tactics", json={"options": [999999]}).status_code, 400)
        self.lg.players[self.order[1]].team_id = 2               # traded away: no longer shown
        self.assertEqual(c.get("/api/tactics").json()["options"], [])

    def test_live_match_command(self):
        from hoopsim.spatial.live import LiveMatch
        lg = self.lg
        game = next(g for g in lg.games.values() if 1 in (g.home_team_id, g.away_team_id))
        m = LiveMatch(lg, game, random.Random(0))
        r = m.command({"type": "tactics", "changes": {"options": [self.order[2], self.order[0]]}})
        self.assertEqual(r["state"]["tactics"]["options"], [self.order[2], self.order[0]])
        with self.assertRaises(Exception):
            m.command({"type": "tactics", "changes": {"options": [12345678]}})


if __name__ == "__main__":
    unittest.main()
