"""Match highlights: key / extended / routine possessions for the viewer."""
import random
import unittest
from collections import Counter

from hoopsim import generate_league
from hoopsim.spatial.fullgame import FullGame
from hoopsim.spatial.highlights import EXTENDED, KEY, ROUTINE, annotate, classify


def poss(offense="home", start=(10, 10), end=(10, 10), events=(), period=1, clock=600.0, transition=False):
    frame = [0] * 26 + [clock]
    return {"offense": offense, "start_score": list(start), "end_score": list(end),
            "events": [[0, e, 0, 0] for e in events], "period": period, "frames": [frame],
            "transition": transition}


class ClassifyTests(unittest.TestCase):
    def level(self, p, run=None):
        return classify(p, run or {"side": None, "pts": 0})[0]

    def test_routine_miss_and_routine_two(self):
        self.assertEqual(self.level(poss(events=["Owen fires a three", "No good", "Rebound Kai"])), ROUTINE)
        self.assertEqual(self.level(poss(start=(10, 20), end=(12, 20), events=["Owen floater", "Good!"])), ROUTINE)

    def test_extended_plays(self):
        self.assertEqual(self.level(poss(start=(10, 20), end=(13, 20), events=["Owen fires a three", "Good!"])), EXTENDED)
        self.assertEqual(self.level(poss(start=(10, 20), end=(12, 20), events=["Good!"], transition=True)), EXTENDED)
        self.assertEqual(self.level(poss(events=["Kai strips Owen!"])), EXTENDED)
        self.assertEqual(self.level(poss(events=["Owen goes up at the rim... BLOCKED by Kai!"])), EXTENDED)

    def test_key_moments(self):
        self.assertEqual(self.level(poss(start=(10, 11), end=(12, 11), events=["Good!"])), KEY)       # lead change
        self.assertEqual(self.level(poss(start=(10, 12), end=(12, 12), events=["Good!"])), KEY)       # tie
        self.assertEqual(self.level(poss(start=(10, 30), end=(12, 30), events=["Owen rises for the alley-oop", "Good!"])), KEY)
        self.assertEqual(self.level(poss(start=(10, 30), end=(13, 30), events=["Good! And one!"])), KEY)
        # Close finish: last five minutes of the 4th, within eight, even a miss.
        self.assertEqual(self.level(poss(start=(100, 104), end=(100, 104), period=4, clock=90.0,
                                         events=["No good"])), KEY)
        self.assertEqual(self.level(poss(start=(100, 120), end=(100, 120), period=4, clock=90.0,
                                         events=["No good"])), ROUTINE)

    def test_runs(self):
        run = {"side": None, "pts": 0}
        levels = [classify(poss(start=(s, 40), end=(s + 2, 40), events=["Good!"]), run)[0] for s in (20, 22, 24, 26)]
        self.assertEqual(levels[-1], KEY)                      # 8-0
        self.assertEqual(run["pts"], 8)
        classify(poss(offense="away", start=(28, 40), end=(28, 42), events=["Good!"]), run)
        self.assertEqual((run["side"], run["pts"]), ("away", 2))   # the run is broken


class GameHighlightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g = FullGame(generate_league(seed=5, systems=False), 1, 5, random.Random(0), record=True)
        g.run()
        cls.data = g.to_dict()

    def test_export_carries_levels_in_sensible_numbers(self):
        P = self.data["possessions"]
        self.assertTrue(all("hl" in p and "hl_why" in p for p in P))
        c = Counter(p["hl"] for p in P)
        self.assertTrue(10 <= c[KEY] <= 0.35 * len(P), c)
        self.assertTrue(0.3 * len(P) <= c[KEY] + c[EXTENDED] <= 0.65 * len(P), c)

    def test_annotate_is_idempotent(self):
        P = [dict(p) for p in self.data["possessions"]]
        first = [(p["hl"], p["hl_why"]) for p in annotate(P)]
        self.assertEqual(first, [(p["hl"], p["hl_why"]) for p in annotate(P)])
