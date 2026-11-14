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

    def test_facing_is_recorded(self):
        """Which way each player faces, a character per player per frame (sim.pack_facing)."""
        import math
        from hoopsim.spatial.sim import FACING_CODE, RIM, TURN_RATE
        self.assertEqual(len(FACING_CODE), 62)
        self.assertEqual(len(set(FACING_CODE)), 62)
        to_ball, to_rim, turns = [], [], []
        for p in self.replay["possessions"]:
            fr, fc = p["frames"], p["facing"]
            self.assertEqual(len(fc), 10 * len(fr))
            ang = [[FACING_CODE.index(fc[10 * i + n]) * 6 for n in range(10)] for i in range(len(fr))]
            self.assertTrue(all(0 <= a < 360 for row in ang for a in row))
            diff = lambda a, b: abs((a - b + 180) % 360 - 180)
            for i in range(1, len(fr)):
                if not 0.05 < fr[i][0] - fr[i - 1][0] < 0.15:
                    continue                                        # (a join between parts of the possession)
                turns += [diff(ang[i][n], ang[i - 1][n]) for n in range(10)]
                h = fr[i][5]
                if h < 0 or i < 8:
                    continue
                xy = lambda j, n: (fr[j][6 + 2 * n] / 10, fr[j][7 + 2 * n] / 10)
                still = lambda n: math.dist(xy(i, n), xy(i - 8, n)) < 0.4 and fr[i - 8][5] == h
                off = range(0, 5) if h < 5 else range(5, 10)
                for n in off:
                    if not still(n):
                        continue
                    x, y = xy(i, n)
                    if n == h:
                        to_rim.append(diff(ang[i][n], math.degrees(math.atan2(RIM[0] - x, RIM[1] - y))))
                    else:
                        bx, by = fr[i][2] / 10, fr[i][3] / 10
                        to_ball.append(diff(ang[i][n], math.degrees(math.atan2(bx - x, by - y))))
        med = lambda v: sorted(v)[len(v) // 2]
        self.assertGreater(len(to_ball), 200)
        self.assertLess(med(to_ball), 12)                           # standing off the ball: he watches it
        self.assertLess(med(to_rim), 12)                            # standing with it: he faces the rim
        # nobody spins: a frame's turn is within the turn rate (plus the 6-degree steps)
        self.assertLess(sorted(turns)[int(0.999 * len(turns))], math.degrees(TURN_RATE) * 0.1 + 13)

    def test_the_ball_flies_true(self):
        """A shot is a parabola under gravity; a miss strikes the rim, a make the middle."""
        import math
        from hoopsim.spatial.sim import GRAVITY, MISS_AT, RIM, Flight, gravity_apex, shot_time
        for d, lo, hi in ((23.75, 14.5, 16.5), (15.0, 12.5, 14.5), (3.0, 10.0, 10.6)):
            t = shot_time(d)
            f = Flight("shot", (0.0, RIM[1] + d), RIM, 0.0, t, apex=gravity_apex(t), z0=7.5, z1=10.0)
            zs = [f.position(t * k / 200)[2] for k in range(201)]
            self.assertTrue(lo < max(zs) < hi, (d, max(zs)))           # a three peaks near 15 ft, a lay-up just over the rim
            dt = t / 200                                                # it falls at g all the way
            acc = [(zs[k + 1] - 2 * zs[k] + zs[k - 1]) / dt ** 2 for k in range(1, 200)]
            self.assertTrue(all(abs(a + GRAVITY) < 0.5 for a in acc), d)
        self.assertGreater(shot_time(23.75), shot_time(15.0))
        self.assertTrue(1.15 < shot_time(23.75) < 1.4)
        made = missed = 0
        for p in self.replay["possessions"]:
            fr = p["frames"]
            for frame, player, action, other in p["actions"]:
                if action not in ("jumper_release", "layup", "floater") or frame >= len(fr) or fr[frame][5] >= 0:
                    continue
                j = frame
                while j + 1 < len(fr) and fr[j + 1][5] < 0 and not (fr[j][4] >= fr[j + 1][4] and fr[j + 1][4] <= 100 and j > frame + 2):
                    j += 1
                end = fr[min(j + 1, len(fr) - 1)]
                off = math.hypot(end[2] / 10 - RIM[0], end[3] / 10 - RIM[1])
                if end[4] != 100:
                    continue                                            # (not sampled at the rim)
                if off < 0.15:
                    made += 1
                elif abs(off - MISS_AT) < 0.2:
                    missed += 1
        self.assertGreater(made, 10)
        self.assertGreater(missed, 10)                                  # misses arrive on the rim, not through the middle

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
