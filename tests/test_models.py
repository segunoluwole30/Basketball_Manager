import random
import tempfile
import unittest
from collections import Counter
from datetime import date
from pathlib import Path

from hoopsim import (
    Attributes, Contract, Game, GameResult, League, Player, Position, RosterError, Tactics,
    compute_ability, generate_league, generate_player, load_league, save_league,
)
from hoopsim.generate import birth_date_for_age, development_fraction, round_robin


class AttributeTests(unittest.TestCase):
    def test_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            Attributes(three_point=21)
        with self.assertRaises(ValueError):
            Attributes(passing=0)

    def test_ability_bounds_and_monotonic(self):
        self.assertEqual(compute_ability(Attributes(**{n: 1 for n in Attributes.names()}), Position.C), 1)
        self.assertEqual(compute_ability(Attributes(**{n: 20 for n in Attributes.names()}), Position.C), 200)
        self.assertGreater(compute_ability(Attributes(passing=15), Position.PG),
                           compute_ability(Attributes(), Position.PG))

    def test_position_weights_matter(self):
        shooter = Attributes(three_point=20, ball_handling=18, passing=18, blocking=3,
                             offensive_rebounding=3, defensive_rebounding=3)
        self.assertGreater(compute_ability(shooter, Position.PG), compute_ability(shooter, Position.C))


class GeneratorTests(unittest.TestCase):
    def test_birth_date_gives_exact_age(self):
        rng = random.Random(1)
        for on in (date(2026, 10, 1), date(2028, 2, 29), date(2026, 1, 1)):
            for age in range(18, 40):
                p = Player.age  # reuse the model's age logic
                fake = type("P", (), {"birth_date": birth_date_for_age(age, on, rng)})()
                self.assertEqual(p(fake, on), age, (on, age))

    def test_player_hits_target_ability(self):
        rng = random.Random(3)
        on = date(2026, 10, 1)
        for _ in range(200):
            p = generate_player(rng, 1, on, age=27, potential=120)
            # age-27 development is ~100% of potential (with a small random spread)
            self.assertLessEqual(abs(p.current_ability - 120), 12)
            self.assertGreaterEqual(p.potential_ability, p.current_ability)

    def test_young_players_below_potential(self):
        self.assertLess(development_fraction(19), development_fraction(27))
        self.assertLess(development_fraction(36), development_fraction(27))

    def test_centres_taller_and_better_rebounders_than_guards(self):
        rng = random.Random(5)
        on = date(2026, 10, 1)
        gs = [generate_player(rng, i, on, position=Position.PG, potential=110) for i in range(100)]
        cs = [generate_player(rng, i, on, position=Position.C, potential=110) for i in range(100)]
        avg = lambda ps, f: sum(f(p) for p in ps) / len(ps)
        self.assertGreater(avg(cs, lambda p: p.physique.height_cm), avg(gs, lambda p: p.physique.height_cm) + 15)
        self.assertGreater(avg(cs, lambda p: p.attributes.defensive_rebounding),
                           avg(gs, lambda p: p.attributes.defensive_rebounding) + 3)
        self.assertGreater(avg(gs, lambda p: p.attributes.ball_handling),
                           avg(cs, lambda p: p.attributes.ball_handling) + 3)

    def test_league_is_valid_and_deterministic(self):
        a = generate_league(seed=42)
        b = generate_league(seed=42)
        self.assertEqual(a.validate(), [])
        self.assertEqual([p.to_dict() for p in a.players.values()],
                         [p.to_dict() for p in b.players.values()])

    def test_lineups(self):
        league = generate_league(seed=7)
        for team in league.teams.values():
            t = team.tactics
            self.assertEqual(len(t.starters), 5)
            self.assertEqual(sum(t.minutes_targets.values()), 240)
            self.assertTrue(all(m <= 40 for m in t.minutes_targets.values()))
            self.assertTrue(set(t.starters) <= set(t.minutes_targets))

    def test_schedule_balanced(self):
        league = generate_league(seed=1, num_teams=12)
        games, home = Counter(), Counter()
        per_day = Counter()
        for g in league.games.values():
            games[g.home_team_id] += 1
            games[g.away_team_id] += 1
            home[g.home_team_id] += 1
            per_day[(g.day, g.home_team_id)] += 1
            per_day[(g.day, g.away_team_id)] += 1
        self.assertEqual(set(games.values()), {league.rules.games_per_season})
        self.assertTrue(all(abs(h - league.rules.games_per_season / 2) <= 6 for h in home.values()))
        self.assertEqual(max(per_day.values()), 1)  # nobody plays twice in a day

    def test_round_robin_odd_teams(self):
        rounds = round_robin([1, 2, 3, 4, 5])
        pairs = {frozenset(p) for r in rounds for p in r}
        self.assertEqual(len(pairs), 10)


class LeagueRuleTests(unittest.TestCase):
    def setUp(self):
        self.league = generate_league(seed=3)
        self.fa = self.league.free_agents()[0]
        self.team_id = min(self.league.teams)

    def contract(self, salary=2_000_000):
        return Contract(salary, self.league.season + 1)

    def test_sign_and_release(self):
        self.league.sign(self.fa.id, self.team_id, self.contract())
        self.assertEqual(self.fa.team_id, self.team_id)
        with self.assertRaises(RosterError):
            self.league.sign(self.fa.id, self.team_id, self.contract())
        self.league.release(self.fa.id)
        self.assertIsNone(self.fa.team_id)
        self.assertIsNone(self.fa.contract)

    def test_sign_rejects_bad_contracts(self):
        with self.assertRaises(RosterError):
            self.league.sign(self.fa.id, self.team_id, self.contract(salary=1))
        with self.assertRaises(RosterError):
            self.league.sign(self.fa.id, self.team_id, self.contract(salary=10**9))

    def test_roster_limit(self):
        fas = self.league.free_agents()
        room = self.league.rules.max_roster - len(self.league.roster(self.team_id))
        for p in fas[:room]:
            self.league.sign(p.id, self.team_id, self.contract())
        with self.assertRaises(RosterError):
            self.league.sign(fas[room].id, self.team_id, self.contract())

    def test_trade_moves_players_and_clears_lineups(self):
        a, b = sorted(self.league.teams)[:2]
        pa = self.league.teams[a].tactics.starters[0]
        pb = self.league.roster(b)[-1].id
        self.league.trade(a, [pa], b, [pb])
        self.assertEqual(self.league.players[pa].team_id, b)
        self.assertEqual(self.league.players[pb].team_id, a)
        self.assertEqual(self.league.teams[a].tactics.starters, [])
        with self.assertRaises(RosterError):
            self.league.trade(a, [pa], b, [])  # pa is no longer on team a

    def test_standings(self):
        games = sorted(self.league.games.values(), key=lambda g: g.id)[:6]
        for g in games:
            g.result = GameResult(100, 90)
        total_w = sum(w for _, w, _ in self.league.standings())
        self.assertEqual(total_w, 6)

    def test_model_guards(self):
        with self.assertRaises(ValueError):
            GameResult(100, 100)
        with self.assertRaises(ValueError):
            Game(1, 2026, 1, 3, 3)
        with self.assertRaises(ValueError):
            Tactics(starters=[1, 2, 3])
        with self.assertRaises(ValueError):
            self.league.add_player(self.fa)  # duplicate id


class PersistenceTests(unittest.TestCase):
    def test_round_trip(self):
        league = generate_league(seed=9)
        g = league.games[1]
        g.result = GameResult(110, 108, overtimes=1)
        league.players[1].injury = None
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "league.db"
            save_league(league, path)
            save_league(league, path)  # saving twice overwrites cleanly
            loaded = load_league(path)
        self.assertIsInstance(loaded, League)
        self.assertEqual(loaded.season, league.season)
        self.assertEqual(loaded.rules, league.rules)
        self.assertEqual({k: t.to_dict() for k, t in loaded.teams.items()},
                         {k: t.to_dict() for k, t in league.teams.items()})
        self.assertEqual({k: p.to_dict() for k, p in loaded.players.items()},
                         {k: p.to_dict() for k, p in league.players.items()})
        self.assertEqual(loaded.games[1].result, g.result)
        # tactics minutes keys come back as ints, not JSON strings
        t = next(iter(loaded.teams.values())).tactics
        self.assertTrue(all(isinstance(k, int) for k in t.minutes_targets))

    def test_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            load_league("/nonexistent/league.db")


if __name__ == "__main__":
    unittest.main()
