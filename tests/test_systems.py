"""Coaching systems (systems.py): presets, their effects in both engines, AI choices."""
import random
import statistics
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from hoopsim import generate_league, load_league, save_league
from hoopsim.engine import simulate_game
from hoopsim.manager import set_tactics
from hoopsim.models import DefensiveScheme, Game, OffensiveFocus, Tactics
from hoopsim.systems import (DEFENSE, NEUTRAL, OFFENSE, apply, defense_style, describe, fit_for_team,
                             offense_style)


def measure(league, me, opps, n, engine="statistical"):
    """Average box-score shape of team ``me`` over ``n`` games against ``opps``."""
    league.rules.match_engine = engine
    out = Counter()
    for i in range(n):
        g = Game(id=10**6 + i, season=league.season, day=1, home_team_id=me, away_team_id=opps[i % len(opps)])
        if engine == "statistical":
            sim = simulate_game(league, g, random.Random(i))
        else:
            from hoopsim.spatial.season_game import simulate_spatial
            sim = simulate_spatial(league, g, random.Random(i), record=False)
        mine, them = sim.box_score.home, sim.box_score.away
        t, o = mine.totals(), them.totals()
        out["poss"] += mine.possessions
        out["tpa"] += t.tpa; out["fga"] += t.fga; out["ast"] += t.ast; out["fgm"] += t.fgm
        out["top_fga"] += max(ln.fga for ln in mine.lines)
        out["opp_tpa"] += o.tpa; out["opp_fga"] += o.fga
    return {"poss": out["poss"] / n, "three": out["tpa"] / out["fga"], "ast": out["ast"] / out["fgm"],
            "top": out["top_fga"] / out["fga"], "opp_three": out["opp_tpa"] / out["opp_fga"]}


class PresetTests(unittest.TestCase):
    def test_apply_sets_sliders_and_scheme(self):
        t = Tactics()
        apply(t, offense="seven_seconds", defense="press")
        self.assertEqual((t.offense_system, t.defense_system), ("seven_seconds", "press"))
        self.assertEqual(t.pace, OFFENSE["seven_seconds"].sliders["pace"])
        self.assertIs(t.defensive_scheme, DefensiveScheme.PRESS)
        apply(t, offense="triangle")
        self.assertEqual(t.three_point_rate, OFFENSE["triangle"].sliders["three"])
        apply(t, defense="drop")
        self.assertIs(t.defensive_scheme, DefensiveScheme.MAN)          # drop is man with a sag
        self.assertGreater(defense_style(t).gap, 0)
        with self.assertRaises(ValueError):
            apply(t, offense="run_and_shoot")
        half = Tactics(pace=10)
        apply(half, offense="grit_grind", sliders=0.5)                   # AI: part of the way
        self.assertEqual(half.pace, 8)

    def test_every_system_is_valid_and_described(self):
        for s in list(OFFENSE.values()) + list(DEFENSE.values()):
            t = Tactics()
            apply(t, **({"offense": s.key} if s.key in OFFENSE and s in OFFENSE.values() else {"defense": s.key}))
            Tactics.from_dict(t.to_dict())                               # sliders within range
        d = describe()
        self.assertEqual([x["key"] for x in d["offense"]], list(OFFENSE))
        self.assertEqual(len(d["defense"]), 5)
        self.assertEqual(offense_style(Tactics()), NEUTRAL)

    def test_old_saves_default_to_balanced(self):
        d = Tactics().to_dict()
        del d["offense_system"], d["defense_system"]
        t = Tactics.from_dict(d)
        self.assertEqual((t.offense_system, t.defense_system), ("balanced", "man"))

    def test_manager_and_save(self):
        lg = generate_league(seed=3, num_teams=4)
        lg.user_team_id = 1
        ok, msg = set_tactics(lg, system="pace_space", defense_system="switch", pace=11)
        self.assertTrue(ok, msg)
        t = lg.teams[1].tactics
        self.assertEqual((t.offense_system, t.pace, t.offensive_focus), ("pace_space", 11, OffensiveFocus.PERIMETER))
        self.assertIn("Pace and Space", msg)
        self.assertFalse(set_tactics(lg, system="nope")[0])
        with tempfile.TemporaryDirectory() as d:
            save_league(lg, Path(d) / "x.db")
            back = load_league(Path(d) / "x.db")
        self.assertEqual(back.teams[1].tactics.offense_system, "pace_space")
        self.assertEqual(back.teams[1].tactics.defense_system, "switch")


class EffectTests(unittest.TestCase):
    """The same team, the same opponents and seeds: only the system changes."""

    @classmethod
    def setUpClass(cls):
        cls.lg = generate_league(seed=11, num_teams=12, systems=False)
        cls.opps = [t for t in cls.lg.teams if t != 1]

    def run_system(self, n=250, engine="statistical", **kw):
        t = self.lg.teams[1].tactics
        saved = t.to_dict()
        apply(t, **kw)
        try:
            return measure(self.lg, 1, self.opps, n, engine)
        finally:
            self.lg.teams[1].tactics = Tactics.from_dict(saved)

    def test_identities_on_the_statistical_engine(self):
        bal = self.run_system(offense="balanced")
        ps, tri = self.run_system(offense="pace_space"), self.run_system(offense="triangle")
        fast, slow = self.run_system(offense="seven_seconds"), self.run_system(offense="grit_grind")
        helio, motion = self.run_system(offense="heliocentric"), self.run_system(offense="motion")
        self.assertGreater(ps["three"], bal["three"] + 0.05)
        self.assertLess(tri["three"], bal["three"] - 0.03)
        self.assertGreater(fast["poss"], slow["poss"] + 6)
        # The star takes his shots, though his share levels off (engine.usage_ceiling):
        # about 23% of the team's shots against 20%.
        self.assertGreater(helio["top"], bal["top"] + 0.02)
        self.assertGreater(motion["ast"], helio["ast"] + 0.05)            # the ball moves
        # NBA-like ranges: three-point share 25-62% (a weak-shooting roster), 90-110 possessions.
        for r in (bal, ps, tri, fast, slow, helio, motion):
            self.assertTrue(0.25 < r["three"] < 0.62 and 90 < r["poss"] < 110, r)

    def test_drop_coverage_concedes_threes(self):
        man, drop = self.run_system(defense="man"), self.run_system(defense="drop")
        self.assertGreater(drop["opp_three"], man["opp_three"] + 0.015)

    def test_spatial_engine_tempo(self):
        fast = self.run_system(n=6, engine="spatial", offense="seven_seconds")
        slow = self.run_system(n=6, engine="spatial", offense="grit_grind")
        # 6 games each: 99.0 against 95.8 possessions with the values retrained in README
        # "Values that match the engine" (the steadier-leagues dampers had made it ~7).
        self.assertGreater(fast["poss"], slow["poss"] + 2.5)


class AICoachTests(unittest.TestCase):
    def test_ai_teams_get_varied_suitable_systems(self):
        seen_o, seen_d = Counter(), Counter()
        for seed in range(4):
            lg = generate_league(seed=seed, num_teams=30)
            seen_o.update(t.tactics.offense_system for t in lg.teams.values())
            seen_d.update(t.tactics.defense_system for t in lg.teams.values())
        self.assertGreaterEqual(len(seen_o), 7)
        self.assertGreaterEqual(len(seen_d), 4)
        self.assertGreater(seen_d["man"], seen_d["press"])                # presses are rare
        lg = generate_league(seed=1, num_teams=12, systems=False)
        self.assertTrue(all(t.tactics.offense_system == "balanced" for t in lg.teams.values()))

    def test_fit_ratings(self):
        lg = generate_league(seed=2, num_teams=12)
        fit = fit_for_team(lg, 1)
        self.assertEqual(set(fit["offense"]), set(OFFENSE))
        self.assertTrue(all(1 <= v <= 5 for side in fit.values() for v in side.values()))
        # The team with the best post big rates the post-up systems higher than the worst.
        from hoopsim.systems import _features
        best = max(lg.teams, key=lambda t: _features(lg, t)["post"])
        worst = min(lg.teams, key=lambda t: _features(lg, t)["post"])
        self.assertGreater(fit_for_team(lg, best)["offense"]["inside_out"],
                           fit_for_team(lg, worst)["offense"]["inside_out"])

    def test_league_files_keep_their_sliders_and_can_name_systems(self):
        from tests.test_league_file import synthetic_file
        from hoopsim.league_file import LeagueFileError, load_league_file
        data = synthetic_file()
        data["teams"][1]["offense_system"] = "triangle"
        lg = load_league_file(data)
        self.assertEqual(lg.teams[1].tactics.pace, 12)                    # the file's own pace survives
        self.assertEqual(lg.teams[2].tactics.offense_system, "triangle")
        data["teams"][1]["offense_system"] = "wishbone"
        with self.assertRaises(LeagueFileError):
            load_league_file(data)


if __name__ == "__main__":
    unittest.main()
