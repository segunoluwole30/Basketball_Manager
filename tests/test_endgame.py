import random
import unittest

from hoopsim import generate_league
from hoopsim.engine import DEFAULT_TUNING, THREE, _GameSim, simulate_game


def late_game(home_score, away_score, clock, period=4):
    """A sim frozen at a chosen moment of the game."""
    league = generate_league(seed=21, num_teams=4)
    game = next(iter(league.games.values()))
    sim = _GameSim(league, game, random.Random(1), DEFAULT_TUNING, record_pbp=True)
    sim.period, sim.clock = period, float(clock)
    sim.elapsed = (period - 1) * 720 + (720 - clock)
    for side in (sim.home, sim.away):
        side.period_points = [0] * period
    sim.home.score, sim.away.score = home_score, away_score
    return sim


class FoulingTests(unittest.TestCase):
    def test_foul_when_down_one_to_three_and_shot_clock_off(self):
        for deficit in (1, 2, 3):
            sim = late_game(100, 100 + deficit, clock=15)   # away leads, home defends
            self.assertTrue(sim.should_foul(sim.home, sim.away), deficit)

    def test_play_defence_when_there_is_time(self):
        sim = late_game(100, 102, clock=40)
        self.assertFalse(sim.should_foul(sim.home, sim.away))

    def test_down_four_plus_defends_while_shot_clock_is_on(self):
        sim = late_game(100, 106, clock=40)
        self.assertFalse(sim.should_foul(sim.home, sim.away))

    def test_foul_when_down_four_plus_but_catchable(self):
        sim = late_game(100, 106, clock=20)
        self.assertTrue(sim.should_foul(sim.home, sim.away))

    def test_give_up_when_out_of_reach(self):
        sim = late_game(100, 120, clock=30)
        self.assertFalse(sim.should_foul(sim.home, sim.away))

    def test_no_fouling_before_the_fourth(self):
        sim = late_game(100, 102, clock=15, period=3)
        self.assertFalse(sim.should_foul(sim.home, sim.away))

    def test_last_two_minute_penalty(self):
        sim = late_game(100, 100, clock=90)
        self.assertFalse(sim.in_bonus(sim.home))
        sim.foul(sim.home, sim.home.lineup[0])
        self.assertFalse(sim.in_bonus(sim.home))           # first one is "free"
        sim.foul(sim.home, sim.home.lineup[1])
        self.assertTrue(sim.in_bonus(sim.home))

    def test_intentional_foul_targets_role_player(self):
        sim = late_game(100, 102, clock=15)
        sim.intentional_foul(sim.away, sim.home)
        foulers = [ig for ig in sim.home.players.values() if ig.line.pf > 0]
        stars = sorted(sim.home.lineup, key=lambda ig: ig.ca, reverse=True)[:2]
        self.assertTrue(foulers)
        self.assertFalse(set(foulers) & set(stars))


class ClockManagementTests(unittest.TestCase):
    def test_leader_dribbles_out(self):
        sim = late_game(105, 100, clock=18)
        mode, _, _ = sim.plan_possession(sim.home, sim.away, "dead")
        self.assertEqual(mode, "run_out")

    def test_leader_milks_clock(self):
        sim = late_game(105, 100, clock=50)
        mode, seconds, _ = sim.plan_possession(sim.home, sim.away, "dead")
        self.assertEqual(mode, "hold")
        self.assertGreaterEqual(seconds, 18)

    def test_down_three_needs_a_three(self):
        sim = late_game(97, 100, clock=20)
        _, _, bias = sim.plan_possession(sim.home, sim.away, "steal")
        self.assertGreaterEqual(bias[THREE], 10)

    def test_trailing_team_hurries(self):
        sim = late_game(90, 98, clock=60)
        mode, seconds, bias = sim.plan_possession(sim.home, sim.away, "steal")
        self.assertEqual(mode, "hurry")
        self.assertLessEqual(seconds, 8)
        self.assertGreater(bias[THREE], 1)

    def test_tied_holds_for_last_shot(self):
        sim = late_game(100, 100, clock=20)
        mode, seconds, _ = sim.plan_possession(sim.home, sim.away, "steal")
        self.assertEqual(mode, "hold")
        self.assertLess(sim.clock - seconds, 3.5)           # shot goes up with < 3.5s left

    def test_two_for_one_early_periods(self):
        sim = late_game(50, 50, clock=34, period=2)
        mode, seconds, _ = sim.plan_possession(sim.home, sim.away, "dead")
        self.assertEqual(mode, "hurry")
        self.assertGreaterEqual(sim.clock - seconds, 24)    # we get the ball back

    def test_buzzer_heave(self):
        sim = late_game(50, 52, clock=2, period=2)
        mode, _, _ = sim.plan_possession(sim.home, sim.away, "drb")
        self.assertEqual(mode, "heave")

    def test_normal_play_mid_game(self):
        sim = late_game(50, 52, clock=400, period=2)
        self.assertIsNone(sim.plan_possession(sim.home, sim.away, "dead"))

    def test_late_timeout_advances_ball(self):
        sim = late_game(97, 100, clock=20)
        before = sim.home.timeouts
        # Coaches call it 80% of the time; find a seed where this one does.
        for seed in range(20):
            sim.rng = random.Random(seed)
            sim.home.late_timeouts = 0
            if sim.late_timeout(sim.home, "dead", 3):
                break
        self.assertEqual(sim.home.timeouts, before - 1)


class WholeGameTests(unittest.TestCase):
    def test_timeouts_and_clock_are_consistent(self):
        league = generate_league(seed=22)
        rng = random.Random(4)
        for game in list(league.games.values())[:60]:
            sim = simulate_game(league, game, rng, record_pbp=True)
            ot = sim.result.overtimes
            used = {}
            prev = None
            for e in sim.play_by_play:
                if e.text.startswith("Timeout"):
                    used[e.team] = used.get(e.team, 0) + 1
                    self.assertNotIn("-1 left", e.text)
                if prev and e.period == prev.period:
                    self.assertLessEqual(e.clock, prev.clock + 1e-9)   # clock never runs backwards
                prev = e
            for count in used.values():
                self.assertLessEqual(count, 7 + 2 * ot)


if __name__ == "__main__":
    unittest.main()
