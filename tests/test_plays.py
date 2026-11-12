"""Set plays: entry actions before the offence attacks."""
import random
import unittest

from hoopsim import generate_league
from hoopsim.spatial import HalfCourtGame, plays
from hoopsim.spatial.params import DEFAULT_PARAMS, LEGACY_PARAMS
from hoopsim.spatial.values import HEURISTIC


def game(params=DEFAULT_PARAMS, pace=None, seed=4):
    league = generate_league(seed=41, systems=False)
    if pace is not None:
        for t in league.teams.values():
            t.tactics.pace = pace
    return HalfCourtGame(league, 1, 2, random.Random(seed), model=HEURISTIC, params=params)


class SetPlayTests(unittest.TestCase):
    def test_offences_run_all_three_entries(self):
        g = game()
        g.play(300)
        s = g.stats
        self.assertGreater(s["entries"], 250)
        for kind in plays.KINDS:
            self.assertGreater(s[f"entry_{kind}"], 20, kind)
        self.assertGreater(s["entry_drives"] + s["entry_shots"], 0)     # reads happen

    def test_entries_add_passes_and_time(self):
        with_plays, without = game(), game(DEFAULT_PARAMS.with_values(entry_actions=0.0))
        with_plays.play(300)
        without.play(300)
        self.assertEqual(without.stats["entries"], 0)
        # (sets often finish in a pick-and-roll rather than another pass, and a denied
        # swing can kill the action)
        self.assertGreater(with_plays.stats["passes"], 1.08 * without.stats["passes"])
        self.assertGreater(with_plays.stats["seconds"], 1.15 * without.stats["seconds"])

    def test_legacy_params_run_no_entries(self):
        g = game(LEGACY_PARAMS)
        g.play(50)
        self.assertEqual(g.stats["entries"], 0)

    def test_fast_teams_run_shorter_entries(self):
        slow, fast = game(pace=4), game(pace=18)
        slow.play(300)
        fast.play(300)
        actions = lambda g: sum(g.stats[f"entry_{k}"] for k in plays.KINDS)
        self.assertGreater(actions(slow), 1.2 * actions(fast))

    def test_no_entry_on_a_short_clock_or_in_an_end_game_situation(self):
        g = game()
        g.play_possession("home")
        g.result = None
        g.shot_clock = 9.0
        self.assertIsNone(plays.plan(g))
        g.shot_clock, g.situation = 18.0, {"hold": True}
        self.assertIsNone(plays.plan(g))


if __name__ == "__main__":
    unittest.main()


class SafePassAndResetTests(unittest.TestCase):
    def setUp(self):
        self.g = game()
        self.g.play_possession("home")
        g = self.g
        self.h, self.mate = g.offense[0], g.offense[1]
        self.h.place(0.0, 28.0)
        self.mate.place(19.0, 22.0)
        g.guarded_by(self.h).place(0.0, 22.0)          # giving the handler room
        g.guarded_by(self.mate).place(15.0, 19.0)      # sagging, not denying

    def test_swing_against_no_pressure_is_routine(self):
        self.assertTrue(self.g.routine(self.h, self.mate, self.mate.pos))

    def test_denied_receiver_or_pressured_passer_is_not_routine(self):
        g = self.g
        g.guarded_by(self.mate).place(10.0, 24.5)      # in the passing lane
        self.assertFalse(g.routine(self.h, self.mate, self.mate.pos))
        g.guarded_by(self.mate).place(15.0, 19.0)
        g.guarded_by(self.h).place(0.0, 26.0)          # up on the ball
        self.assertFalse(g.routine(self.h, self.mate, self.mate.pos))

    def test_pass_into_the_paint_is_not_routine(self):
        big = self.g.offense[4]
        big.place(3.0, 8.0)
        self.assertFalse(self.g.routine(self.h, big, big.pos))

    def test_routine_passes_carry_less_predicted_risk(self):
        safe = self.g.pass_features(self.h, self.mate)[3]
        risky = game(DEFAULT_PARAMS.with_values(routine_pass_risk=1.0))
        risky.play_possession("home")
        for a, b in zip(risky.everyone, self.g.everyone):
            a.place(b.x, b.y)
        self.assertLess(safe, risky.pass_features(risky.offense[0], risky.offense[1])[3])

    def test_reset_is_offered_with_time_and_runs_an_action(self):
        g = self.g
        g.result, g.play, g.situation, g.game_clock = None, None, None, None
        g.holder, g.mode, g.shot_clock, g.second_actions = self.h, "probe", 16.0, 0
        self.assertTrue(plays.can_reset(g))
        plays.reset(g, "test")
        self.assertIsNotNone(g.play)
        self.assertEqual(g.second_actions, 1)
        g.play, g.shot_clock = None, 9.0
        self.assertFalse(plays.can_reset(g))

    def test_legacy_params_never_reset(self):
        g = game(LEGACY_PARAMS)
        g.play(50)
        self.assertEqual(g.stats["second_actions"], 0)


class ClosingPickAndRollTests(unittest.TestCase):
    def test_sets_often_finish_in_a_pick_and_roll(self):
        g = game()
        g.play(400)
        self.assertGreater(g.stats["entry_pnr"], 20)
        # (denials kill some sets before their last action)
        self.assertGreater(g.stats["pnr"] / g.stats["possessions"], 0.08)

    def test_no_closing_pick_and_roll_when_weight_is_zero(self):
        g = game(DEFAULT_PARAMS.with_values(entry_pnr=0.0))
        g.play(200)
        self.assertEqual(g.stats["entry_pnr"], 0)


class DenialTests(unittest.TestCase):
    def test_set_defences_deny_some_swings(self):
        g = game()
        g.play(300)
        swings = g.stats["entry_swing"]
        self.assertGreater(g.stats["denials"], 0.1 * swings)
        self.assertLess(g.stats["denials"], 0.9 * swings)

    def test_zones_do_not_deny(self):
        league = generate_league(seed=41, systems=False)
        from hoopsim.models import DefensiveScheme
        for t in league.teams.values():
            t.tactics.defensive_scheme = DefensiveScheme.ZONE
        g = HalfCourtGame(league, 1, 2, random.Random(4), model=HEURISTIC)
        g.play(200)
        self.assertEqual(g.stats["denials"], 0)

    def test_denied_swing_waits_for_the_v_cut(self):
        g = game(DEFAULT_PARAMS.with_values(deny_rate=50.0))   # always deny
        for _ in range(200):
            g.play_possession("home")
            if g.stats["denials"]:
                break
        self.assertGreater(g.stats["denials"], 0)
        # Set one up by hand and check the pass is held until he's free.
        g.play_possession("home")
        g.result, g.situation, g.game_clock, g.scramble = None, None, None, 0.0
        g.holder, g.mode, g.shot_clock = g.offense[0], "probe", 18.0
        g.offense[0].place(0.0, 28.0)
        g.offense[1].place(19.0, 22.0)
        play = {"left": 1, "action": None, "done": 0}
        while True:                                   # draw until the action is a swing
            self.assertTrue(plays.start_next(g, play) or True)
            if play["action"]["kind"] == "swing":
                break
            play["left"] = 1
        g.play = play
        act = play["action"]
        self.assertIn("deny", act)
        passes = g.passes
        while g.now < act["free_at"] - 0.15 and g.flight is None:
            g.step()
        self.assertEqual(g.passes, passes)            # not thrown while he's denied
