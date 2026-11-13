"""The drawing_fouls rating (README "Data model", "Star scoring"): saves, generation,
development, the editor and the statistical engine."""
import random
import statistics
import unittest
from dataclasses import replace

from hoopsim import Attributes, generate_league
from hoopsim import generate as gen
from hoopsim.development import develop
from hoopsim.editor import EditError, edit_player
from hoopsim.engine import DEFAULT_TUNING, simulate_game
from hoopsim.league_file import load_league_file
from hoopsim.models import (ABILITY_ATTRIBUTES, Player, Position, compute_ability,
                            derived_drawing_fouls)
from tests.test_league_file import synthetic_file


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.lg = generate_league(seed=3, num_teams=4)
        self.p = next(iter(self.lg.players.values()))

    def test_round_trip(self):
        self.p.attributes.drawing_fouls = 17
        back = Player.from_dict(self.p.to_dict())
        self.assertEqual(back.attributes, self.p.attributes)
        self.assertEqual(back.attributes.drawing_fouls, 17)

    def test_old_saves_derive_it_from_the_other_ratings(self):
        d = self.p.to_dict()
        del d["attributes"]["drawing_fouls"]
        back = Player.from_dict(d)
        self.assertEqual(back.attributes.drawing_fouls, derived_drawing_fouls(self.p.attributes))
        # A strong finisher who attacks gets to the line; a weak jump shooter doesn't.
        slasher = Attributes(finishing=18, strength=16, close_range=15, ball_handling=15, vertical=15)
        shooter = Attributes(finishing=6, strength=6, close_range=7, ball_handling=8, vertical=7)
        self.assertGreater(derived_drawing_fouls(slasher), 14)
        self.assertLessEqual(derived_drawing_fouls(shooter), 7)
        self.assertEqual(derived_drawing_fouls(Attributes(**dict.fromkeys(ABILITY_ATTRIBUTES, 10))), 10)

    def test_old_league_files_derive_it_too(self):
        f = synthetic_file()
        for p in f["players"]:
            p["attributes"] = {"finishing": 18, "strength": 17}
        lg = load_league_file(f)
        p = next(iter(lg.players.values()))
        self.assertEqual(p.attributes.drawing_fouls, derived_drawing_fouls(p.attributes))
        self.assertGreater(p.attributes.drawing_fouls, 10)
        f["players"][0]["attributes"] = {"drawing_fouls": 21}
        with self.assertRaises(ValueError):
            load_league_file(f)

    def test_it_does_not_count_toward_ability(self):
        for pos in Position:
            lo = replace(self.p.attributes, drawing_fouls=1)
            hi = replace(self.p.attributes, drawing_fouls=20)
            self.assertEqual(compute_ability(lo, pos), compute_ability(hi, pos))


class GenerationTests(unittest.TestCase):
    def test_realistic_spread_that_follows_finishing_and_strength(self):
        lg = generate_league(seed=5, num_teams=30, roster_size=15)
        ps = [p for p in lg.players.values() if p.team_id is not None]
        df = [p.attributes.drawing_fouls for p in ps]
        self.assertTrue(9 <= statistics.fmean(df) <= 11, statistics.fmean(df))
        self.assertTrue(2.5 <= statistics.pstdev(df) <= 4.5, statistics.pstdev(df))
        self.assertGreaterEqual(max(df), 17)
        self.assertLessEqual(min(df), 4)
        for k in ("finishing", "strength"):
            r = statistics.correlation(df, [getattr(p.attributes, k) for p in ps])
            self.assertGreater(r, 0.3, k)
        # Better scorers (who carry the load) draw more.
        best = sorted(ps, key=lambda p: -p.current_ability)[:30]
        self.assertGreater(statistics.fmean(p.attributes.drawing_fouls for p in best), statistics.fmean(df) + 2)

    def test_drawing_it_leaves_every_other_draw_alone(self):
        def league(patched):
            if patched:
                saved = gen.derived_drawing_fouls
                gen.derived_drawing_fouls = lambda a, noise=0.0: 10
            try:
                return generate_league(seed=11, num_teams=6)
            finally:
                if patched:
                    gen.derived_drawing_fouls = saved
        a, b = league(False), league(True)
        strip = lambda lg: [{**p.to_dict(), "attributes": {k: v for k, v in p.attributes.to_dict().items()  # noqa: E731
                                                          if k != "drawing_fouls"}} for p in lg.players.values()]
        self.assertEqual(strip(a), strip(b))
        self.assertNotEqual({p.attributes.drawing_fouls for p in a.players.values()}, {10})


class DevelopmentTests(unittest.TestCase):
    def test_it_develops_without_touching_the_other_ratings(self):
        lg = generate_league(seed=2, num_teams=4)
        young = [p for p in lg.players.values() if p.age(lg.current_date) <= 23][:10]
        for p in young:
            q = Player.from_dict(p.to_dict())
            q.attributes.drawing_fouls = 3 if p.attributes.drawing_fouls > 10 else 17
            develop(p, 21, 30, random.Random(p.id))
            develop(q, 21, 30, random.Random(p.id))
            for k in ABILITY_ATTRIBUTES:
                self.assertEqual(getattr(p.attributes, k), getattr(q.attributes, k))
        before = {p.id: p.attributes.drawing_fouls for p in young}
        for p in young:
            develop(p, 20, 30, random.Random(1))
        self.assertTrue(any(p.attributes.drawing_fouls != before[p.id] for p in young))


class EditorTests(unittest.TestCase):
    def test_edit_and_validation(self):
        lg = load_league_file(synthetic_file())
        p = next(p for p in lg.players.values() if p.team_id == 1)
        edit_player(lg, p.id, {"attributes": {"drawing_fouls": 19}})
        self.assertEqual(p.attributes.drawing_fouls, 19)
        for bad in (0, 21, 7.5, "x"):
            with self.assertRaises(EditError):
                edit_player(lg, p.id, {"attributes": {"drawing_fouls": bad}})
        self.assertEqual(p.attributes.drawing_fouls, 19)

    def test_player_card_lists_it_with_the_scoring_ratings(self):
        from hoopsim.web.session import GameSession
        import inspect
        src = inspect.getsource(GameSession.player_card)
        self.assertIn('"free_throw", "drawing_fouls"', src)


class EngineTests(unittest.TestCase):
    def fta(self, rating: int, games: int = 30) -> tuple[int, int]:
        """(team's free-throw attempts, opponents') with every home player rated ``rating``."""
        lg = generate_league(seed=4, num_teams=4)
        for p in lg.players.values():
            if p.team_id == 1:
                p.attributes.drawing_fouls = rating
        mine = theirs = 0
        for i, g in enumerate([g for g in lg.games.values() if 1 in (g.home_team_id, g.away_team_id)][:games]):
            sim = simulate_game(lg, g, random.Random(i))
            home = g.home_team_id == 1
            us, them = (sim.box_score.home, sim.box_score.away) if home else (sim.box_score.away, sim.box_score.home)
            mine += us.totals().fta
            theirs += them.totals().fta
        return mine, theirs

    def test_better_foul_drawers_get_to_the_line(self):
        lo, hi = self.fta(5), self.fta(16)
        self.assertGreater(hi[0], 1.4 * lo[0])
        self.assertAlmostEqual(hi[1] / lo[1], 1.0, delta=0.25)    # the other side's rating didn't change

    def test_slope_and_centre(self):
        self.assertAlmostEqual(DEFAULT_TUNING.foul_drawing, 0.10)
        self.assertTrue(10 < DEFAULT_TUNING.foul_drawing_centre < 13)


if __name__ == "__main__":
    unittest.main()
