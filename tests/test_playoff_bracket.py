"""The play-in, the playoff bracket the web app draws, and the series context of a
playoff game (game lists, box scores and the match viewer's scoreboard)."""
import json
import random
import tempfile
import unittest

from hoopsim.engine import play_season
from hoopsim.generate import generate_league, generate_schedule
from hoopsim.models import Phase
from hoopsim.playoffs import PLAY_IN_GAP, PLAYOFF_GAME_GAP, eliminated, play_playoffs, seeding, start_playoffs
from hoopsim.season import finish_season
from hoopsim.stats import season_totals
from hoopsim.web.bracket import series_status

try:
    from fastapi.testclient import TestClient
    from hoopsim.web.app import create_app
    from hoopsim.web.session import GameSession
    HAVE_WEB = True
except ImportError:                                    # pragma: no cover
    HAVE_WEB = False


def short_league(seed=3, teams=30, games=4):
    lg = generate_league(seed=seed, num_teams=teams)
    lg.games = {}
    lg.rules.games_per_season = games
    generate_schedule(lg, random.Random(0))
    return lg


class PlayInTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lg = lg = short_league()
        play_season(lg, random.Random(1))
        cls.last = max(g.day for g in lg.games.values())
        cls.seeds = seeding(lg)
        start_playoffs(lg)
        cls.drawn = [(s.conference, s.play_in, s.high_id, s.low_id) for s in lg.playoffs]
        cls.champ = play_playoffs(lg, random.Random(2))

    def test_seeds_seven_to_ten_play_in(self):
        self.assertEqual(len(self.drawn), 4)                     # 7v8 and 9v10 in both conferences
        for conf, kind, hi, lo in self.drawn:
            order = self.seeds[conf]
            want = (order[6], order[7]) if kind == "7v8" else (order[8], order[9])
            self.assertEqual((hi, lo), want)

    def test_decider_and_first_round_seeds(self):
        lg = self.lg
        for conf, order in self.seeds.items():
            pi = {s.play_in: s for s in lg.playoffs if s.conference == conf and s.play_in}
            self.assertEqual(set(pi), {"7v8", "9v10", "decider"})
            self.assertTrue(all(s.round == 0 and s.best_of == 1 and len(s.game_ids) == 1 for s in pi.values()))
            dec = pi["decider"]
            self.assertEqual({dec.high_id, dec.low_id}, {pi["7v8"].loser_id, pi["9v10"].winner_id})
            # The 7v8 loser is the higher seed and plays at home.
            self.assertEqual(lg.games[dec.game_ids[0]].home_team_id, pi["7v8"].loser_id)
            first = {(s.high_seed, s.low_seed): s for s in lg.playoffs if s.conference == conf and s.round == 1}
            self.assertEqual(set(first), {(1, 8), (4, 5), (2, 7), (3, 6)})
            self.assertEqual(first[(2, 7)].low_id, pi["7v8"].winner_id)
            self.assertEqual(first[(1, 8)].low_id, dec.winner_id)
            self.assertEqual(first[(1, 8)].high_id, order[0])
            out = {pi["9v10"].loser_id, dec.loser_id}
            self.assertTrue(all(eliminated(lg, t) for t in out))
            self.assertFalse(any(t in (s.high_id, s.low_id) for t in out for s in lg.playoffs if s.round >= 1))

    def test_calendar_play_in_week(self):
        lg = self.lg
        day = lambda s: lg.games[s.game_ids[0]].day
        for s in lg.playoffs:
            if s.play_in in ("7v8", "9v10"):
                self.assertEqual(day(s), self.last + PLAY_IN_GAP)
            elif s.play_in == "decider":
                self.assertEqual(day(s), self.last + PLAY_IN_GAP + PLAYOFF_GAME_GAP)
            elif s.round == 1:
                self.assertEqual(day(s), self.last + 6)          # round one: six days after the season

    def test_champion_and_play_in_stats_left_out(self):
        lg = self.lg
        finals = max(lg.playoffs, key=lambda s: s.round)
        self.assertIsNone(finals.conference)
        self.assertEqual(self.champ, finals.winner_id)
        out = {s.loser_id for s in lg.playoffs if s.play_in in ("9v10", "decider")}
        po = season_totals(lg, playoff=True)
        self.assertFalse(any(t["team_id"] in out for t in po.values()))

    def test_no_play_in_for_small_conferences_or_when_off(self):
        lg = short_league(seed=4, teams=12, games=2)
        play_season(lg, random.Random(1))
        start_playoffs(lg)
        self.assertFalse(any(s.play_in for s in lg.playoffs))
        lg = short_league(seed=4, teams=30, games=2)
        lg.rules.play_in = False
        play_season(lg, random.Random(1))
        start_playoffs(lg)
        self.assertFalse(any(s.play_in for s in lg.playoffs))
        self.assertEqual(sum(1 for s in lg.playoffs if s.round == 1), 8)

    def test_who_is_still_alive_during_the_play_in(self):
        lg = short_league(seed=5, teams=30, games=2)
        play_season(lg, random.Random(1))
        start_playoffs(lg)
        order = seeding(lg)["East"]
        self.assertFalse(eliminated(lg, order[0]))             # seeds 1-6 wait for round one
        self.assertFalse(eliminated(lg, order[5]))
        self.assertFalse(eliminated(lg, order[9]))             # in the play-in
        self.assertTrue(eliminated(lg, order[10]))             # 11th: missed out

    def test_series_status(self):
        self.assertEqual(series_status("AAA", 2, "BBB", 0, 4), "AAA leads 2–0")
        self.assertEqual(series_status("AAA", 2, "BBB", 2, 4), "Series tied 2–2")
        self.assertEqual(series_status("AAA", 1, "BBB", 4, 4), "BBB wins 4–1")
        self.assertEqual(series_status("AAA", 0, "BBB", 0, 1, "decider"), "Winner is the 8 seed")
        self.assertEqual(series_status("AAA", 0, "BBB", 1, 1, "7v8"), "BBB wins")


def page_data(html: str) -> dict:
    return json.loads(html.split("window.GAME = ", 1)[1].split(";\n", 1)[0])


@unittest.skipUnless(HAVE_WEB, "fastapi/httpx not installed (pip install -r requirements-web.txt)")
class BracketApiTests(unittest.TestCase):
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

    def new_game(self, teams, games, engine="statistical"):
        choices = self.post("/api/new", {"seed": 7, "num_teams": teams, "match_engine": engine})["teams"]
        lg = self.session.league
        lg.games = {}
        lg.rules.games_per_season = games
        generate_schedule(lg, random.Random(0))
        me = choices[0]["id"]
        self.post("/api/new/team", {"team_id": me})
        return me

    def sim_to_end(self):
        for _ in range(30):                      # trade offers pause the sim
            if self.post("/api/sim", {"mode": "end"})["state"].get("season_over"):
                return
        self.fail("the regular season didn't end")

    def play_nights(self, n):
        """Sim calendar days (off days only move the date) until ``n`` nights had games."""
        for _ in range(4 * n):
            if n == 0:
                return
            n -= self.post("/api/playoffs/sim", {"mode": "day"}).get("games_played", 1) > 0
        self.assertEqual(n, 0)

    @staticmethod
    def slots(b):
        out = [b["finals"]]
        for c in b["conferences"]:
            out += c["play_in"] + [s for col in c["rounds"] for s in col]
        return out

    def test_bracket_through_the_postseason(self):
        me = self.new_game(30, 4)
        self.assertIsNone(self.get("/api/playoffs")["bracket"])           # nothing played yet
        for _ in range(10):                      # "Next day" can be a day without games
            self.post("/api/sim", {"mode": "day"})
            if self.get("/api/playoffs")["bracket"]:
                break
        # Projected from the standings mid-season.
        d = self.get("/api/playoffs")
        b = d["bracket"]
        self.assertTrue(b["projected"])
        self.assertEqual(d["user_team_id"], me)
        self.assertEqual([c["conference"] for c in b["conferences"]], ["East", "West"])
        for c in b["conferences"]:
            self.assertEqual([len(col) for col in c["rounds"]], [4, 2, 1])
            self.assertEqual(c["round_labels"], ["First Round", "Conference Semifinals", "Conference Finals"])
            self.assertEqual([s["play_in"] for s in c["play_in"]], ["7v8", "9v10", "decider"])
            self.assertEqual([t["seed"] for t in c["play_in"][0]["teams"]], [7, 8])
            self.assertEqual([[t["seed"] for t in s["teams"]] for s in c["rounds"][0]],
                             [[1, 8], [4, 5], [2, 7], [3, 6]])
            self.assertIsNone(c["rounds"][0][0]["teams"][1]["id"])         # the 8 seed: play-in winner
            self.assertIsNone(c["rounds"][1][0]["teams"][0]["id"])         # TBD
        self.assertTrue(all(s["id"] is None and s["projected"] for s in self.slots(b)))

        self.sim_to_end()
        self.assertEqual(self.get("/api/playoffs")["stage"], "season_over")
        self.post("/api/playoffs/start")
        b = self.get("/api/playoffs")["bracket"]
        self.assertFalse(b["projected"])
        drawn = [s for s in self.slots(b) if s["id"] is not None]
        self.assertEqual(len(drawn), 4)                                    # the play-in's first night

        # Mid play-in: the first night is over, the deciders are drawn.
        self.play_nights(1)
        b = self.get("/api/playoffs")["bracket"]
        for c in b["conferences"]:
            s78, s910, dec = c["play_in"]
            self.assertTrue(s78["over"] and s910["over"])
            self.assertEqual(s78["status"], f"{next(t['abbr'] for t in s78['teams'] if t['id'] == s78['winner_id'])} wins")
            # The decider's teams are known (drawn or about to be).
            loser78 = next(t["id"] for t in s78["teams"] if t["id"] != s78["winner_id"])
            self.assertEqual([t["id"] for t in dec["teams"]], [loser78, s910["winner_id"]])
            self.assertEqual(c["rounds"][0][2]["teams"][1]["id"], s78["winner_id"])   # the 7 seed is in
            self.assertEqual(c["rounds"][0][2]["teams"][1]["seed"], 7)
            self.assertIsNone(c["rounds"][0][0]["teams"][1]["id"])         # the 8 seed isn't yet

        # Mid round one.
        self.play_nights(4)
        d = self.get("/api/playoffs")
        b = d["bracket"]
        first = [s for c in b["conferences"] for s in c["rounds"][0]]
        self.assertTrue(all(s["id"] is not None and s["games"] for s in first))
        self.assertTrue(all(s["teams"][0]["seed"] < s["teams"][1]["seed"] for s in first))
        live = [s for s in first if not s["over"]]
        self.assertTrue(live)
        for s in first:
            self.assertEqual(sum(s["wins"]), len([g for g in s["games"] if g["played"]]))
            self.assertRegex(s["status"], r"^(Series tied \d–\d|\w+ (leads|wins) \d–\d)$")
            for i, g in enumerate(s["games"]):
                self.assertEqual(g["series"]["game"], i + 1)
                self.assertEqual(g["series"]["title"], s["conference"] + " First Round")
        self.assertEqual(first[0]["games"][-1]["series"]["status"], first[0]["status"])
        # Game lists and box scores carry the series too.
        g = first[0]["games"][0]
        box = self.get(f"/api/game/{g['id']}")
        self.assertEqual(box["series"]["game"], 1)
        self.assertIn(box["series"]["status"], ("{} leads 1–0".format(box["home"]), "{} leads 1–0".format(box["away"])))

        # After the Finals.
        self.post("/api/playoffs/sim", {"mode": "all"})
        d = self.get("/api/playoffs")
        if d["stage"] == "playoffs":
            self.fail("the playoffs didn't finish")
        b = d["bracket"]
        fin = b["finals"]
        self.assertTrue(fin["over"])
        self.assertEqual(fin["winner_id"], d["champion"])
        self.assertEqual(fin["status"].split(" ")[1], "wins")
        self.assertTrue(all(s["over"] for s in self.slots(b)))
        # Conference champions face each other, East on top.
        east, west = b["conferences"]
        self.assertEqual([t["id"] for t in fin["teams"]],
                         [east["rounds"][-1][0]["winner_id"], west["rounds"][-1][0]["winner_id"]])

    def test_series_on_the_viewer_scoreboard(self):
        me = self.new_game(4, 2, engine="hybrid")
        lg = self.session.league
        lg.rules.playoff_series_length = 3
        # A regular-season replay: no series line (as with every replay saved before it existed).
        r = self.post("/api/live/start")
        self.assertNotIn("series", page_data(self.client.get("/api/live/page").text))
        self.post("/api/live/finish")
        self.assertNotIn("series", page_data(self.client.get(f"/api/replay/{r['game_id']}").text))
        self.sim_to_end()
        self.post("/api/playoffs/start")
        # Game 1, played live.
        r = self.post("/api/live/start")
        live = page_data(self.client.get("/api/live/page").text)
        s = live["series"]
        g1 = lg.games[r["game_id"]]
        self.assertEqual((s["game"], s["home_wins"], s["away_wins"], s["play_in"]), (1, 0, 0, ""))
        self.assertEqual((s["home"], s["away"]), (lg.teams[g1.home_team_id].abbreviation,
                                                  lg.teams[g1.away_team_id].abbreviation))
        self.assertEqual(s["wins_needed"], 2)
        self.assertTrue(s["title"].endswith("Finals"))
        self.assertEqual(s["status"], "Series tied 0–0")
        self.assertNotIn("status_after", s)
        self.post("/api/live/finish")
        # Game 2, watched: the series as it stood after game 1.
        w = self.post("/api/watch-next")
        data = page_data(self.client.get(w["url"]).text)
        s2 = data["series"]
        self.assertEqual(s2["game"], 2)
        winner = g1.winner_id()
        home2 = lg.games[w["game_id"]].home_team_id
        self.assertEqual((s2["home_wins"], s2["away_wins"]), (1, 0) if winner == home2 else (0, 1))
        lead = lg.teams[winner].abbreviation
        self.assertEqual(s2["status"], f"{lead} leads 1–0")
        # The replay of game 1 shows the series before it.
        self.assertEqual(page_data(self.client.get(f"/api/replay/{g1.id}").text)["series"]["game"], 1)


if __name__ == "__main__":
    unittest.main()
