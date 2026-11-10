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
