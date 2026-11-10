import json
import random
import tempfile
import unittest
from pathlib import Path

from hoopsim import generate_league
from hoopsim.spatial import HalfCourtGame
from hoopsim.spatial.values import (
    FEATURES, HEURISTIC, DecisionModel, default_model, fit, fit_kind, hold_curve,
)


class FitTests(unittest.TestCase):
    def test_least_squares_recovers_known_weights(self):
        rng = random.Random(0)
        true = [0.5, -1.0, 2.0]
        samples = []
        for _ in range(2000):
            x = [1.0, rng.uniform(-1, 1), rng.uniform(-1, 1)]
            samples.append((x, sum(w * v for w, v in zip(true, x)) + rng.gauss(0, 0.05)))
        w = fit_kind(samples, prior=[0, 0, 0], ridge=0.001)
        for got, want in zip(w, true):
            self.assertAlmostEqual(got, want, places=2)

    def test_ridge_pulls_toward_prior_when_data_is_thin(self):
        w = fit_kind([([1.0, 0.0], 10.0)], prior=[0.0, 0.0], ridge=1000.0)
        self.assertLess(abs(w[0]), 0.1)

    def test_missing_kind_keeps_prior(self):
        model = fit({"hold": [([1.0, 1.0, 1.0], 1.0)] * 50}, prior=HEURISTIC)
        self.assertEqual(model.weights["pnr"], HEURISTIC.weights["pnr"])


class ModelTests(unittest.TestCase):
    def test_heuristic_reproduces_formula(self):
        feats = [1.0, hold_curve(18.0), 18.0 / 24]
        self.assertAlmostEqual(HEURISTIC.value("hold", feats), hold_curve(18.0))
        self.assertAlmostEqual(HEURISTIC.value("shoot", [1.0, 1.1, 1.0, 0.0, 0.0]), 1.1)

    def test_save_load_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "v.json"
            HEURISTIC.save(path)
            loaded = DecisionModel.load(path)
            self.assertEqual(loaded.weights, HEURISTIC.weights)
            data = json.loads(path.read_text())
            data["features"]["hold"] = ["bias"]               # stale file from older features
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                DecisionModel.load(path)

    def test_wrong_shape_rejected(self):
        bad = dict(HEURISTIC.weights)
        bad["hold"] = [1.0]
        with self.assertRaises(ValueError):
            DecisionModel(bad)

    def test_default_model_loads_trained_values(self):
        model = default_model()
        self.assertEqual(set(model.weights), set(FEATURES))


class RecordingTests(unittest.TestCase):
    def test_decisions_are_recorded_with_possession_points(self):
        league = generate_league(seed=51)
        samples = {}
        g = HalfCourtGame(league, 1, 2, random.Random(1), record=False, explore=0.2, recorder=samples)
        g.play(40)
        self.assertTrue(samples)
        total = sum(len(v) for v in samples.values())
        self.assertGreater(total, 40)
        for kind, rows in samples.items():
            self.assertIn(kind, FEATURES)
            for feats, points in rows:
                self.assertEqual(len(feats), len(FEATURES[kind]))
                self.assertIn(points, (0.0, 1.0, 2.0, 3.0, 4.0))

    def test_models_change_behaviour(self):
        league = generate_league(seed=52)
        def threes(model):
            g = HalfCourtGame(league, 1, 2, random.Random(3), record=False, model=model)
            g.play(300)
            return g.stats["fga_three"]
        shooters = DecisionModel({**HEURISTIC.weights, "shoot": [0.6, 1.0, 0.5, 0.0, 0.0]})
        self.assertGreater(threes(shooters), threes(HEURISTIC))


if __name__ == "__main__":
    unittest.main()
