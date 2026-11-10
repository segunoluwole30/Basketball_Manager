"""Set plays: entry actions before the offence attacks."""
import random
import unittest

from hoopsim import generate_league
from hoopsim.spatial import HalfCourtGame, plays
from hoopsim.spatial.params import DEFAULT_PARAMS, LEGACY_PARAMS
from hoopsim.spatial.values import HEURISTIC


def game(params=DEFAULT_PARAMS, pace=None, seed=4):
    league = generate_league(seed=41)
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
        self.assertGreater(with_plays.stats["passes"], 1.3 * without.stats["passes"])
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
