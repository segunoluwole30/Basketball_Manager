import argparse
import random
import tempfile
import time
import unittest
from pathlib import Path

from hoopsim import generate_league
from hoopsim.spatial import HalfCourtGame
from hoopsim.spatial import court as C
from hoopsim.spatial.export import PLACEHOLDER, build_html


class CourtTests(unittest.TestCase):
    def test_three_point_line(self):
        self.assertTrue(C.is_three((22.5, 3.0)))           # corner three (22 ft)
        self.assertFalse(C.is_three((21.5, 3.0)))          # inside the corner line
        self.assertTrue(C.is_three((0.0, 5.25 + 24.0)))    # top of the arc
        self.assertFalse(C.is_three((0.0, 5.25 + 23.0)))

    def test_zones(self):
        self.assertEqual(C.zone((0.0, 7.0)), C.RIM_ZONE)
        self.assertEqual(C.zone((0.0, 15.0)), C.CLOSE_ZONE)
        self.assertEqual(C.zone((0.0, 22.0)), C.MID_ZONE)
        self.assertEqual(C.zone((0.0, 30.0)), C.THREE_ZONE)

    def test_clamp_keeps_players_on_court(self):
        x, y = C.clamp((40.0, -5.0))
        self.assertLessEqual(abs(x), C.WIDTH / 2)
        self.assertGreaterEqual(y, 0)


class SimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.league = generate_league(seed=41)
        cls.game = HalfCourtGame(cls.league, 1, 2, random.Random(41), record=True)
        cls.game.play(60)

    def test_every_possession_ends(self):
        results = {p["result"] for p in self.game.possessions}
        self.assertNotIn("stalled", results)
        self.assertTrue(results <= {"score", "drb", "tov", "ft"})
        self.assertEqual(len(self.game.possessions), 60)

    def test_offence_alternates(self):
        sides = [p["offense"] for p in self.game.possessions]
        self.assertEqual(sides[:4], ["home", "away", "home", "away"])

    def test_frames_are_well_formed_and_on_court(self):
        for p in self.game.possessions:
            self.assertGreater(len(p["frames"]), 5)
            for f in p["frames"]:
                self.assertEqual(len(f), 6 + 20)
                self.assertTrue(-1 <= f[5] <= 9)
                for n in range(10):
                    x, y = f[6 + 2 * n] / 10, f[7 + 2 * n] / 10
                    self.assertLessEqual(abs(x), C.WIDTH / 2)
                    self.assertTrue(0 <= y <= C.HALF_LENGTH)

    def test_score_adds_up(self):
        g = self.game
        box_points = {side: sum(g.box[id(a)]["pts"] for a in g.agents[side]) for side in ("home", "away")}
        self.assertEqual(box_points, g.score)
        for p in g.possessions:
            self.assertEqual(p["events"][-1][2:], p["end_score"])   # last call carries the final score

    def test_scores_never_go_down(self):
        last = (0, 0)
        for p in self.game.possessions:
            for e in p["events"]:
                self.assertGreaterEqual(e[2], last[0])
                self.assertGreaterEqual(e[3], last[1])
                last = (e[2], e[3])

    def test_deterministic(self):
        def run():
            g = HalfCourtGame(self.league, 3, 4, random.Random(7), record=True)
            g.play(10)
            return g.possessions
        self.assertEqual(run(), run())

    def test_fast_enough_for_whole_seasons(self):
        g = HalfCourtGame(self.league, 5, 6, random.Random(1), record=False)
        start = time.time()
        g.play(200)                                  # about one game
        self.assertLess(time.time() - start, 3.0)


class ExportTests(unittest.TestCase):
    def test_page_embeds_the_game(self):
        league = generate_league(seed=42)
        g = HalfCourtGame(league, 1, 2, random.Random(2))
        g.play(4)
        data = g.to_dict()
        data["home"]["name"] = "Test </script> Team"                 # must not break out of the script tag
        page = build_html(data)
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertNotIn(PLACEHOLDER, page)
        self.assertNotIn("Test </script>", page)
        self.assertIn("<title>Hoopsim Court View</title>", page)
        fragment = build_html(data, standalone=False)
        self.assertNotIn("<!doctype", fragment.lower())

    def test_watch_command(self):
        from hoopsim.__main__ import cmd_watch
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "w.html"
            cmd_watch(argparse.Namespace(seed=3, possessions=6, home=None, away=None, load=None,
                                         out=str(out), no_open=True))
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 10_000)


if __name__ == "__main__":
    unittest.main()


class InsideGameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from hoopsim.spatial.values import HEURISTIC
        cls.league = generate_league(seed=61)
        cls.games = []
        for home, away in ((1, 2), (3, 4), (5, 6)):
            g = HalfCourtGame(cls.league, home, away, random.Random(home), record=True, model=HEURISTIC)
            g.play(150)
            cls.games.append(g)
        cls.events = [e[1] for g in cls.games for p in g.possessions for e in p["events"]]

    def total(self, key):
        return sum(g.stats[key] for g in self.games)

    def test_inside_actions_happen(self):
        self.assertGreater(self.total("cuts"), 20)
        self.assertGreater(self.total("post_ups"), 3)
        rim_sources = {k for g in self.games for k in g.stats if k.startswith("src_rim_")}
        self.assertTrue({"src_rim_drive", "src_rim_cut"} <= rim_sources, rim_sources)

    def test_no_stalled_possessions(self):
        self.assertEqual(self.total("end_stalled"), 0)

    def test_lobs_go_to_athletic_bigs(self):
        names = {a.p.name: a for g in self.games for a in g.everyone}
        receivers = [e.split(" throws the lob to ")[1] for e in self.events if " throws the lob to " in e]
        for name in receivers:
            a = names[name]
            self.assertGreaterEqual(a.height, 6.75)
            self.assertGreaterEqual(a.a("vertical"), 12)

    def test_alley_oops_finish_at_the_rim(self):
        for g in self.games:
            self.assertEqual(g.stats["src_close_lob"] + g.stats["src_mid_lob"] + g.stats["src_three_lob"], 0)

    def test_only_capable_bigs_post_up(self):
        names = {a.p.name: a for g in self.games for a in g.everyone}
        posters = [e[:-len(" posts up")] for e in self.events if e.endswith(" posts up")]
        self.assertTrue(posters)
        for name in posters:
            a = names[name]
            self.assertGreaterEqual(a.slot, 3)
            self.assertGreaterEqual((a.a("close_range") + a.a("strength")) / 2, 11)


def frozen_scene(seed: int = 5):
    """A game paused mid-possession: offence spread along y=30, each defender 4.5 ft
    off his man toward the rim, the second player holding the ball."""
    league = generate_league(seed=41)
    g = HalfCourtGame(league, 1, 2, random.Random(seed), record=False)
    g.play(1)
    g.result, g.points, g.flight = None, 0, None
    h = g.offense[1]
    g.holder, g.mode, g.mode_since, g.now, g.shot_clock = h, "probe", 0.0, 10.0, 12.0
    g.pnr = g.post = g.cutter = None
    g.next_decision = 99.0
    for i, a in enumerate(g.offense):
        a.place(0.0, 30.0) if a is h else a.place(-20 + 10 * i, 30.0)
    for i, d in enumerate(g.defense):
        d.place(*C.toward(g.offense[i].pos, C.RIM, 4.5))
        d.next_think = 0.0
    return g, h


class DefenceTests(unittest.TestCase):
    def test_jumpers_are_contested_at_release(self):
        g, h = frozen_scene()
        g.catch_time = g.now - 0.2
        guard = g.guarded_by(h)
        start = C.dist(guard.pos, h.pos)
        g.take_shot(h, moving=False)
        self.assertEqual(g.mode, "release")            # the shooting motion takes time...
        self.assertIsNone(g.flight)
        steps = 0
        while g.flight is None and steps < 10:
            g.step()
            steps += 1
        self.assertEqual(g.flight.kind, "shot")         # ...then it goes up
        self.assertGreaterEqual(steps, 2)
        self.assertLess(C.dist(guard.pos, h.pos), start - 1.0)   # and his man closed out meanwhile

    def test_closeouts_depend_on_the_shooter(self):
        g, h = frozen_scene()
        shooter, non_shooter = sorted(g.offense, key=lambda a: a.a("three_point"))[::-1][0::4]
        self.assertGreater(shooter.a("three_point"), non_shooter.a("three_point"))
        self.assertLess(g.closeout_gap(shooter), g.closeout_gap(non_shooter))

    def test_help_defenders_dig_at_drives(self):
        g, h = frozen_scene()
        on = g.guarded_by(h)
        helper = next(d for d in g.defense if d is not on)
        def strips(near: bool, trials: int = 300) -> int:
            n = 0
            for _ in range(trials):
                g.result = None
                helper.place(h.x + (1.0 if near else 9.0), h.y)
                helper.stuck_until = -1.0
                n += g.dig(h)
            return n
        self.assertEqual(strips(near=False), 0)
        self.assertGreater(strips(near=True), 3)

    def test_errant_passes_under_pressure(self):
        g, h = frozen_scene()
        on = g.guarded_by(h)
        on.stuck_until = -1.0
        on.place(h.x, h.y - 1.5)
        pressured = g.errant_chance(h, 20.0, lob=False)
        on.place(h.x, h.y - 8.0)
        free = g.errant_chance(h, 20.0, lob=False)
        self.assertGreater(pressured, free)
        self.assertGreater(g.errant_chance(h, 35.0, lob=False), free)    # long passes are riskier
        self.assertLess(free, 0.05)

    def test_charges_need_a_defender_set_in_front(self):
        g, h = frozen_scene()
        on = g.guarded_by(h)
        g.mode = "drive"
        def charges(in_front: bool, trials: int = 1500) -> int:
            n = 0
            for _ in range(trials):
                g.result, g.charge_tried = None, []
                spot = C.toward(h.pos, C.RIM, 1.5) if in_front else C.toward(h.pos, C.RIM, -1.5)
                on.place(*spot)
                n += g.charge_check(h)
            return n
        self.assertEqual(charges(in_front=False), 0)
        self.assertGreater(charges(in_front=True), 5)

    def test_travels_on_drives_happen_rarely(self):
        g, _ = frozen_scene()
        g.mode = "drive"
        h = min(g.offense, key=lambda a: a.a("ball_handling"))
        n = 0
        for _ in range(10000):                       # 1,000 seconds of driving
            g.result = None
            n += g.travel_check(h)
        self.assertGreater(n, 3)
        self.assertLess(n, 200)


class ContestAndMovementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.league = generate_league(seed=81)
        cls.stats = None
        from collections import Counter
        total = Counter()
        for home, away in ((1, 2), (3, 4)):
            g = HalfCourtGame(cls.league, home, away, random.Random(home), record=False)
            g.play(600)
            total += g.stats
        cls.stats = total

    def test_threes_are_rarely_contested_body_to_body(self):
        s = self.stats
        threes = sum(s[f"contest_three_{b}"] for b in ("tight", "close", "open", "wide"))
        self.assertGreater(threes, 200)
        # NBA: ~2% of threes have a defender within 2 ft; allow some slack.
        self.assertLess(s["contest_three_tight"] / threes, 0.2)
        self.assertGreater(s["contest_three_wide"] / threes, 0.08)

    def test_three_point_accuracy_near_nba(self):
        s = self.stats
        self.assertTrue(0.31 <= s["fgm_three"] / s["fga_three"] <= 0.42)

    def test_closeouts_stop_short_of_shooters(self):
        g = HalfCourtGame(self.league, 1, 2, random.Random(1), record=False)
        g.play(1)
        shooters = sorted((a for a in g.everyone), key=lambda a: a.a("three_point"))
        best, worst = shooters[-1], shooters[0]
        best.place(19.0, 22.0)
        worst.place(-19.0, 22.0)
        self.assertGreaterEqual(g.closeout_gap(best), 2.8)                 # hand up, not body to body
        self.assertGreater(g.closeout_gap(worst), g.closeout_gap(best))    # more room for poor shooters

    def test_drives_draw_a_stunt(self):
        g = HalfCourtGame(self.league, 1, 2, random.Random(2), record=False)
        g.play_possession("home")
        h = g.offense[0]
        h.place(0.0, 15.0)
        g.holder, g.mode = h, "drive"
        for i, a in enumerate(g.offense[1:], 1):
            a.place(*[(19.0, 22.0), (-19.0, 22.0), (-22.6, 4.5), (5.0, 4.0)][i - 1])
        for d, m in zip(g.defense, g.offense):
            d.place(m.x * 0.8, m.y * 0.9)
        stunter = g.choose_stunter(exclude=None)
        self.assertIsNotNone(stunter)
        self.assertGreaterEqual(C.dist_to_rim(g.man_of(stunter).pos), 18)
