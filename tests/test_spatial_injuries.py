"""Spatial full games: in-game injuries and morale-based game-day form."""
import random
import unittest
from dataclasses import replace

from hoopsim import generate_league
from hoopsim.engine import DEFAULT_TUNING as SEASON_TUNING
from hoopsim.models import Injury
from hoopsim.spatial.fullgame import FullGame


def short_league(seed: int, quarter_minutes: int = 3):
    league = generate_league(seed=seed)
    league.rules.quarter_minutes = quarter_minutes
    league.rules.overtime_minutes = 2
    return league


def boosted(factor: float):
    """The statistical engine's tuning with injuries ``factor`` times as likely."""
    return replace(SEASON_TUNING, injury_rate_per_36=SEASON_TUNING.injury_rate_per_36 * factor)


class InjuryRateTests(unittest.TestCase):
    def test_rate_matches_the_statistical_engine(self):
        league = generate_league(seed=5)
        risk = hurt = 0
        for i, (home, away) in enumerate(((1, 2), (3, 4))):
            g = FullGame(league, home, away, random.Random(i))
            sim = g.run()
            risk += g.stats["injury_risk"]
            hurt += len(sim.injuries)
        # Expected injuries per team-game: the statistical engine has ~0.17.
        self.assertGreater(risk / 4, 0.10)
        self.assertLess(risk / 4, 0.30)
        self.assertLess(hurt, 6)

    def test_rolls_match_the_expected_number(self):
        league = short_league(3)
        risk = hurt = 0
        for i in range(8):
            g = FullGame(league, 1 + i % 4, 5 + i % 4, random.Random(i), season_tuning=boosted(20))
            sim = g.run()
            risk += g.stats["injury_risk"]
            hurt += len(sim.injuries)
        self.assertGreater(risk, 5)
        self.assertLess(abs(hurt - risk), 3 * risk ** 0.5 + 1)


class HurtPlayerTests(unittest.TestCase):
    """Short games at 20x the injury rate."""

    @classmethod
    def setUpClass(cls):
        league = short_league(4)
        cls.games = []
        for i in range(6):          # 4 gave only two injuries with some engine versions
            g = FullGame(league, 1, 2, random.Random(10 + i), record=True, season_tuning=boosted(20))
            g.run()
            cls.games.append(g)

    def hurt(self):
        return [(g, side, s) for g in self.games for side in g.sides.values() for s in side.roster
                if s.injury is not None]

    def test_injuries_are_on_the_result_and_logged(self):
        self.assertGreater(len(self.hurt()), 2)
        for g in self.games:
            ids = [pid for pid, _ in g.result.injuries]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertEqual(set(ids), {s.p.id for side in g.sides.values() for s in side.roster if s.injury})
            for pid, injury in g.result.injuries:
                self.assertIsInstance(injury, Injury)
            texts = [e.text for e in g.events]
            for side in g.sides.values():
                for s in side.roster:
                    if s.injury:
                        self.assertIn(f"{s.p.name} is hurt ({s.injury.description})", texts)

    def test_a_hurt_player_leaves_and_does_not_return(self):
        left_at_once = 0
        for g, side, s in self.hurt():
            self.assertIsNotNone(s.hurt_at)
            back = [x for x in g.sub_log if x[2] == s.p.id and x[0] >= s.hurt_at]
            self.assertEqual(back, [], f"{s.p.name} came back")
            out = [x for x in g.sub_log if x[3] == s.p.id and x[0] == s.hurt_at]
            if out:
                left_at_once += 1
                self.assertTrue(any(e.text.startswith("Sub: ") and e.text.endswith(f"for {s.p.name} (injured)")
                                    for e in g.events))
            # Still on the floor only if nobody was left on the bench, or he was hurt on
            # the game's last play (no dead ball left to sub him out).
            if s.on_court and s.hurt_at < g.elapsed:
                self.assertFalse([b for b in side.bench if not g.unavailable(b)])
        self.assertGreater(left_at_once, 0)


class ManualSubsTests(unittest.TestCase):
    def test_hurt_players_are_replaced_when_the_coach_makes_the_subs(self):
        # A game where the home team gets hurt (which seed does it depends on the engine).
        for seed in range(12, 40):
            g = FullGame(short_league(4), 1, 2, random.Random(seed), season_tuning=boosted(40))
            g.attach_manager("home")["auto_subs"] = False
            sim = g.run()
            if any(s.injury for s in g.home.roster):
                break
        home_out = [x[3] for x in g.sub_log if x[1] == "home"]
        self.assertTrue(home_out)
        self.assertTrue(any(g.home.seats[pid].injury for pid in home_out))
        for pid in home_out:                               # only forced changes were made
            self.assertTrue(g.unavailable(g.home.seats[pid]))
        self.assertNotEqual(sim.result.home_score, sim.result.away_score)

    def test_a_queued_sub_bringing_in_a_hurt_player_is_dropped(self):
        g = FullGame(generate_league(seed=5), 1, 2, random.Random(3), season_tuning=boosted(0))
        m = g.attach_manager("away")
        it = g.iter_game()
        for _ in range(3):
            next(it)
        out = g.away.lineup[0]
        inn = next(s for s in g.away.bench if not g.unavailable(s))
        inn.injury = Injury("sore knee", 1)
        m["subs"].append((out.p.id, inn.p.id))
        for _ in range(12):
            next(it)
        self.assertEqual(m["subs"], [])
        self.assertFalse(inn.on_court)
        self.assertFalse(any(x[2] == inn.p.id for x in g.sub_log))

    def test_a_queued_sub_for_a_player_who_gets_hurt_is_dropped(self):
        g = FullGame(short_league(4), 1, 2, random.Random(3), season_tuning=boosted(1e5))
        m = g.attach_manager("home")
        m["auto_subs"] = False
        it = g.iter_game()
        out = g.home.lineup[0]
        inn = next(s for s in g.home.bench if not g.unavailable(s))
        m["subs"].append((out.p.id, inn.p.id))
        while out.injury is None:
            next(it)
        self.assertNotIn((out.p.id, inn.p.id), m["subs"])
        self.assertFalse(out.on_court)
        for _ in it:                                       # everyone gets hurt: the game still ends
            pass
        self.assertIsNotNone(g.result)
        self.assertFalse(any(x[2] == out.p.id for x in g.sub_log))


class LiveTests(unittest.TestCase):
    def test_live_state_shows_injuries(self):
        from hoopsim.spatial.live import LiveError, LiveMatch
        league = short_league(5)
        game = min(league.games.values(), key=lambda g: g.id)
        league.user_team_id = game.home_team_id
        match = LiveMatch(league, game, random.Random(2))
        match.fg.st = boosted(200)
        n, notes, hurt = 0, [], []
        while not match.done and not hurt:
            chunk = match.next_chunk(n)
            n = chunk["next"]
            notes += [t for p in chunk["possessions"] for t in p["notes"]]
            hurt = [p for p in chunk["state"]["home"]["players"] if p["injured"]]
        self.assertTrue(hurt)
        p = hurt[0]
        self.assertIsInstance(p["injury"], str)
        self.assertFalse(p["on_court"])
        healthy = [x for x in match.state()["home"]["players"] if not x["injured"]]
        self.assertTrue(all(x["injury"] is None for x in healthy))
        on = next(x for x in match.state()["home"]["players"] if x["on_court"])
        with self.assertRaises(LiveError):
            match.command({"type": "sub", "out": on["id"], "in": p["id"]})
        while not match.done:                              # the note follows the possession it came after
            chunk = match.next_chunk(n)
            n = chunk["next"]
            notes += [t for q in chunk["possessions"] for t in q["notes"]]
        self.assertTrue(any(f"{p['name']} is hurt" in t for t in notes))
        sim = match.finish()
        ids = [pid for pid, _ in sim.injuries]
        self.assertIn(p["id"], ids)
        self.assertEqual(len(ids), len(set(ids)))


class SeasonGameTests(unittest.TestCase):
    def test_season_games_report_the_in_game_injuries_once(self):
        from hoopsim.spatial.season_game import _model, finish_spatial, simulate_spatial
        league = short_league(6)
        league.rules.match_engine = "spatial"
        league.user_team_id = None
        game = min(league.games.values(), key=lambda g: g.id)
        found = False
        for i in range(12):
            fg = FullGame(league, game.home_team_id, game.away_team_id, random.Random(i), model=_model(),
                          season_tuning=boosted(15))
            fg.run()
            again = finish_spatial(fg, random.Random(99), False)
            self.assertEqual(again.injuries, fg.result.injuries)    # no post-game roll on top
            ids = [pid for pid, _ in again.injuries]
            self.assertEqual(len(ids), len(set(ids)))
            found |= bool(ids)
        self.assertTrue(found)
        # simulate_spatial is the same game, injuries included.
        a = simulate_spatial(league, game, random.Random(1))
        fg = FullGame(league, game.home_team_id, game.away_team_id, random.Random(1), model=_model())
        b = fg.run()
        self.assertEqual(a.result, b.result)
        self.assertEqual(a.injuries, b.injuries)


class MoraleFormTests(unittest.TestCase):
    def forms(self, morale: int, **kw) -> dict:
        league = generate_league(seed=5)
        for p in league.roster(1):
            p.morale = morale
        g = FullGame(league, 1, 2, random.Random(7), **kw)
        return {s.p.id: s.form for s in g.home.roster}

    def test_morale_shifts_form_by_the_statistical_engines_amount(self):
        high, low = self.forms(90), self.forms(10)
        for pid in high:
            self.assertAlmostEqual(high[pid] - low[pid], 80 * SEASON_TUNING.morale_form_effect, places=9)
        flat = self.forms(90, season_tuning=replace(SEASON_TUNING, morale_form_effect=0.0))
        self.assertTrue(all(high[pid] > flat[pid] for pid in high))
        # The random part is modest: about 1 +/- 0.045 for an average player.
        self.assertTrue(all(0.8 < f < 1.2 for f in flat.values()))

    def test_form_multiplies_make_chances(self):
        g = FullGame(generate_league(seed=5), 1, 2, random.Random(0))
        self.assertEqual(g.home.lineup[1].agent.form, g.home.lineup[1].form)
        from hoopsim.spatial import HalfCourtGame
        hc = HalfCourtGame(generate_league(seed=5), 1, 2, random.Random(0))
        hc.play_possession("home")
        shooter = hc.offense[1]
        self.assertEqual(shooter.form, 1.0)                     # the half-court engine alone: no form
        spots = [(0.0, 6.0), (0.0, 15.0), (0.0, 26.0)]          # rim, mid-range, three
        shooter.form = 1.0
        base = [hc.make_prob(shooter, p, contest_d=6.0)[0] for p in spots]
        shooter.form = 1.03
        good = [hc.make_prob(shooter, p, contest_d=6.0)[0] for p in spots]
        shooter.form = 0.97
        bad = [hc.make_prob(shooter, p, contest_d=6.0)[0] for p in spots]
        for b, hi, lo in zip(base, good, bad):
            self.assertAlmostEqual(hi / b, 1.03, places=3)
            self.assertAlmostEqual(lo / b, 0.97, places=3)


class DeterminismTests(unittest.TestCase):
    def run_game(self, seed: int, tuning=SEASON_TUNING):
        g = FullGame(short_league(7), 3, 4, random.Random(seed), season_tuning=tuning)
        sim = g.run()
        return sim.result, sim.box_score.to_dict(), sim.injuries, g.sub_log

    def test_same_seed_same_game_and_injuries(self):
        seed = next(s for s in range(5, 40) if self.run_game(s, boosted(20))[2])   # a game with an injury
        a, b = self.run_game(seed, boosted(20)), self.run_game(seed, boosted(20))
        self.assertTrue(a[2])
        self.assertEqual(a, b)

    def test_a_game_without_injuries_is_unchanged_by_the_rolls(self):
        for seed in range(6):
            normal = self.run_game(seed)
            if not normal[2]:
                break
        self.assertFalse(normal[2])
        self.assertEqual(normal, self.run_game(seed, boosted(0)))


if __name__ == "__main__":
    unittest.main()
