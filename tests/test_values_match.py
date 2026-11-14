"""Values that match the engine (README "Values that match the engine"): shots valued as
they're played, pull-up contests predicted from where the man is, training on full games
with on-policy returns and damped steps, and a check that the committed values are
converged for the engine as it is now."""
import random
import unittest

from hoopsim import generate_league
from hoopsim.spatial import court as C
from hoopsim.spatial.check_values import MARGIN, gap_table, off_options, record
from hoopsim.spatial.fullgame import FullGame
from hoopsim.spatial.params import DEFAULT_PARAMS, LEGACY_PARAMS
from hoopsim.spatial.sim import HalfCourtGame
from hoopsim.spatial.train import gaps
from hoopsim.spatial.values import FEATURES, HEURISTIC, DecisionModel, default_model, fit


def half_court(params=None, seed=5):
    league = generate_league(seed=41, systems=False)
    g = HalfCourtGame(league, 1, 2, random.Random(seed), record=False, model=HEURISTIC, params=params)
    g.play(2)
    g.offense, g.defense = g.agents["home"], g.agents["away"]
    g.guards = list(range(5))
    for i, a in enumerate(g.everyone):              # everyone far away
        place(a, -24.0 + 5.0 * i, 45.0)
    return g


def place(a, x, y, vx=0.0, vy=0.0):
    a.place(x, y)
    a.vx, a.vy = vx, vy


class SwitchTests(unittest.TestCase):
    def test_new_behaviour_is_off_in_the_legacy_engine(self):
        self.assertEqual(LEGACY_PARAMS.shot_value_whole, 0.0)
        self.assertEqual(LEGACY_PARAMS.pullup_contest, 0.0)
        self.assertEqual(LEGACY_PARAMS.dribble_contest, 1.0)
        self.assertEqual(LEGACY_PARAMS.short_make_adj, 0.0)
        self.assertEqual(DEFAULT_PARAMS.shot_value_whole, 1.0)
        self.assertEqual(DEFAULT_PARAMS.pullup_contest, 1.0)
        self.assertLess(DEFAULT_PARAMS.dribble_contest, 1.0)
        self.assertLess(DEFAULT_PARAMS.short_make_adj, 0.0)


class ShotValueTests(unittest.TestCase):
    def setUp(self):
        self.g = half_court()

    def test_a_contested_layup_counts_its_fouls_and_blocks(self):
        g = self.g
        h, d = g.offense[4], g.defense[4]
        place(h, 0.0, 7.0)
        place(d, 0.0, 5.5)                              # 1.5 ft: blocks and fouls are live
        whole = g.shot_value(h, p=h.pos, contest_d=1.5, defender=d)
        prob, z = g.make_prob(h, h.pos, 1.5, d)
        self.assertEqual(z, C.RIM_ZONE)
        fouled = g.foul_chance(h, z, 1.5)
        block = g.block_chance(h, d, z, 1.5)
        ft = g.free_throw_pct(h)
        self.assertGreater(fouled, 0.2)
        self.assertGreater(block, 0.0)
        made_fouled = prob * g.t.fouled_make
        want = ((1 - fouled) * prob * 2 + fouled * (made_fouled * (2 + ft) + (1 - made_fouled) * 2 * ft)) \
            * (1 - block)
        self.assertAlmostEqual(whole, want)
        g.p = LEGACY_PARAMS                             # make chance x points
        prob, _ = g.make_prob(h, h.pos, 1.5, d)
        self.assertAlmostEqual(g.shot_value(h, p=h.pos, contest_d=1.5, defender=d), prob * 2)

    def test_an_open_three_is_about_make_chance_times_three(self):
        g = self.g
        h, d = g.offense[1], g.defense[1]
        place(h, 0.0, 29.0)
        place(d, 0.0, 15.0)
        prob, z = g.make_prob(h, h.pos, 14.0, d)
        self.assertEqual(z, C.THREE_ZONE)
        whole = g.shot_value(h, p=h.pos, contest_d=14.0, defender=d)
        self.assertAlmostEqual(whole, 3 * prob, delta=0.03)   # a few free throws, no blocks


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.g = half_court()

    def test_a_pull_up_is_contested_less_than_a_catch(self):
        g = self.g
        h, d = g.offense[1], g.defense[1]
        place(h, 0.0, 26.0)
        place(d, 0.0, 22.5)
        open_, _ = g.make_prob(h, h.pos, 12.0, d, catch=True)
        catch, _ = g.make_prob(h, h.pos, 3.5, d, catch=True)
        pull, _ = g.make_prob(h, h.pos, 3.5, d, catch=False)
        # The man giving ground takes dribble_contest of what a closeout takes.
        self.assertAlmostEqual(open_ - g.t.catch_shoot_bonus - g.p.off_dribble_penalty - pull,
                               g.p.dribble_contest * (open_ - catch))
        g.p = LEGACY_PARAMS
        catch, _ = g.make_prob(h, h.pos, 3.5, d, catch=True)
        pull, _ = g.make_prob(h, h.pos, 3.5, d, catch=False)
        self.assertAlmostEqual(catch - pull, g.t.catch_shoot_bonus + g.p.off_dribble_penalty)

    def test_short_paint_adjustment_only_in_the_short_paint(self):
        g = self.g
        h, d = g.offense[2], g.defense[2]
        place(d, 0.0, 40.0)
        spots = {C.CLOSE_ZONE: (0.0, 15.0), C.MID_ZONE: (0.0, 21.0)}
        for z, spot in spots.items():
            self.assertEqual(C.zone(spot), z)
        new = {z: g.make_prob(h, p, 20.0, d)[0] for z, p in spots.items()}
        g.p = LEGACY_PARAMS.with_values(contest_outside=DEFAULT_PARAMS.contest_outside,
                                        contest_inside=DEFAULT_PARAMS.contest_inside)
        old = {z: g.make_prob(h, p, 20.0, d)[0] for z, p in spots.items()}
        self.assertAlmostEqual(new[C.CLOSE_ZONE] - old[C.CLOSE_ZONE], DEFAULT_PARAMS.short_make_adj)
        self.assertAlmostEqual(new[C.MID_ZONE], old[C.MID_ZONE])

    def test_holding_knows_whether_the_defence_is_set(self):
        g = self.g
        h = g.offense[0]
        place(h, 0.0, 27.0)
        g.holder, g.mode, g.now = h, "probe", 0.0
        seen = []
        original = g.model.value

        def spy(kind, feats):
            if kind == "hold":
                seen.append(feats[-1])
            return original(kind, feats)

        g.model.value = spy
        try:
            for flow in (False, True):
                g.holder, g.mode, g.flight, g.pnr, g.post = h, "probe", None, None, None
                place(h, 0.0, 27.0)
                g.set_defense = not flow
                g.decide(h)
        finally:
            del g.model.value
        self.assertEqual(seen, [0.0, 1.0])


class PullUpContestTests(unittest.TestCase):
    def test_the_man_in_front_is_on_him_at_the_pull_up(self):
        g = half_court()
        h, on = g.offense[0], g.defense[0]
        place(h, 0.0, 30.0)
        place(on, 0.0, 21.0)            # sagging 9 ft off, between him and the rim
        opt = g.pull_up_option(h)
        self.assertIsNotNone(opt)
        point = opt[1][2]
        guess = 9.0 - 0.5 + 0.02 * (h.a("speed") - on.a("speed")) * 10
        honest = g.pullup_contest(h, on, point, guess)
        self.assertLess(honest, 5.0)                    # he steps into his man: a stride off at most
        self.assertGreaterEqual(honest, min(C.dist(on.pos, point), g.p.contest_stop) - 1e-9)
        g.p = LEGACY_PARAMS
        self.assertEqual(g.pullup_contest(h, on, point, guess), guess)

    def test_never_more_room_than_the_old_guess(self):
        g = half_court()
        h, on = g.offense[1], g.defense[1]
        h.p.attributes.three_point = 16
        place(h, 0.0, 26.0)
        place(on, *C.toward(h.pos, C.RIM, 2.5))
        opt = g.step_back_option(h)
        self.assertIsNotNone(opt)
        point = opt[1][2]
        guess = 2.5 + 0.5 * C.dist(point, h.pos)
        self.assertLessEqual(g.pullup_contest(h, on, point, guess), guess)


class RecordingTests(unittest.TestCase):
    def test_decisions_before_a_random_choice_are_not_recorded(self):
        league = generate_league(seed=51)
        everything, on_policy = {}, {}
        g = HalfCourtGame(league, 1, 2, random.Random(1), record=False, explore=1.0, recorder=on_policy)
        g.play(30)
        # Exploring every time: only the last decision of each possession counts.
        self.assertLessEqual(sum(len(v) for v in on_policy.values()), 30)
        g = HalfCourtGame(league, 1, 2, random.Random(1), record=False, explore=0.0, recorder=everything)
        g.play(30)
        self.assertGreater(sum(len(v) for v in everything.values()), 60)

    def test_full_games_record_what_the_possession_scored_from_there(self):
        league = generate_league(seed=52)
        samples = {}
        game = FullGame(league, 1, 2, random.Random(4), explore=0.1, recorder=samples)
        sim = game.run()
        total = sum(len(v) for v in samples.values())
        self.assertGreater(total, 300)
        for kind, rows in samples.items():
            self.assertIn(kind, FEATURES)
            for feats, points in rows:
                self.assertEqual(len(feats), len(FEATURES[kind]))
                self.assertTrue(0 <= points <= 9 and points == int(points))
        mean = sum(y for rows in samples.values() for _, y in rows) / total
        self.assertTrue(0.8 < mean < 1.6, mean)
        self.assertGreater(sim.result.home_score + sim.result.away_score, 150)

    def test_a_damped_step_goes_part_of_the_way(self):
        rows = [([1.0, 1.0, 1.0, 0.0], 2.0)] * 200 + [([1.0, 0.5, 0.2, 1.0], 1.0)] * 200
        full = fit({"hold": rows}, prior=HEURISTIC, ridge=0.001)
        half = fit({"hold": rows}, prior=HEURISTIC, ridge=0.001, step=0.5)
        for p, f, h in zip(HEURISTIC.weights["hold"], full.weights["hold"], half.weights["hold"]):
            self.assertAlmostEqual(h, p + 0.5 * (f - p))
        self.assertAlmostEqual(gaps(full, {"hold": rows})["hold"], 0.0, places=3)


class ConvergedTests(unittest.TestCase):
    """The committed values are worth what their options score in this engine. A change
    to the engine that changes what options are worth (the reason to retrain) shows up
    here: retrain with ``python -m hoopsim.spatial.train --resume --check 64``."""

    @classmethod
    def setUpClass(cls):
        cls.model = default_model()
        cls.samples = record(cls.model, games=20, jobs=1)

    def test_committed_values_match_the_engine(self):
        self.assertEqual(self.model.meta.get("source"), "learned")
        g = gap_table(self.model, self.samples)
        detail = {k: round(v - y, 3) for k, (n, v, y) in g.items()}
        self.assertEqual(off_options(g, margin=MARGIN + 0.02), [], detail)
        # Twos against threes: the shot mix follows the difference.
        (_, v2, y2), (_, v3, y3) = g["shoot_2"], g["shoot_3"]
        self.assertLess(abs((v2 - y2) - (v3 - y3)), 0.12, detail)

    def test_stale_values_are_caught(self):
        # As the values trained before "Shots with room" were: holding the ball and
        # carrying on with a drive worth ~0.15-0.2 more than they score.
        w = {k: list(v) for k, v in self.model.weights.items()}
        w["hold"][0] += 0.2
        w["continue"][0] += 0.2
        off = off_options(gap_table(DecisionModel(w), self.samples))
        self.assertIn("hold", off)
        self.assertIn("continue", off)
        self.assertNotIn("pass", off)


if __name__ == "__main__":
    unittest.main()
