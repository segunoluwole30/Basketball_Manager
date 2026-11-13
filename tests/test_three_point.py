"""The Three-Point Contest, shot by shot: format, calibration, the final and shoot-offs,
the viewer page built from its record, the web route, and saves from before records."""
import copy
import json
import random
import statistics
import tempfile
import unittest
from pathlib import Path

from hoopsim import allstar, generate_league, load_league, save_league
from hoopsim.spatial import contest as page
from hoopsim.spatial.export import build_html

FIELD_RATINGS = (20, 19, 18, 18, 17, 17, 17, 16)        # an All-Star field's three-point ratings


def contest_league(seed=3):
    """A small league whose eight best shooters are rated like an All-Star field."""
    lg = generate_league(seed=seed, num_teams=4)
    field = sorted((p for p in lg.players.values() if p.team_id is not None), key=lambda p: p.id)[:8]
    for p, r in zip(field, FIELD_RATINGS):
        p.attributes.three_point = r
    return lg, [p.id for p in field]


class FormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lg, cls.field = contest_league()

    def test_course(self):
        # five racks corner to corner, deep shots between rack 2/3 and rack 3/4
        self.assertEqual(allstar.COURSE, (("rack", 0), ("rack", 1), ("deep", 0), ("rack", 2), ("deep", 1),
                                          ("rack", 3), ("rack", 4)))
        self.assertEqual(allstar.ROUND_SECONDS, 70)
        self.assertEqual(allstar.SHOOTOFF_SECONDS, 30)
        for mr in range(5):
            values = [allstar.ball_value(st, b, mr) for st in allstar.COURSE
                      for b in range(5 if st[0] == "rack" else 1)]
            self.assertEqual(len(values), 27)
            self.assertEqual(sum(values), allstar.MAX_POINTS)
            self.assertEqual(values.count(3), 2)                       # the deep shots
            self.assertEqual(values.count(2), 4 + 5)                   # four money balls + the money rack
            self.assertEqual(values.count(1), 16)

    def test_round_record(self):
        rng = random.Random(1)
        for p in (self.lg.players[pid] for pid in self.field):
            r = allstar.shoot_round(p, rng)
            self.assertLessEqual(len(r["t"]), 27)
            self.assertEqual(len(r["t"]), len(r["m"]))
            self.assertEqual(r["t"], sorted(set(r["t"])))              # one release at a time, in order
            self.assertLessEqual(r["t"][-1], 700)                     # before the buzzer (tenths)
            gaps = [b - a for a, b in zip(r["t"], r["t"][1:])]
            self.assertGreaterEqual(min(gaps), allstar.MIN_GAP * 10 - 1e-9)
            self.assertEqual(r["points"], sum(map(sum, r["racks"])) + sum(r["deep"]))
            self.assertLessEqual(r["points"], 40)
            self.assertEqual(allstar.expand_round({"mr": r["money_rack"], "t": r["t"], "m": r["m"]})["points"], r["points"])
            shots = allstar.shots_of({"mr": r["money_rack"], "t": r["t"], "m": r["m"]})
            self.assertEqual(sum(s["value"] for s in shots if s["made"]), r["points"])
        p = self.lg.players[self.field[0]]
        short = allstar.shoot_round(p, random.Random(2), seconds=30)            # a shoot-off
        self.assertLess(len(short["t"]), 27)
        self.assertLessEqual(short["t"][-1], 300)

    def test_money_rack_is_his_own(self):
        p = self.lg.players[self.field[0]]
        picks = {allstar.shoot_round(p, random.Random(s))["money_rack"] for s in range(5)}
        self.assertEqual(picks, {allstar.money_rack_choice(p)})

    def test_deterministic(self):
        a = allstar.run_contest(self.lg, self.field, random.Random(9))
        b = allstar.run_contest(copy.deepcopy(self.lg), self.field, random.Random(9))
        self.assertEqual(a["record"], b["record"])
        self.assertEqual(page.page_data(a["record"], 2025), page.page_data(b["record"], 2025))
        c = allstar.run_contest(self.lg, self.field, random.Random(10))
        self.assertNotEqual(a["record"]["rounds"], c["record"]["rounds"])

    def test_record_is_small_and_complete(self):
        res = allstar.run_contest(self.lg, self.field, random.Random(4))
        rec = res["record"]
        self.assertLess(len(json.dumps(rec, separators=(",", ":"))), 5000)
        self.assertEqual([s["p"] for s in rec["rounds"][0]["shooters"]], list(reversed(self.field)))   # top seed last
        self.assertEqual(rec["winner"], res["winner"])
        for rnd in rec["rounds"]:
            stage = {"first": "first", "final": "final"}.get(rnd["stage"])
            for s in rnd["shooters"]:
                pts = allstar.expand_round(s)["points"]
                if stage:
                    self.assertEqual(pts, res[stage][s["p"]]["points"])
        final = rec["rounds"][-1] if rec["rounds"][-1]["stage"] == "final" else \
            next(r for r in rec["rounds"] if r["stage"] == "final")
        firsts = [res["first"][s["p"]]["points"] for s in final["shooters"]]
        self.assertEqual(firsts, sorted(firsts))                        # worst first-round score shoots first

    def test_calibration(self):
        """Scores look like the real contest: round-one scores mostly 15-30, winners in the
        mid-to-high twenties, nobody near 40 more than very rarely."""
        r1, win, fin = [], [], []
        for i in range(250):
            res = allstar.run_contest(self.lg, self.field, random.Random(i))
            r1 += [v["points"] for v in res["first"].values()]
            fin += [v["points"] for v in res["final"].values()]
            win.append(res["final"][res["winner"]]["points"])
        q = lambda xs, f: sorted(xs)[int(f * (len(xs) - 1))]           # noqa: E731
        self.assertTrue(19 <= statistics.mean(r1) <= 25, statistics.mean(r1))
        self.assertGreaterEqual(q(r1, 0.05), 12)
        self.assertLessEqual(q(r1, 0.95), 32)
        self.assertTrue(25 <= statistics.mean(win) <= 30, statistics.mean(win))
        self.assertTrue(21 <= q(win, 0.1) and q(win, 0.9) <= 33, (q(win, 0.1), q(win, 0.9)))
        self.assertLessEqual(max(r1 + fin), 40)
        self.assertLess(sum(1 for x in r1 + fin if x >= 36) / len(r1 + fin), 0.01)

    def test_better_shooters_score_more(self):
        lg = copy.deepcopy(self.lg)
        p = lg.players[self.field[0]]
        rng = random.Random(0)
        p.attributes.three_point = 20
        hot = statistics.mean(allstar.shoot_round(p, rng, pct=0.43)["points"] for _ in range(300))
        cold = statistics.mean(allstar.shoot_round(p, rng, pct=0.33)["points"] for _ in range(300))
        self.assertGreater(hot, cold + 3)                              # his real 3P% counts too


class StagesTests(unittest.TestCase):
    """The final and the shoot-offs, with the shooting rigged to force ties."""
    def setUp(self):
        self.lg, self.field = contest_league()
        self.real = allstar.shoot_round

    def tearDown(self):
        allstar.shoot_round = self.real

    def rig(self, scores):
        """scores: {(stage index, pid): points}; a round is the next call for that player."""
        calls = {}

        def fake(p, rng, final=False, pct=None, seconds=70.0, money_rack=None):
            n = calls[p.id] = calls.get(p.id, -1) + 1
            want = scores.get((n, p.id), 10)
            n_balls = 13 if seconds < 70 else 27
            values = [allstar.ball_value(st, b, 4) for st in allstar.COURSE
                      for b in range(5 if st[0] == "rack" else 1)][:n_balls]
            made, left = ["0"] * n_balls, want                       # the biggest balls first: exact
            for k in sorted(range(n_balls), key=lambda k: -values[k]):
                if values[k] <= left:
                    made[k], left = "1", left - values[k]
            assert left == 0
            m = "".join(made)
            r = allstar.expand_round({"mr": 4, "t": [], "m": m})
            return {**r, "t": [10 + 16 * k for k in range(n_balls)], "m": m}
        allstar.shoot_round = fake

    def test_tie_at_the_cut_goes_to_a_shootoff(self):
        f = self.field
        # round 1: f0 20, f1 19, then f2 f3 f4 tied on 15 for the last two places
        scores = {(0, f[0]): 20, (0, f[1]): 19, (0, f[2]): 15, (0, f[3]): 15, (0, f[4]): 15,
                  (1, f[2]): 8, (1, f[3]): 6, (1, f[4]): 4}              # shoot-off: f2 goes through
        self.rig(scores)
        res = allstar.run_contest(self.lg, f, random.Random(0))
        self.assertEqual(sorted(res["finalists"]), sorted([f[0], f[1], f[2]]))
        self.assertEqual(res["shootoffs"][0]["stage"], "first round")
        self.assertEqual(sorted(res["shootoffs"][0]["players"]), sorted([f[2], f[3], f[4]]))
        stages = [(r["stage"], r["seconds"]) for r in res["record"]["rounds"]]
        self.assertEqual(stages[:2], [("first", 70), ("first-shootoff", 30)])
        self.assertEqual(stages[2][0], "final")

    def test_tie_in_the_final_goes_to_a_shootoff(self):
        f = self.field
        scores = {(0, f[0]): 20, (0, f[1]): 19, (0, f[2]): 18,
                  (1, f[0]): 24, (1, f[1]): 24, (1, f[2]): 17,           # the final: f0 and f1 tie
                  (2, f[0]): 5, (2, f[1]): 9}                            # 30 seconds: f1 wins it
        self.rig(scores)
        res = allstar.run_contest(self.lg, f, random.Random(0))
        self.assertEqual(res["winner"], f[1])
        self.assertEqual(res["final_order"][:2], [f[1], f[0]])
        self.assertEqual([r["stage"] for r in res["record"]["rounds"]], ["first", "final", "final-shootoff"])
        self.assertEqual(res["record"]["rounds"][-1]["seconds"], 30)
        # the viewer gets every round, the shoot-off last, and names the winner
        d = page.page_data(res["record"], 2025)
        self.assertEqual(len(d["possessions"]), 8 + 3 + 2)
        self.assertEqual([s["label"] for s in d["contest"]["stages"]], ["Round 1", "Final", "Shoot-off for the title"])
        self.assertEqual(d["home"]["players"][d["contest"]["winner"]]["id"], f[1])
        self.assertIn("wins the Three-Point Contest", d["possessions"][-1]["events"][-1][1])

    def test_no_tie_no_shootoff(self):
        f = self.field
        self.rig({(0, pid): 20 - k for k, pid in enumerate(f)} | {(1, f[0]): 25, (1, f[1]): 22, (1, f[2]): 21})
        res = allstar.run_contest(self.lg, f, random.Random(0))
        self.assertEqual(res["shootoffs"], [])
        self.assertEqual(res["winner"], f[0])
        self.assertEqual([r["stage"] for r in res["record"]["rounds"]], ["first", "final"])


class PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lg, cls.field = contest_league()
        cls.res = allstar.run_contest(cls.lg, cls.field, random.Random(5))
        cls.data = page.page_data(cls.res["record"], 2025)

    def test_one_possession_per_round(self):
        d, rec = self.data, self.res["record"]
        self.assertEqual(len(d["possessions"]), sum(len(r["shooters"]) for r in rec["rounds"]))
        self.assertEqual([pl["id"] for pl in d["home"]["players"]], [r["p"] for r in rec["field"]])
        self.assertEqual(d["away"]["players"], [])
        for p in d["possessions"]:
            self.assertEqual(p["n"], 1)
            self.assertTrue(all(len(f) == 8 for f in p["frames"]))      # time, clock, ball, holder, one shooter
            self.assertEqual(p["frames"][0][1], 70.0)
            self.assertGreaterEqual(min(f[1] for f in p["frames"]), 0)
            c = p["contest"]
            self.assertEqual(p["end_score"][0], c["points"])
            self.assertEqual(p["events"][-1][2], c["points"])
            # the stage's running best never exceeds what was shot before
            self.assertLessEqual(p["start_score"][1], 40)

    def test_shots_and_flights(self):
        rim = (0, 52.5)
        for p in self.data["possessions"]:
            fr, c = p["frames"], p["contest"]
            prev = -1
            for kind, spot, ball, value, made, pick, rel, arr in c["shots"]:
                self.assertTrue(prev <= pick < rel < arr < len(fr))
                prev = rel
                self.assertEqual(fr[rel - 1][5], 0)                      # in his hands, then gone
                self.assertEqual(fr[rel][5], -1)
                peak = max(f[4] for f in fr[rel:arr + 1])
                self.assertGreaterEqual(peak, 110)
                a = fr[arr]                                               # at the rim (the viewer's shot test)
                self.assertLessEqual(((a[2] - rim[0]) ** 2 + (a[3] - rim[1]) ** 2) ** 0.5, 12)
                self.assertTrue(85 <= a[4] <= 115, a)
                ev = [e for e in p["events"] if e[0] == arr]
                self.assertTrue(ev)
                self.assertEqual(": good" in ev[0][1], bool(made))
            acts = {(a[0], a[2]) for a in p["actions"]}
            self.assertTrue(all((s[6], "jumper_release") in acts for s in c["shots"]))

    def test_page_builds(self):
        html = build_html(self.data)
        self.assertIn('"contest":{', html)
        self.assertIn("Three-Point Contest", html)
        self.assertIn('id="cboard"', html)
        self.assertIn('data-pane="standPane"', html)


class SavesTests(unittest.TestCase):
    def test_record_saved_and_old_saves(self):
        lg, field = contest_league()
        res = allstar.run_contest(lg, field, random.Random(5))
        lg.all_star = {"season": lg.season, "done": ["three"], "three": {"field": field, "record": res["record"],
                       "winner": res["winner"], "first": {}, "final": {}}}
        self.assertEqual(allstar.contest_record(lg), res["record"])
        with tempfile.TemporaryDirectory() as d:
            save_league(lg, Path(d) / "x.db")
            back = load_league(Path(d) / "x.db")
        self.assertEqual(allstar.contest_record(back), res["record"])
        # in the season's history (where past contests are watched from)
        lg.history.append({"season": lg.season - 1, "all_star": {"score": {}, "mvp": field[0], "mvp_name": "x",
                                                                   "three_point_champion": field[0],
                                                                   "three_point_record": res["record"]}})
        self.assertEqual(allstar.contest_record(lg, lg.season - 1), res["record"])
        # a save from before records: the results stay, nothing to watch
        old = copy.deepcopy(lg)
        del old.all_star["three"]["record"]
        old.history[-1]["all_star"].pop("three_point_record")
        self.assertIsNone(allstar.contest_record(old))
        self.assertIsNone(allstar.contest_record(old, old.season - 1))
        self.assertIsNone(allstar._past(old)[0]["three_point_watch_url"])
        self.assertEqual(allstar._past(lg)[0]["three_point_watch_url"], allstar.contest_url(lg.season - 1))


try:
    from fastapi.testclient import TestClient
    from hoopsim.web.app import create_app
    from hoopsim.web.session import GameSession
    HAVE_WEB = True
except ImportError:                                    # pragma: no cover
    HAVE_WEB = False


@unittest.skipUnless(HAVE_WEB, "fastapi/httpx not installed")
class WebRouteTests(unittest.TestCase):
    def test_watch_the_contest(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = GameSession(saves_dir=tmp)
            client = TestClient(create_app(session))
            self.assertEqual(client.post("/api/new", json={"seed": 7, "num_teams": 4}).status_code, 200)
            lg = session.league
            url = f"/api/replay/{allstar.REPLAY_IDS['three']}"
            self.assertEqual(client.get(url).status_code, 404)              # not held yet
            field = [p.id for p in lg.players.values() if p.team_id is not None][:8]
            res = allstar.run_contest(lg, field, random.Random(1))
            lg.all_star = {"season": lg.season, "done": ["three"], "three": {"field": field, "record": res["record"]}}
            r = client.get(allstar.contest_url(lg.season))
            self.assertEqual(r.status_code, 200)
            self.assertIn('"contest":{', r.text)
            self.assertIn("/viewer-assets/", r.text)                       # the animated players, as for replays
            lg.history.append({"season": lg.season - 1, "all_star": {"score": {}, "mvp": field[0], "mvp_name": "x",
                                                                       "three_point_record": res["record"]}})
            self.assertEqual(client.get(allstar.contest_url(lg.season - 1)).status_code, 200)
            self.assertEqual(client.get(allstar.contest_url(lg.season - 2)).status_code, 404)
            past = client.get("/api/allstar").json()["past"]
            self.assertEqual(past[0]["three_point_watch_url"], allstar.contest_url(lg.season - 1))


if __name__ == "__main__":
    unittest.main()
