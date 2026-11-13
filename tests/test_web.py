"""The web API (FastAPI TestClient). Skipped when the web extras aren't installed."""
import json
import random
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
    from hoopsim.web.app import STATIC, create_app
    HAVE_WEB = True
except ImportError:                                    # pragma: no cover
    HAVE_WEB = False

from hoopsim.generate import generate_schedule
from hoopsim.season import regular_season_over
from hoopsim.web.session import GameSession

FORBIDDEN = ("potential_ability", "hidden", "consistency", "injury_proneness", "work_ethic",
             "professionalism", "loyalty", "ambition", "clutch")


def short_season(session: GameSession, games: int = 12) -> None:
    """Make the regular season short so tests run quickly."""
    lg = session.league
    lg.games = {}
    lg.rules.games_per_season = games
    generate_schedule(lg, random.Random(0))


@unittest.skipUnless(HAVE_WEB, "fastapi/httpx not installed (pip install -r requirements-web.txt)")
class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.session = GameSession(saves_dir=self.tmp.name)
        self.client = TestClient(create_app(self.session))

    def tearDown(self):
        self.tmp.cleanup()

    def get(self, url):
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200, f"{url}: {r.text[:300]}")
        return r.json()

    def post(self, url, body=None):
        r = self.client.post(url, json=body if body is not None else {})
        self.assertEqual(r.status_code, 200, f"{url}: {r.text[:300]}")
        return r.json()

    def error(self, url, body=None, status=400):
        r = self.client.post(url, json=body if body is not None else {})
        self.assertEqual(r.status_code, status, f"{url}: {r.text[:300]}")
        detail = r.json()["detail"]
        self.assertIsInstance(detail, str)
        self.assertTrue(detail)
        return detail

    def new_game(self, teams=4):
        choices = self.post("/api/new", {"seed": 7, "num_teams": teams})["teams"]
        short_season(self.session)
        me = choices[0]["id"]
        state = self.post("/api/new/team", {"team_id": me})
        self.assertEqual(state["user_team_id"], me)
        return me

    def assert_no_secrets(self, data):
        text = json.dumps(data)
        for word in FORBIDDEN:
            self.assertNotIn(f'"{word}"', text)

    # ------------------------------------------------------------------
    def test_no_game_yet(self):
        self.assertEqual(self.get("/api/state"), {"loaded": False})
        r = self.client.get("/api/dashboard")
        self.assertEqual(r.status_code, 409)
        self.assertIn("new game", r.json()["detail"])
        self.assertEqual(self.client.get("/api/nonsense").status_code, 404)

    def test_screens_and_secrets(self):
        me = self.new_game()
        for url in ("/api/state", "/api/dashboard", "/api/roster", "/api/lineup", "/api/tactics",
                    "/api/free_agents", "/api/standings", "/api/leaders", "/api/schedule", "/api/results",
                    "/api/history", "/api/playoffs", "/api/teams", "/api/finances", "/api/offseason",
                    "/api/inbox", "/api/saves"):
            self.assert_no_secrets(self.get(url))
        roster = self.get("/api/roster")["players"]
        self.assertTrue(roster)
        card = self.get(f"/api/player/{roster[0]['id']}")
        self.assert_no_secrets(card)
        self.assertIn("scouting_notes", card)
        pot = card["potential"]
        self.assertTrue(pot["peaked"] or pot["lo"] <= pot["est"] <= pot["hi"])
        self.assertTrue(card["is_mine"])
        other = next(t for t in self.get("/api/teams")["teams"] if t["id"] != me)
        self.assertFalse(self.get(f"/api/roster?team_id={other['id']}")["is_mine"])
        self.assertEqual(self.client.get("/api/player/999999").status_code, 404)

    def test_team_management(self):
        self.new_game()
        lineup = self.get("/api/lineup")
        self.assertEqual(lineup["minutes_total"], 240)
        players = [p for p in lineup["players"] if not p["injury"]]
        ids = [p["id"] for p in players]
        # Starters
        msg = self.post("/api/lineup/starters", {"ids": ids[-5:]})["message"]
        self.assertIn("Starting", msg)
        self.assertFalse(self.get("/api/lineup")["auto"])
        self.assertIn("5 different", self.error("/api/lineup/starters", {"ids": ids[:4]}))
        # Minutes: a full plan that totals 240 replaces the old one
        plan = {str(pid): 0 for pid in ids}
        plan.update({str(pid): 48 for pid in ids[-5:]})
        self.post("/api/lineup/minutes", {"minutes": plan})
        targets = self.session.league.teams[self.session.league.user_team_id].tactics.minutes_targets
        self.assertEqual(sum(targets.values()), 240)
        self.assertIn("0-48", self.error("/api/lineup/minutes", {"minutes": {str(ids[0]): 60}}))
        self.assertIn("assistant", self.post("/api/lineup/auto", {"on": True})["message"])
        # Tactics
        self.assertIn("zone", self.post("/api/tactics", {"defense": "zone", "pace": 15})["message"])
        self.assertEqual(self.get("/api/tactics")["pace"], 15)
        self.assertIn("1-20", self.error("/api/tactics", {"pace": 30}))
        self.assertIn("bad value", self.error("/api/tactics", {"defense": "blitz"}))
        # Player instructions
        self.assertIn("threes 18", self.post(f"/api/player/{ids[0]}/instructions", {"three": 18})["message"])
        self.assertIn("1-20", self.error(f"/api/player/{ids[0]}/instructions", {"post": 0}))
        other = next(p for p in self.session.league.players.values()
                     if p.team_id not in (None, self.session.league.user_team_id))
        self.assertIn("your own", self.error(f"/api/player/{other.id}/instructions", {"three": 12}))

    def test_transfers(self):
        me = self.new_game()
        fa = self.get("/api/free_agents")
        best = fa["players"][0]
        self.assertGreater(best["ask"], 0)
        if fa["min_salary"] < best["ask"]:
            self.assertTrue(self.error("/api/sign", {"player_id": best["id"], "salary": fa["min_salary"], "years": 1}))
        self.assertIn("1-5 years", self.error("/api/sign", {"player_id": best["id"], "salary": best["ask"], "years": 9}))
        cheap = min(fa["players"], key=lambda p: p["ask"])
        if self.get("/api/finances")["roster_size"] < 15:
            msg = self.post("/api/sign", {"player_id": cheap["id"], "salary": max(cheap["ask"], fa["min_salary"]), "years": 1})["message"]
            self.assertIn("Signed", msg)
        roster = self.get("/api/roster")["players"]
        worst = min(roster, key=lambda p: p["ca"])
        self.assertIn("Released", self.post("/api/release", {"player_id": worst["id"]})["message"])
        # Trades: feedback without executing, then a refusal with a reason
        other = next(t for t in self.get("/api/teams")["teams"] if t["id"] != me)
        theirs = self.get(f"/api/roster?team_id={other['id']}")["players"]
        mine = self.get("/api/roster")["players"]
        check = self.post("/api/trade/check", {"give": [mine[-1]["id"]], "team_id": other["id"], "get": [theirs[0]["id"]]})
        for key in ("ok", "message", "salary_out", "salary_in", "salary_match", "your_roster_after"):
            self.assertIn(key, check)
        self.assertFalse(check["ok"])                     # their best for our worst: no
        self.assertTrue(self.error("/api/trade", {"give": [mine[-1]["id"]], "team_id": other["id"], "get": [theirs[0]["id"]]}))
        self.assertIn("another team", self.error("/api/trade/check", {"give": [], "team_id": me, "get": []}))

    def test_whole_loop_with_save_and_load_mid_offseason(self):
        me = self.new_game()
        self.assertEqual(self.post("/api/sim", {"mode": "day"})["days"], 1)
        self.assertIn("unknown sim mode", self.error("/api/sim", {"mode": "forever"}))
        for _ in range(4):                                # a preseason week before opening night
            if self.get("/api/results")["games"]:
                break
            self.post("/api/sim", {"mode": "week"})
        self.assertTrue(self.get("/api/results")["games"])
        game_id = self.get("/api/results")["games"][0]["id"]
        box = self.get(f"/api/game/{game_id}")["box"]
        self.assertEqual(sum(box["home"]["periods"]), box["home"]["totals"]["pts"])
        self.assertIn("finish the regular season", self.error("/api/playoffs/start", {}))
        for _ in range(30):                               # offers pause the sim
            r = self.post("/api/sim", {"mode": "end"})
            for item in self.get("/api/inbox")["items"]:
                if item["kind"] == "offer" and item["status"] == "open":
                    self.post(f"/api/offers/{item['id']}", {"accept": False})
            if r["state"]["stage"] == "season_over":
                break
        self.assertEqual(self.get("/api/state")["stage"], "season_over")
        self.assertIn("over", self.error("/api/sim", {"mode": "day"}))
        self.assertTrue(self.get("/api/leaders")["categories"])
        self.post("/api/playoffs/start")
        self.assertFalse(self.post("/api/playoffs/sim", {"mode": "day"})["finished"])
        done = self.post("/api/playoffs/sim", {"mode": "all"})
        self.assertTrue(done["finished"])
        self.assertIsNotNone(done["playoffs"]["champion"])
        self.assertEqual(self.get("/api/state")["stage"], "awards")
        self.assertEqual(len(self.get("/api/history")["seasons"]), 1)
        # Offseason, step by step
        self.assertIn("no offseason decision", self.error("/api/offseason/answer", {"answer": None}))
        off = self.post("/api/offseason/start")
        self.assertTrue(off["active"])
        season = self.get("/api/state")["season"]
        kinds, saved = [], False
        while off["active"]:
            p = off["pending"]
            kinds.append(p["kind"])
            self.assertTrue(p["prompt"] and p["title"] and "finances" in p)
            self.assert_no_secrets(p)
            if p["kind"] == "draft":
                self.assertIn("isn't on the board", self.error("/api/offseason/answer", {"answer": 1}))
                card = self.get(f"/api/player/{p['players'][0]['id']}")      # prospects have cards
                self.assertTrue(card["is_prospect"])
                self.assert_no_secrets(card)
                off = self.post("/api/offseason/answer", {"answer": p["players"][0]["id"]})
                self.assertIn(p["players"][0]["id"], {x.id for x in self.session.league.roster(me)})
            elif p["kind"] == "free_agency" and not saved:
                path = str(Path(self.tmp.name) / "mid.db")
                self.post("/api/save", {"path": path})
                self.post("/api/load", {"path": path})
                after = self.get("/api/offseason")
                self.assertEqual(after["pending"]["kind"], "free_agency")
                self.assertEqual(after["pending"]["round"], p["round"])
                self.assertIn("pool", self.error("/api/offseason/answer", {"answer": [[1, 1_000_000, 1]]}))
                saved = True
                off = self.post("/api/offseason/answer", {"answer": []})
            elif p["kind"] == "resign":
                off = self.post("/api/offseason/answer", {"answer": {}})              # all walk
            elif p["kind"] == "cuts":
                self.assertIn("exactly", self.error("/api/offseason/answer", {"answer": []}))
                off = self.post("/api/offseason/auto", {"scope": "one"})
            else:
                off = self.post("/api/offseason/auto", {"scope": "one"})
        self.assertIn("draft", kinds)
        state = self.get("/api/state")
        self.assertEqual(state["phase"], "regular_season")
        self.assertEqual(state["season"], season)
        self.assertEqual(state["stage"], "regular_season")
        self.assertIsNotNone(self.get("/api/offseason")["last_report"])
        self.assertEqual(self.session.league.validate(), [])
        self.assertEqual(self.post("/api/sim", {"mode": "day"})["days"], 1)          # next season runs

    def _to_season_end(self):
        for _ in range(30):                               # offers pause the sim
            r = self.post("/api/sim", {"mode": "end"})
            for item in self.get("/api/inbox")["items"]:
                if item["kind"] == "offer" and item["status"] == "open":
                    self.post(f"/api/offers/{item['id']}", {"accept": False})
            if r["state"]["stage"] == "season_over":
                return r["state"]
        self.fail("the regular season didn't end")

    def test_awards_night(self):
        me = self.new_game()
        lg = self.session.league
        none = self.get("/api/awards")
        self.assertFalse(none["available"])
        self.assertIsNone(self.get("/api/state")["awards"])
        state = self._to_season_end()
        self.assertEqual(state["awards"], {"season": lg.season, "seen": False})
        self.assertFalse(lg.playoffs)                                   # before the playoffs
        d = self.get("/api/awards")
        self.assert_no_secrets(d)
        self.assertTrue(d["available"] and d["full"] and d["current"])
        self.assertEqual([a["key"] for a in d["single"]], ["mvp", "dpoy", "roy", "sixth_man", "mip"])
        mvp = d["single"][0]
        self.assertEqual(mvp["winner"], mvp["finalists"][0]["id"])
        shares = [f["share"] for f in mvp["finalists"]]
        self.assertEqual(shares, sorted(shares, reverse=True))
        all_nba = next(t for t in d["teams"] if t["key"] == "all_nba")
        self.assertEqual([len(t["players"]) for t in all_nba["tiers"]], [5, 5, 5])
        rows = [r for a in d["single"] for r in a["finalists"]] + [
            r for t in d["teams"] for tier in t["tiers"] for r in tier["players"]]
        self.assertTrue(all(r["mine"] == (r["team_id"] == me) for r in rows))
        self.assertIsNone(d["finals_mvp"])
        news = [m for m in self.get("/api/inbox")["items"] if m["kind"] == "awards"]
        self.assertEqual(len(news), 1 + sum(a["winner"] is not None for a in d["single"]))  # + the teams
        self.assertTrue(all(m["awards_season"] == lg.season for m in news))
        card = self.get(f"/api/player/{mvp['winner']}")
        self.assertIn("MVP", card["awards_now"])
        self.assertEqual(self.post("/api/awards/seen")["awards"]["seen"], True)
        path = str(Path(self.tmp.name) / "awards.db")
        self.post("/api/save", {"path": path})
        self.post("/api/load", {"path": path})
        self.assertEqual(self.get("/api/state")["awards"]["seen"], True)
        self.assertEqual(self.get("/api/awards")["single"], d["single"])
        # The playoffs, the Finals MVP and the history.
        self.post("/api/playoffs/start")
        self.post("/api/playoffs/sim", {"mode": "all"})
        after = self.get("/api/awards")
        self.assertEqual(after["seasons"], [lg.season])
        self.assertFalse(after["current"])
        self.assertIsNotNone(after["finals_mvp"])
        self.assertEqual(after["single"], d["single"])
        h = self.get("/api/history")["seasons"][0]
        self.assertEqual([p["id"] for p in h["all_nba_first"]], [p["id"] for p in all_nba["tiers"][0]["players"]])
        self.assertEqual(self.get(f"/api/player/{mvp['winner']}")["awards_now"], [])
        self.assertGreaterEqual(self.get(f"/api/player/{mvp['winner']}")["honours"]["mvp"], 1)

    def test_awards_for_old_saves(self):
        self.new_game()
        lg = self.session.league
        self._to_season_end()
        expected = self.get("/api/awards")["single"]
        lg.awards = None                                                # a save from before awards night
        lg.inbox = [m for m in lg.inbox if m["kind"] != "awards"]
        path = str(Path(self.tmp.name) / "old.db")
        self.post("/api/save", {"path": path})
        state = self.post("/api/load", {"path": path})
        self.assertEqual(state["awards"], {"season": lg.season - 0, "seen": False})
        self.assertEqual(self.get("/api/awards")["single"], expected)
        # A history entry from before the full vote was kept: winners only.
        lg = self.session.league
        some = next(iter(lg.players.values()))
        lg.history.insert(0, {"season": lg.season - 1, "champion": min(lg.teams), "runner_up": max(lg.teams),
                              "finals": "4-2", "awards": {"mvp": some.id, "finals_mvp": some.id, "dpoy": None},
                              "award_names": {"mvp": some.name, "finals_mvp": some.name, "dpoy": None},
                              "standings": [], "playoffs": []})
        some.career.append({"season": lg.season - 1, "playoffs": False, "team_id": some.team_id, "team": "XYZ",
                            "gp": 70, "gs": 70, "min": 2300.0, "pts": 1800, "fgm": 600, "fga": 1200, "tpm": 100,
                            "tpa": 300, "ftm": 500, "fta": 600, "orb": 50, "drb": 400, "ast": 300, "stl": 70,
                            "blk": 30, "tov": 150, "pf": 150, "plus_minus": 200, "mvp": True, "all_nba": 1})
        old = self.get(f"/api/awards?season={lg.season - 1}")
        self.assertFalse(old["full"])
        self.assertEqual(old["single"][0]["winner"], some.id)
        self.assertEqual(old["single"][0]["finalists"][0]["ppg"], round(1800 / 70, 1))
        self.assertEqual(old["teams"][0]["tiers"][0]["players"][0]["id"], some.id)
        self.assertEqual(old["finals_mvp"]["id"], some.id)
        self.assertEqual(len(self.get("/api/history")["seasons"]), 1)
        self.assertIn("no awards", self.client.get("/api/awards?season=1900").json()["detail"])

    def test_auto_rest_of_summer(self):
        self.new_game()
        self.session.sim("end")
        while self.session._stage() != "season_over":
            self.session.sim("end")
        self.post("/api/playoffs/start")
        self.post("/api/playoffs/sim", {"mode": "all"})
        self.post("/api/offseason/start")
        off = self.post("/api/offseason/auto", {"scope": "all"})
        self.assertFalse(off["active"])
        self.assertEqual(self.get("/api/state")["phase"], "regular_season")
        titles = [m["title"] for m in self.get("/api/inbox")["items"]]
        self.assertTrue(any(t.startswith("Summer report") for t in titles))

    def test_save_and_load(self):
        self.new_game()
        self.post("/api/sim", {"mode": "week"})
        path = str(Path(self.tmp.name) / "game.db")
        self.assertIn("Saved", self.post("/api/save", {"path": path})["message"])
        before = self.get("/api/dashboard")
        self.assertTrue(any(s["name"] == "game.db" for s in self.get("/api/saves")["saves"]))
        self.post("/api/new", {"seed": 99, "num_teams": 4})
        state = self.post("/api/load", {"path": "game.db"})                   # relative to the saves dir
        self.assertEqual(state["record"], before["state"]["record"])
        self.assertEqual(len(self.get("/api/inbox")["items"]), len(before["inbox"]))
        self.assertEqual(self.client.post("/api/load", json={"path": "nope.db"}).status_code, 404)

    @unittest.skipUnless(HAVE_WEB and (STATIC / "index.html").exists(), "front end not built")
    def test_front_end_is_served(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('<div id="root">', r.text)
        self.assertEqual(self.client.get("/squad").status_code, 200)              # client-side routes

    # -- stats pages ------------------------------------------------------
    def sim_days(self, days):
        """Sim at least ``days`` days of the regular season (trade offers pause the sim)."""
        target = self.session._days_played() + days
        for _ in range(50):
            if self.session._days_played() >= target or self.get("/api/state")["stage"] != "regular_season":
                return
            self.post("/api/sim", {"mode": "days", "days": target - self.session._days_played()})

    def sim_season_and_playoffs(self):
        for _ in range(50):
            if self.post("/api/sim", {"mode": "end"})["state"]["stage"] == "season_over":
                break
        self.post("/api/playoffs/start")
        self.post("/api/playoffs/sim", {"mode": "all"})

    def test_stats_leaders_are_sorted_and_qualified(self):
        from hoopsim.stats import season_totals
        me = self.new_game()
        self.sim_days(8)
        d = self.get("/api/stats/leaders")
        self.assert_no_secrets(d)
        totals = season_totals(self.session.league)
        most = max(sum(1 for g in self.session.league.games.values() if g.played and not g.playoff
                       and tid in (g.home_team_id, g.away_team_id)) for tid in self.session.league.teams)
        self.assertEqual(d["min_games"], max(1, round(0.6 * most)))
        need_volume = {"fg_pct": ("fga", 5), "tp_pct": ("tpa", 2), "ft_pct": ("fta", 2), "ts": ("fga", 5)}
        keys = {c["key"] for c in d["categories"]}
        self.assertTrue({"pts", "trb", "ast", "stl", "blk", "fg_pct", "tp_pct", "ft_pct", "ts", "usg",
                         "fga_share", "pts36"} <= keys)
        for c in d["categories"]:
            values = [r["value"] for r in c["rows"]]
            self.assertEqual(values, sorted(values, reverse=True), c["key"])
            for r in c["rows"]:
                t = totals[r["player_id"]]
                self.assertGreaterEqual(t["gp"], d["min_games"])
                self.assertEqual(r["gp"], t["gp"])
                self.assertEqual(r["mine"], t["team_id"] == me)
                if c["key"] in need_volume:
                    field, per_game = need_volume[c["key"]]
                    self.assertGreaterEqual(t[field] / t["gp"], per_game)
                if c["key"] in ("usg", "fga_share", "pts36", "trb36", "ast36"):
                    self.assertGreaterEqual(t["seconds"] / 60 / t["gp"], 15)
        pts = next(c for c in d["categories"] if c["key"] == "pts")
        self.assertTrue(pts["rows"])
        top = pts["rows"][0]
        self.assertAlmostEqual(top["value"], totals[top["player_id"]]["pts"] / totals[top["player_id"]]["gp"], places=1)
        fg = next(c for c in d["categories"] if c["key"] == "fg_pct")
        for r in fg["rows"]:
            t = totals[r["player_id"]]
            self.assertAlmostEqual(r["value"], t["fgm"] / t["fga"], delta=0.00051)     # shown to 3 places
        # Nobody qualified is left out of a counting list that isn't full
        qualified = [pid for pid, t in totals.items() if t["gp"] >= d["min_games"]]
        self.assertEqual(len(pts["rows"]), min(10, len(qualified)))
        # Position filter and the playoffs (none yet)
        guards = self.get("/api/stats/leaders?pos=PG")
        for c in guards["categories"]:
            for r in c["rows"]:
                self.assertEqual(self.session.league.players[r["player_id"]].position.value, "PG")
        self.assertFalse(any(c["rows"] for c in self.get("/api/stats/leaders?playoffs=true")["categories"]))
        self.assertEqual(self.client.get("/api/stats/leaders?pos=XX").status_code, 400)
        self.assertEqual(self.client.get("/api/stats/leaders?season=1900").status_code, 404)

    def test_team_ratings_match_box_scores(self):
        self.new_game()
        self.sim_days(6)
        d = self.get("/api/stats/teams")
        lg = self.session.league
        games = [g for g in lg.games.values() if g.season == lg.season and g.played and not g.playoff]
        self.assertEqual(len(d["teams"]), len(lg.teams))
        self.assertTrue(any(r["mine"] for r in d["teams"]))
        by_id = {r["team_id"]: r for r in d["teams"]}

        def tot(box):
            t = box.totals()
            return t, t.fga + 0.44 * t.fta - t.orb + t.tov

        for tid, row in by_id.items():
            pts = opp = poss = 0.0
            fgm = tpm = fga = orb = opp_drb = gp = wins = 0
            for g in games:
                if tid not in (g.home_team_id, g.away_team_id):
                    continue
                mine, theirs = ((g.box_score.home, g.box_score.away) if g.home_team_id == tid
                                else (g.box_score.away, g.box_score.home))
                (m, pm), (o, po) = tot(mine), tot(theirs)
                gp += 1
                wins += g.winner_id() == tid
                pts += m.pts
                opp += o.pts
                poss += (pm + po) / 2
                fgm, tpm, fga, orb, opp_drb = fgm + m.fgm, tpm + m.tpm, fga + m.fga, orb + m.orb, opp_drb + o.drb
                score = g.result.home_score if g.home_team_id == tid else g.result.away_score
                self.assertEqual(m.pts, score)                     # box totals are the final score
            self.assertEqual((row["gp"], row["w"]), (gp, wins))
            self.assertEqual(row["totals"]["pts"], pts)
            self.assertEqual(row["totals"]["opp_pts"], opp)
            self.assertAlmostEqual(row["totals"]["poss"], poss, places=1)
            self.assertAlmostEqual(row["ppg"], pts / gp, places=1)
            self.assertAlmostEqual(row["ortg"], 100 * pts / poss, places=1)
            self.assertAlmostEqual(row["drtg"], 100 * opp / poss, places=1)
            self.assertAlmostEqual(row["efg"], (fgm + 0.5 * tpm) / fga, places=3)
            self.assertAlmostEqual(row["orb_pct"], orb / (orb + opp_drb), places=3)
            self.assertTrue(60 < row["pace"] < 140)
        # League-wide, points for equal points against and the average ratings agree
        self.assertEqual(sum(r["totals"]["pts"] for r in d["teams"]), sum(r["totals"]["opp_pts"] for r in d["teams"]))
        self.assertAlmostEqual(d["league"]["ortg"], d["league"]["drtg"], places=1)
        nets = [r["net"] for r in d["teams"]]
        self.assertEqual(nets, sorted(nets, reverse=True))

    def test_player_history_matches_career_lines(self):
        from hoopsim.stats import season_totals
        me = self.new_game()
        self.sim_days(4)
        lg = self.session.league
        pid = max(season_totals(lg).items(), key=lambda kv: kv[1]["pts"])[0]
        h = self.get(f"/api/players/{pid}/history")
        self.assert_no_secrets(h)
        live = [r for r in h["seasons"] if r["in_progress"] and not r["playoffs"]]
        self.assertEqual(len(live), 1)
        t = season_totals(lg)[pid]
        self.assertEqual((live[0]["gp"], live[0]["totals"]["pts"]), (t["gp"], t["pts"]))
        self.assertEqual(h["chart"][-1]["ca"], lg.players[pid].current_ability)
        self.sim_season_and_playoffs()
        p = lg.players[pid]
        h = self.get(f"/api/players/{pid}/history")
        self.assertEqual(len(h["seasons"]), len(p.career))
        for row, c in zip(h["seasons"], sorted(p.career, key=lambda c: (c["season"], c["playoffs"]))):
            self.assertFalse(row["in_progress"])
            self.assertEqual((row["season"], row["playoffs"], row["team"], row["gp"], row["ca"]),
                             (c["season"], c["playoffs"], c["team"], c["gp"], c["ca"]))
            for k in ("pts", "fgm", "fga", "ast", "tov"):
                self.assertEqual(row["totals"][k], c[k])
            self.assertAlmostEqual(row["pts"], c["pts"] / c["gp"], delta=0.051)   # shown to one decimal
            self.assertAlmostEqual(row["trb"], (c["orb"] + c["drb"]) / c["gp"], places=1)
        reg = [c for c in p.career if not c["playoffs"]]
        self.assertEqual([x["season"] for x in h["chart"]], [c["season"] for c in reg])
        self.assertEqual(h["chart"][-1]["ca"], reg[-1]["ca"])
        # Box scores are pruned now: leaders come from career lines, team stats say why they're empty
        leaders = self.get("/api/stats/leaders")
        self.assertFalse(leaders["from_box_scores"])
        self.assertTrue(next(c for c in leaders["categories"] if c["key"] == "pts")["rows"])
        teams = self.get("/api/stats/teams")
        self.assertEqual(teams["teams"], [])
        self.assertIn("box scores", teams["note"])
        self.assertEqual(self.client.get("/api/players/999999/history").status_code, 404)
        log = self.get("/api/team/gamelog")
        self.assertTrue(log["games"])
        self.assertTrue(all(g["top_scorer"] is None and not g["has_box"] for g in log["games"]))
        self.assertEqual(log["team_id"], me)

    def test_game_log_matches_the_schedule(self):
        me = self.new_game()
        self.sim_days(5)
        log = self.get("/api/team/gamelog")
        sched = [g for g in self.get("/api/schedule")["games"] if g["played"]]
        self.assertEqual([g["id"] for g in log["games"]], [g["id"] for g in sched])
        lg = self.session.league
        for row, g in zip(log["games"], sched):
            self.assertEqual(f"{row['team_score']}-{row['opp_score']}", g["score"])
            self.assertEqual(row["won"], g["won"])
            self.assertEqual(row["opponent_id"], g["opponent_id"])
            self.assertEqual(row["at_home"], g["at_home"])
            self.assertEqual(row["has_box"], g["has_box"])
            self.assertEqual(row["has_replay"], g["has_replay"])
            box = lg.games[row["id"]].box_score
            lines = (box.home if row["at_home"] else box.away).lines
            self.assertEqual(row["top_scorer"]["pts"], max(l.pts for l in lines))
            self.assertIn(row["top_scorer"]["player_id"], {l.player_id for l in lines})
        self.assertEqual(log["record"], self.get("/api/state")["record"])
        self.assertEqual(log["games"][-1]["record"], "-".join(map(str, log["record"])))
        self.assertEqual(log["remaining"], len([g for g in self.get("/api/schedule")["games"] if not g["played"]]))
        other = next(t for t in lg.teams if t != me)
        self.assertEqual(self.get(f"/api/team/gamelog?team_id={other}")["team_id"], other)
        self.assertEqual(self.client.get("/api/team/gamelog?team_id=999").status_code, 404)


if __name__ == "__main__":
    unittest.main()


class WebMatchEngineTests(unittest.TestCase):
    setUp = WebApiTests.setUp
    post = WebApiTests.post

    def tearDown(self):
        if hasattr(WebApiTests, "tearDown"):
            WebApiTests.tearDown(self)

    def test_new_game_can_use_the_spatial_engine(self):
        self.post("/api/new", {"seed": 7, "num_teams": 4, "match_engine": "spatial"})
        self.assertEqual(self.session.league.rules.match_engine, "spatial")

    def test_watch_your_spatial_games(self):
        choices = self.post("/api/new", {"seed": 7, "num_teams": 4, "match_engine": "spatial"})["teams"]
        short_season(self.session, games=4)
        me = choices[0]["id"]
        self.post("/api/new/team", {"team_id": me})
        lg = self.session.league
        while not any(g.played for g in lg.games.values() if me in (g.home_team_id, g.away_team_id)):
            self.post("/api/sim", {"mode": "day"})
        mine = [g for g in lg.games.values() if g.played and me in (g.home_team_id, g.away_team_id)]
        others = [g for g in lg.games.values() if g.played and me not in (g.home_team_id, g.away_team_id)]
        self.assertTrue(all(g.id in lg.replays for g in mine))
        self.assertTrue(all(g.id not in lg.replays for g in others))
        view = self.client.get(f"/api/game/{mine[0].id}").json()
        self.assertTrue(view["has_replay"])
        page = self.client.get(f"/api/replay/{mine[0].id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.headers["content-type"])
        self.assertIn("window.GAME", page.text)
        if others:
            self.assertEqual(self.client.get(f"/api/replay/{others[0].id}").status_code, 404)

    def test_replays_carry_ids_and_edited_looks(self):
        """Saved replays name each player by id; the page gets his build and editor look by
        id, and a replay saved before ids (none in it) still finds them by name and number."""
        import json as _json
        import zlib
        from hoopsim.spatial.season_game import replay_data
        choices = self.post("/api/new", {"seed": 7, "num_teams": 4, "match_engine": "spatial"})["teams"]
        short_season(self.session, games=4)
        me = choices[0]["id"]
        self.post("/api/new/team", {"team_id": me})
        lg = self.session.league
        while not any(g.played for g in lg.games.values() if me in (g.home_team_id, g.away_team_id)):
            self.post("/api/sim", {"mode": "day"})
        gid = next(g.id for g in lg.games.values() if g.played and me in (g.home_team_id, g.away_team_id))
        data = replay_data(lg, gid)
        rows = data["home"]["players"] + data["away"]["players"]
        self.assertTrue(all(lg.players[r["id"]].name == r["name"] for r in rows))
        star = lg.players[rows[0]["id"]]
        look = {"hair": "afro", "hairColor": "#AA3311", "beard": True, "band": None, "sleeve": "both",
                "shoes": "team"}
        r = self.post(f"/api/editor/player/{star.id}", {"look": look})
        self.assertIn("look", r["changed"])
        self.assertEqual(r["player"]["look"]["hairColor"], "#aa3311")
        page = self.client.get(f"/api/replay/{gid}").text
        game = _json.loads(page.split("window.GAME = ", 1)[1].split(";\n", 1)[0])
        row = next(x for x in game["home"]["players"] + game["away"]["players"] if x.get("id") == star.id)
        self.assertEqual(row["look"], {**look, "hairColor": "#aa3311"})
        self.assertEqual(row["ht"], star.physique.height_cm)
        # a renamed player (or a changed number) is still found by id
        self.post(f"/api/editor/player/{star.id}", {"first_name": "Renamed"})
        out = self.session._viewer_builds(replay_data(lg, gid))
        row = next(x for x in out["home"]["players"] + out["away"]["players"] if x.get("id") == star.id)
        self.assertEqual(row["look"]["hair"], "afro")
        # an old replay: no ids, found by name and number; it still serves
        old = replay_data(lg, gid)
        for side in ("home", "away"):
            for x in old[side]["players"]:
                del x["id"]
        lg.replays[gid] = zlib.compress(_json.dumps(old).encode())
        out = self.session._viewer_builds(replay_data(lg, gid))
        mate = lg.players[rows[1]["id"]]
        found = next(x for x in out["home"]["players"] + out["away"]["players"] if x["name"] == mate.name)
        self.assertEqual(found["ht"], mate.physique.height_cm)
        self.assertNotIn("id", found)
        page = self.client.get(f"/api/replay/{gid}")
        self.assertEqual(page.status_code, 200)
        # "use default" clears the look
        r = self.post(f"/api/editor/player/{star.id}", {"look": None})
        self.assertIsNone(r["player"]["look"])
        self.assertIsNone(lg.players[star.id].look)

    def test_watch_next_game_live(self):
        choices = self.post("/api/new", {"seed": 7, "num_teams": 4, "match_engine": "spatial"})["teams"]
        short_season(self.session, games=4)
        me = choices[0]["id"]
        self.post("/api/new/team", {"team_id": me})
        lg = self.session.league
        nxt = min((g for g in lg.games.values() if me in (g.home_team_id, g.away_team_id)),
                  key=lambda g: (g.day, g.id))
        r = self.post("/api/watch-next")
        self.assertEqual(r["game_id"], nxt.id)
        self.assertTrue(lg.games[nxt.id].played)
        # Everything up to and including that day is played; nothing after it.
        self.assertTrue(all(g.played == (g.day <= nxt.day) for g in lg.games.values()))
        page = self.client.get(r["url"])
        self.assertEqual(page.status_code, 200)
        self.assertIn('"live":true', page.text)
        self.assertIn("revealBox", page.text)
        self.assertIn('"live":false', self.client.get(f"/api/replay/{nxt.id}").text)

    def test_watch_next_needs_the_spatial_engine(self):
        choices = self.post("/api/new", {"seed": 7, "num_teams": 4})["teams"]
        self.post("/api/new/team", {"team_id": choices[0]["id"]})
        r = self.client.post("/api/watch-next")
        self.assertEqual(r.status_code, 400)
        self.assertIn("spatial", r.json()["detail"])

    def _spatial_team(self, games=4):
        choices = self.post("/api/new", {"seed": 7, "num_teams": 4, "match_engine": "spatial"})["teams"]
        short_season(self.session, games=games)
        me = choices[0]["id"]
        self.post("/api/new/team", {"team_id": me})
        return me

    def test_live_match_start_stream_coach_and_finish(self):
        me = self._spatial_team()
        lg = self.session.league
        nxt = min((g for g in lg.games.values() if me in (g.home_team_id, g.away_team_id)),
                  key=lambda g: (g.day, g.id))
        saved_pace = lg.teams[me].tactics.pace
        r = self.post("/api/live/start")
        self.assertTrue(r["started"])
        self.assertEqual(r["game_id"], nxt.id)
        self.assertEqual(r["state"]["live_game"], nxt.id)
        self.assertFalse(lg.games[nxt.id].played)
        # Days before the game are played; nothing on its day yet.
        self.assertTrue(all(g.played == (g.day < nxt.day) for g in lg.games.values()))
        page = self.client.get("/api/live/page")
        self.assertIn('"live_client"', page.text)
        # While live, the rest of the game waits.
        self.assertEqual(self.client.post("/api/sim", json={"mode": "day"}).status_code, 409)
        chunk = self.client.get("/api/live/next", params={"after": 0}).json()
        self.assertTrue(chunk["possessions"])
        st = chunk["state"]
        side = st[st["user_side"]]
        on = [p for p in side["players"] if p["on_court"]]
        bench = [p for p in side["players"] if not p["on_court"]]
        self.post("/api/live/command", {"type": "auto_subs", "on": False})
        self.post("/api/live/command", {"type": "sub", "out": on[0]["id"], "in": bench[0]["id"]})
        self.post("/api/live/command", {"type": "timeout"})
        self.post("/api/live/command", {"type": "tactics", "changes": {"pace": 18}})
        bad = self.client.post("/api/live/command", json={"type": "sub", "out": bench[1]["id"], "in": bench[2]["id"]})
        self.assertEqual(bad.status_code, 400)
        n = chunk["next"]
        for _ in range(8):
            chunk = self.client.get("/api/live/next", params={"after": n}).json()
            n = chunk["next"]
        side = chunk["state"][chunk["state"]["user_side"]]
        self.assertLess(side["timeouts"], lg.rules.timeouts_per_game)
        self.assertTrue(next(p for p in side["players"] if p["id"] == bench[0]["id"])["on_court"])
        done = self.post("/api/live/finish")
        self.assertTrue(lg.games[nxt.id].played)
        self.assertIn(nxt.id, lg.replays)
        self.assertIsNone(done["state"]["live_game"])
        self.assertEqual(lg.teams[me].tactics.pace, saved_pace)            # match tactics only
        self.assertTrue(all(g.played == (g.day <= nxt.day) for g in lg.games.values()))
        self.post("/api/sim", {"mode": "day"})                               # back to normal

    def test_live_last_game_names_the_awards(self):
        self._spatial_team(games=2)
        lg = self.session.league
        for _ in range(4):
            if self.session.state()["stage"] == "season_over":
                break
            r = self.post("/api/live/start")
            if r["started"]:
                self.post("/api/live/finish")
        self.assertTrue(regular_season_over(lg))
        self.assertIsNotNone(self.session.state()["awards"])
        self.assertTrue(any(m["kind"] == "awards" for m in lg.inbox))

    def test_live_shouts_plays_and_end_of_game(self):
        self._spatial_team()
        self.post("/api/live/start")
        chunk = self.client.get("/api/live/next", params={"after": 0}).json()
        st = chunk["state"]
        mine = st[st["user_side"]]["players"]
        star = mine[0]["id"]
        cmd = lambda body: self.post("/api/live/command", body)
        r = cmd({"type": "shout", "shout": "slow"})
        self.assertIn("Slow it down", r["message"])
        r = cmd({"type": "shout", "shout": "push", "possessions": 4})
        self.assertIn("instead of Slow it down", r["message"])
        cmd({"type": "shout", "shout": "ball_to", "player": star})
        r = cmd({"type": "shout", "shout": "foul"})
        names = [s["name"] for s in r["state"]["manager"]["shouts"]]
        self.assertEqual(names, ["push", "ball_to", "foul"])
        r = cmd({"type": "cancel_shout", "shout": "foul"})
        self.assertEqual(len(r["state"]["manager"]["shouts"]), 2)
        r = cmd({"type": "timeout", "play": "pnr", "target": mine[4]["id"]})
        self.assertEqual(r["state"]["manager"]["ato"]["play"], "pnr")
        r = cmd({"type": "endgame", "last_shot": "off", "two_for_one": "on"})
        self.assertEqual(r["state"]["manager"]["endgame"]["two_for_one"], "on")
        for body, text in (({"type": "shout", "shout": "nap"}, "unknown instruction"),
                           ({"type": "shout", "shout": "ball_to"}, "pick one of your players"),
                           ({"type": "ato", "play": "flex"}, "unknown play"),
                           ({"type": "endgame", "foul": "maybe"}, "must be one of")):
            res = self.client.post("/api/live/command", json=body)
            self.assertEqual(res.status_code, 400, body)
            self.assertIn(text, res.json()["detail"])
        n = chunk["next"]
        for _ in range(10):
            chunk = self.client.get("/api/live/next", params={"after": n}).json()
            n = chunk["next"]
        match = self.session.live["match"]
        self.assertEqual(match.fg.stats["ato_run"] + match.fg.stats["ato_missed"], 1)
        self.post("/api/live/finish")

    def test_live_injuries_are_reported_once(self):
        from hoopsim.models import Injury
        me = self._spatial_team()
        lg = self.session.league
        self.post("/api/live/start")
        match = self.session.live["match"]
        chunk = self.client.get("/api/live/next", params={"after": 0}).json()
        self.assertIn("injured", chunk["state"]["home"]["players"][0])
        seat = match.fg.sides[match.user_side].lineup[0]
        seat.injury = Injury("sprained ankle", 5)          # as if the roll had hit him
        chunk = self.client.get("/api/live/next", params={"after": chunk["next"]}).json()
        p = next(x for x in chunk["state"][match.user_side]["players"] if x["id"] == seat.p.id)
        self.assertTrue(p["injured"])
        self.assertEqual(p["injury"], "sprained ankle")
        self.post("/api/live/finish")
        hurt = [pid for pid, _ in match.sim.injuries if lg.players[pid].team_id == me]
        self.assertTrue(hurt)
        self.post("/api/sim", {"mode": "day"})
        for pid in hurt:
            name = lg.players[pid].name
            news = [m for m in lg.inbox if m["kind"] == "injury" and m["title"] == f"Injury: {name}"]
            self.assertEqual(len(news), 1, name)

    def test_live_playoff_game(self):
        me = self._spatial_team(games=2)
        lg = self.session.league
        lg.rules.playoff_series_length = 1
        for _ in range(20):                       # trade offers pause the sim
            if self.post("/api/sim", {"mode": "end"})["state"].get("season_over"):
                break
        self.post("/api/playoffs/start")
        if not any(me in (s.high_id, s.low_id) for s in lg.playoffs):
            self.skipTest("this seed's team missed the playoffs")
        r = self.post("/api/live/start")
        g = lg.games[r["game_id"]]
        self.assertTrue(g.playoff and me in (g.home_team_id, g.away_team_id) and not g.played)
        self.post("/api/live/finish")
        self.assertTrue(g.played)
        series = next(s for s in lg.playoffs if g.id in s.game_ids)
        self.assertEqual(series.high_wins + series.low_wins, len(series.game_ids))
        # Every game scheduled that day was played.
        self.assertTrue(all(x.played for x in lg.games.values() if x.playoff and x.day == g.day))

    def test_live_on_the_hybrid_engine(self):
        choices = self.post("/api/new", {"seed": 7, "num_teams": 4, "match_engine": "hybrid"})["teams"]
        short_season(self.session, games=4)
        me = choices[0]["id"]
        self.post("/api/new/team", {"team_id": me})
        lg = self.session.league
        r = self.post("/api/live/start")
        self.post("/api/live/finish")
        self.assertIn(r["game_id"], lg.replays)
        others = [g for g in lg.games.values() if g.played and me not in (g.home_team_id, g.away_team_id)]
        self.assertTrue(all(g.id not in lg.replays for g in others))

    def test_highlight_levels_in_replays_and_live_chunks(self):
        me = self._spatial_team()
        lg = self.session.league
        self.post("/api/live/start")
        chunk = self.client.get("/api/live/next", params={"after": 0, "want": 8}).json()
        self.assertEqual(len(chunk["possessions"]), 8)                  # highlight modes ask for more
        self.assertTrue(all("hl" in p for p in chunk["possessions"]))
        gid = self.post("/api/live/finish")["game"]["id"]
        page = self.client.get(f"/api/replay/{gid}").text
        self.assertIn('"hl":', page)
        self.assertIn('id="hlBox"', page)

    def test_calendar_and_sim_to_a_day(self):
        choices = self.post("/api/new", {"seed": 3, "num_teams": 8})["teams"]
        me = choices[0]["id"]
        self.post("/api/new/team", {"team_id": me})
        cal = self.client.get("/api/calendar").json()
        days = cal["days"]
        mine = [d for d in days if d["game"]]
        self.assertEqual(len(mine), 82)
        self.assertEqual(cal["summary"]["games"], 82)
        self.assertEqual(cal["summary"]["home"], 41)
        self.assertEqual(sum(d["b2b"] for d in days), cal["summary"]["back_to_backs"])
        marks = [m for d in days for m in d["marks"]]
        for m in ("Opening night", "Christmas Day", "All-Star Game", "Trade deadline",
                  "Final day of the regular season"):
            self.assertIn(m, marks)
        # Go to my fifth game's day: every day before it is played, and it stops at the
        # start of that day, my game still to play.
        target = mine[4]["day"]
        r = self.post("/api/sim/to", {"day": target})
        self.assertEqual(r["at_day"], target)
        lg = self.session.league
        self.assertTrue(all(g.played == (g.day < target) for g in lg.games.values()))
        day = self.client.get(f"/api/calendar/day/{target}").json()
        self.assertTrue(day["games"] and not any(g["played"] for g in day["games"]))
        cal = self.client.get("/api/calendar").json()
        self.assertEqual(cal["today"], target - 1)
        self.assertEqual([d["day"] for d in cal["days"] if d["next"]], [target])
        self.assertEqual(self.client.get("/api/state").json()["next_day_game"]["id"], mine[4]["game"]["id"])
        self.assertEqual(self.post("/api/sim", {"mode": "day"})["games"][0]["id"], mine[4]["game"]["id"])
        self.assertEqual(self.client.post("/api/sim/to", json={"day": target}).status_code, 400)
        other = self.client.get("/api/calendar", params={"team_id": choices[1]["id"]}).json()
        self.assertEqual(other["team_id"], choices[1]["id"])

    def test_live_needs_the_spatial_engine(self):
        choices = self.post("/api/new", {"seed": 7, "num_teams": 4})["teams"]
        self.post("/api/new/team", {"team_id": choices[0]["id"]})
        self.assertEqual(self.client.post("/api/live/start").status_code, 400)

    def test_unknown_engine_is_refused(self):
        r = self.client.post("/api/new", json={"seed": 7, "num_teams": 4, "match_engine": "arcade"})
        self.assertGreaterEqual(r.status_code, 400)


@unittest.skipUnless(HAVE_WEB, "fastapi/httpx not installed (pip install -r requirements-web.txt)")
class LeagueFileAndEditorApiTests(unittest.TestCase):
    def setUp(self):
        from tests.test_league_file import synthetic_file
        self.tmp = tempfile.TemporaryDirectory()
        leagues = Path(self.tmp.name) / "leagues"
        leagues.mkdir()
        (leagues / "test.json").write_text(json.dumps(synthetic_file()))
        self.session = GameSession(saves_dir=self.tmp.name, leagues_dir=str(leagues))
        self.client = TestClient(create_app(self.session))

    def tearDown(self):
        self.tmp.cleanup()

    def ok(self, method, url, body=None):
        r = getattr(self.client, method)(url, **({"json": body} if body is not None else {}))
        self.assertEqual(r.status_code, 200, f"{url}: {r.text[:300]}")
        return r.json()

    def test_new_game_from_a_league_file(self):
        files = self.ok("get", "/api/new/files")["files"]
        self.assertEqual([f["file"] for f in files], ["test.json"])
        teams = self.ok("post", "/api/new", {"league_file": "test.json"})["teams"]
        self.assertEqual(sorted(t["abbr"] for t in teams), ["T0", "T1", "T2", "T3"])
        # Only files in the leagues folder, by name: no paths.
        for name in ("../x.json", "/etc/passwd", "missing.json"):
            r = self.client.post("/api/new", json={"league_file": name})
            self.assertEqual(r.status_code, 404, name)

    def test_editor_end_to_end(self):
        teams = self.ok("post", "/api/new", {"league_file": "test.json"})["teams"]
        me = teams[0]["id"]
        self.ok("post", "/api/new/team", {"team_id": me})
        self.assertIsNone(self.ok("get", "/api/state")["edited"])
        rows = self.ok("get", f"/api/editor/players?team_id={me}")["players"]
        self.assertEqual(len(rows), 13)
        pid = rows[-1]["id"]
        sheet = self.ok("get", f"/api/editor/player/{pid}")
        self.assertIn("hidden", sheet)                        # the editor shows true values
        r = self.ok("post", f"/api/editor/player/{pid}", {"attributes": {"three_point": 20}, "last_name": "Splash"})
        self.assertEqual(r["player"]["attributes"]["three_point"], 20)
        self.assertEqual(self.ok("get", f"/api/player/{pid}")["name"].split()[-1], "Splash")
        r = self.client.post(f"/api/editor/player/{pid}", json={"attributes": {"three_point": 0}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("1-20", r.json()["detail"])
        self.ok("post", f"/api/editor/team/{me}", {"city": "Metro", "name": "Comets"})
        state = self.ok("get", "/api/state")
        self.assertEqual(state["teams"][str(me)]["name"], "Metro Comets")
        self.assertEqual(state["edited"]["count"], 2)
        self.assertEqual(self.ok("get", "/api/editor/players?q=splash")["players"][0]["id"], pid)


@unittest.skipUnless(HAVE_WEB, "fastapi/httpx not installed (pip install -r requirements-web.txt)")
class AllStarApiTests(unittest.TestCase):
    def test_short_season_has_no_weekend_and_event_replays_404(self):
        session = GameSession()
        client = TestClient(create_app(session))
        teams = client.post("/api/new", json={"seed": 7, "num_teams": 4}).json()["teams"]
        short_season(session)
        client.post("/api/new/team", json={"team_id": teams[0]["id"]})
        d = client.get("/api/allstar").json()
        self.assertFalse(d["available"])
        self.assertEqual(d["past"], [])
        self.assertEqual(client.get("/api/replay/10000001").status_code, 404)

    def test_weekend_through_the_api(self):
        from tests.test_allstar import through_all_star
        session = GameSession()
        session.league = through_all_star(seed=6)
        session.rng = random.Random(0)
        client = TestClient(create_app(session))
        d = client.get("/api/allstar").json()
        self.assertTrue(d["available"])
        self.assertEqual(len(d["west"]["reserves"]), 7)
        self.assertIn("final", d["three"])
        self.assertIn("result", d["rising"])
        card = client.get(f"/api/player/{d['game']['mvp']}").json()
        self.assertTrue(card["all_star_now"])
        cal = client.get("/api/calendar").json()
        marks = [m for day in cal["days"] for m in day["marks"]]
        for m in ("All-Star starters", "Rising Stars", "Three-Point Contest", "All-Star Game"):
            self.assertIn(m, marks)
