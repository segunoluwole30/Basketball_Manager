import random
import tempfile
import unittest
from pathlib import Path

from hoopsim import Attributes, Injury, generate_league, load_league, save_league
from hoopsim.engine import play_day, play_game, play_season, simulate_game


def boost(league, team_id, amount):
    for p in league.roster(team_id):
        vals = {k: max(1, min(20, v + amount)) for k, v in p.attributes.to_dict().items()}
        p.attributes = Attributes(**vals)


class BoxScoreConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.league = generate_league(seed=11)
        rng = random.Random(5)
        cls.sims = [simulate_game(cls.league, g, rng, record_pbp=True)
                    for g in list(cls.league.games.values())[:40]]

    def test_points_add_up(self):
        for sim in self.sims:
            for box, score in ((sim.box_score.home, sim.result.home_score),
                               (sim.box_score.away, sim.result.away_score)):
                self.assertEqual(box.points, score)
                self.assertEqual(box.totals().pts, score)
                self.assertEqual(len(box.period_points), 4 + sim.result.overtimes)

    def test_minutes_add_up(self):
        for sim in self.sims:
            game_seconds = 4 * 12 * 60 + sim.result.overtimes * 5 * 60
            for box in (sim.box_score.home, sim.box_score.away):
                self.assertAlmostEqual(box.totals().seconds, 5 * game_seconds, delta=0.01)
                self.assertEqual(sum(l.starter for l in box.lines), 5)

    def test_plus_minus_matches_margin(self):
        for sim in self.sims:
            margin = sim.result.home_score - sim.result.away_score
            self.assertEqual(sum(l.plus_minus for l in sim.box_score.home.lines), 5 * margin)
            self.assertEqual(sum(l.plus_minus for l in sim.box_score.away.lines), -5 * margin)

    def test_shooting_lines_are_sane(self):
        for sim in self.sims:
            for box in (sim.box_score.home, sim.box_score.away):
                for l in box.lines:
                    self.assertLessEqual(l.fgm, l.fga)
                    self.assertLessEqual(l.tpm, l.tpa)
                    self.assertLessEqual(l.tpa, l.fga)
                    self.assertLessEqual(l.ftm, l.fta)
                    self.assertLessEqual(l.pf, 6)
                    self.assertLessEqual(l.minutes, 53 + 5 * sim.result.overtimes)

    def test_no_ties_and_realistic_scores(self):
        for sim in self.sims:
            self.assertNotEqual(sim.result.home_score, sim.result.away_score)
            self.assertTrue(70 < sim.result.home_score < 170)

    def test_play_by_play(self):
        events = self.sims[0].play_by_play
        self.assertGreater(len(events), 200)
        self.assertIn("win the tip", events[1].text)
        self.assertEqual(events[-1].text, "Final")
        self.assertTrue(all(1 <= e.period for e in events))
        last = events[-1]
        self.assertEqual((last.home_score, last.away_score),
                         (self.sims[0].result.home_score, self.sims[0].result.away_score))


class EngineBehaviourTests(unittest.TestCase):
    def test_deterministic_with_seed(self):
        league = generate_league(seed=2)
        g = league.games[1]
        a = simulate_game(league, g, random.Random(9))
        b = simulate_game(league, g, random.Random(9))
        self.assertEqual(a.box_score.to_dict(), b.box_score.to_dict())

    def test_simulate_does_not_mutate(self):
        league = generate_league(seed=2)
        before = league.games[1].to_dict()
        simulate_game(league, league.games[1], random.Random(1))
        self.assertEqual(league.games[1].to_dict(), before)

    def test_better_team_wins_more(self):
        league = generate_league(seed=3)
        g = league.games[1]
        boost(league, g.away_team_id, 3)
        rng = random.Random(0)
        away_wins = 0
        for _ in range(300):               # true rate is ~0.77; 300 games keeps noise ~±2.5%
            r = simulate_game(league, g, rng).result
            away_wins += r.away_score > r.home_score
        self.assertGreater(away_wins / 300, 0.70)

    def test_injured_players_sit(self):
        league = generate_league(seed=4)
        g = league.games[1]
        star = league.players[league.teams[g.home_team_id].tactics.starters[0]]
        star.injury = Injury("sprained ankle", 5)
        box = simulate_game(league, g, random.Random(1)).box_score.home
        self.assertNotIn(star.id, {l.player_id for l in box.lines})
        self.assertEqual(sum(l.starter for l in box.lines), 5)

    def test_three_point_tactic(self):
        league = generate_league(seed=6)
        g = league.games[1]
        tac = league.teams[g.home_team_id].tactics
        rng = random.Random(2)

        def threes(rate):
            tac.three_point_rate = rate
            return sum(simulate_game(league, g, rng).box_score.home.totals().tpa for _ in range(30))

        self.assertGreater(threes(20), threes(1) * 1.3)

    def test_stars_play_more_than_bench(self):
        league = generate_league(seed=8)
        g = league.games[1]
        tac = league.teams[g.home_team_id].tactics
        box = simulate_game(league, g, random.Random(3)).box_score.home
        mins = {l.player_id: l.minutes for l in box.lines}
        starters = [mins[p] for p in tac.starters]
        deep_bench = [m for pid, m in mins.items() if pid not in tac.minutes_targets]
        self.assertGreater(min(starters), max(deep_bench, default=0))

    def test_too_few_players(self):
        league = generate_league(seed=4)
        g = league.games[1]
        for p in league.roster(g.home_team_id)[:9]:
            p.injury = Injury("flu", 1)
        with self.assertRaises(ValueError):
            simulate_game(league, g, random.Random(1))


class SeasonTests(unittest.TestCase):
    def test_play_game_stores_and_refuses_replay(self):
        league = generate_league(seed=5)
        play_game(league, 1, random.Random(1))
        self.assertTrue(league.games[1].played)
        self.assertIsNotNone(league.games[1].box_score)
        with self.assertRaises(ValueError):
            play_game(league, 1)

    def test_play_day_and_season(self):
        league = generate_league(seed=5, num_teams=6)
        played = play_day(league, 1, random.Random(1))
        self.assertEqual(len(played), 3)
        play_season(league, random.Random(2))
        self.assertTrue(all(g.played for g in league.games.values()))
        wins = sum(w for _, w, _ in league.standings())
        self.assertEqual(wins, len(league.games))

    def test_box_scores_persist(self):
        league = generate_league(seed=5, num_teams=4)
        play_day(league, 1, random.Random(1))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "l.db"
            save_league(league, path)
            loaded = load_league(path)
        for gid, g in league.games.items():
            self.assertEqual(loaded.games[gid].to_dict(), g.to_dict())


if __name__ == "__main__":
    unittest.main()
