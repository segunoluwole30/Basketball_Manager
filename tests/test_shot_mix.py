"""The shot mix (README "Shots with room"): finishes that don't run into bodies,
threes above the break, early offence."""
import math
import random
import unittest
from unittest import mock

from hoopsim import generate_league
from hoopsim.spatial import court as C
from hoopsim.spatial.fullgame import FullGame
from hoopsim.spatial.params import DEFAULT_PARAMS, LEGACY_PARAMS
from hoopsim.spatial.season_game import _model
from hoopsim.spatial.sim import FOUR_HIGH, HalfCourtGame
from hoopsim.spatial.values import HEURISTIC


def shot_mix(params, games=24, seed=3):
    """Full games as ``python -m hoopsim.spatial.audit`` plays them: every field-goal
    attempt's closest defender (from where the shooter is), shot clock and corner flag."""
    shots = []
    original = HalfCourtGame.count_attempt

    def logged(self, h, z):
        _, d = self.nearest_defender(h.pos)
        corner = z == C.THREE_ZONE and abs(h.x) >= C.CORNER_THREE_X - 0.5 and h.y <= C.CORNER_THREE_Y
        shots.append((d, self.shot_clock, z == C.THREE_ZONE, corner))
        return original(self, h, z)

    with mock.patch.object(HalfCourtGame, "count_attempt", logged):
        for i in range(games):
            league = generate_league(seed=seed + i // 6, num_teams=12)
            ids = sorted(league.teams)
            FullGame(league, ids[(2 * i) % 12], ids[(2 * i + 1) % 12], random.Random(seed * 1000 + i),
                     model=_model(), params=params).run()
    n = len(shots)
    threes = [s for s in shots if s[2]]
    return {"tight": sum(s[0] < 2 for s in shots) / n,
            "early": sum(s[1] > 18 for s in shots) / n,
            "corner": sum(s[3] for s in threes) / len(threes)}


def half_court(params=None, seed=5):
    league = generate_league(seed=41, systems=False)
    g = HalfCourtGame(league, 1, 2, random.Random(seed), record=False, model=HEURISTIC, params=params)
    g.play(2)
    return g


def place(a, x, y, vx=0.0, vy=0.0):
    a.place(x, y)
    a.vx, a.vy = vx, vy


class MechanismTests(unittest.TestCase):
    def setUp(self):
        self.g = half_court()
        g = self.g
        g.offense, g.defense = g.agents["home"], g.agents["away"]
        g.guards = list(range(5))
        for i, a in enumerate(g.everyone):              # everyone far away
            place(a, -24.0 + 5.0 * i, 45.0)

    def test_a_driver_gathers_before_running_into_a_man(self):
        g = self.g
        h, d = g.offense[0], g.defense[1]
        place(h, 0.0, 12.0, 0.0, -12.0)               # downhill at 12 ft/s
        place(d, 0.0, 9.6)                              # set 2.4 ft in front of him
        self.assertTrue(g.contact_ahead(h))
        place(d, 0.0, 14.4)                             # behind him: no
        self.assertFalse(g.contact_ahead(h))
        place(d, 2.4, 12.0)                             # beside him: no
        self.assertFalse(g.contact_ahead(h))
        place(d, 0.0, 10.3)                             # already on him: too late to gather
        self.assertFalse(g.contact_ahead(h))
        place(d, 0.0, 9.6)
        g.p = LEGACY_PARAMS                             # off in the legacy engine
        self.assertFalse(g.contact_ahead(h))

    def test_a_pull_up_counts_the_length_of_his_stop(self):
        g = self.g
        h, d = g.offense[0], g.defense[1]
        place(h, 0.0, 22.0, 0.0, -9.0)
        place(d, 0.0, 17.8)                             # 4.2 ft ahead: room for a step...
        self.assertFalse(g.contact_ahead(h))
        self.assertTrue(g.contact_ahead(h, plant=True))  # ... but a jumper still slides ~1.3 ft stopping

    def test_angled_gather_never_ends_in_a_body(self):
        g = self.g
        h = g.offense[4]
        place(h, 3.0, 14.0)
        g.holder, g.mode, g.catch_time, g.catch_source = h, "probe", g.now, "cut"
        place(g.defense[4], 1.0, 9.0)                   # sitting on the straight line to the rim
        spot = g.finish_point(h)
        self.assertGreaterEqual(min(C.dist(spot, d.pos) for d in g.defense), 1.6)
        self.assertLess(C.dist_to_rim(spot), C.dist_to_rim(h.pos))
        g.p = LEGACY_PARAMS                             # the old gather: straight at the rim
        self.assertAlmostEqual(C.dist_to_rim(g.finish_point(h)), C.dist_to_rim(h.pos) - 5.0)
        self.assertLess(C.dist(g.finish_point(h), g.defense[4].pos), 1.6)   # (through him)

    def test_a_driver_heads_for_the_open_side_of_the_rim(self):
        g = self.g
        h = g.offense[0]
        place(h, 0.0, 12.0, 0.0, -10.0)
        h.target((2.5, 6.0))
        place(g.defense[4], 2.0, 7.5)                   # the help is on the right
        g.open_side(h)
        self.assertLess(h.tx, 0)

    def test_step_back_three_is_above_the_break(self):
        g = self.g
        h, on = g.offense[1], g.defense[1]
        h.p.attributes.three_point = 16
        for x, y in ((0.0, 26.0), (12.0, 22.0), (-15.0, 18.0)):
            place(h, x, y)
            place(on, *C.toward(h.pos, C.RIM, 3.0))
            opt = g.step_back_option(h)
            self.assertIsNotNone(opt, (x, y))
            point = opt[1][2]
            self.assertTrue(C.is_three(point))
            self.assertGreater(point[1], C.CORNER_THREE_Y)
            self.assertGreater(C.dist(point, on.pos), C.dist(h.pos, on.pos))
        h.p.attributes.three_point = 8                  # not a shooter: no step-back
        self.assertIsNone(g.step_back_option(h))

    def test_a_shooting_big_pops_above_the_break(self):
        g = self.g
        h, scr = g.offense[0], g.offense[4]
        scr.p.attributes.three_point = 19
        for x in (-8.0, 0.0, 8.0):
            place(h, x, 27.0)
            place(scr, x + 2.0, 25.0)
            spot = g.pop_spot(h, scr)
            if spot is None:                            # he rolls some of the time
                continue
            self.assertTrue(C.is_three(spot))
            self.assertGreater(spot[1], C.CORNER_THREE_Y)
        scr.p.attributes.three_point = 8
        self.assertIsNone(g.pop_spot(h, scr))

    def test_four_high_spots_are_all_above_the_break(self):
        for x, y in FOUR_HIGH:
            self.assertTrue(C.is_three((x, y)))
            self.assertGreater(y, C.CORNER_THREE_Y)
        pairs = [C.dist(a, b) for i, a in enumerate(FOUR_HIGH) for b in FOUR_HIGH[i + 1:]]
        self.assertGreater(min(pairs), 12.0)            # spaced like the four-out set

    def test_contests_are_spread_around_the_stop(self):
        g = self.g
        g.mode, g.release = "release", {"at": 3.3}
        stops = set()
        for d in g.defense:
            for at in (1.0, 2.0, 3.0, 4.0, 5.0):
                g.release = {"at": at}
                s = g.contest_stop(d)
                self.assertLessEqual(abs(s - DEFAULT_PARAMS.contest_stop), DEFAULT_PARAMS.contest_spread + 1e-9)
                stops.add(round(s, 2))
        self.assertGreater(len(stops), 10)


class ShotMixTests(unittest.TestCase):
    """Full games, measured as the audit does. Effect sizes were measured on 24
    games per run for seeds 3 and 11 (each run against the same games with the
    change switched off)."""

    @classmethod
    def setUpClass(cls):
        cls.base = shot_mix(DEFAULT_PARAMS)

    def test_fewer_shots_through_contact(self):
        # Very tight (0-2 ft): 8.6% / 10.0% (seeds 3 / 11) with gathers, angled finishes
        # and planted jumpers, 11.8% / 12.9% without (NBA 5-11%).
        off = shot_mix(DEFAULT_PARAMS.with_values(gather_contact=0.0, finish_room=0.0, release_stop=0.0))
        self.assertLess(self.base["tight"], off["tight"] - 0.015)
        self.assertLess(self.base["tight"], 0.11)

    def test_early_offence_shoots_early(self):
        # Shot clock 24-18: 15.1% / 15.6% with early offence, 11.5% / 11.5% without
        # (NBA 14-24%). 13.7% on seed 3 with the values retrained in README "Values that
        # match the engine" (the audit's 48 games: 14.6%).
        off = shot_mix(DEFAULT_PARAMS.with_values(early_offense=0.0))
        self.assertGreater(self.base["early"], off["early"] + 0.02)
        self.assertGreater(self.base["early"], 0.13)

    def test_fewer_corner_threes(self):
        # Corner share of threes: 21.9% / 25.4% with four-high sets, step-backs and
        # pick-and-pops, 26.9% / 30.2% without (NBA 20-28%).
        off = shot_mix(DEFAULT_PARAMS.with_values(four_high=0.0, step_back=0.0, pop_shooter=0.0))
        self.assertLess(self.base["corner"], off["corner"] - 0.02)
        self.assertLess(self.base["corner"], 0.28)


if __name__ == "__main__":
    unittest.main()
