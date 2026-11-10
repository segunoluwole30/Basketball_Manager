import random
import statistics
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path

from hoopsim import Contract, Injury, generate_league, load_league, save_league
from hoopsim.contracts import best_offer, rookie_contract
from hoopsim.development import develop, retirement_chance
from hoopsim.engine import DEFAULT_TUNING, play_day, simulate_game
from hoopsim.generate import generate_player, market_salary
from hoopsim.injuries import INJURY_TABLE, draw_injury
from hoopsim.lineup import plan_rotation
from hoopsim.models import Phase
from hoopsim.playoffs import play_playoffs, start_playoffs, teams_per_conference
from hoopsim.season import play_full_season, play_regular_season, run_offseason


class InjuryTests(unittest.TestCase):
    def test_durations_in_range(self):
        rng = random.Random(1)
        lo = min(r[1] for r in INJURY_TABLE)
        hi = max(r[2] for r in INJURY_TABLE)
        for _ in range(500):
            self.assertTrue(lo <= draw_injury(rng).games_remaining <= hi)

    def test_player_misses_exactly_n_team_games(self):
        league = generate_league(seed=1, num_teams=4)
        tid = min(league.teams)
        star = league.roster(tid)[0]
        star.injury = Injury("sprained ankle", 2)
        played_while_out, rng = 0, random.Random(2)
        day = 0
        while played_while_out < 3:
            day += 1
            games = play_day(league, day, rng)
            for g in games:
                if tid in (g.home_team_id, g.away_team_id):
                    side = g.box_score.home if g.home_team_id == tid else g.box_score.away
                    appeared = any(l.player_id == star.id and l.seconds > 0 for l in side.lines)
                    played_while_out += 1
                    # out for team games 1 and 2, back for game 3
                    self.assertEqual(appeared, played_while_out == 3, played_while_out)

    def test_in_game_injuries_keep_box_score_consistent(self):
        league = generate_league(seed=2, num_teams=4)
        tuning = replace(DEFAULT_TUNING, injury_rate_per_36=0.6)   # lots of injuries
        rng = random.Random(3)
        injured = 0
        for g in list(league.games.values())[:10]:
            sim = simulate_game(league, g, rng, tuning=tuning)
            injured += len(sim.injuries)
            game_seconds = 2880 + 300 * sim.result.overtimes
            for box in (sim.box_score.home, sim.box_score.away):
                self.assertAlmostEqual(box.totals().seconds, 5 * game_seconds, delta=0.01)
        self.assertGreater(injured, 5)

    def test_rotation_with_five_healthy_players(self):
        league = generate_league(seed=3, num_teams=4)
        roster = league.roster(min(league.teams))
        for p in roster[5:]:
            p.injury = Injury("flu", 3)
        starters, minutes = plan_rotation(roster, 9)
        self.assertEqual(len(starters), 5)
        self.assertEqual(sum(minutes.values()), 240)


class DevelopmentTests(unittest.TestCase):
    def test_young_grow_old_decline(self):
        rng = random.Random(4)
        on = date(2026, 10, 1)
        young = [develop(generate_player(rng, i, on, age=20), 20, 25, rng) for i in range(200)]
        old = [develop(generate_player(rng, i, on, age=34), 34, 25, rng) for i in range(200)]
        self.assertGreater(statistics.mean(young), 5)
        self.assertLess(statistics.mean(old), -4)

    def test_never_exceeds_potential(self):
        rng = random.Random(5)
        on = date(2026, 10, 1)
        for i in range(200):
            p = generate_player(rng, i, on, age=19)
            for age in range(19, 26):
                develop(p, age, 30, rng)
                self.assertLessEqual(p.current_ability, p.potential_ability)

    def test_retirement_chance(self):
        p = generate_player(random.Random(6), 1, date(2026, 10, 1), age=30)
        self.assertEqual(retirement_chance(p, 29, True), 0.0)
        self.assertEqual(retirement_chance(p, 41, True), 1.0)
        self.assertGreater(retirement_chance(p, 37, False), retirement_chance(p, 37, True))


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.league = generate_league(seed=7)

    def test_salary_curve(self):
        rules = self.league.rules
        salaries = [market_salary(v, rules) for v in range(40, 200, 10)]
        self.assertEqual(salaries, sorted(salaries))
        self.assertEqual(salaries[0], rules.min_salary)
        self.assertEqual(salaries[-1], int(rules.salary_cap * rules.max_salary_cap_fraction))

    def test_rookie_scale(self):
        firsts = [rookie_contract(i, self.league, 2027).salary for i in range(1, 13)]
        self.assertEqual(firsts, sorted(firsts, reverse=True))
        second = rookie_contract(13, self.league, 2027)
        self.assertEqual(second.salary, self.league.rules.min_salary)

    def test_soft_cap_offers(self):
        league, tid = self.league, min(self.league.teams)
        rules = league.rules
        # Push the team over the cap.
        league.roster(tid)[0].contract = Contract(rules.salary_cap, league.season + 2)
        salary, how = best_offer(league, tid, 30_000_000, league.season, mle_available=True)
        self.assertIsNone(salary)                                  # can't afford a star
        salary, how = best_offer(league, tid, 10_000_000, league.season, mle_available=True)
        self.assertEqual(how, "mid-level exception")
        salary, how = best_offer(league, tid, 10_000_000, league.season, mle_available=False)
        self.assertIsNone(salary)
        salary, how = best_offer(league, tid, rules.min_salary, league.season, mle_available=False)
        self.assertEqual(how, "minimum")


class PlayoffTests(unittest.TestCase):
    def test_bracket_sizes(self):
        self.assertEqual(teams_per_conference(6), 4)
        self.assertEqual(teams_per_conference(15), 8)
        self.assertEqual(teams_per_conference(2), 2)

    def test_full_playoffs(self):
        league = generate_league(seed=8)
        rng = random.Random(8)
        play_regular_season(league, rng)
        with self.assertRaises(ValueError):
            run_offseason(league, rng)                  # not allowed mid-season
        start_playoffs(league)
        self.assertIs(league.phase, Phase.PLAYOFFS)
        champ = play_playoffs(league, rng)
        rounds = max(s.round for s in league.playoffs)
        self.assertEqual(rounds, 3)                     # 4 per conference -> 2 rounds + finals
        for s in league.playoffs:
            self.assertTrue(s.over)
            self.assertEqual(max(s.high_wins, s.low_wins), 4)
            self.assertTrue(4 <= len(s.game_ids) <= 7)
            homes = [league.games[g].home_team_id for g in s.game_ids]
            for i, h in enumerate(homes):                # 2-2-1-1-1
                self.assertEqual(h, s.high_id if i in (0, 1, 4, 6) else s.low_id)
        finals = [s for s in league.playoffs if s.round == rounds][0]
        self.assertIsNone(finals.conference)
        self.assertEqual(champ, finals.winner_id)


class SeasonCycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.league = generate_league(seed=9)
        cls.rng = random.Random(9)
        cls.summaries, cls.reports = [], []
        cls.first_season = cls.league.season
        for _ in range(2):
            cls.summaries.append(play_full_season(cls.league, cls.rng))
            cls.reports.append(run_offseason(cls.league, cls.rng))

    def test_league_stays_legal(self):
        for r in self.reports:
            self.assertEqual(r["problems"], [])
        self.assertEqual(self.league.validate(), [])

    def test_seasons_advance(self):
        self.assertEqual(self.league.season, self.first_season + 2)
        self.assertIs(self.league.phase, Phase.REGULAR_SEASON)
        self.assertEqual(len(self.league.history), 2)
        self.assertTrue(all(g.season == self.league.season for g in self.league.games.values()))
        self.assertTrue(all(not g.played for g in self.league.games.values()))

    def test_history_has_champion_and_awards(self):
        for h in self.league.history:
            self.assertIn(h["champion"], self.league.teams)
            self.assertIsNotNone(h["awards"]["mvp"])
            self.assertIsNotNone(h["awards"]["finals_mvp"])
        self.assertIsNotNone(self.league.history[1]["awards"]["mip"])   # needs a prior season

    def test_draft_and_rookies(self):
        rookies = [p for p in self.league.players.values()
                   if p.draft_year == self.first_season + 1 and p.draft_pick]
        self.assertEqual(len(rookies), len(self.league.teams) * self.league.rules.draft_rounds)
        self.assertTrue(all(p.contract and p.contract.rookie_scale for p in rookies if p.team_id))

    def test_careers_recorded(self):
        vets = [p for p in self.league.players.values()
                if sum(1 for c in p.career if not c["playoffs"]) == 2]
        self.assertGreater(len(vets), 50)
        seasons = {c["season"] for p in vets for c in p.career}
        self.assertEqual(seasons, {self.first_season, self.first_season + 1})

    def test_retirements_happen_and_stay_retired(self):
        retired = [p for p in self.league.players.values() if p.retired]
        self.assertTrue(retired)
        self.assertTrue(all(p.team_id is None and p.contract is None for p in retired))

    def test_contracts_valid_for_new_season(self):
        for tid in self.league.teams:
            for p in self.league.roster(tid):
                self.assertGreaterEqual(p.contract.expires_season, self.league.season)

    def test_save_load_after_seasons(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "l.db"
            save_league(self.league, path)
            loaded = load_league(path)
        self.assertEqual(loaded.history, self.league.history)
        self.assertIs(loaded.phase, self.league.phase)
        pid = next(p.id for p in self.league.players.values() if p.career)
        self.assertEqual(loaded.players[pid].career, self.league.players[pid].career)


class DeterminismTests(unittest.TestCase):
    def test_same_seed_same_history(self):
        def run():
            league = generate_league(seed=10, num_teams=4)
            rng = random.Random(10)
            play_full_season(league, rng)
            run_offseason(league, rng)
            return league.history[0]["champion"], [p.to_dict() for p in league.players.values()]
        self.assertEqual(run(), run())


if __name__ == "__main__":
    unittest.main()
