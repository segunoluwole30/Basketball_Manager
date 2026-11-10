"""Seasons played on the spatial match engine."""
import random
import tempfile
import unittest
from pathlib import Path

from hoopsim import generate_league, load_league, save_league
from hoopsim.engine import play_game
from hoopsim.models import LeagueRules
from hoopsim.season import play_full_season


def short_spatial_league(seed: int = 4, days: int = 6):
    league = generate_league(seed=seed, num_teams=4)
    league.rules.match_engine = "spatial"
    league.rules.playoff_series_length = 1
    for gid in [g.id for g in league.games.values() if g.day > days]:
        del league.games[gid]                     # a few days of games is enough
    return league


class SpatialSeasonTests(unittest.TestCase):
    def test_rule_is_validated_and_defaults_to_statistical(self):
        self.assertEqual(LeagueRules().match_engine, "statistical")
        with self.assertRaises(ValueError):
            LeagueRules(match_engine="arcade")

    def test_game_result_and_box_score_are_stored(self):
        league = short_spatial_league()
        g = min(league.games.values(), key=lambda g: g.id)
        sim = play_game(league, g.id, random.Random(1))
        self.assertTrue(league.games[g.id].played)
        for box, score in ((sim.box_score.home, sim.result.home_score),
                           (sim.box_score.away, sim.result.away_score)):
            self.assertEqual(sum(l.pts for l in box.lines), score)
            self.assertEqual(round(sum(l.seconds for l in box.lines) / 60),
                             5 * (48 + 5 * sim.result.overtimes))
        self.assertNotEqual(sim.result.home_score, sim.result.away_score)

    def test_a_short_season_with_playoffs_runs(self):
        league = short_spatial_league()
        summary = play_full_season(league, random.Random(2))
        self.assertTrue(all(g.played for g in league.games.values()))
        self.assertTrue(summary)
        played = [g for g in league.games.values() if not g.playoff]
        self.assertGreater(len(played), 6)

    def test_injuries_happen_at_a_plausible_rate(self):
        from hoopsim.spatial.season_game import simulate_spatial
        league = short_spatial_league()
        g = min(league.games.values(), key=lambda g: g.id)
        hurt = sum(len(simulate_spatial(league, g, random.Random(i)).injuries) for i in range(8))
        self.assertLess(hurt, 8)               # ~0.5 a game at the statistical engine's rate

    def test_engine_choice_survives_save_and_load(self):
        league = short_spatial_league()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "lg.db"
            save_league(league, path)
            self.assertEqual(load_league(path).rules.match_engine, "spatial")


if __name__ == "__main__":
    unittest.main()
