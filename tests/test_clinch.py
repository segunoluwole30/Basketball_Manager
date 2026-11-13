"""Clinch and elimination markers (hoopsim/clinch.py): hand-built standings, a brute-force
check that a marker is never wrong, timing within a short season, the CLI and the API."""
import contextlib
import io
import itertools
import random
import tempfile
import time
import unittest
from datetime import date

from hoopsim import clinch as C
from hoopsim import generate_league
from hoopsim.generate import generate_schedule
from hoopsim.models import Game, GameResult, League, Team
from hoopsim.playoffs import _Tiebreak, ranked
from hoopsim.season import play_regular_season, regular_season_over


def build(teams, games, season=2026):
    """teams: [(id, conference, division)]; games: [(home, away, winner or None)].
    Every played game is won by one point, unless a margin is given as a 4th item."""
    lg = League("Test", season, date(season, 10, 20))
    for tid, conf, div in teams:
        lg.add_team(Team(tid, f"City{tid}", f"Team{tid}", f"T{tid:02d}", conf, div))
    for i, g in enumerate(games):
        home, away, winner = g[:3]
        margin = g[3] if len(g) > 3 else 1
        game = Game(i + 1, season, i + 1, home, away)
        if winner is not None:
            game.result = GameResult(100 + margin, 100) if winner == home else GameResult(100, 100 + margin)
        lg.add_game(game)
    return lg


def wins(team, opp, n, home_first=True):
    return [(team, opp, team) if (i % 2 == 0) == home_first else (opp, team, team) for i in range(n)]


def left(a, b, n):
    return [(a, b, None) if i % 2 == 0 else (b, a, None) for i in range(n)]


def padded(teams, games):
    """build() with every team given losses to a filler West team (id 99), so that all
    of them play the same number of games and win totals compare like percentages."""
    F = 99
    if F not in [t[0] for t in teams]:
        teams = teams + [(F, "West", "Pacific")]
    count = {t[0]: 0 for t in teams}
    for g in games:
        count[g[0]] += 1
        count[g[1]] += 1
    target = max(n for t, n in count.items() if t != F)
    games = list(games)
    for t, n in count.items():
        if t != F:
            games += [(F, t, F)] * (target - n)
    return build(teams, games)


def ctx_group(lg, tid, kind="conference"):
    attr = kind
    return C._Ctx(lg), [x for x in lg.teams if getattr(lg.teams[x], attr) == getattr(lg.teams[tid], attr)]


# -- brute force -------------------------------------------------------------------------
def random_league(rng, nteams, per_pair, played_frac, confs):
    divs = ["Atlantic", "Central", "Northwest", "Pacific"]
    teams = []
    for i in range(nteams):
        c = i % confs
        teams.append((i + 1, "East" if c == 0 else "West", divs[(i // confs) % 2 + 2 * c]))
    pairs = [p for p in itertools.combinations(range(1, nteams + 1), 2) for _ in range(per_pair)]
    rng.shuffle(pairs)
    bias = {t: rng.random() for t in range(1, nteams + 1)}
    cut = int(len(pairs) * played_frac)
    games = []
    for i, (a, b) in enumerate(pairs):
        home, away = (a, b) if rng.random() < 0.5 else (b, a)
        winner = None
        if i < cut:
            winner = home if rng.random() < bias[home] / (bias[home] + bias[away] + 1e-9) else away
        games.append((home, away, winner))
    return build(teams, games)


def rank_ranges(lg, T, groups):
    """{group key: (best, worst rank of T)} over every way the remaining games can go,
    and with T's margins either all huge or all tiny (point differential is the last
    tie-break). Also, per number of further wins j: T's worst and best rank when it wins
    at least j / at most j of its remaining games."""
    rem = [g for g in lg.games.values() if not g.played]
    out = {key: [99, 0] for key in groups}
    by_wins = {}
    for bits in itertools.product((0, 1), repeat=len(rem)):
        j = sum(1 for g, b in zip(rem, bits)
                if T in (g.home_team_id, g.away_team_id) and (g.home_team_id if b else g.away_team_id) == T)
        for sign in (1, -1):
            for g, b in zip(rem, bits):
                winner = g.home_team_id if b else g.away_team_id
                m = (50 if (winner == T) == (sign > 0) else 1) if T in (g.home_team_id, g.away_team_id) else 1
                g.result = GameResult(100 + m, 100) if b else GameResult(100, 100 + m)
            tb = _Tiebreak(lg)
            for key, group in groups.items():
                r = tb.order(group).index(T) + 1
                o = out[key]
                o[0], o[1] = min(o[0], r), max(o[1], r)
                bw = by_wins.setdefault((key, j), [99, 0])
                bw[0], bw[1] = min(bw[0], r), max(bw[1], r)
    for g in rem:
        g.result = None
    return out, by_wins


class BruteForceTests(unittest.TestCase):
    """Enumerate every completion of tiny leagues: a marker is never wrong."""

    def check(self, seeds, **kw):
        shown = truth = 0
        for seed in seeds:
            lg = random_league(random.Random(seed), **kw)
            ctx = C._Ctx(lg)
            for T in lg.teams:
                groups = {"conf": [x for x in lg.teams if lg.teams[x].conference == lg.teams[T].conference],
                          "div": [x for x in lg.teams if lg.teams[x].division == lg.teams[T].division],
                          "league": list(lg.teams)}
                ranges, by_wins = rank_ranges(lg, T, groups)
                rem = ctx.rem[T]
                for key, group in groups.items():
                    best, worst = ranges[key]
                    for k in range(1, len(group)):
                        cl, el = C.clinched(ctx, T, group, k), C.eliminated(ctx, T, group, k)
                        msg = f"seed {seed} team {T} {key} top {k}: ranks {best}-{worst}"
                        if cl:
                            self.assertLessEqual(worst, k, "wrong clinch: " + msg)
                        if el:
                            self.assertGreater(best, k, "wrong elimination: " + msg)
                        self.assertFalse(cl and el)
                        shown += cl + el
                        truth += (worst <= k) + (best > k)
                        # the wins-to-clinch / losses-to-elimination bounds hold too
                        ctx.limit = C.DETAIL_BUDGET
                        wc = C._smallest(lambda j: C.clinched(ctx, T, group, k, j), rem)
                        le = C._smallest(lambda j: C.eliminated(ctx, T, group, k, j), rem)
                        ctx.limit = C.SEARCH_BUDGET
                        if wc is not None:
                            for j in range(wc, rem + 1):
                                if (key, j) in by_wins:
                                    self.assertLessEqual(by_wins[(key, j)][1], k, "wins to clinch: " + msg)
                        if le is not None:
                            for j in range(0, rem - le + 1):
                                if (key, j) in by_wins:
                                    self.assertGreater(by_wins[(key, j)][0], k, "losses to elimination: " + msg)
        # conservative, but not uselessly so
        self.assertGreater(shown, 0.75 * truth)

    def test_one_conference_two_divisions(self):
        self.check(range(8), nteams=6, per_pair=2, played_frac=0.75, confs=1)

    def test_two_conferences(self):
        self.check(range(6), nteams=8, per_pair=2, played_frac=0.86, confs=2)

    def test_many_games_between_few_teams(self):
        self.check(range(6), nteams=5, per_pair=4, played_frac=0.8, confs=1)


# -- hand-built standings ----------------------------------------------------------------
class HeadToHeadTests(unittest.TestCase):
    """T, A, B, C in one conference (a top-2 bracket): T is 10-0 and done; A and B are
    8-0 and have two games left, against each other."""

    def league(self, a_b_meet=True):
        T, A, B, Cc = 1, 2, 3, 4
        teams = [(T, "East", "Atlantic"), (A, "East", "Atlantic"), (B, "East", "Central"), (Cc, "East", "Central")]
        games = wins(T, Cc, 10) + wins(A, Cc, 8) + wins(B, Cc, 8)
        games += left(A, B, 2) if a_b_meet else left(A, Cc, 2) + left(B, Cc, 2)
        return padded(teams, games)

    def test_clinch_only_through_the_games_between_rivals(self):
        lg = self.league()
        self.assertEqual(C.race_format(4)["direct"], 2)
        ctx, conf = ctx_group(lg, 1)
        # each rival alone can still catch T...
        for x in (2, 3):
            self.assertGreaterEqual(ctx.wins[x] + ctx.rem[x], 10)
        # ...but not both, so T is in the top two.
        self.assertTrue(C.clinched(ctx, 1, conf, 2))
        self.assertIn("x", C.race(lg)[1]["markers"])
        # A or B can still finish 10-0, level with T, and they never met: no #1 seed yet.
        self.assertNotIn("z", C.race(lg)[1]["markers"])
        self.assertEqual(C.race(lg)[4]["markers"], ["o"])

    def test_without_the_head_to_head_games_it_isnt_clinched(self):
        lg = self.league(a_b_meet=False)
        self.assertNotIn("x", C.race(lg)[1]["markers"])

    def test_elimination_through_the_games_between_rivals(self):
        # T can finish 10-x at best; C already has 11 wins; A and B (8 each) meet six
        # more times, so one of them passes 10: T can't make the top two.
        T, A, B, Cc, D = 1, 2, 3, 4, 5
        teams = [(T, "East", "Atlantic"), (A, "East", "Atlantic"), (B, "East", "Central"),
                 (Cc, "East", "Central"), (D, "East", "Central")]
        games = (wins(T, D, 9) + left(T, D, 1) + wins(Cc, D, 11) + wins(A, D, 8) + wins(B, D, 8)
                 + left(A, B, 6))
        lg = padded(teams, games)
        ctx, conf = ctx_group(lg, T)
        for x in (A, B):                       # each alone could stay behind T
            self.assertLessEqual(ctx.wins[x], 10)
        self.assertTrue(C.eliminated(ctx, T, conf, 2))
        self.assertGreater(ctx.rem[T], 0)      # with a game still to play
        self.assertEqual(C.race(lg)[T]["markers"], ["o"])


class TiebreakTests(unittest.TestCase):
    """T is 10-2 and done; X is 9-2 with one game left, so X can tie T for first."""

    def league(self, t_won_series: bool):
        T, X, Cc, D = 1, 2, 3, 4
        teams = [(T, "East", "Atlantic"), (Cc, "East", "Atlantic"), (X, "East", "Central"), (D, "East", "Central")]
        if t_won_series:
            games = wins(T, X, 2) + wins(T, Cc, 8) + wins(D, T, 2) + wins(X, Cc, 9) + left(X, Cc, 1)
        else:
            games = (wins(T, X, 1) + wins(X, T, 1) + wins(T, Cc, 9) + wins(D, T, 1)
                     + wins(X, Cc, 8) + wins(D, X, 1) + left(X, Cc, 1))
        return padded(teams, games)

    def test_season_series_won_settles_the_tie(self):
        lg = self.league(True)
        self.assertIn("z", C.race(lg)[1]["markers"])

    def test_split_series_leaves_the_tie_undecided(self):
        lg = self.league(False)
        self.assertNotIn("z", C.race(lg)[1]["markers"])
        # Indeed: if X wins out, point differential decides first place.
        g = next(g for g in lg.games.values() if not g.played)
        firsts = set()
        for margin in (1, 60):
            winner_home = g.home_team_id == 2
            g.result = GameResult(100 + margin, 100) if winner_home else GameResult(100, 100 + margin)
            firsts.add(ranked(lg, [1, 2, 3, 4])[0])
        g.result = None
        self.assertEqual(firsts, {1, 2})


class DivisionTests(unittest.TestCase):
    """Eight East teams (top-4 bracket), two divisions; games against a West filler."""

    def league(self):
        F = 99
        teams = [(i, "East", "Atlantic" if i <= 4 else "Central") for i in range(1, 9)] + [(F, "West", "Pacific")]
        games = wins(1, F, 10) + wins(F, 1, 10)                     # T = 1: 10-10, done
        for x in (2, 3, 4):                                          # its division: 2-8, 5 left
            games += wins(x, F, 2) + wins(F, x, 6) + left(x, F, 5)
        games += wins(5, F, 18) + wins(F, 5, 2)                       # P = 5: 18-2, done
        games += wins(6, F, 15) + left(6, F, 5)                      # Q = 6: 15-0, 5 left
        for x in (7, 8):
            games += wins(x, F, 9) + wins(F, x, 1) + left(x, F, 10)   # 9-1, 10 left
        return padded(teams, games)

    def test_division_without_a_playoff_spot_and_the_other_way_round(self):
        lg = self.league()
        r = C.race(lg)
        self.assertEqual(r[1]["markers"], ["y"])       # nobody in its division can catch it...
        details = {d["key"]: d for d in C.race_details(lg, 1)}
        self.assertFalse(details["playoffs"]["clinched"])
        self.assertTrue(details["division"]["clinched"])
        self.assertIn("x", r[5]["markers"])            # P: only Q, 7 and 8 can pass it -> top 4
        self.assertNotIn("y", r[5]["markers"])         # ...but Q is in its division
        self.assertEqual(r[5]["marker"], "x")

    def test_wins_to_clinch_and_losses_to_elimination(self):
        lg = self.league()
        q = {d["key"]: d for d in C.race_details(lg, 6)}["division"]
        # Q (15-0, 5 left) against P (18-2, done) and 7 and 8 (9-1, 10 left, so up to 19):
        # 20 wins clinch the division; 17 or fewer lose it to P (ties are open: no games met).
        self.assertEqual((q["wins_to_clinch"], q["losses_to_elimination"]), (5, 3))
        p = {d["key"]: d for d in C.race_details(lg, 5)}["division"]
        self.assertEqual((p["clinched"], p["eliminated"], p["wins_to_clinch"]), (False, False, None))


class PlayInTests(unittest.TestCase):
    """A 12-team East (an 8-team bracket): NBA format, 1-6 in, 7-10 play-in."""

    def league(self):
        F = 99
        teams = [(i, "East", "Atlantic" if i <= 6 else "Central") for i in range(1, 13)] + [(F, "West", "Pacific")]
        games = []
        for x in range(1, 7):                                  # 35-15, done
            games += wins(x, F, 35) + wins(F, x, 15)
        games += wins(7, F, 30) + wins(F, 7, 20)               # T: 30-20, done
        for x in (8, 9, 10):                                   # 25-15, 10 left
            games += wins(x, F, 25) + wins(F, x, 15) + left(x, F, 10)
        for x in (11, 12):                                     # 18-27, 5 left
            games += wins(x, F, 18) + wins(F, x, 27) + left(x, F, 5)
        return padded(teams, games)

    def test_play_in_clinched_not_top_six(self):
        lg = self.league()
        self.assertEqual(C.race_format(12), {"direct": 6, "play_in": 10, "bracket": 8})
        r = C.race(lg)
        self.assertEqual(r[7]["markers"], ["pi"])
        d = {x["key"]: x for x in C.race_details(lg, 7)}
        self.assertTrue(d["playoffs"]["eliminated"])         # six teams already have 35 wins
        self.assertTrue(d["play_in"]["clinched"])
        for x in (11, 12):
            self.assertEqual(r[x]["markers"], ["o"])          # with games left
        self.assertTrue(all(r[x]["marker"] != "o" for x in (8, 9, 10)))
        legend = [m["marker"] for m in C.marker_legend(lg)]
        self.assertEqual(legend, ["w", "z", "y", "x", "pi", "o"])

    def test_rivals_below_can_only_fall_so_far(self):
        r = C.race(self.league())
        for x in (8, 9, 10):                     # 11 and 12 can't reach their 25 wins
            self.assertEqual(r[x]["markers"], ["pi"])


class SeasonTests(unittest.TestCase):
    def test_markers_appear_mid_season_and_are_right(self):
        lg = generate_league(seed=5, num_teams=12)
        lg.games = {}
        lg.rules.games_per_season = 20
        generate_schedule(lg, random.Random(0))
        rng = random.Random(1)
        seen = []                              # (day, team, marker, games left in the season)
        slowest = 0.0
        while not regular_season_over(lg):
            play_regular_season(lg, rng, days=1)
            left_now = sum(1 for g in lg.games.values() if not g.played)
            t0 = time.perf_counter()
            r = C.race(lg)
            slowest = max(slowest, time.perf_counter() - t0)
            seen += [(t, m, left_now) for t, v in r.items() for m in v["markers"]]
        early = {(t, m) for t, m, n in seen if n > 0}
        self.assertTrue(early, "no marker before the last day")
        self.assertTrue(any(n >= 20 for _, _, n in seen), "markers only at the very end")
        # every marker shown mid-season holds in the final standings
        final = C.race(lg)
        for t, m in early:
            self.assertIn(m, final[t]["markers"], f"team {t} lost marker {m}")
        self.assertLess(slowest, 0.25)

    def test_thirty_teams_fast_enough(self):
        lg = generate_league(seed=3, num_teams=30)
        rng = random.Random(1)
        games = sorted(lg.games.values(), key=lambda g: (g.day, g.id))
        strength = {t: rng.random() + 0.3 for t in lg.teams}
        done = 0
        for frac in (0.1, 0.5, 0.8, 0.9, 0.97):
            cut = int(len(games) * frac)
            for g in games[done:cut]:
                p = strength[g.home_team_id] / (strength[g.home_team_id] + strength[g.away_team_id])
                g.result = GameResult(101, 100) if rng.random() < p else GameResult(100, 101)
            done = cut
            t0 = time.perf_counter()
            C.race(lg)
            self.assertLess(time.perf_counter() - t0, 0.15, f"at {frac:.0%} of the season")
        r = C.race(lg)
        self.assertTrue(any(v["marker"] for v in r.values()))

    def test_final_standings_mark_everyone(self):
        lg = generate_league(seed=2, num_teams=12)
        lg.games = {}
        lg.rules.games_per_season = 6
        generate_schedule(lg, random.Random(0))
        play_regular_season(lg, random.Random(2))
        r = C.race(lg)
        for conf in {t.conference for t in lg.teams.values()}:
            order = ranked(lg, [t for t in lg.teams if lg.teams[t].conference == conf])
            k = C.race_format(len(order))["direct"]
            for i, t in enumerate(order):
                self.assertIn("x" if i < k else "o", r[t]["markers"])
            self.assertIn("z", r[order[0]]["markers"])


class CliTests(unittest.TestCase):
    def test_print_standings_and_play_command(self):
        from hoopsim.__main__ import print_standings
        from hoopsim.play import IO, Game as PlayGame
        lg = generate_league(seed=2, num_teams=12)
        lg.games = {}
        lg.rules.games_per_season = 6
        generate_schedule(lg, random.Random(0))
        play_regular_season(lg, random.Random(2))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            print_standings(lg)
        text = out.getvalue()
        self.assertIn("GB", text)
        self.assertIn("L10", text)
        self.assertIn("x: clinched a playoff spot", text)
        self.assertRegex(text, r"(?m)^ z  ")
        self.assertRegex(text, r"(?m)^ o  ")
        lg.user_team_id = next(iter(lg.teams))
        said = []
        PlayGame(lg, IO(ask=lambda p: "n", say=said.append), random.Random(1)).cmd_standings([])
        self.assertTrue(any(line.endswith("<--") for line in said))
        self.assertTrue(any(line.startswith(" x") or line.startswith(" z") or line.startswith(" y")
                            for line in said))

    def test_sim_reports_new_markers(self):
        from hoopsim.play import IO, Game as PlayGame
        lg = generate_league(seed=2, num_teams=12)
        lg.games = {}
        lg.rules.games_per_season = 6
        generate_schedule(lg, random.Random(0))
        lg.user_team_id = next(iter(lg.teams))
        said = []
        game = PlayGame(lg, IO(ask=lambda p: "n", say=said.append), random.Random(1))
        game._sim_days(None)
        self.assertTrue(any("clinched" in line for line in said))
        self.assertTrue(any("eliminated" in line for line in said))
        self.assertIn("in the", game.status_line())


try:
    from fastapi.testclient import TestClient
    from hoopsim.web.app import create_app
    from hoopsim.web.session import GameSession
    HAVE_WEB = True
except ImportError:                                    # pragma: no cover
    HAVE_WEB = False


@unittest.skipUnless(HAVE_WEB, "fastapi/httpx not installed")
class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.session = GameSession(saves_dir=self.tmp.name)
        self.client = TestClient(create_app(self.session))

    def tearDown(self):
        self.tmp.cleanup()

    def test_standings_markers_details_and_news(self):
        c = self.client
        teams = c.post("/api/new", json={"seed": 7, "num_teams": 12}).json()["teams"]
        lg = self.session.league
        lg.games = {}
        lg.rules.games_per_season = 8
        generate_schedule(lg, random.Random(0))
        me = teams[0]["id"]
        c.post("/api/new/team", json={"team_id": me})
        s = c.get("/api/standings").json()
        self.assertEqual([m["marker"] for m in s["legend"]], ["w", "z", "y", "x", "o"])
        row = s["conferences"][0]["teams"][0]
        for key in ("marker", "markers", "gb", "last10", "streak"):
            self.assertIn(key, row)
        self.assertEqual(row["marker"], "")
        self.assertIn("marker", s["divisions"][0]["teams"][0])
        self.assertIn("marker", c.get("/api/dashboard").json()["standings"][0])
        d = c.get(f"/api/standings/race/{me}").json()
        self.assertEqual(d["team_id"], me)
        self.assertEqual({r["key"] for r in d["races"]}, {"league", "seed1", "division", "playoffs"})
        self.assertEqual(c.get("/api/standings/race/9999").status_code, 404)
        for _ in range(20):                    # a trade offer can stop the sim early
            r = c.post("/api/sim", json={"mode": "end"})
            self.assertEqual(r.status_code, 200, r.text[:300])
            if r.json()["stopped_for"] is None:
                break
        s = c.get("/api/standings").json()
        rows = [t for conf in s["conferences"] for t in conf["teams"]]
        self.assertTrue(all(t["marker"] for t in rows))
        news = [m for m in c.get("/api/inbox").json()["items"] if m["kind"] == "race"]
        self.assertTrue(news)
        self.assertIn("Playoff race", [m["title"] for m in news])
        d = c.get(f"/api/standings/race/{me}").json()
        self.assertTrue(all(r["clinched"] != r["eliminated"] for r in d["races"]))

    def test_old_save_loads_with_markers(self):
        # Markers are computed from the games alone: nothing new is stored, so a save
        # made before them loads and shows them.
        from hoopsim.db import load_league, save_league
        lg = generate_league(seed=2, num_teams=12)
        lg.games = {}
        lg.rules.games_per_season = 6
        generate_schedule(lg, random.Random(0))
        play_regular_season(lg, random.Random(2), days=8)
        path = f"{self.tmp.name}/old.db"
        save_league(lg, path)
        back = load_league(path)
        self.assertEqual(C.race(back), C.race(lg))


if __name__ == "__main__":
    unittest.main()
