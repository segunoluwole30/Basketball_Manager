import math
import random
import statistics
import tempfile
import unittest
from pathlib import Path

from hoopsim import Attributes, Tendencies, generate_league, load_league, save_league
from hoopsim.engine import simulate_game
from hoopsim.manager import choose_team, set_player_tendencies
from hoopsim.models import DefensiveScheme, OffensiveFocus, Player
from hoopsim.play import IO, Game
from hoopsim.spatial import HalfCourtGame


def with_tendency(player: Player, **kw) -> None:
    player.tendencies = Tendencies(**{**player.tendencies.to_dict(), **kw})


class ModelTests(unittest.TestCase):
    def test_from_attributes_is_neutral_for_average_player(self):
        t = Tendencies.from_attributes(Attributes(**{n: 9 for n in Attributes.names()}))
        for v in t.to_dict().values():
            self.assertTrue(8 <= v <= 11, t)

    def test_generated_tendencies_follow_skills(self):
        league = generate_league(seed=71)
        ps = list(league.players.values())
        xs = [p.attributes.three_point for p in ps]
        ys = [p.tendencies.three_point for p in ps]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        corr = (sum((x - mx) * (y - my) for x, y in zip(xs, ys))
                / math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)))
        self.assertGreater(corr, 0.5)
        self.assertLess(corr, 0.95)                         # personality, not a copy

    def test_hidden_attributes_still_vary(self):
        # Regression: adding Tendencies once detached @dataclass from HiddenAttributes,
        # silently giving every player 10s.
        league = generate_league(seed=77)
        for name in ("consistency", "clutch", "injury_proneness", "work_ethic"):
            values = {getattr(p.hidden, name) for p in league.players.values()}
            self.assertGreater(len(values), 5, name)

    def test_saved_and_old_saves(self):
        league = generate_league(seed=72)
        p = next(iter(league.players.values()))
        d = p.to_dict()
        self.assertEqual(Player.from_dict(d).tendencies, p.tendencies)
        del d["tendencies"]                                 # a save from before tendencies existed
        derived = Player.from_dict(d).tendencies
        self.assertEqual(derived, Tendencies.from_attributes(p.attributes))


class SeasonEngineTests(unittest.TestCase):
    def test_three_point_tendency_changes_shot_mix(self):
        league = generate_league(seed=73)
        game = league.games[1]
        shooter = league.players[league.teams[game.home_team_id].tactics.starters[1]]

        def threes(level):
            with_tendency(shooter, three_point=level)
            rng = random.Random(4)
            tpa = 0
            for _ in range(40):
                box = simulate_game(league, game, rng).box_score.home
                tpa += next(l.tpa for l in box.lines if l.player_id == shooter.id)
            return tpa

        self.assertGreater(threes(20), threes(1) * 1.3)


class SpatialTests(unittest.TestCase):
    def run_game(self, setup, n=800, seed=5, league_seed=74):
        league = generate_league(seed=league_seed)
        setup(league)
        g = HalfCourtGame(league, 1, 2, random.Random(seed), record=False)
        g.play(n)
        return g

    def home(self, g, stat):
        return sum(g.box[id(a)][stat] for a in g.agents["home"])

    def test_perimeter_vs_inside_focus(self):
        def focus(f, rate):
            def setup(lg):
                lg.teams[1].tactics.offensive_focus = f
                lg.teams[1].tactics.three_point_rate = rate
            return setup
        out = self.run_game(focus(OffensiveFocus.PERIMETER, 20))
        inside = self.run_game(focus(OffensiveFocus.INSIDE, 1))
        share = lambda g: self.home(g, "tpa") / max(1, self.home(g, "fga"))
        self.assertGreater(share(out), share(inside) + 0.1)
        self.assertGreater(inside.stats["post_ups"], out.stats["post_ups"])

    def test_press_forces_turnovers(self):
        def scheme(s):
            def setup(lg):
                lg.teams[1].tactics.defensive_scheme = s
            return setup
        # A pressing defence multiplies the opponent's turnover risk, so test against a
        # team that handles the ball enough to show it (league 7). One 2,400-possession
        # game gives a press/man ratio anywhere from 1.2 to 1.7 (the offence adapts by
        # passing less), so pool three games per scheme: ~1.4 expected, sd ~0.1.
        away_tov = lambda g: sum(g.box[id(a)]["tov"] for a in g.agents["away"])
        def tov(s):
            return sum(away_tov(self.run_game(scheme(s), n=2400, seed=seed, league_seed=7))
                       for seed in (5, 6, 7))
        self.assertGreater(tov(DefensiveScheme.PRESS), tov(DefensiveScheme.MAN) * 1.15)

    def test_mid_range_tendency_creates_mid_range_shots(self):
        def mids(level):
            def setup(lg):
                for pid in lg.teams[1].tactics.starters:
                    with_tendency(lg.players[pid], mid_range=level, three_point=8)
            return setup
        lovers = self.run_game(mids(20))
        haters = self.run_game(mids(1))
        self.assertGreater(lovers.stats["fga_mid"], haters.stats["fga_mid"] + 10)

    def test_style_bias_signs(self):
        league = generate_league(seed=75)
        g = HalfCourtGame(league, 1, 2, random.Random(1), record=False)
        g.play(1)
        h = g.agents["home"][0]
        g.side = "home"
        with_tendency(h.p, three_point=20, post_up=1)
        self.assertGreater(g.style_bias(h, "shoot", ("shoot", "three")), 0.1)
        self.assertLess(g.style_bias(h, "post", ("post",)), -0.1)


class InstructionTests(unittest.TestCase):
    def setUp(self):
        self.league = generate_league(seed=76)
        self.uid = min(self.league.teams)
        choose_team(self.league, self.uid)
        self.mine = self.league.roster(self.uid)[0]

    def test_set_and_validate(self):
        ok, msg = set_player_tendencies(self.league, self.mine.id, three=18, post=3)
        self.assertTrue(ok, msg)
        self.assertEqual((self.mine.tendencies.three_point, self.mine.tendencies.post_up), (18, 3))
        self.assertFalse(set_player_tendencies(self.league, self.mine.id, three=25)[0])
        self.assertFalse(set_player_tendencies(self.league, self.mine.id, swagger=10)[0])
        other = next(p for p in self.league.players.values() if p.team_id not in (None, self.uid))
        self.assertFalse(set_player_tendencies(self.league, other.id, three=18)[0])

    def test_instructions_persist_and_cli(self):
        script = iter([f"instruct {self.mine.id} mid=17 cut=4", f"player {self.mine.id}",
                       "instruct 1", "quit", "n"])
        out = []
        Game(self.league, IO(ask=lambda _: next(script), say=out.append), random.Random(1)).run()
        text = "\n".join(out)
        self.assertIn("mid-range 17 (often)", text)
        self.assertIn("Usage: instruct", text)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "s.db"
            save_league(self.league, path)
            self.assertEqual(load_league(path).players[self.mine.id].tendencies.mid_range, 17)


if __name__ == "__main__":
    unittest.main()
