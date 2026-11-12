import contextlib
import hashlib
import io
import json
import random
import tempfile
import unittest
from pathlib import Path

from hoopsim import generate_league
from hoopsim.spatial import HalfCourtGame
from hoopsim.spatial import calibrate
from hoopsim.spatial.params import DEFAULT_PARAMS, LEGACY_PARAMS, SEARCH_SPACE, SpatialParams
from hoopsim.spatial.values import HEURISTIC


def play(params, seed=5, possessions=40):
    league = generate_league(seed=41, systems=False)
    g = HalfCourtGame(league, 1, 2, random.Random(seed), record=True, model=HEURISTIC, params=params)
    g.play(possessions)
    return g


def fingerprint(g) -> str:
    return hashlib.sha256(json.dumps(g.possessions).encode()).hexdigest()[:16]


class ParamsTests(unittest.TestCase):
    def test_no_params_means_defaults(self):
        self.assertEqual(play(None).possessions, play(DEFAULT_PARAMS).possessions)
        self.assertEqual(play(None).possessions, play(SpatialParams()).possessions)

    def test_legacy_params_reproduce_the_engine_before_calibration(self):
        # Pinned before the constants moved into SpatialParams: with the old values
        # (and the hand-written decision values) the engine plays exactly as it did.
        # Re-pinned when player generation changed (stretch bigs, shooting point
        # guards), which changes league 41 itself; with the old generate.py the
        # legacy physics still gives the original pin a5958e8ac648389b / 28-18.
        g = play(LEGACY_PARAMS)
        self.assertEqual(fingerprint(g), "5ba83331e1e1b1c7")
        self.assertEqual(g.score, {"home": 29, "away": 22})

    def test_params_change_behaviour(self):
        loose = DEFAULT_PARAMS.with_values(help_trigger=10.0, closeout_speed=0.7, contest_outside=0.5)
        self.assertNotEqual(play(DEFAULT_PARAMS).possessions, play(loose).possessions)

    def test_round_trip(self):
        p = DEFAULT_PARAMS.with_values(help_depth=4.25, errant_pass=0.011)
        self.assertEqual(SpatialParams.from_dict(p.to_dict()), p)
        self.assertEqual(SpatialParams.from_dict(json.loads(json.dumps(p.to_dict()))), p)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "p.json"
            p.save(path)
            self.assertEqual(SpatialParams.load(path), p)
        with self.assertRaises(ValueError):
            SpatialParams.from_dict({"no_such_knob": 1.0})

    def test_search_space_is_valid(self):
        names = set(DEFAULT_PARAMS.to_dict())
        for k, (lo, hi) in SEARCH_SPACE.items():
            self.assertIn(k, names)
            self.assertLess(lo, hi)
            for p in (DEFAULT_PARAMS, LEGACY_PARAMS):
                self.assertTrue(lo <= getattr(p, k) <= hi, (k, getattr(p, k)))

    def test_unit_cube_round_trip(self):
        x = calibrate.to_unit(DEFAULT_PARAMS)
        back = calibrate.from_unit(x)
        for k in SEARCH_SPACE:
            self.assertAlmostEqual(getattr(back, k), getattr(DEFAULT_PARAMS, k), places=3)

    def test_latin_hypercube_covers_each_stratum(self):
        design = calibrate.latin_hypercube(10, 3, random.Random(1))
        for d in range(3):
            self.assertEqual(sorted(int(row[d] * 10) for row in design), list(range(10)))


class CalibrateSmokeTests(unittest.TestCase):
    def test_tiny_search_runs_logs_and_resumes(self):
        with tempfile.TemporaryDirectory() as d:
            log, out = Path(d) / "c.jsonl", Path(d) / "best.json"
            args = ["--log", str(log), "--out", str(out), "--workers", "1", "--rounds", "1",
                    "--train-possessions", "60", "--possessions", "60", "--noise", "1",
                    "--samples", "1", "--generations", "1"]
            with contextlib.redirect_stdout(io.StringIO()):
                result = calibrate.main(args)
            recs = [json.loads(line) for line in log.read_text().splitlines()]
            evals = [r for r in recs if r["phase"] != "state"]
            self.assertTrue({"noise", "sample", "refine"} <= {r["phase"] for r in evals})
            for r in evals:
                self.assertGreaterEqual(r["score"], 0.0)
                self.assertIn("points_per_possession", r["metrics"])
            self.assertEqual(SpatialParams.load(out), result["best"])
            # Resuming with the same budget re-uses every logged evaluation.
            with contextlib.redirect_stdout(io.StringIO()):
                calibrate.main(args + ["--resume"])
            self.assertEqual(len(log.read_text().splitlines()), len(recs))


if __name__ == "__main__":
    unittest.main()


class FullGameObjectiveTests(unittest.TestCase):
    def test_score_full_is_zero_on_target_and_grows_off_it(self):
        from hoopsim.spatial.measure import FULLGAME_TARGETS
        on = {k: t for k, (t, _) in FULLGAME_TARGETS.items()}
        self.assertEqual(calibrate.score_full(on), 0.0)
        self.assertEqual(calibrate.within_full(on), len(FULLGAME_TARGETS))
        off = dict(on, pace=on["pace"] + 2 * FULLGAME_TARGETS["pace"][1])
        self.assertGreater(calibrate.score_full(off), 0.0)
        self.assertEqual(calibrate.within_full(off), len(FULLGAME_TARGETS) - 1)

    def test_fullgame_evaluation_runs(self):
        job = {"id": "t", "phase": "test", "params": DEFAULT_PARAMS.to_dict(), "seed": 1,
               "warm": str(calibrate.DEFAULT_PATH), "rounds": 0, "train_possessions": 0,
               "possessions": 0, "objective": "fullgame", "games": 3}
        rec = calibrate.evaluate(job)
        for k in ("pace", "points_per_100", "three_share", "efg", "orb_pct"):
            self.assertIn(k, rec["metrics"])
        self.assertGreater(rec["metrics"]["pace"], 80)
        self.assertGreaterEqual(rec["score"], 0.0)
