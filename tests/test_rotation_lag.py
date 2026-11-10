"""Rotation lag: a defence pulled out of position falls further behind with each pass."""
import random
import unittest

from hoopsim import generate_league
from hoopsim.spatial import HalfCourtGame
from hoopsim.spatial.params import DEFAULT_PARAMS
from hoopsim.spatial.values import HEURISTIC


def game(**params):
    league = generate_league(seed=41)
    g = HalfCourtGame(league, 1, 2, random.Random(3), model=HEURISTIC,
                      params=DEFAULT_PARAMS.with_values(**params))
    g.play_possession("home")          # sets up agents; we then arrange them by hand
    return g


class RotationLagTests(unittest.TestCase):
    def setUp(self):
        self.g = game(rotation_lag=0.6)
        self.mate = self.g.offense[2]
        self.mate.place(-22.0, 6.0)                 # corner
        self.guard = self.g.guarded_by(self.mate)
        for d in self.g.defense:
            d.helping = False

    def test_pass_to_a_sagging_mans_receiver_scrambles_the_defence(self):
        self.guard.place(-4.0, 8.0)                 # sagged into the paint
        self.g.scramble = 0.0
        self.assertEqual(self.g.scramble_after(self.mate), 1.0)
        self.g.scramble = 2.5
        self.assertEqual(self.g.scramble_after(self.mate), self.g.p.scramble_max)

    def test_pass_against_a_set_defence_lets_it_reset(self):
        self.guard.place(-19.0, 7.0)                # right on him
        self.g.scramble = 2.0
        self.assertEqual(self.g.scramble_after(self.mate), 1.0)
        self.g.scramble = 0.0
        self.assertEqual(self.g.scramble_after(self.mate), 0.0)

    def test_helper_counts_as_out_of_position(self):
        self.guard.place(-19.0, 7.0)
        self.guard.helping = True
        self.g.scramble = 0.0
        self.assertEqual(self.g.scramble_after(self.mate), 1.0)

    def test_scramble_recovers_over_time(self):
        g = game(rotation_lag=0.6, scramble_decay=0.5)
        g.scramble, g.result = 2.0, None
        g.mode, g.holder = "setup", g.offense[0]
        g.next_decision = g.now + 10.0               # keep the handler waiting
        for _ in range(10):                          # one second
            g.step()
        self.assertAlmostEqual(g.scramble, 1.5, places=6)

    def test_scrambled_receiver_is_predicted_more_open(self):
        h = self.g.offense[0]
        h.place(0.0, 26.0)
        self.guard.place(-12.0, 10.0)
        # Same geometry with lag: the receiver's man is predicted to read the pass later.
        self.g.scramble = 2.0
        lagged = self.g.pass_features(h, self.mate)
        self.g.scramble = 0.0
        fresh = self.g.pass_features(h, self.mate)
        self.assertGreater(lagged[-1], fresh[-1])             # the scramble feature
        self.assertGreaterEqual(lagged[1], fresh[1])          # catch-and-shoot value


if __name__ == "__main__":
    unittest.main()
