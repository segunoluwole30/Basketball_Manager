"""Live coaching: shouted instructions, plays out of a timeout and end-of-game calls."""
import random
import unittest
from collections import Counter

from hoopsim import generate_league
from hoopsim.spatial import court as C
from hoopsim.spatial.fullgame import FullGame

# 24 games: 12 were too few to see every shout through the noise once the defence
# recovered (README "Defence that recovers": shot-mix shouts moved by +0.02 to +0.09
# per 12-game batch).
SEEDS = tuple(range(100, 124))


def coached_games(shouts=None, seeds=SEEDS, user="home"):
    """Play games with ``user`` coached by ``shouts``; per-side shot mix, time,
    turnovers and the on-ball cushion of the other side's handlers."""
    tot = Counter()
    for seed in seeds:
        g = FullGame(generate_league(seed=5, systems=False), 1, 2, random.Random(seed))
        m = g.attach_manager(user)
        for name, extra in (shouts or {}).items():
            m["shouts"][name] = dict({"left": None}, **extra)
        hc = g.hc
        count_attempt, possession, dribble_turnover = hc.count_attempt, g.possession, hc.dribble_turnover

        def count(h, z):
            tot[f"{h.side}_fga_{z}"] += 1
            tot[f"{h.side}_fga"] += 1
            tot[f"fga_{h.p.id}"] += 1
            count_attempt(h, z)

        def poss(offense, how, carrier):
            t0 = g.elapsed
            r = possession(offense, how, carrier)
            tot[f"{offense.key}_secs"] += g.elapsed - t0
            tot[f"{offense.key}_poss"] += 1
            return r

        def dribble(h):
            if hc.mode in ("probe", "drive") and C.dist_to_rim(h.pos) > 20:
                tot[f"{h.side}_cushion"] += C.dist(h.pos, hc.guarded_by(h).pos)
                tot[f"{h.side}_cushion_n"] += 1
            lost = dribble_turnover(h)
            tot[f"{h.side}_stripped"] += lost
            return lost

        hc.count_attempt, g.possession, hc.dribble_turnover = count, poss, dribble
        sim = g.run()
        for side in ("home", "away"):
            tot[f"{side}_tov"] += getattr(sim.box_score, side).totals().tov
        tot["intentional"] += g.stats["intentional_fouls"]
        tot["post_ups"] += hc.stats["post_ups"]
    return tot


def share(t, side, zone):
    return t[f"{side}_fga_{zone}"] / t[f"{side}_fga"]


def per_poss(t, side, what):
    return t[f"{side}_{what}"] / t[f"{side}_poss"]


class ShoutTests(unittest.TestCase):
    """Each shout moves what it should (``SEEDS`` games each against the same games
    without it; user team at home)."""

    @classmethod
    def setUpClass(cls):
        cls.base = coached_games()
        cls.runs = {}

    def run_with(self, name, **extra):
        if name not in self.runs:
            self.runs[name] = coached_games({name: extra})
        return self.runs[name]

    def test_attack_the_rim(self):
        t = self.run_with("attack_rim")
        # ~+0.05 over 48 games since README "Defence that recovers" (+0.02 to +0.09 per
        # 12-game batch; these seeds +0.021), so the bar sits inside that (was +0.02).
        self.assertGreater(share(t, "home", "rim"), share(self.base, "home", "rim") + 0.012)
        self.assertLess(share(t, "home", "three"), share(self.base, "home", "three"))

    def test_look_for_threes(self):
        t = self.run_with("threes")
        # The shout adds ~+0.05 to the three share over 48 games since README "Defence that
        # recovers" (+0.04 to +0.075 per 12-game batch; these seeds +0.057). It was ~+0.027
        # with the patient offence, when the bar went down to +0.015 (was +0.025).
        self.assertGreater(share(t, "home", "three"), share(self.base, "home", "three") + 0.015)
        # The threes come instead of shots in the paint: rim + short -0.053 on these seeds
        # (-0.053 over 48 games). Mid-range was the check before README "Shots with room";
        # with more patience mid-range jumpers are mostly late-clock shots now and the
        # shout moves them by ~0 (+0.001 over 48 games; -0.001 with twice the penalty).
        inside = lambda r: share(r, "home", "rim") + share(r, "home", "close")
        self.assertLess(inside(t), inside(self.base) - 0.02)

    def test_slow_it_down_and_push_the_pace(self):
        slow, push = self.run_with("slow"), self.run_with("push")
        base = per_poss(self.base, "home", "secs")
        self.assertGreater(per_poss(slow, "home", "secs"), base + 1.0)
        self.assertLess(per_poss(push, "home", "secs"), base - 1.0)
        self.assertLess(slow["home_poss"], self.base["home_poss"])

    def test_feed_the_post(self):
        t = self.run_with("post")
        self.assertGreater(t["post_ups"], self.base["post_ups"] * 1.15)

    def test_get_him_the_ball(self):
        league = generate_league(seed=5, systems=False)
        g = FullGame(league, 1, 2, random.Random(0))
        target = g.home.lineup[2].p.id                     # a wing, not the point guard
        t = self.run_with("ball_to", player=target)
        before = self.base[f"fga_{target}"] / self.base["home_fga"]
        # ~x1.25 over 48 games since README "Defence that recovers" (x1.04-x1.40 per
        # 12-game batch; these 24 seeds x1.18). It was ~x1.23 with the patient offence,
        # when the bar went down to x1.05 on 12 games.
        self.assertGreater(t[f"fga_{target}"] / t["home_fga"], before * 1.12)

    def test_pressure_the_ball(self):
        t = self.run_with("pressure")
        self.assertLess(t["away_cushion"] / t["away_cushion_n"],
                        self.base["away_cushion"] / self.base["away_cushion_n"] - 0.4)
        # Up on the ball, the defence strips more dribblers.
        # ~x1.26 over 48 games since README "Defence that recovers" (x1.04-x1.63 per
        # 12-game batch; these seeds x1.43), so the bar sits inside that (was x1.3).
        self.assertGreater(per_poss(t, "away", "stripped"), 1.15 * per_poss(self.base, "away", "stripped"))

    def test_protect_the_paint(self):
        t = self.run_with("paint")
        # Threes +0.044 and rim shots -0.040 over 72 games since minutes follow ability
        # (README "Minutes"), but per 12-game batch threes run -0.021 to +0.065 (these
        # seeds: +0.039 and -0.021) and rim shots -0.010 to -0.078. So: more threes and
        # clearly fewer rim shots (was threes +0.025 and rim below +0.01).
        self.assertGreater(share(t, "away", "three"), share(self.base, "away", "three"))
        self.assertLess(share(t, "away", "rim"), share(self.base, "away", "rim") - 0.01)

    def test_foul_now(self):
        t = coached_games({"foul": {}}, seeds=(5,), user="away")    # the away side trails this one
        base = coached_games(seeds=(5,), user="away")
        self.assertGreater(t["intentional"], base["intentional"] + 5)


class CoachingStateTests(unittest.TestCase):
    def game(self, seed=3, side="home"):
        g = FullGame(generate_league(seed=5, systems=False), 1, 2, random.Random(seed), record=True)
        return g, g.attach_manager(side)

    def test_untouched_commands_change_nothing(self):
        a = FullGame(generate_league(seed=5, systems=False), 1, 2, random.Random(3), record=True)
        b, m = self.game()
        self.assertEqual(m["endgame"], {"last_shot": "auto", "two_for_one": "auto", "foul": "auto"})
        self.assertEqual(a.run().result, b.run().result)
        self.assertEqual(a.stats, b.stats)

    def test_shouts_for_n_possessions_run_out(self):
        g, m = self.game()
        m["shouts"]["threes"] = {"left": 3}
        m["shouts"]["paint"] = {"left": 2}
        it = g.iter_game()
        for _ in range(4):
            next(it)
        self.assertEqual(m["shouts"]["threes"]["left"], 1)
        self.assertNotIn("paint", m["shouts"])
        for _ in range(3):
            next(it)
        self.assertEqual(m["shouts"], {})

    def test_play_out_of_a_timeout_runs(self):
        for play in ("swing", "handoff", "downscreen", "pnr"):
            g, m = self.game()
            it = g.iter_game()
            for _ in range(5):
                next(it)
            target = g.home.lineup[3].p.id if play == "pnr" else g.home.lineup[2].p.id
            m["timeout"] = True
            m["ato"] = {"play": play, "target": target if play in ("pnr", "downscreen") else None}
            for _ in range(8):
                next(it)
            self.assertEqual(g.stats["ato_run"], 1, play)
            self.assertEqual(g.hc.stats[f"called_{play}"], 1, play)
            self.assertTrue(any(e.text.startswith("Out of the timeout") for e in g.events))
            self.assertIsNone(m["ato_next"])

    def test_called_set_uses_the_target(self):
        g, m = self.game()
        hc = g.hc
        hc.play_possession("home")                        # set the engine up for a possession
        h = hc.offense[0]
        hc.holder, hc.shot_clock = h, 18.0
        for kind, who in (("pnr", hc.offense[2]), ("downscreen", hc.offense[1])):
            hc.forced_entry = (kind, who)
            from hoopsim.spatial import plays
            play = plays.plan(hc)
            self.assertIsNotNone(play)
            self.assertEqual(play["action"]["kind"], kind)
            self.assertIs(play["action"]["to"], who)
            self.assertIsNone(hc.forced_entry)

    def late(self, period=4, clock=20.0, mine=100, theirs=100):
        g, m = self.game()
        g.period, g.clock = period, clock
        g.hc.score.update(home=mine, away=theirs)
        return g, m

    def test_last_shot_override(self):
        g, m = self.late(clock=22.0)                       # tied, 22 s left
        self.assertEqual(g.plan(g.home, g.away)["hold"], 0.35)
        m["endgame"]["last_shot"] = "off"
        self.assertIsNone(g.plan(g.home, g.away))
        self.assertEqual(g.plan(g.away, g.home)["hold"], 0.35)   # the other bench decides for itself
        m["endgame"]["last_shot"] = "on"
        g.hc.score["home"] = 99                             # down one: the bench coach would hurry
        self.assertNotIn("hold", g.plan(g.away, g.home) or {})
        s = g.plan(g.home, g.away)
        self.assertEqual(s["hold"], 0.35)
        self.assertNotIn("hurry", s)

    def test_two_for_one_override(self):
        g, m = self.late(period=2, clock=33.0)
        self.assertEqual(g.plan(g.home, g.away), {"hurry": 0.12})
        m["endgame"]["two_for_one"] = "off"
        self.assertIsNone(g.plan(g.home, g.away))
        m["endgame"]["two_for_one"] = "on"
        self.assertEqual(g.plan(g.home, g.away), {"hurry": 0.25})
        g.period = 4                                        # tied in the 4th: no two-for-one by default
        self.assertEqual(g.plan(g.home, g.away), {"hurry": 0.25})
        self.assertIsNone(g.plan(g.away, g.home))

    def test_foul_override(self):
        g, m = self.late(clock=40.0, mine=90, theirs=97)    # down 7 with 40 s: bench coach waits
        self.assertFalse(g.should_foul(g.home, g.away))
        m["endgame"]["foul"] = "on"
        self.assertTrue(g.should_foul(g.home, g.away))
        g.clock = 20.0
        m["endgame"]["foul"] = "auto"
        self.assertTrue(g.should_foul(g.home, g.away))
        m["endgame"]["foul"] = "off"
        self.assertFalse(g.should_foul(g.home, g.away))
        m["shouts"]["foul"] = {"left": None}                # "foul now" beats the setting
        g.clock = 200.0
        self.assertTrue(g.should_foul(g.home, g.away))
        g.period = 3
        self.assertFalse(g.should_foul(g.home, g.away))     # only in the last period
        g.period, g.hc.score["home"] = 4, 98
        self.assertFalse(g.should_foul(g.home, g.away))     # ... and only when behind


class LiveCommandTests(unittest.TestCase):
    def setUp(self):
        from hoopsim.spatial.live import LiveMatch
        league = generate_league(seed=5, systems=False)
        league.rules.quarter_minutes = 3
        game = min(league.games.values(), key=lambda g: g.id)
        league.user_team_id = game.home_team_id
        self.match = LiveMatch(league, game, random.Random(2))
        self.match.next_chunk(0)
        self.players = self.match.state()["home"]["players"]

    def cmd(self, **c):
        return self.match.command(c)

    def bad(self, text, **c):
        from hoopsim.spatial.live import LiveError
        with self.assertRaisesRegex(LiveError, text):
            self.match.command(c)

    def test_shouts(self):
        pid = self.players[1]["id"]
        r = self.cmd(type="shout", shout="attack_rim")
        self.assertIn("until you cancel", r["message"])
        r = self.cmd(type="shout", shout="threes", possessions=5)
        self.assertIn("instead of Attack the rim", r["message"])
        self.cmd(type="shout", shout="ball_to", player=pid)
        self.cmd(type="shout", shout="pressure")
        shouts = {s["name"]: s for s in r["state"]["manager"]["shouts"]}
        shouts = {s["name"]: s for s in self.match.state()["manager"]["shouts"]}
        self.assertEqual(set(shouts), {"threes", "ball_to", "pressure"})
        self.assertEqual(shouts["threes"]["left"], 5)
        self.assertEqual(shouts["pressure"]["end"], "defense")
        self.assertIn(self.players[1]["name"], shouts["ball_to"]["label"])
        self.cmd(type="cancel_shout", shout="threes")
        self.assertEqual(len(self.match.state()["manager"]["shouts"]), 2)
        self.cmd(type="cancel_shout")
        self.assertEqual(self.match.state()["manager"]["shouts"], [])
        self.bad("unknown instruction", type="shout", shout="dance")
        self.bad("possessions must be", type="shout", shout="slow", possessions=0)
        self.bad("pick one of your players", type="shout", shout="ball_to", player=-5)
        self.bad("isn't one of your instructions", type="cancel_shout", shout="slow")

    def test_timeout_with_a_play(self):
        big = self.players[4]["id"]
        self.bad("unknown play", type="timeout", play="flex")
        self.bad("call a timeout first", type="ato", play="pnr")
        self.bad("only the pick-and-roll", type="timeout", play="swing", target=big)
        r = self.cmd(type="timeout", play="pnr", target=big)
        self.assertIn("pick-and-roll", r["message"])
        self.assertEqual(r["state"]["manager"]["ato"]["target"], big)
        r = self.cmd(type="ato", play="downscreen")
        self.assertEqual(r["state"]["manager"]["ato"]["play"], "downscreen")
        n = 0
        for _ in range(40):
            chunk = self.match.next_chunk(n, 1)
            n = chunk["next"]
            if self.match.fg.stats["ato_run"] + self.match.fg.stats["ato_missed"]:
                break
        self.assertEqual(self.match.fg.stats["ato_run"], 1)

    def test_endgame(self):
        r = self.cmd(type="endgame", last_shot="on", foul="off")
        self.assertEqual(r["state"]["manager"]["endgame"],
                         {"last_shot": "on", "two_for_one": "auto", "foul": "off"})
        self.bad("unknown end-of-game", type="endgame", overtime="on")
        self.bad("must be one of", type="endgame", foul="sometimes")
        self.bad("nothing to change", type="endgame")


if __name__ == "__main__":
    unittest.main()
