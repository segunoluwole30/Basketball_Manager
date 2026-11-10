import argparse
import random
import tempfile
import unittest
from pathlib import Path

from hoopsim import Contract, Injury, generate_league, load_league, save_league
from hoopsim.manager import (
    AIManager, choose_team, offer_contract, propose_trade, release_player, scouted_potential,
    set_minutes, set_starters, set_tactics,
)
from hoopsim.models import DefensiveScheme, Phase
from hoopsim.play import IO, Game
from hoopsim.season import play_full_season, play_regular_season, run_offseason


class HandsOff(AIManager):
    """Says no to everything and records what it was asked."""

    def __init__(self):
        self.asked = {"options": [], "resign": [], "draft": [], "fa": 0, "trades": [], "waive": 0}

    def exercise_team_option(self, league, player, fair):
        self.asked["options"].append(player.id)
        return False

    def resign(self, league, expiring):
        self.asked["resign"] += [p.id for p, _, _ in expiring]
        return {}

    def draft_pick(self, league, pick, available):
        choice = min(available, key=lambda p: p.current_ability)   # the worst one: AI never would
        self.asked["draft"].append(choice.id)
        return choice

    def free_agency(self, league, pool, terms, round_no):
        self.asked["fa"] += 1
        return []

    def respond_to_trade(self, league, offer):
        self.asked["trades"].append(offer)
        return False

    def choose_waivers(self, league, count):
        self.asked["waive"] += 1
        return super().choose_waivers(league, count)


class UserDecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.league = generate_league(seed=31)
        cls.rng = random.Random(31)
        cls.uid = min(cls.league.teams)
        choose_team(cls.league, cls.uid)
        cls.mgr = HandsOff()
        play_full_season(cls.league, cls.rng, cls.mgr)
        cls.before = {p.id for p in cls.league.roster(cls.uid)}
        cls.expiring = {p.id for p in cls.league.roster(cls.uid)
                        if p.contract.expires_season <= cls.league.season}
        cls.report = run_offseason(cls.league, cls.rng, cls.mgr)

    def test_expiring_players_left_because_we_said_no(self):
        self.assertEqual(set(self.mgr.asked["resign"]), self.expiring - {
            pid for pid in self.expiring if self.league.players[pid].retired})
        roster = {p.id for p in self.league.roster(self.uid)}
        self.assertFalse(roster & self.expiring)

    def test_our_draft_picks_are_ours(self):
        drafted = {p.id for p in self.league.roster(self.uid)
                   if p.draft_year == self.league.season and p.draft_pick}
        self.assertTrue(set(self.mgr.asked["draft"]))
        self.assertTrue(drafted <= set(self.mgr.asked["draft"]))

    def test_no_signings_or_trades_we_didnt_make(self):
        roster = {p.id for p in self.league.roster(self.uid)}
        arrivals = roster - self.before
        for pid in arrivals:
            p = self.league.players[pid]
            drafted = pid in self.mgr.asked["draft"]
            minimum_fill = p.contract.salary == self.league.rules.min_salary   # only to reach 13
            self.assertTrue(drafted or minimum_fill, p.name)
        for line in self.report["trades"]:
            self.assertNotIn(self.league.teams[self.uid].abbreviation + " send", line)
        self.assertGreater(self.mgr.asked["fa"], 0)

    def test_league_still_legal(self):
        self.assertEqual(self.report["problems"], [])


class LineupTests(unittest.TestCase):
    def setUp(self):
        self.league = generate_league(seed=32, num_teams=4)
        self.uid = min(self.league.teams)
        choose_team(self.league, self.uid)
        self.roster = self.league.roster(self.uid)

    def test_starters_persist_through_the_season(self):
        bench_guys = [p.id for p in self.roster[-5:]]
        ok, msg = set_starters(self.league, bench_guys)
        self.assertTrue(ok, msg)
        play_regular_season(self.league, random.Random(1), days=20)
        self.assertEqual(self.league.teams[self.uid].tactics.starters, bench_guys)
        self.assertFalse(self.league.teams[self.uid].auto_lineup)

    def test_bad_lineups_rejected(self):
        ids = [p.id for p in self.roster]
        self.assertFalse(set_starters(self.league, ids[:4])[0])
        self.assertFalse(set_starters(self.league, ids[:4] + [ids[0]])[0])
        other = next(p.id for p in self.league.players.values() if p.team_id not in (None, self.uid))
        self.assertFalse(set_starters(self.league, ids[:4] + [other])[0])
        self.roster[0].injury = Injury("sprain", 3)
        self.assertFalse(set_starters(self.league, ids[:5])[0])

    def test_minutes_always_total_240(self):
        ids = [p.id for p in self.roster]
        ok, _ = set_minutes(self.league, {ids[0]: 40, ids[6]: 25})
        self.assertTrue(ok)
        targets = self.league.teams[self.uid].tactics.minutes_targets
        self.assertEqual(sum(targets.values()), 240)
        self.assertEqual(targets[ids[0]], 40)
        self.assertFalse(set_minutes(self.league, {ids[0]: 60})[0])
        starter = self.league.teams[self.uid].tactics.starters[0]
        self.assertFalse(set_minutes(self.league, {starter: 0})[0])
        self.assertIn(starter, targets)                           # unchanged after the refusal

    def test_tactics(self):
        ok, _ = set_tactics(self.league, pace=18, defense="zone", focus="perimeter")
        self.assertTrue(ok)
        t = self.league.teams[self.uid].tactics
        self.assertEqual((t.pace, t.defensive_scheme), (18, DefensiveScheme.ZONE))
        self.assertFalse(set_tactics(self.league, pace=25)[0])
        self.assertFalse(set_tactics(self.league, defense="box-and-one")[0])
        self.assertFalse(set_tactics(self.league, swagger=20)[0])


class MoveTests(unittest.TestCase):
    def setUp(self):
        self.league = generate_league(seed=33)
        self.uid = min(self.league.teams)
        choose_team(self.league, self.uid)
        self.rules = self.league.rules

    def over_the_cap(self):
        self.league.roster(self.uid)[0].contract = Contract(self.rules.salary_cap, self.league.season + 2)

    def test_signing_respects_ask_and_cap(self):
        fa = self.league.free_agents()[0]
        self.assertFalse(offer_contract(self.league, fa.id, self.rules.min_salary, 1)[0] and
                         fa.current_ability > 80)
        self.over_the_cap()
        too_much = self.rules.mid_level_exception + 1_000_000
        ok, msg = offer_contract(self.league, fa.id, too_much, 1)
        self.assertFalse(ok)
        self.assertIn("can't pay", msg)

    def test_mle_once_per_year(self):
        self.over_the_cap()
        fas = [p for p in self.league.free_agents() if not p.injury][:2]
        ok, msg = offer_contract(self.league, fas[0].id, self.rules.mid_level_exception, 1)
        self.assertTrue(ok, msg)
        self.assertIn("mid-level", msg)
        ok, _ = offer_contract(self.league, fas[1].id, self.rules.mid_level_exception, 1)
        self.assertFalse(ok)

    def test_minimum_deal_keeps_the_mle(self):
        # Regression: over the cap, a minimum contract used to use up the mid-level exception.
        self.over_the_cap()
        fas = [p for p in self.league.free_agents() if not p.injury]
        cheap = min(fas, key=lambda p: p.current_ability)
        ok, msg = offer_contract(self.league, cheap.id, self.rules.min_salary, 1)
        self.assertTrue(ok, msg)
        self.assertIn("minimum", msg)
        self.assertNotIn(self.uid, self.league.mle_used)
        ok, msg = offer_contract(self.league, fas[0].id, self.rules.mid_level_exception, 1)
        self.assertTrue(ok, msg)
        self.assertIn("mid-level", msg)

    def test_full_roster(self):
        while len(self.league.roster(self.uid)) < self.rules.max_roster:
            fa = self.league.free_agents()[-1]
            ok, msg = offer_contract(self.league, fa.id, self.rules.min_salary, 1)
            self.assertTrue(ok, msg)
        fa = self.league.free_agents()[-1]
        ok, msg = offer_contract(self.league, fa.id, self.rules.min_salary, 1)
        self.assertFalse(ok)
        self.assertIn("full", msg)

    def test_release_keeps_minimum_roster(self):
        roster = self.league.roster(self.uid)
        self.assertEqual(len(roster), self.rules.min_roster)
        self.assertFalse(release_player(self.league, roster[-1].id)[0])
        fa = self.league.free_agents()[-1]
        offer_contract(self.league, fa.id, self.rules.min_salary, 1)
        ok, _ = release_player(self.league, roster[-1].id)
        self.assertTrue(ok)
        self.assertIsNone(self.league.players[roster[-1].id].team_id)

    def test_trades_judged_by_ai(self):
        mine = self.league.roster(self.uid)
        other = sorted(self.league.teams)[1]
        theirs = self.league.roster(other)
        # Our worst for their best: no chance.
        ok, msg = propose_trade(self.league, [mine[-1].id], other, [theirs[0].id])
        self.assertFalse(ok, msg)
        # Our star for their overpaid bench player on the same salary: they'll take that.
        star = mine[0]
        other_team = other
        scrub = theirs[-1]
        scrub.contract = Contract(star.contract.salary, self.league.season + 1)
        self.assertLess(scrub.current_ability, star.current_ability)
        ok, msg = propose_trade(self.league, [star.id], other_team, [scrub.id])
        self.assertTrue(ok, msg)
        self.assertEqual(self.league.players[star.id].team_id, other_team)
        self.assertEqual(self.league.players[scrub.id].team_id, self.uid)
        # Not our player any more.
        self.assertFalse(propose_trade(self.league, [star.id], other_team, [])[0])
        # Salary rules apply: an over-the-cap team can't absorb a big contract for a small one.
        self.league.roster(other)[0].contract = Contract(self.rules.salary_cap, self.league.season + 1)
        big = max(self.league.roster(self.uid), key=lambda p: p.contract.salary)
        cheap = min(self.league.roster(other), key=lambda p: p.contract.salary)
        if big.contract.salary > cheap.contract.salary * 1.25 + 100_000:
            ok, msg = propose_trade(self.league, [big.id], other, [cheap.id])
            self.assertFalse(ok)
            self.assertIn("salaries", msg)

    def test_scouting_is_stable_and_narrows_with_age(self):
        young = [p for p in self.league.players.values() if p.age(self.league.current_date) <= 20]
        old = [p for p in self.league.players.values() if p.age(self.league.current_date) >= 28]
        for p in young[:5]:
            self.assertEqual(scouted_potential(self.league, p), scouted_potential(self.league, p))
        width = lambda ps: sum(hi - lo for _, lo, hi in (scouted_potential(self.league, p) for p in ps)) / len(ps)
        self.assertGreater(width(young), width(old))

    def test_save_load_keeps_user_settings(self):
        set_starters(self.league, [p.id for p in self.league.roster(self.uid)[:5]])
        self.league.mle_used = [self.uid]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "s.db"
            save_league(self.league, path)
            loaded = load_league(path)
        self.assertEqual(loaded.user_team_id, self.uid)
        self.assertFalse(loaded.teams[self.uid].auto_lineup)
        self.assertEqual(loaded.mle_used, [self.uid])


class CliTests(unittest.TestCase):
    def test_commands_run(self):
        league = generate_league(seed=34, num_teams=4)
        uid = min(league.teams)
        choose_team(league, uid)
        roster = league.roster(uid)
        fa = league.free_agents()[0]
        other = next(t for t in league.teams.values() if t.id != uid)
        with tempfile.TemporaryDirectory() as d:
            save = str(Path(d) / "game.db")
            script = iter([
                "help", "roster", f"roster {other.abbreviation}", f"player {roster[0].id}", "lineup",
                "start " + " ".join(str(p.id) for p in roster[:5]), f"minutes {roster[6].id}=20",
                "tactics pace=15 defense=switch", "tactics nonsense=1", "auto on", "sim 2", "week",
                "fa 5", f"sign {fa.id} 0.5 1", "sign 1 2", "trade nonsense",
                f"trade {roster[-1].id} for {other.abbreviation} {league.roster(other.id)[0].id}",
                "standings", "leaders", "schedule", "results", "teams", "finances", "history",
                "next", "bogus", f"save {save}", "quit", "n",
            ])
            out = []
            Game(league, IO(ask=lambda prompt: next(script), say=out.append), random.Random(1)).run()
            text = "\n".join(out)
            self.assertIn("Starting:", text)
            self.assertIn("Unknown command 'bogus'", text)
            self.assertIn("isn't over yet", text)
            self.assertIn("unknown tactic", text)
            self.assertIn("Saved to", text)
            self.assertNotIn("Traceback", text)
            loaded = load_league(save)
            self.assertEqual(loaded.user_team_id, uid)
            self.assertEqual(loaded.teams[uid].tactics.pace, 15)


if __name__ == "__main__":
    unittest.main()
