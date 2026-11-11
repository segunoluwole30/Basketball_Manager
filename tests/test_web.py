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

    def test_live_needs_the_spatial_engine(self):
        choices = self.post("/api/new", {"seed": 7, "num_teams": 4})["teams"]
        self.post("/api/new/team", {"team_id": choices[0]["id"]})
        self.assertEqual(self.client.post("/api/live/start").status_code, 400)

    def test_unknown_engine_is_refused(self):
        r = self.client.post("/api/new", json={"seed": 7, "num_teams": 4, "match_engine": "arcade"})
        self.assertGreaterEqual(r.status_code, 400)
