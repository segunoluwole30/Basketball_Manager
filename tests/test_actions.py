"""Animation cues in recorded replays ("actions", see sim.ACTIONS)."""
import json
import random
import unittest
import zlib
from collections import Counter

from hoopsim import generate_league
from hoopsim.spatial.season_game import simulate_spatial
from hoopsim.spatial.sim import ACTIONS


class ActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lg = generate_league(seed=1, num_teams=4)
        lg.rules.match_engine = "spatial"
        g = next(iter(lg.games.values()))
        cls.replay = json.loads(zlib.decompress(simulate_spatial(lg, g, random.Random(1), record=True).replay))
        cls.lg, cls.game = lg, g

    def test_every_possession_has_valid_actions(self):
        seen = Counter()
        for p in self.replay["possessions"]:
            self.assertIn("actions", p)
            for frame, player, action, other in p["actions"]:
                self.assertIn(action, ACTIONS)
                self.assertTrue(0 <= frame <= len(p["frames"]), (frame, len(p["frames"])))
                self.assertTrue(0 <= player < 10 and -1 <= other < 10 and other != player)
                seen[action] += 1
            self.assertEqual([a[0] for a in p["actions"]], sorted(a[0] for a in p["actions"]))
        for a in ("pass", "catch", "drive", "jumper", "jumper_release", "layup", "rebound",
                  "free_throw", "contest", "inbound"):
            self.assertGreater(seen[a], 0, a)

    def test_actions_match_the_play(self):
        """A shot's release is the frame the ball leaves his hands; a pass's catch is by the receiver."""
        shots = ("jumper_release", "layup", "dunk", "floater", "hook", "fadeaway")   # an alley-oop is never held
        checked = 0
        for p in self.replay["possessions"]:
            fr = p["frames"]
            for i, (frame, player, action, other) in enumerate(p["actions"]):
                if action in shots and 0 < frame < len(fr):
                    self.assertEqual(fr[frame - 1][5], player)        # he had it
                    self.assertEqual(fr[frame][5], -1)                # then it's in the air
                    checked += 1
                if action == "pass":
                    catch = next((a for a in p["actions"][i + 1:] if a[2] in ("catch", "intercept")), None)
                    if catch and catch[2] == "catch":
                        self.assertEqual((catch[1], catch[3]), (other, player))
        self.assertGreater(checked, 50)

    def test_recording_changes_nothing(self):
        g = self.game
        plain = simulate_spatial(self.lg, g, random.Random(1), record=False)
        self.assertEqual(plain.box_score.home.totals().pts, sum(ln["pts"] for ln in self.replay["box"]["home"]))
        self.assertEqual(self.replay["final"][0], plain.box_score.home.totals().pts)


if __name__ == "__main__":
    unittest.main()
