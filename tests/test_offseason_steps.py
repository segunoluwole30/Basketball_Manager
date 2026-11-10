"""The offseason as resumable steps: same results as before, pausable, savable."""
import copy
import json
import random
import tempfile
import unittest
from pathlib import Path

from hoopsim import generate_league, load_league, save_league
from hoopsim.manager import AIManager, choose_team
from hoopsim.models import Phase
from hoopsim.season import (
    answer_offseason, manager_answer, offseason_pending, offseason_report, pending_players,
    play_full_season, run_offseason, start_offseason,
)
from tests import legacy_offseason
from tests.test_manager import HandsOff


def fingerprint(league) -> str:
    """Everything that matters about a league, as one string."""
    return json.dumps({
        "season": league.season, "date": league.current_date.isoformat(), "phase": league.phase.value,
        "teams": [t.to_dict() for t in league.teams.values()],
        "players": sorted((p.to_dict() for p in league.players.values()), key=lambda d: d["id"]),
        "games": sorted((g.to_dict() for g in league.games.values()), key=lambda d: d["id"]),
        "mle": league.mle_used, "user": league.user_team_id,
    }, sort_keys=True)


def clone(league, rng):
    lg = copy.deepcopy(league)
    r = random.Random()
    r.setstate(rng.getstate())
    return lg, r


def normal(report):
    """Reports compared as JSON (development keys become strings either way)."""
    return json.loads(json.dumps(report, sort_keys=True))


class Recorder(AIManager):
    """The AI's answers, but also records every question asked."""

    def __init__(self):
        self.kinds = []

    def exercise_team_option(self, league, player, fair):
        self.kinds.append("options")
        return super().exercise_team_option(league, player, fair)

    def resign(self, league, expiring):
        self.kinds.append("resign")
        return super().resign(league, expiring)

    def draft_pick(self, league, pick, available):
        self.kinds.append("draft")
        return super().draft_pick(league, pick, available)

    def free_agency(self, league, pool, terms, round_no):
        self.kinds.append("free_agency")
        return super().free_agency(league, pool, terms, round_no)

    def respond_to_trade(self, league, offer):
        self.kinds.append("trade_offer")
        return super().respond_to_trade(league, offer)

    def choose_waivers(self, league, count):
        self.kinds.append("cuts")
        return super().choose_waivers(league, count)


class OffseasonStepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.league = generate_league(seed=41, num_teams=8)
        cls.rng = random.Random(41)
        cls.uid = min(cls.league.teams)
        choose_team(cls.league, cls.uid)
        play_full_season(cls.league, cls.rng, AIManager())
        cls.legacy = {}
        for name, mgr in (("ai", None), ("assistant", AIManager()), ("handsoff", HandsOff())):
            lg, r = clone(cls.league, cls.rng)
            report = legacy_offseason.run_offseason(lg, r, mgr)
            cls.legacy[name] = (fingerprint(lg), normal(report), r.random())

    def check_same(self, name, lg, report, r):
        fp, legacy_report, next_draw = self.legacy[name]
        self.assertEqual(normal(report), legacy_report)
        self.assertEqual(fingerprint(lg), fp)
        self.assertEqual(r.random(), next_draw)            # the generator is in the same place too

    def test_run_offseason_unchanged_without_a_manager(self):
        lg, r = clone(self.league, self.rng)
        self.check_same("ai", lg, run_offseason(lg, r), r)

    def test_run_offseason_unchanged_with_the_assistant(self):
        lg, r = clone(self.league, self.rng)
        self.check_same("assistant", lg, run_offseason(lg, r, AIManager()), r)

    def test_run_offseason_unchanged_when_saying_no_to_everything(self):
        lg, r = clone(self.league, self.rng)
        self.check_same("handsoff", lg, run_offseason(lg, r, HandsOff()), r)

    def test_steps_with_save_and_load_at_every_pause(self):
        """Driving it a step at a time, saving and loading (with a fresh, wrongly
        seeded generator) at every question, gives exactly the same summer."""
        lg, r = clone(self.league, self.rng)
        mgr = AIManager()
        kinds = []
        pending = start_offseason(lg, r)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mid.db"
            while pending is not None:
                kinds.append(pending["kind"])
                self.assertEqual(lg.phase, Phase.OFFSEASON)
                save_league(lg, path)
                lg = load_league(path)
                self.assertEqual(offseason_pending(lg), json.loads(json.dumps(pending)))
                r = random.Random(999)                        # restored from the save
                pending = answer_offseason(lg, r, manager_answer(lg, mgr, offseason_pending(lg)))
        self.assertIsNone(lg.offseason)
        self.assertEqual(lg.phase, Phase.REGULAR_SEASON)
        self.check_same("assistant", lg, lg.last_offseason, r)
        self.assertIn("draft", kinds)
        self.assertIn("free_agency", kinds)

    def test_questions_match_what_the_manager_is_asked(self):
        lg, r = clone(self.league, self.rng)
        rec = Recorder()
        run_offseason(lg, r, rec)
        lg2, r2 = clone(self.league, self.rng)
        kinds = []
        pending = start_offseason(lg2, r2)
        while pending is not None:
            kinds.append(pending["kind"])
            if pending["kind"] == "options":
                kinds[-1:] = ["options"] * len(pending["options"])
            pending = answer_offseason(lg2, r2, manager_answer(lg2, AIManager(), pending))
        self.assertEqual(kinds, rec.kinds)

    def test_pending_questions_carry_what_to_show(self):
        lg, r = clone(self.league, self.rng)
        pending = start_offseason(lg, r)
        seen = set()
        while pending is not None:
            kind = pending["kind"]
            seen.add(kind)
            self.assertTrue(pending["title"] and pending["prompt"])
            self.assertIsNotNone(offseason_report(lg))
            if kind == "draft":
                prospects = pending_players(lg)
                self.assertEqual(set(prospects), set(pending["available"]))
                for pid in pending["available"]:
                    self.assertNotIn(pid, lg.players)             # not league players yet
                self.assertGreaterEqual(pending["pick"], 1)
            elif kind == "free_agency":
                for e in pending["pool"]:
                    self.assertIsNone(lg.players[e["player_id"]].team_id)
                    self.assertGreater(e["ask"], 0)
            elif kind == "resign":
                for e in pending["expiring"]:
                    self.assertEqual(lg.players[e["player_id"]].team_id, self.uid)
            pending = answer_offseason(lg, r, manager_answer(lg, AIManager(), pending))
        self.assertTrue({"draft", "free_agency"} <= seen)

    def test_user_choices_are_applied(self):
        lg, r = clone(self.league, self.rng)
        pending = start_offseason(lg, r)
        drafted = []
        while pending is not None:
            kind = pending["kind"]
            if kind == "draft":
                worst = min(pending_players(lg).values(), key=lambda p: p.current_ability)
                drafted.append(worst.id)
                answer = worst.id
            elif kind == "resign":
                answer = {}                                         # let them all walk
                walking = [e["player_id"] for e in pending["expiring"]]
            elif kind == "free_agency":
                answer = []
            elif kind == "trade_offer":
                answer = False
            else:
                answer = manager_answer(lg, AIManager(), pending)
            pending = answer_offseason(lg, r, answer)
        roster = {p.id for p in lg.roster(self.uid)}
        self.assertTrue(set(drafted) <= roster | {p.id for p in lg.players.values() if p.team_id is None})
        self.assertTrue(all(lg.players[d].draft_pick for d in drafted))
        if "walking" in locals():
            self.assertFalse(roster & set(walking))
        self.assertEqual(lg.validate(), [])

    def test_errors(self):
        lg, r = clone(self.league, self.rng)
        with self.assertRaises(ValueError):
            answer_offseason(lg, r, {})                       # nothing asked yet
        pending = start_offseason(lg, r)
        with self.assertRaises(ValueError):
            start_offseason(lg, r)                            # already under way
        while pending is not None and pending["kind"] != "draft":
            pending = answer_offseason(lg, r, manager_answer(lg, AIManager(), pending))
        with self.assertRaises(ValueError):
            answer_offseason(lg, r, 123456789)                # not on the board
        self.assertEqual(offseason_pending(lg)["kind"], "draft")   # still waiting

    def test_old_saves_still_load(self):
        lg, _ = clone(self.league, self.rng)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            save_league(lg, path)
            import sqlite3
            conn = sqlite3.connect(path)
            with conn:
                conn.execute("DELETE FROM meta WHERE key IN ('offseason', 'last_offseason', 'inbox')")
                conn.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
            conn.close()
            old = load_league(path)
        self.assertIsNone(old.offseason)
        self.assertEqual(old.inbox, [])
        self.assertEqual(fingerprint(old), fingerprint(lg))


if __name__ == "__main__":
    unittest.main()
