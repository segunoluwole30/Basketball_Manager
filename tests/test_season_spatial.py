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
    opening = min(g.day for g in league.games.values())
    for gid in [g.id for g in league.games.values() if g.day > opening + days]:
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


class ReplayTests(unittest.TestCase):
    def test_user_games_are_recorded_saved_and_capped(self):
        from hoopsim import engine
        league = short_spatial_league(days=40)
        league.user_team_id = 1
        mine = sorted((g for g in league.games.values() if 1 in (g.home_team_id, g.away_team_id)),
                      key=lambda g: g.id)
        other = next(g for g in league.games.values() if 1 not in (g.home_team_id, g.away_team_id))
        play_game(league, other.id, random.Random(0))
        self.assertNotIn(other.id, league.replays)
        old_keep = engine.REPLAYS_KEPT
        engine.REPLAYS_KEPT = 2
        try:
            for i, g in enumerate(mine[:3]):
                play_game(league, g.id, random.Random(i))
        finally:
            engine.REPLAYS_KEPT = old_keep
        self.assertEqual(sorted(league.replays), [g.id for g in mine[1:3]])   # oldest dropped
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "lg.db"
            save_league(league, path)
            back = load_league(path)
            self.assertEqual(back.replays, league.replays)
        from hoopsim.spatial.season_game import replay_data
        data = replay_data(league, mine[2].id)
        self.assertEqual({p["period"] for p in data["possessions"]} >= {1, 2, 3, 4}, True)

    def test_statistical_leagues_record_nothing(self):
        league = generate_league(seed=4, num_teams=4)
        league.user_team_id = 1
        g = next(g for g in league.games.values() if 1 in (g.home_team_id, g.away_team_id))
        play_game(league, g.id, random.Random(0))
        self.assertEqual(league.replays, {})


class HybridTests(unittest.TestCase):
    def league(self):
        league = generate_league(seed=4, num_teams=4)
        league.rules.match_engine = "hybrid"
        league.user_team_id = 1
        return league

    def test_only_the_users_games_are_spatial(self):
        league = self.league()
        mine = next(g for g in league.games.values() if 1 in (g.home_team_id, g.away_team_id))
        other = next(g for g in league.games.values() if 1 not in (g.home_team_id, g.away_team_id))
        self.assertTrue(league.plays_spatial(mine))
        self.assertFalse(league.plays_spatial(other))
        self.assertTrue(league.rules.user_games_spatial)
        play_game(league, mine.id, random.Random(0))
        play_game(league, other.id, random.Random(0))
        self.assertIn(mine.id, league.replays)                  # recorded: watchable
        self.assertNotIn(other.id, league.replays)
        self.assertTrue(other.played and mine.played)

    def test_without_a_user_team_everything_is_statistical(self):
        league = self.league()
        league.user_team_id = None
        g = next(iter(league.games.values()))
        self.assertFalse(league.plays_spatial(g))

    def test_hybrid_season_is_fast(self):
        import time
        league = self.league()
        for gid in [g.id for g in league.games.values() if g.day > 12]:
            del league.games[gid]
        league.rules.playoff_series_length = 1
        t = time.time()
        play_full_season(league, random.Random(1))
        mine = [g for g in league.games.values() if 1 in (g.home_team_id, g.away_team_id)]
        others = [g for g in league.games.values() if 1 not in (g.home_team_id, g.away_team_id)]
        self.assertTrue(all(g.played for g in league.games.values()))
        self.assertTrue(others)
        # Only the user's games cost ~1 s each; the rest are near-instant.
        self.assertLess(time.time() - t, 2.0 * len(mine) + 5)
