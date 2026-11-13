"""The last realism-audit flags (README "Last audit flags"): foul drawing in the spatial
engine, very tight shots, distance run and teleports."""
import math
import random
import unittest
from unittest import mock

from hoopsim import generate_league
from hoopsim.spatial import audit
from hoopsim.spatial import court as C
from hoopsim.spatial import transition as T
from hoopsim.spatial.fullgame import FullGame
from hoopsim.spatial.params import DEFAULT_PARAMS, LEGACY_PARAMS
from hoopsim.spatial.season_game import _model
from hoopsim.spatial.sim import HalfCourtGame
from hoopsim.spatial.values import HEURISTIC


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


_BODIES = {}


def audit_bodies(games=6, seed=8):
    """The audit's body measurements on full games recorded as it records them."""
    if (games, seed) not in _BODIES:
        poss, _, _, _ = audit.play(games, seed)
        _BODIES[(games, seed)] = audit.bodies(poss)
    return _BODIES[(games, seed)]


class FoulDrawingTests(unittest.TestCase):
    def setUp(self):
        self.g = half_court()

    def test_off_in_the_legacy_engine(self):
        self.assertEqual(LEGACY_PARAMS.foul_drawing, 0.0)
        self.assertAlmostEqual(DEFAULT_PARAMS.foul_drawing, 0.10)        # as the statistical engine
        self.assertTrue(11.5 < DEFAULT_PARAMS.foul_drawing_centre < 13.0)

    def test_each_point_multiplies_the_odds_as_in_the_statistical_engine(self):
        g = self.g
        h = g.offense[1]
        rating = h.p.attributes.drawing_fouls
        try:
            h.p.attributes.drawing_fouls = 8
            low = g.foul_chance(h, C.CLOSE_ZONE, 3.0, share=0.22)
            h.p.attributes.drawing_fouls = 14
            high = g.foul_chance(h, C.CLOSE_ZONE, 3.0, share=0.22)
            self.assertAlmostEqual(high / low, math.exp(0.6))
            centre = DEFAULT_PARAMS.foul_drawing_centre
            g.p = DEFAULT_PARAMS.with_values(foul_drawing=0.0)
            plain = g.foul_chance(h, C.CLOSE_ZONE, 3.0, share=0.22)
            g.p = DEFAULT_PARAMS
            self.assertAlmostEqual(high, plain * math.exp(0.1 * (14 - centre)))
            h.p.attributes.drawing_fouls = 20                    # edited extremes: capped
            self.assertLessEqual(g.foul_chance(h, C.RIM_ZONE, 0.5, share=0.45), 0.6)
        finally:
            h.p.attributes.drawing_fouls = rating

    def test_whole_shot_values_see_it(self):
        # A contested layup is worth more to a good foul drawer: the same foul_chance
        # take_shot uses goes into the whole-shot value.
        g = self.g
        h, d = g.offense[4], g.defense[4]
        place(h, 0.0, 7.0)
        place(d, 0.0, 5.0)
        rating = h.p.attributes.drawing_fouls
        try:
            values = []
            for r in (6, 17):
                h.p.attributes.drawing_fouls = r
                values.append(g.shot_value(h, p=h.pos, contest_d=2.0, defender=d))
            self.assertGreater(values[1], values[0] + 0.05)
            g.p = DEFAULT_PARAMS.with_values(foul_drawing=0.0)
            plain = []
            for r in (6, 17):
                h.p.attributes.drawing_fouls = r
                plain.append(g.shot_value(h, p=h.pos, contest_d=2.0, defender=d))
            self.assertAlmostEqual(plain[0], plain[1])
        finally:
            h.p.attributes.drawing_fouls = rating
            g.p = DEFAULT_PARAMS

    def test_good_foul_drawers_get_to_the_line_in_full_games(self):
        def fta(rating, games=5):
            lg = generate_league(seed=4, num_teams=4)
            ids = sorted(lg.teams)
            for p in lg.players.values():
                if p.team_id == ids[0]:
                    p.attributes.drawing_fouls = rating
            mine = 0
            for i in range(games):
                sim = FullGame(lg, ids[0], ids[1 + i % 3], random.Random(i), model=_model()).run()
                mine += sim.box_score.home.totals().fta
            return mine
        self.assertGreater(fta(17), 1.3 * fta(6))


class VeryTightTests(unittest.TestCase):
    def test_off_in_the_legacy_engine(self):
        self.assertEqual(LEGACY_PARAMS.closeout_paint, 0.0)
        self.assertGreater(DEFAULT_PARAMS.closeout_paint, 3.0)

    def test_a_catch_in_the_paint_is_walled_up_a_step_off(self):
        g = half_court()
        man = g.offense[4]
        place(man, 3.0, 9.0)                              # the dunker spot
        self.assertLess(C.dist_to_rim(man.pos), DEFAULT_PARAMS.closeout_paint_range)
        self.assertEqual(g.closeout_gap(man), DEFAULT_PARAMS.closeout_paint)
        place(man, 0.0, 19.0)                             # the free-throw line: as before
        self.assertEqual(g.closeout_gap(man), 2.5)
        g.p = LEGACY_PARAMS
        place(man, 3.0, 9.0)
        self.assertEqual(g.closeout_gap(man), 2.5)

    def test_fewer_finishes_with_the_man_in_his_body(self):
        # Very tight (0-2 ft) on the audit's measure, 24 games of seed 3: 9.5% with the
        # paint closeout, 11.1% without. The audit's 48 games: 11.8 / 11.7% (seeds 7 / 8)
        # before, 9.9 / 9.4% after (NBA 5-11%).
        from tests.test_shot_mix import shot_mix
        on = shot_mix(DEFAULT_PARAMS)["tight"]
        off = shot_mix(DEFAULT_PARAMS.with_values(closeout_paint=0.0))["tight"]
        self.assertLess(on, off - 0.012)
        self.assertLess(on, 0.105)


class TeleportTests(unittest.TestCase):
    def test_free_throw_walks_start_from_the_last_frame_shown(self):
        # A foul in the penalty used to start the walk to the lane after that tick's
        # movement, without showing it: the first walk frame carried two ticks of
        # running (35 ft/s for a sprinter). Whenever the walk starts, everyone must be
        # where the last frame shows him.
        seen = []
        original = HalfCourtGame.walk_to

        def walk_to(self, spots, max_seconds=8.0):
            if not self.frames:
                return original(self, spots, max_seconds)
            last = self.frames[-1]
            seen.append(max(math.dist((a.x, a.y), (last[6 + 2 * i] / 10, last[7 + 2 * i] / 10))
                            for i, a in enumerate(self.everyone)))
            return original(self, spots, max_seconds)

        with mock.patch.object(HalfCourtGame, "walk_to", walk_to):
            league = generate_league(seed=8, num_teams=12)
            ids = sorted(league.teams)
            for i in range(3):
                FullGame(league, ids[2 * i], ids[2 * i + 1], random.Random(8000 + i), record=True,
                         model=_model()).run()
        self.assertGreater(len(seen), 20)
        self.assertLess(max(seen), 0.1)          # positions are stored to 0.1 ft

    def test_the_inbounder_after_a_make_steps_in_without_jumping(self):
        jumps = []
        original = T.OpenCourt.run

        def run(self):
            res = original(self)
            if self.how == "score" and type(self) is T.OpenCourt:
                fr = res.frames
                for a, b in zip(fr, fr[1:]):
                    for n in range(10):
                        jumps.append(math.dist((a[6 + 2 * n], a[7 + 2 * n]), (b[6 + 2 * n], b[7 + 2 * n])) / 10)
            return res

        with mock.patch.object(T.OpenCourt, "run", run):
            league = generate_league(seed=6, num_teams=12)
            ids = sorted(league.teams)
            FullGame(league, ids[0], ids[1], random.Random(6000), record=True, model=_model()).run()
        self.assertGreater(len(jumps), 1000)
        self.assertLess(max(jumps), 2.5)         # a sprint is ~2.2 ft a tick

    def test_no_teleports_in_full_games(self):
        b = audit_bodies()
        self.assertEqual(b["teleports_per_game"], 0)


class DistanceTests(unittest.TestCase):
    def test_nobody_runs_under_the_rim_on_a_walk_up(self):
        # Only a push sends the deepest defender to the rim; on a walk-up everyone picks
        # up a man (they used to run under the basket and back out again).
        deepest = {True: [], False: []}
        original = T.OpenCourt.run

        def run(self):
            res = original(self)
            if type(self) is T.OpenCourt and self.how != "steal":
                dfn = [self.g.index[id(d)] for d in self.defense]
                deep = min(f[7 + 2 * j] / 10 for f in res.frames for j in dfn)
                deepest[self.push].append(deep)
            return res

        with mock.patch.object(T.OpenCourt, "run", run):
            league = generate_league(seed=6, num_teams=12)
            ids = sorted(league.teams)
            FullGame(league, ids[2], ids[3], random.Random(6001), record=True, model=_model()).run()
        self.assertGreater(len(deepest[False]), 40)
        self.assertGreater(sum(d > 10.0 for d in deepest[False]) / len(deepest[False]), 0.9)
        if deepest[True]:                           # a push still sends one back to the rim
            self.assertLess(sorted(deepest[True])[len(deepest[True]) // 2],
                            sorted(deepest[False])[len(deepest[False]) // 2])

    def test_open_court_runners_brake_into_their_spots(self):
        self.assertGreater(T.DEFAULT_OPEN_TUNING.brake, 0.0)
        g = half_court()
        a = g.defense[0]
        place(a, 0.0, 60.0, 0.0, -20.0)             # sprinting back
        a.target((0.0, 30.0))
        a.tx, a.ty = 0.0, 30.0
        low = 99.0
        for k in range(60):
            a.move(0.1, k * 0.1, 1.4, 1.0, T.DEFAULT_OPEN_TUNING.brake, T.DEFAULT_OPEN_TUNING.brake_range)
            low = min(low, a.y)
        self.assertGreater(low, 29.0)                # stops at his spot instead of running past
        self.assertAlmostEqual(a.y, 30.0, delta=0.3)

    def test_distance_per_player(self):
        # NBA 3.1-3.6 miles per 48 minutes (was 3.73 on the audit's seed 7).
        b = audit_bodies()
        per48 = b["distance_ft"] / b["player_seconds"] * 48 * 60 / audit.FT_PER_MILE
        self.assertLess(per48, 3.6)
        self.assertGreater(per48, 3.1)


if __name__ == "__main__":
    unittest.main()
