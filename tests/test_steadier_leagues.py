"""Steadier leagues (README "Steadier leagues"): a team's tactics and system move its
numbers about as far as an NBA team's identity does, so a league's means don't
follow whichever systems and rosters it happens to have."""
import math
import random
import unittest
from collections import Counter
from unittest import mock

from hoopsim import generate_league
from hoopsim.models import OffensiveFocus
from hoopsim.spatial.fullgame import FullGame
from hoopsim.spatial.params import DEFAULT_PARAMS, LEGACY_PARAMS
from hoopsim.spatial.sim import POST_BLOCK, HalfCourtGame
from hoopsim.systems import apply

# The new behaviour switched off (everything else at its default).
UNDAMPED = DEFAULT_PARAMS.with_values(tempo_span=0.0, system_tempo=1.0, team_bias_span=0.0,
                                      rebound_rating=0.25, post_strip=1.0, inside_pair=1.0)


class SwitchTests(unittest.TestCase):
    def test_legacy_params_switch_it_all_off(self):
        for k, off in (("tempo_span", 0.0), ("system_tempo", 1.0), ("team_bias_span", 0.0),
                       ("rebound_rating", 0.25), ("post_strip", 1.0), ("inside_pair", 1.0)):
            self.assertEqual(getattr(LEGACY_PARAMS, k), off, k)
            self.assertNotEqual(getattr(DEFAULT_PARAMS, k), off, k)

    def test_pace_is_bent_not_shifted(self):
        p = DEFAULT_PARAMS
        self.assertEqual(p.pace(10), 10.0)
        self.assertAlmostEqual(p.pace(11), 10.0 + 3 * math.tanh(1 / 3))      # small differences kept
        self.assertAlmostEqual(p.pace(14), 20 - p.pace(6))                    # symmetric
        self.assertLess(p.pace(14), 12.8)
        self.assertLess(p.pace(20), 13.0)                                     # never past 10 + span
        self.assertEqual(LEGACY_PARAMS.pace(14), 14)
        self.assertEqual(LEGACY_PARAMS.bend(-0.07, 0.0), -0.07)


class MechanismTests(unittest.TestCase):
    def setUp(self):
        self.lg = generate_league(seed=41, systems=False)

    def game(self, params, seed=5):
        g = HalfCourtGame(self.lg, 1, 2, random.Random(seed), record=False, params=params)
        g.play_possession("home")
        g.side = "home"
        g.shot_clock = 12.0
        return g

    def test_team_nudges_are_bent_and_tendencies_are_not(self):
        t = self.lg.teams[1].tactics
        apply(t, offense="grit_grind")
        t.three_point_rate = 1                   # slider, focus and system all say: no threes
        g, raw = self.game(DEFAULT_PARAMS), self.game(UNDAMPED)
        span = DEFAULT_PARAMS.team_bias_span
        for h in g.agents["home"]:
            player, team = g.team_style(h, "shoot", ("shoot", "three"))
            bent = g.style_bias(h, "shoot", ("shoot", "three"))
            self.assertAlmostEqual(bent, player + span * math.tanh((team - 0.02) / span))
            self.assertGreater(bent - player, -span)
            other = next(a for a in raw.agents["home"] if a.p.id == h.p.id)
            # Undamped, the same nudges stack: -0.072 slider and focus, -0.02 system.
            self.assertAlmostEqual(raw.style_bias(other, "shoot", ("shoot", "three"))
                                   + raw.system_bias(other, "shoot", ("shoot", "three")),
                                   player + team - 0.02)

    def test_a_running_system_runs_after_makes_less(self):
        t = self.lg.teams[1].tactics
        apply(t, offense="seven_seconds")
        push = {}
        for name, params in (("on", DEFAULT_PARAMS), ("off", UNDAMPED), ("legacy", LEGACY_PARAMS)):
            fg = FullGame(self.lg, 1, 2, random.Random(1), params=params)
            push[name] = fg._push_after_make(fg.home)
        logit = lambda q: math.log(q / (1 - q))                                  # noqa: E731
        base = logit(0.08)
        self.assertAlmostEqual(logit(push["off"]), base + 0.05 * (t.pace - 10) + 0.5)
        self.assertAlmostEqual(logit(push["on"]), base + 0.05 * (DEFAULT_PARAMS.pace(t.pace) - 10) + 0.25)
        self.assertEqual(push["off"], push["legacy"])

    def test_strips_in_the_post_are_rarer(self):
        rates = {}
        for name, params in (("on", DEFAULT_PARAMS), ("off", UNDAMPED)):
            g = self.game(params)
            h = g.offense[4]
            on = g.guarded_by(h)
            h.place(8.0, 6.0)
            on.place(7.0, 4.8)
            on.stuck_until = -1.0
            g.mode, g.holder = "post", h
            hits = 0
            g.rng = random.Random(1)
            with mock.patch.object(HalfCourtGame, "end"), mock.patch.object(HalfCourtGame, "say"):
                for _ in range(20000):
                    g.result = None
                    hits += g.dribble_turnover(h)
            rates[name] = hits
        self.assertAlmostEqual(rates["on"] / rates["off"], DEFAULT_PARAMS.post_strip, delta=0.12)

    def test_inside_focus_pairs_the_bigs_only_some_of_the_time(self):
        t = self.lg.teams[1].tactics
        t.offensive_focus = OffensiveFocus.INSIDE
        g = HalfCourtGame(self.lg, 1, 2, random.Random(3), record=False)
        if not g.can_post(g.agents["home"][3]):
            self.skipTest("the four can't post in this league")
        paired = 0
        n = 300
        for _ in range(n):
            g.play_possession("home", {"shot_clock": 0.5})
            paired += abs(abs(g.agents["home"][3].spot[0]) - POST_BLOCK[0]) < 0.01 and \
                abs(g.agents["home"][3].spot[1] - POST_BLOCK[1]) < 0.01
        self.assertAlmostEqual(paired / n, DEFAULT_PARAMS.inside_pair, delta=0.1)


def team_numbers(params, system, games=6):
    """A Seven Seconds or a Grit and Grind team's pace, turnovers and offensive rebound
    rate in full games against the same opponents and seeds."""
    lg = generate_league(seed=11, num_teams=12, systems=False)
    apply(lg.teams[1].tactics, offense=system)
    opps = [t for t in sorted(lg.teams) if t != 1]
    c = Counter()
    for i in range(games):
        sim = FullGame(lg, 1, opps[i % len(opps)], random.Random(100 + i), params=params).run()
        mine, them = sim.box_score.home, sim.box_score.away
        t, o = mine.totals(), them.totals()
        c["poss"] += mine.possessions
        c["tov"] += t.tov
        c["orb"] += t.orb
        c["opp_drb"] += o.drb
    return {"poss": c["poss"] / games, "tov": 100 * c["tov"] / c["poss"],
            "orb": c["orb"] / (c["orb"] + c["opp_drb"])}


class TeamSpreadTests(unittest.TestCase):
    """Paired full games: the same team as Seven Seconds or Less and as Grit and Grind.
    Measured on 6 games each (seed 11), Seven Seconds / Grit and Grind: possessions
    105.7 / 90.0 undamped against 100.2 / 92.8, turnovers per 100 12.8 / 24.3 against
    12.6 / 20.6, offensive rebound rate .213 / .301 against .204 / .237."""

    @classmethod
    def setUpClass(cls):
        cls.on = {s: team_numbers(DEFAULT_PARAMS, s) for s in ("seven_seconds", "grit_grind")}
        cls.off = {s: team_numbers(UNDAMPED, s) for s in ("seven_seconds", "grit_grind")}

    def gap(self, side, k):
        return side["seven_seconds"][k] - side["grit_grind"][k]

    def test_tempo_still_differs_but_less(self):
        self.assertGreater(self.gap(self.on, "poss"), 4)
        self.assertLess(self.gap(self.on, "poss"), self.gap(self.off, "poss") - 3)

    def test_turnovers_follow_the_system_less(self):
        self.assertLess(-self.gap(self.on, "tov"), -self.gap(self.off, "tov") - 2)

    def test_crashing_the_glass_pays_less(self):
        self.assertLess(-self.gap(self.on, "orb"), -self.gap(self.off, "orb") - 0.03)
        self.assertGreater(self.on["grit_grind"]["orb"], self.on["seven_seconds"]["orb"])


if __name__ == "__main__":
    unittest.main()
