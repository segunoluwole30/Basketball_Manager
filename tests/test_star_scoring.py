"""Star scoring (README "Star scoring"): shot shares level off and the go-to scorer
draws the fouls, on both engines."""
import random
import unittest
from dataclasses import replace

from hoopsim import Attributes, generate_league
from hoopsim.engine import DEFAULT_TUNING, simulate_game, usage_ceiling
from hoopsim.models import Game
from hoopsim.spatial import HalfCourtGame
from hoopsim.spatial.params import DEFAULT_PARAMS, LEGACY_PARAMS

SCORING = ("finishing", "close_range", "mid_range", "three_point", "ball_handling", "basketball_iq")


def make_star(lg, team_id):
    """The team's best starter, given elite scoring ratings."""
    team = lg.teams[team_id]
    star = max(team.tactics.starters, key=lambda pid: lg.players[pid].current_ability)
    p = lg.players[star]
    p.attributes = Attributes(**{**p.attributes.to_dict(), **{k: 20 for k in SCORING}})
    return star


class UsageCeilingTests(unittest.TestCase):
    def test_shares_below_the_knee_are_untouched(self):
        w = [1.0, 1.1, 0.9, 1.0, 1.0]
        self.assertEqual(usage_ceiling(w, 0.23, 0.36), [x / sum(w) for x in w])

    def test_a_dominant_share_levels_off_and_the_rest_goes_to_teammates(self):
        w = [8.0, 1.0, 1.0, 1.0, 1.0]                        # two-thirds of the shots, raw
        s = usage_ceiling(w, 0.23, 0.36)
        self.assertAlmostEqual(sum(s), 1.0)
        self.assertLess(s[0], 0.36)
        self.assertGreater(s[0], 0.30)
        self.assertTrue(all(abs(x - s[1]) < 1e-12 for x in s[1:]))   # shared out evenly
        # Diminishing returns: more raw weight still means more shots, but less and less.
        shares = [usage_ceiling([k, 1, 1, 1, 1], 0.23, 0.36)[0] for k in (2, 4, 8, 16)]
        self.assertEqual(shares, sorted(shares))
        self.assertLess(shares[3] - shares[2], shares[1] - shares[0])

    def test_role_players_pick_up_as_much_as_the_second_option(self):
        w = [4.0, 1.6, 1.2, 1.0, 1.0]
        raw = [x / sum(w) for x in w]
        s = usage_ceiling(w, 0.22, 0.35)
        gains = [b - a for a, b in zip(raw, s)]
        self.assertLess(gains[0], 0)
        for g in gains[2:]:
            self.assertAlmostEqual(g, gains[1])
        self.assertAlmostEqual(sum(s), 1.0)

    def test_nobody_is_lifted_past_the_knee(self):
        w = [10.0, 4.4, 4.4, 1.0, 1.0]                        # the 2nd and 3rd sit just under it
        raw = [x / sum(w) for x in w]
        self.assertLess(raw[1], 0.22)
        s = usage_ceiling(w, 0.22, 0.35)
        self.assertAlmostEqual(s[1], 0.22)
        self.assertAlmostEqual(s[2], 0.22)
        self.assertGreater(s[3] - raw[3], s[1] - raw[1])      # the rest share what they couldn't take
        self.assertAlmostEqual(sum(s), 1.0)

    def test_continuous_at_the_knee(self):
        def top(k):
            return usage_ceiling([k, 1, 1, 1, 1], 0.23, 0.36)[0]
        k = 0.23 * 4 / 0.77                                   # raw share exactly at the knee
        self.assertAlmostEqual(top(k * 0.999), top(k * 1.001), places=3)

    def test_ceiling_at_or_below_the_knee_turns_it_off(self):
        w = [8.0, 1.0, 1.0, 1.0, 1.0]
        self.assertEqual(usage_ceiling(w, 0.23, 0.0), [x / 12 for x in w])

    def test_order_is_kept(self):
        rng = random.Random(3)
        for _ in range(500):
            w = [rng.lognormvariate(0, 1) for _ in range(5)]
            s = usage_ceiling(w, 0.22, 0.35)
            self.assertAlmostEqual(sum(s), 1.0)
            for i in range(5):
                for j in range(5):
                    if w[i] < w[j]:
                        self.assertLessEqual(s[i], s[j] + 1e-12)


class StarInGamesTests(unittest.TestCase):
    """One team's best starter made an elite scorer; same games with and without the ceiling."""

    @classmethod
    def setUpClass(cls):
        lg = generate_league(seed=21, num_teams=4, systems=False)
        cls.lg, cls.star = lg, make_star(lg, 1)
        cls.on = cls.play(DEFAULT_TUNING)
        cls.off = cls.play(replace(DEFAULT_TUNING, usage_ceiling=0.0, foul_draw=0.0))

    @classmethod
    def play(cls, tuning, n=80):
        """Star's share of the team's plays (FGA + 0.44 FTA) while he's on the floor,
        and FTA per FGA for him and for his teammates."""
        mine = {"plays": 0.0, "fga": 0, "fta": 0, "sec": 0.0}
        team = {"plays": 0.0, "fga": 0, "fta": 0, "sec": 0.0}
        for i in range(n):
            g = Game(id=10**6 + i, season=cls.lg.season, day=1, home_team_id=1, away_team_id=2 + i % 3)
            box = simulate_game(cls.lg, g, random.Random(i), tuning=tuning).box_score.home
            for line in box.lines:
                d = mine if line.player_id == cls.star else team
                d["plays"] += line.fga + 0.44 * line.fta
                d["fga"] += line.fga
                d["fta"] += line.fta
                d["sec"] += line.seconds
        # Team plays per second of one player's court time, so the share is per on-court time.
        team_rate = (mine["plays"] + team["plays"]) / ((mine["sec"] + team["sec"]) / 5)
        return {"share": mine["plays"] / mine["sec"] / team_rate,
                "ftr": mine["fta"] / mine["fga"], "mates_ftr": team["fta"] / team["fga"]}

    def test_star_share_is_capped_near_nba_levels(self):
        # NBA: the highest usage rates are 33-37%; without the ceiling he takes far more.
        self.assertLess(self.on["share"], 0.40)
        self.assertGreater(self.on["share"], 0.27)
        self.assertGreater(self.off["share"], self.on["share"] + 0.04)

    def test_star_draws_more_fouls(self):
        # NBA: top scorers shoot about 0.40 free throws per field-goal attempt, the league 0.25.
        self.assertGreater(self.on["ftr"], self.on["mates_ftr"] * 1.15)
        self.assertGreater(self.on["ftr"], self.off["ftr"])


class SpatialUsageTests(unittest.TestCase):
    def setUp(self):
        self.lg = generate_league(seed=21, num_teams=4, systems=False)
        self.star = make_star(self.lg, 1)

    def game(self, params=None, seed=0):
        return HalfCourtGame(self.lg, 1, 2, random.Random(seed), record=False, params=params)

    def test_legacy_params_switch_it_off(self):
        self.assertEqual(LEGACY_PARAMS.usage_penalty, 0.0)
        self.assertEqual(LEGACY_PARAMS.foul_draw, 0.0)
        self.assertGreater(DEFAULT_PARAMS.usage_penalty, 0.0)

    def test_share_starts_from_a_fifth_and_follows_his_shots(self):
        g = self.game()
        g.play_possession("home")
        a = next(x for x in g.agents["home"] if x.p.id == self.star)
        g.shots_taken.clear()
        g.shots_on_floor.clear()
        self.assertAlmostEqual(g.usage_share(a), 0.2)
        g.shots_taken[id(a)] += 30
        g.shots_on_floor[id(a)] += 60
        self.assertAlmostEqual(g.usage_share(a), (30 + 2) / (60 + 10))

    def test_bias_costs_his_shots_and_passes_to_him_only(self):
        g = self.game()
        g.play_possession("home")
        g.side = "home"
        star = next(x for x in g.agents["home"] if x.p.id == self.star)
        mate = next(x for x in g.agents["home"] if x is not star)
        g.shots_taken[id(star)], g.shots_on_floor[id(star)] = 40, 90     # ~41% of the shots
        g.shots_taken[id(mate)], g.shots_on_floor[id(mate)] = 10, 90
        g.shot_clock = 10.0
        self.assertLess(g.usage_bias(star, "shoot", ("shoot", "three")), -0.2)
        self.assertLess(g.usage_bias(mate, "pass", ("pass", star)), -0.2)
        self.assertEqual(g.usage_bias(star, "drive", ("drive",)), 0.0)
        self.assertEqual(g.usage_bias(mate, "shoot", ("shoot", "three")), 0.0)
        self.assertEqual(g.usage_bias(star, "pass", ("pass", mate)), 0.0)
        g.shouts["home"] = {"ball_to": star}    # the coach wants him to have it
        self.assertEqual(g.usage_bias(mate, "pass", ("pass", star)), 0.0)
        g.shouts["home"] = {}
        g.shot_clock = 20.0                     # early offence: the defence isn't set yet
        self.assertEqual(g.usage_bias(star, "shoot", ("shoot", "three")), 0.0)
        off = self.game(params=replace(DEFAULT_PARAMS, usage_penalty=0.0))
        self.assertEqual(off.usage_bias(star, "shoot", ("shoot", "three")), 0.0)

    def test_star_share_levels_off_in_play(self):
        def share(params):
            g = self.game(params=params, seed=3)
            g.play(240)
            a = next(x for x in g.agents["home"] if x.p.id == self.star)
            return g.shots_taken[id(a)] / max(1, g.shots_on_floor[id(a)])
        on = share(DEFAULT_PARAMS)
        off = share(replace(DEFAULT_PARAMS, usage_penalty=0.0, foul_draw=0.0))
        self.assertLess(on, off - 0.03)
        self.assertLess(on, 0.42)


if __name__ == "__main__":
    unittest.main()
