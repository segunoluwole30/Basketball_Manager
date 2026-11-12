"""Full spatial games: clock, overtime, fouls, substitutions, fatigue, transition, box scores."""
import argparse
import random
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from hoopsim import generate_league
from hoopsim.spatial.export import build_html
from hoopsim.spatial.fullgame import FullGame


def short_league(seed: int, quarter_minutes: int = 3, **rules):
    league = generate_league(seed=seed, systems=False)
    league.rules.quarter_minutes = quarter_minutes
    league.rules.overtime_minutes = 2
    for k, v in rules.items():
        setattr(league.rules, k, v)
    return league


class FullGameTests(unittest.TestCase):
    """One recorded regulation-length game."""

    @classmethod
    def setUpClass(cls):
        cls.league = generate_league(seed=5, systems=False)
        cls.game = FullGame(cls.league, 1, 2, random.Random(11), record=True)
        cls.sim = cls.game.run()

    def test_clock_and_quarters(self):
        g, r = self.game, self.sim.result
        self.assertGreaterEqual(g.period, 4)
        self.assertEqual(g.period, 4 + r.overtimes)
        game_seconds = 4 * 12 * 60 + 5 * 60 * r.overtimes
        for box in (self.sim.box_score.home, self.sim.box_score.away):
            self.assertEqual(len(box.period_points), g.period)
            self.assertAlmostEqual(sum(l.seconds for l in box.lines), 5 * game_seconds, places=3)
            self.assertGreater(box.possessions, 80)
        self.assertNotEqual(r.home_score, r.away_score)

    def test_box_score_totals_match_the_score(self):
        r = self.sim.result
        for box, score in ((self.sim.box_score.home, r.home_score), (self.sim.box_score.away, r.away_score)):
            t = box.totals()
            self.assertEqual(t.pts, score)
            self.assertEqual(sum(box.period_points), score)
            self.assertLessEqual(t.fgm, t.fga)
            self.assertLessEqual(t.tpm, t.tpa)
            self.assertLessEqual(t.ftm, t.fta)
            self.assertGreater(t.drb, 0)
            self.assertGreater(t.pf, 0)
        # Plus-minus balances across the two teams.
        pm = sum(l.plus_minus for b in (self.sim.box_score.home, self.sim.box_score.away) for l in b.lines)
        self.assertEqual(pm, 0)

    def test_substitutions_follow_the_rotation(self):
        g = self.game
        self.assertGreater(g.stats["substitutions"], 10)
        for side in g.sides.values():
            starters = [s for s in side.roster if s.starter]
            rotation = [s for s in side.roster if not s.starter and s.target_seconds > 0]
            unplanned = [s for s in side.roster if s.target_seconds == 0]
            self.assertEqual(len(starters), 5)
            avg = lambda seats: sum(s.seconds for s in seats) / max(1, len(seats))
            self.assertGreater(avg(starters), 24 * 60)
            self.assertGreater(avg(starters), avg(rotation))
            self.assertTrue(all(s.seconds > 0 for s in rotation))
            self.assertLess(sum(s.seconds for s in unplanned), 0.15 * sum(s.seconds for s in side.roster))
            self.assertTrue(all(s.seconds <= 46 * 60 for s in side.roster))

    def test_transition_follows_steals(self):
        g = self.game
        self.assertGreater(g.stats["transition"], 5)
        self.assertGreater(g.stats["transition_after_steal"], 0)
        # Half-court steals only: an intercepted ahead pass already happens at the
        # stealing team's end, so there is no floor to run.
        after = [b for a, b in zip(g.possessions, g.possessions[1:])
                 if a["result"] == "steal" and a["period"] == b["period"]
                 and not any("ahead pass" in e[1] for e in a["events"])]
        self.assertTrue(after)
        # The players start the next possession at the far end and run the floor.
        ran = [p for p in after if max(p["frames"][0][7 + 2 * n] for n in range(10)) > 600]
        self.assertGreaterEqual(len(ran), 0.9 * len(after))
        self.assertGreaterEqual(sum(p["transition"] for p in after), 0.5 * len(after))

    def test_export_and_quarter_filter(self):
        data = self.game.to_dict(period=2)
        self.assertTrue(data["possessions"])
        self.assertTrue(all(p["period"] == 2 for p in data["possessions"]))
        roster = {k: len(data[k]["players"]) for k in ("home", "away")}
        for p in data["possessions"]:
            for f in p["frames"]:
                self.assertEqual(len(f), 6 + 20 + 1)      # + the game clock
            for _, lineup in p["lineups"]:
                self.assertEqual(len(set(lineup[:5])), 5)
                self.assertTrue(all(0 <= i < roster["home"] for i in lineup[:5]))
                self.assertTrue(all(0 <= i < roster["away"] for i in lineup[5:]))
        page = build_html(self.game.to_dict())
        self.assertLess(len(page), 8_000_000)


class ShortGameTests(unittest.TestCase):
    def test_deterministic(self):
        def run():
            league = short_league(7)
            sim = FullGame(league, 3, 4, random.Random(5)).run()
            return sim.result, sim.box_score.to_dict()
        self.assertEqual(run(), run())

    def test_overtime(self):
        league = short_league(8, quarter_minutes=1)
        g = FullGame(league, 1, 2, random.Random(3))
        tip = g.jump_ball()
        other = g.other(tip)
        for starting in (tip, other, other, tip):
            g.play_period(starting)
        g.hc.score["away"] = g.hc.score["home"]                # force a tie after regulation
        while g.hc.score["home"] == g.hc.score["away"]:
            g.play_period(g.jump_ball())
        self.assertGreaterEqual(g.period, 5)
        overtime = g.period - 4
        for side in g.sides.values():
            self.assertAlmostEqual(sum(s.seconds for s in side.roster), 5 * (4 * 60 + 120 * overtime), places=3)
            self.assertEqual(side.timeouts <= league.rules.timeouts_per_overtime, True)

    def test_fouling_out(self):
        league = short_league(9, quarter_minutes=6, personal_foul_limit=3)
        # The first game in which someone fouls out (which seed that is depends on the
        # engine: seed 4 until README "Defence that recovers").
        for seed in range(4, 16):
            g = FullGame(league, 1, 2, random.Random(seed))
            g.run()
            fouled = [s for side in g.sides.values() for s in side.roster if g.fouled_out(s)]
            if fouled:
                break
        self.assertTrue(fouled)
        for s in fouled:
            self.assertIsNotNone(s.fouled_out_at)
            side = g.sides[s.side]
            bench_left = any(not g.fouled_out(o) for o in side.roster if not o.on_court)
            if bench_left:
                self.assertFalse(s.on_court)
            # Never brought back in after fouling out.
            back = [t for t, key, inn, _ in g.sub_log if inn == s.p.id and t > s.fouled_out_at]
            self.assertEqual(back, [])
            self.assertEqual(g.pf(s), 3)

    def test_fatigue_drains_on_court_and_recovers_on_bench(self):
        league = short_league(10)
        g = FullGame(league, 1, 2, random.Random(1))
        g.period, g.clock = 1, 600.0
        side = g.home
        on, off = side.lineup[0], side.bench[0]
        on.energy = off.energy = 0.6
        g.advance(120)
        self.assertLess(on.energy, 0.6)
        self.assertAlmostEqual(off.energy, 0.6 + g.gt.recover_per_second * 120)
        # Tired players are slower and shoot worse.
        self.assertLess(on.agent.max_speed, on.base[0])
        self.assertGreater(on.agent.fatigue_shot, 0)
        g.rest(1.0)
        self.assertEqual(on.energy, 1.0)
        self.assertAlmostEqual(on.agent.max_speed, on.base[0])

    def test_watch_full_game(self):
        from hoopsim.__main__ import cmd_watch
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "q.html"
            cmd_watch(argparse.Namespace(seed=2, possessions=6, home=None, away=None, load=None,
                                         out=str(out), no_open=True, full=True, quarter=1))
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 50_000)


if __name__ == "__main__":
    unittest.main()


class NonShootingFoulTests(unittest.TestCase):
    """Reach-ins and holds are a per-second risk of live play, not a per-possession one."""

    def fouls(self, rate: float) -> float:
        from dataclasses import replace
        from hoopsim.spatial.fullgame import DEFAULT_GAME_TUNING
        league = generate_league(seed=5, systems=False)
        gt = replace(DEFAULT_GAME_TUNING, non_shooting_foul_per_second=rate)
        n = 0
        for i in range(3):
            g = FullGame(league, 1 + i, 4 + i, random.Random(i), game_tuning=gt)
            g.run()
            n += g.hc.stats["non_shooting_fouls"]
        return n / 6                                  # per team-game

    def test_rate_sets_the_number_of_fouls(self):
        default = self.fouls(0.011)
        self.assertGreater(default, 3.0)
        self.assertLess(default, 12.0)
        self.assertGreater(self.fouls(0.03), 1.6 * default)        # 2.7x the rate: ~1.75x the fouls
        self.assertLess(self.fouls(1e-9), 0.2 * default)   # only end-of-game intentional fouls left


class FatigueShootingTests(unittest.TestCase):
    def test_fatigue_takes_the_same_fraction_off_every_shot(self):
        from hoopsim.spatial import HalfCourtGame
        league = generate_league(seed=5, systems=False)
        g = HalfCourtGame(league, 1, 2, random.Random(0))
        g.play_possession("home")
        shooter = g.offense[1]
        spots = [(0.0, 6.0), (0.0, 15.0), (0.0, 26.0)]          # rim, mid-range, three
        fresh = [g.make_prob(shooter, p, contest_d=6.0)[0] for p in spots]
        shooter.fatigue_shot = 0.05
        tired = [g.make_prob(shooter, p, contest_d=6.0)[0] for p in spots]
        ratios = [t / f for t, f in zip(tired, fresh)]
        for r in ratios:
            self.assertAlmostEqual(r, 0.95, places=3)


class ManagerTests(unittest.TestCase):
    def game(self, seed=3):
        league = generate_league(seed=5, systems=False)
        return FullGame(league, 1, 2, random.Random(seed), record=True)

    def test_no_manager_plays_exactly_as_before(self):
        a, b = self.game(), self.game()
        b.attach_manager("home")
        self.assertEqual(a.run().result, b.run().result)          # untouched commands change nothing

    def test_manual_subs_only_for_foul_outs(self):
        g = self.game()
        g.attach_manager("home")["auto_subs"] = False
        g.run()
        home_subs = [x for x in g.sub_log if x[1] == "home"]
        forced = sum(1 for s in g.home.roster if g.unavailable(s) and not s.on_court)   # fouled out or hurt
        self.assertEqual(len(home_subs), forced)
        self.assertTrue([x for x in g.sub_log if x[1] == "away"])

    def test_coach_timeout_and_sub_are_applied(self):
        g = self.game()
        m = g.attach_manager("away")
        it = g.iter_game()
        for _ in range(3):
            next(it)
        out = g.away.lineup[0]
        inn = next(s for s in g.away.bench if not g.fouled_out(s))
        m["timeout"] = True
        m["subs"].append((out.p.id, inn.p.id))
        before = g.away.timeouts
        for _ in range(12):
            next(it)
        self.assertEqual(g.away.timeouts, before - 1)
        self.assertTrue(any("coach's decision" in e.text for e in g.events))
        self.assertTrue(any(x[2] == inn.p.id and x[3] == out.p.id for x in g.sub_log))


class InboundTests(unittest.TestCase):
    def possessions(self, **gt):
        from dataclasses import replace
        from hoopsim.spatial.fullgame import DEFAULT_GAME_TUNING
        league = generate_league(seed=5, systems=False)
        g = FullGame(league, 1, 2, random.Random(4), record=True,
                     game_tuning=replace(DEFAULT_GAME_TUNING, **gt))
        g.run()
        return g.possessions

    def after_makes(self, ps):
        return [b for a, b in zip(ps, ps[1:]) if a["result"] == "score" and a["period"] == b["period"]]

    def test_possessions_after_a_make_start_with_the_inbound(self):
        after = self.after_makes(self.possessions())
        self.assertGreater(len(after), 20)
        # Someone is out of bounds under the far basket (y > 94 ft) when it starts.
        inbound = [p for p in after if max(p["frames"][0][7 + 2 * n] for n in range(10)) > 940]
        self.assertGreater(len(inbound), 0.9 * len(after))
        self.assertTrue(all(p["frames"][0][3] > 600 for p in after))     # the ball starts in the backcourt

    def test_switching_walkups_off_jumps_to_the_half_court(self):
        after = self.after_makes(self.possessions(inbound_walkups=False))
        jumped = [p for p in after if not p["transition"] and p["frames"][0][3] < 500]
        self.assertGreater(len(jumped), 0.8 * len(after))


def frame_gap(a, b, flip=False):
    """Largest distance (ft) any of the ten players moves between two frames; ``flip``
    turns ``a`` into the other team's attacking frame first."""
    gap = 0.0
    for n in range(10):
        x, y = a[6 + 2 * n], a[7 + 2 * n]
        if flip:
            x, y = -x, 940 - y
        gap = max(gap, ((x - b[6 + 2 * n]) ** 2 + (y - b[7 + 2 * n]) ** 2) ** 0.5 / 10)
    return gap


def out_of_bounds(frame):
    return any(abs(frame[6 + 2 * n]) > 250 or frame[7 + 2 * n] > 940 for n in range(10))


def inbound_spans(p):
    """(first frame, catch frame) of each dead-ball throw-in in a recorded possession."""
    spans, start = [], None
    for e in p["events"]:
        if " inbound from " in e[1]:
            start = e[0]
        elif " inbounds to " in e[1] and start is not None:
            spans.append((start, e[0]))
            start = None
    return spans


class DeadBallInboundTests(unittest.TestCase):
    """Throw-ins after dead balls: from where play stopped, clocks stopped until the touch."""

    @classmethod
    def setUpClass(cls):
        cls.game = FullGame(generate_league(seed=5, systems=False), 1, 2, random.Random(4), record=True)
        cls.game.run()
        ps = cls.game.possessions
        cls.after_dead = [(a, b) for a, b in zip(ps, ps[1:]) if a["result"] == "dead" and a["period"] == b["period"]]

    def test_possessions_after_dead_balls_start_where_play_stopped(self):
        # Enough dead-ball endings in this one game to check (was > 8; with turnovers
        # down to NBA rates, README "Patient offence", this game has 7).
        self.assertGreater(len(self.after_dead), 5)
        for a, b in self.after_dead:
            # No teleport: everyone starts where the last possession left him (court turned around).
            self.assertLess(frame_gap(a["frames"][-1], b["frames"][0], flip=a["offense"] != b["offense"]), 3.0)
            self.assertIn(" inbound from ", b["events"][0][1])
            start, catch = inbound_spans(b)[0]
            self.assertEqual(start, 0)
            # The inbounder is out of bounds with the ball when he throws it in.
            self.assertTrue(out_of_bounds(b["frames"][catch - 1]) or out_of_bounds(b["frames"][catch - 3]))

    def test_foul_inbounds_continue_the_possession_without_a_jump(self):
        spans = [(p, s) for p in self.game.possessions for s in inbound_spans(p) if s[0] > 0]
        self.assertGreater(len(spans), 3)                 # non-shooting fouls before the bonus
        for p, (start, catch) in spans:
            self.assertLess(frame_gap(p["frames"][start - 1], p["frames"][start]), 3.0)
            self.assertGreaterEqual(p["frames"][catch][1], 13.5)    # shot clock 14 or more

    def test_game_clock_stopped_until_the_touch(self):
        g = self.game
        checked = 0
        for p in g.possessions:
            for start, catch in inbound_spans(p):
                fr = p["frames"]
                clock0, shot0 = fr[start][26], fr[start][1]
                for row in fr[start:catch + 1]:
                    self.assertEqual(row[26], clock0)      # game clock
                    self.assertEqual(row[1], shot0)        # shot clock
                if catch + 5 < len(fr) and clock0 > 1:
                    self.assertLess(fr[catch + 5][26], clock0)   # runs once it's touched
                checked += 1
        self.assertGreater(checked, 10)
        # The setup took real (shown) time but no game time: the clock still adds up.
        self.assertGreater(g.stats["seconds_inbound_setup"], 2.0 * g.stats["inbounds"])
        played = sum(l.seconds for l in g.result.box_score.home.lines)
        self.assertAlmostEqual(played, 5 * (2880 + 300 * g.result.result.overtimes), places=3)

    def test_inbound_steals_are_rare_but_possible(self):
        from dataclasses import replace
        from hoopsim.spatial.transition import DEFAULT_OPEN_TUNING
        rare = Counter()
        for i in range(4):
            g = FullGame(short_league(i, quarter_minutes=6), 1, 2, random.Random(i))
            g.run()
            rare += g.stats
        self.assertGreater(rare["inbounds"], 40)
        self.assertLess(rare["inbound_steals"], 0.04 * rare["inbounds"])
        # A defence that jumps every passing lane does pick some off: turnovers and steals.
        # ~1.4 a game, but 0 in some (seeds 1 and 2 since README "Shots with room";
        # seeds 1-8 gave 0/0/1/1/1/4/1/3), so six games.
        steals = 0
        for seed in range(1, 7):
            g = FullGame(generate_league(seed=seed, systems=False), 1, 2, random.Random(seed), record=True,
                         open_tuning=replace(DEFAULT_OPEN_TUNING, inbound_steal=4.0))
            g.run()
            self.assertEqual(g.stats["inbound_steals"], g.hc.stats["inbound_steals"])
            stolen = [p for p in g.possessions if any("picks off the inbound pass" in e[1] for e in p["events"])]
            self.assertEqual(len(stolen), g.stats["inbound_steals"])
            self.assertTrue(all(p["result"] == "steal" for p in stolen))      # a live-ball turnover
            box = g.result.box_score
            self.assertGreaterEqual(box.home.totals().stl + box.away.totals().stl, len(stolen))
            steals += len(stolen)
        self.assertGreater(steals, 0)

    def test_late_timeout_inbounds_from_the_frontcourt_sideline(self):
        for seed in range(12):
            g = FullGame(generate_league(seed=5, systems=False), 1, 2, random.Random(seed), record=True)
            it = g.iter_game()
            next(it)                                       # a real possession: players somewhere
            if g.possessions[-1]["result"] not in ("drb", "score", "ft"):
                continue
            last = g.possessions[-1]
            offense = g.other(g.sides[last["offense"]])
            g.period, g.clock = 4, 30.0                     # trailing by two, 30 seconds left
            g.hc.score[offense.key] = 100
            g.hc.score[g.other(offense).key] = 102
            g._dead_spot = dict(g._dead_spot, period=4)
            g.possession(offense, last["result"], None)
            p = g.possessions[-1]
            if not any("draw up a play" in e.text for e in g.events):
                continue                                   # no timeout this time (20% don't)
            self.assertIn("frontcourt after the timeout", p["events"][0][1])
            self.assertLess(frame_gap(last["frames"][-1], p["frames"][0], flip=True), 3.0)
            start, catch = inbound_spans(p)[0]
            thrower = p["frames"][catch - 3]
            self.assertTrue(any(abs(thrower[6 + 2 * n]) > 250 and 230 <= thrower[7 + 2 * n] <= 330
                                for n in range(10)))       # on the sideline at the 28-ft mark
            self.assertLess(p["frames"][catch][3], 470)     # caught in the frontcourt
            self.assertEqual(p["frames"][catch][26], 30.0)  # no time off yet
            return
        self.fail("no late timeout in 12 tries")

    def test_deterministic(self):
        def play():
            g = FullGame(short_league(2), 1, 2, random.Random(9), record=True)
            g.run()
            return g.possessions, dict(g.stats)
        (p1, s1), (p2, s2) = play(), play()
        self.assertGreater(s1["inbounds"], 0)
        self.assertEqual(s1, s2)
        self.assertEqual(p1, p2)

    def test_switching_dead_ball_inbounds_off_jumps(self):
        from dataclasses import replace
        from hoopsim.spatial.fullgame import DEFAULT_GAME_TUNING
        g = FullGame(generate_league(seed=5, systems=False), 1, 2, random.Random(4), record=True,
                     game_tuning=replace(DEFAULT_GAME_TUNING, dead_ball_inbounds=False))
        g.run()
        self.assertEqual(g.stats["inbounds"], 0)
        ps = g.possessions
        after = [(a, b) for a, b in zip(ps, ps[1:]) if a["result"] == "dead" and a["period"] == b["period"]]
        jumped = [b for a, b in after if frame_gap(a["frames"][-1], b["frames"][0], flip=True) > 4.0]
        self.assertGreater(len(jumped), 0.8 * len(after))
