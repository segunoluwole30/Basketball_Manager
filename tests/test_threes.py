"""Three-point volume mechanisms: shooting bigs and guards in generation, transition
lanes behind the arc, early-offence spots, off-the-dribble jumpers, relocation."""
import random
import statistics
import unittest
from datetime import date

from hoopsim import generate_league, generate_player
from hoopsim.models import Position
from hoopsim.spatial import HalfCourtGame
from hoopsim.spatial import court as C
from hoopsim.spatial.params import DEFAULT_PARAMS, LEGACY_PARAMS
from hoopsim.spatial.transition import CORNER_LANE, TRAIL_LANE, OpenCourt


class GenerationTests(unittest.TestCase):
    def players(self, pos, n=300):
        rng = random.Random(11)
        return [generate_player(rng, i, date(2026, 10, 1), position=pos, age=27, potential=110)
                for i in range(n)]

    def test_point_guards_shoot_like_shooting_guards(self):
        pg = statistics.mean(p.attributes.three_point for p in self.players(Position.PG))
        sg = statistics.mean(p.attributes.three_point for p in self.players(Position.SG))
        self.assertGreater(pg, sg - 1.5)

    def test_stretch_bigs(self):
        # About half of power forwards and a third of centres are drawn as shooters,
        # so bigs split into shooters and non-shooters rather than all being poor.
        pf = [p.attributes.three_point for p in self.players(Position.PF)]
        c = [p.attributes.three_point for p in self.players(Position.C)]
        self.assertGreater(sum(v >= 12 for v in pf) / len(pf), 0.3)
        self.assertGreater(sum(v >= 10 for v in c) / len(c), 0.15)
        self.assertGreater(sum(v <= 6 for v in c) / len(c), 0.25)
        # The shooting comes at the expense of the rest: ability still hits its target.
        for p in self.players(Position.C, 60):
            self.assertLessEqual(abs(p.current_ability - 110), 12)


def open_court(seed=2, how="steal"):
    league = generate_league(seed=5)
    g = HalfCourtGame(league, 1, 2, random.Random(seed), record=False)
    g.play_possession("home")                 # leaves players spread around the floor
    return g, OpenCourt(g, "away", how, g.agents["away"][0], 600.0, random.Random(seed), push=True)


class TransitionLaneTests(unittest.TestCase):
    def test_lane_spots_are_threes(self):
        self.assertTrue(C.is_three(CORNER_LANE))
        self.assertTrue(C.is_three((-CORNER_LANE[0], CORNER_LANE[1])))
        self.assertTrue(C.is_three(TRAIL_LANE))

    def test_wings_run_to_the_corners_and_shooters_trail_to_the_arc(self):
        g, oc = open_court()
        corners = [a for a in oc.offense if id(a) in oc.lanes and oc.lanes[id(a)][1] == CORNER_LANE[1]]
        self.assertEqual(len(corners), 2)
        for a in oc.offense:
            lane = oc.lanes.get(id(a))
            if lane is None or a.slot < 3 or lane[1] < 10:
                continue                                 # handler, wings, the rim runner
            self.assertEqual(C.is_three(lane), a.a("three_point") >= oc.t.trail_shooter)

    def test_early_offence_keeps_the_runners_on_their_lanes(self):
        g, oc = open_court()
        oc.result.advantage = "none"
        for a in oc.offense:                             # everyone arrived on his lane
            lane = oc.lanes.get(id(a))
            if lane is not None:
                a.place(*lane)
        oc.holder.place(0.0, 32.0)
        for i, d in enumerate(oc.defense):               # the defence got back: no advantage
            d.place(-8.0 + 4.0 * i, 12.0)
        oc.hand_over()
        self.assertEqual(oc.result.advantage, "none")
        spots = oc.result.start.get("spots", {})
        self.assertGreaterEqual(len(spots), 2)
        for a, spot in spots.items():
            self.assertTrue(C.is_three(spot))
            self.assertIsNot(a, oc.holder)
        g.play_possession("away", oc.result.start)       # the half court takes the spots
        self.assertTrue(g.result)


class OffDribbleTests(unittest.TestCase):
    def game(self, params=None):
        league = generate_league(seed=5)
        g = HalfCourtGame(league, 1, 2, random.Random(0), record=False, params=params)
        g.play_possession("home")
        return g

    def test_off_the_dribble_penalty(self):
        g = self.game()
        h = g.offense[1]
        catch = g.make_prob(h, (0.0, 30.0), contest_d=6.0, catch=True)[0]
        dribble = g.make_prob(h, (0.0, 30.0), contest_d=6.0, catch=False)[0]
        # NBA tracking: catch-and-shoot ~37%, pull-up ~34% from three.
        self.assertAlmostEqual(catch - dribble, g.t.catch_shoot_bonus + DEFAULT_PARAMS.off_dribble_penalty)
        self.assertLess(catch - dribble, 0.04)
        legacy = self.game(LEGACY_PARAMS)
        self.assertAlmostEqual(legacy.make_prob(h, (0.0, 30.0), contest_d=6.0, catch=False)[0],
                               legacy.make_prob(h, (0.0, 30.0), contest_d=6.0, catch=True)[0] - 0.055)

    def test_pull_up_valued_as_it_is_shot(self):
        g = self.game()
        h = g.offense[0]
        h.place(0.0, 5.25 + 24.0)
        g.holder = h
        on = g.guarded_by(h)
        on.place(0.0, 5.25 + 19.0)
        kind, action, feats = g.pull_up_option(h)
        point = action[2]
        contest = max(0.0, C.dist(on.pos, h.pos) - 0.5 + 0.02 * (h.a("speed") - on.a("speed")) * 10)
        # take_shot shoots a pull-up set (not on the move): the value must match.
        self.assertAlmostEqual(feats[1], g.shot_value(h, p=point, contest_d=contest, defender=on,
                                                      catch=False))


class RelocationTests(unittest.TestCase):
    def setup(self, params=None):
        league = generate_league(seed=5)
        g = HalfCourtGame(league, 1, 2, random.Random(0), record=False, params=params)
        g.play_possession("home")
        g.holder = g.offense[0]
        g.mode = "drive"
        shooter = max(g.offense[1:], key=lambda a: a.a("three_point"))
        shooter.spot = (19.0, 22.0)
        d = g.guarded_by(shooter)
        d.helping = True
        d.place(8.0, 10.0)                               # gone to help, toward the paint
        return g, shooter, d

    def test_shooter_slides_along_the_arc_away_from_his_helper(self):
        g, a, d = self.setup()
        if a.a("three_point") < g.p.relocate_shooter:
            self.skipTest("no shooter on this team")
        spot = g.relocate_spot(a, d)
        self.assertIsNotNone(spot)
        self.assertTrue(C.is_three(spot))
        self.assertGreater(C.dist(spot, d.pos), C.dist(a.spot, d.pos))

    def test_no_relocation_without_a_drive_or_when_off(self):
        g, a, d = self.setup()
        g.mode = "probe"
        self.assertIsNone(g.relocate_spot(a, d))
        g, a, d = self.setup(LEGACY_PARAMS)
        self.assertIsNone(g.relocate_spot(a, d))


if __name__ == "__main__":
    unittest.main()
