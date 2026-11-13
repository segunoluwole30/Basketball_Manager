"""League files (load_league_file) and the in-game editor."""
import json
import random
import tempfile
import unittest
from pathlib import Path

from hoopsim import load_league, save_league
from hoopsim.editor import EditError, clean_look, edit_player, edit_team, edited, player_list, player_sheet
from hoopsim.league_file import LeagueFileError, league_files, load_league_file
from hoopsim.models import Position
from hoopsim.season import play_regular_season

POS = ["PG", "SG", "SF", "PF", "C"]


def synthetic_file(teams: int = 4, per_team: int = 13, free_agents: int = 3) -> dict:
    """A small made-up league file: every field the format allows, some left out."""
    rng = random.Random(3)
    ts = [{"abbreviation": f"T{i}", "city": f"City {i}", "name": f"Club {i}",
           "conference": "East" if i < teams // 2 else "West", "reputation": 40 + i,
           "tactics": {"pace": 12}} for i in range(teams)]
    ps = []
    for i in range(teams * per_team + free_agents):
        team = f"T{i // per_team}" if i < teams * per_team else None
        p = {"first_name": f"First{i}", "last_name": f"Last{i}", "team": team, "position": POS[i % 5],
             "birth_date": f"{1995 + i % 10}-03-0{1 + i % 9}",
             "attributes": {"three_point": rng.randint(4, 18), "finishing": rng.randint(4, 18),
                            "defensive_rebounding": rng.randint(4, 18)},
             "jersey_number": i % per_team}
        if i % 3 == 0 and team:
            p["contract"] = {"salary": 5_000_000 + 100_000 * i, "expires_season": 2028, "option": "player"}
        if i % 4 == 0:
            p["potential_ability"] = 150
            p["hidden"] = {"clutch": 18}
            p["tendencies"] = {"three_point": 17}
        ps.append(p)
    return {"format": "hoopsim-league", "version": 1, "name": "Test League", "season": 2026,
            "rules": {"salary_cap": 150_000_000}, "source": "test", "teams": ts, "players": ps}


class LeagueFileTests(unittest.TestCase):
    def test_loads_teams_players_contracts_schedule(self):
        lg = load_league_file(synthetic_file())
        self.assertEqual(len(lg.teams), 4)
        self.assertEqual(len(lg.players), 55)
        self.assertEqual(lg.rules.salary_cap, 150_000_000)
        self.assertEqual(lg.info["source"], "test")
        self.assertEqual(lg.validate(), [])
        self.assertEqual(sum(1 for p in lg.players.values() if p.team_id is None), 3)
        p0 = next(p for p in lg.players.values() if p.last_name == "Last0")
        self.assertEqual(p0.contract.salary, 5_000_000)
        self.assertEqual(p0.contract.option.value, "player")
        self.assertEqual(p0.potential_ability, 150)
        self.assertEqual(p0.hidden.clutch, 18)
        self.assertEqual(p0.tendencies.three_point, 17)
        self.assertEqual(p0.attributes.passing, 10)              # missing ratings default to 10
        p1 = next(p for p in lg.players.values() if p.last_name == "Last1")
        self.assertIsNotNone(p1.contract)                         # filled in from ability
        for t in lg.teams.values():
            self.assertEqual(len(t.tactics.starters), 5)
            self.assertEqual(t.tactics.pace, 12)
        self.assertEqual(len(lg.games), 4 * 82 // 2)
        self.assertTrue(all(len(lg.roster(t)) == 13 for t in lg.teams))

    def test_plays_a_season_and_round_trips(self):
        lg = load_league_file(synthetic_file())
        lg.games = {}
        lg.rules.games_per_season = 6
        from hoopsim.generate import generate_schedule
        generate_schedule(lg, random.Random(0))
        play_regular_season(lg, random.Random(1))
        self.assertTrue(all(g.played for g in lg.games.values()))
        with tempfile.TemporaryDirectory() as d:
            save_league(lg, Path(d) / "x.db")
            back = load_league(Path(d) / "x.db")
        self.assertEqual(back.info["source"], "test")

    def test_bad_files_are_explained(self):
        cases = [
            ({"format": "nope"}, "not a league file"),
            ({**synthetic_file(), "teams": synthetic_file()["teams"][:1]}, "two teams"),
            ({**synthetic_file(), "rules": {"salary_kap": 1}}, "unknown rules"),
        ]
        bad_pos = synthetic_file()
        bad_pos["players"][0]["position"] = "QB"
        cases.append((bad_pos, "position"))
        bad_rating = synthetic_file()
        bad_rating["players"][0]["attributes"]["three_point"] = 25
        cases.append((bad_rating, "1-20"))
        bad_team = synthetic_file()
        bad_team["players"][0]["team"] = "ZZZ"
        cases.append((bad_team, "unknown team"))
        few = synthetic_file(per_team=4, free_agents=0)
        cases.append((few, "at least 5"))
        for data, words in cases:
            with self.subTest(words=words), self.assertRaises(LeagueFileError) as cm:
                load_league_file(data)
            self.assertIn(words, str(cm.exception))

    def test_league_files_lists_only_league_files(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "a.json").write_text(json.dumps(synthetic_file()))
            Path(d, "b.json").write_text('{"something": "else"}')
            Path(d, "c.json").write_text("not json")
            files = league_files(d)
        self.assertEqual([f["file"] for f in files], ["a.json"])
        self.assertEqual(files[0]["teams"], 4)


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.lg = load_league_file(synthetic_file())
        self.p = next(p for p in self.lg.players.values() if p.team_id == 1)

    def test_edit_ratings_and_details(self):
        before = self.p.current_ability
        done = edit_player(self.lg, self.p.id, {
            "attributes": {k: 20 for k in self.p.attributes.to_dict()},
            "first_name": "Edited", "position": "C", "height_cm": 220, "potential_ability": 10,
            "hidden": {"clutch": 3}, "tendencies": {"post_up": 19}, "morale": 90})
        self.assertGreater(self.p.current_ability, before)
        self.assertEqual(self.p.first_name, "Edited")
        self.assertIs(self.p.position, Position.C)
        self.assertEqual(self.p.physique.height_cm, 220)
        self.assertEqual(self.p.hidden.clutch, 3)
        self.assertEqual(self.p.tendencies.post_up, 19)
        self.assertEqual(self.p.morale, 90)
        self.assertGreaterEqual(self.p.potential_ability, self.p.current_ability)   # PA never below CA
        self.assertIn("ratings", done)
        self.assertEqual(edited(self.lg)["count"], 1)
        self.assertIn("Edited", edited(self.lg)["last"]["what"])

    def test_invalid_edit_changes_nothing(self):
        name = self.p.first_name
        for changes in ({"first_name": "X", "attributes": {"three_point": 21}},
                        {"first_name": "X", "attributes": {"flying": 5}},
                        {"first_name": "X", "position": "QB"},
                        {"first_name": "X", "height_cm": 300},
                        {"first_name": "X", "potential_ability": 500},
                        {"first_name": "X", "birth_date": "yesterday"},
                        {"first_name": "X", "contract": {"salary": 10**12}},
                        {"first_name": "X", "nonsense": 1}):
            with self.subTest(changes=changes), self.assertRaises(EditError):
                edit_player(self.lg, self.p.id, changes)
        self.assertEqual(self.p.first_name, name)
        self.assertIsNone(edited(self.lg))

    def test_contract_and_team_moves(self):
        edit_player(self.lg, self.p.id, {"contract": {"salary": 9_000_000, "expires_season": 2029, "option": "team"}})
        self.assertEqual((self.p.contract.salary, self.p.contract.expires_season, self.p.contract.option.value),
                         (9_000_000, 2029, "team"))
        with self.assertRaises(EditError):                    # 13 is the minimum roster
            edit_player(self.lg, self.p.id, {"team_id": None})
        fa = next(p for p in self.lg.players.values() if p.team_id is None)
        edit_player(self.lg, fa.id, {"team_id": 1})          # 13 -> 14 players
        self.assertEqual(fa.team_id, 1)
        self.assertIsNotNone(fa.contract)
        edit_player(self.lg, self.p.id, {"team_id": 2})      # moved: 14 -> 13 and 13 -> 14
        self.assertEqual(self.p.team_id, 2)
        self.assertNotIn(self.p.id, self.lg.teams[1].tactics.starters)
        self.assertNotIn(self.p.id, self.lg.teams[1].tactics.minutes_targets)
        self.assertEqual(self.lg.validate(), [])
        nums = [p.jersey_number for p in self.lg.roster(2)]
        self.assertEqual(len(nums), len(set(nums)))
        # A full roster can't take another player.
        self.lg.rules.max_roster = 14
        other = next(p for p in self.lg.players.values() if p.team_id is None)
        with self.assertRaises(EditError):
            edit_player(self.lg, other.id, {"team_id": 2})

    def test_team_edit(self):
        edit_team(self.lg, 1, {"city": "Gotham", "name": "Knights", "abbreviation": "gtk", "reputation": 99})
        t = self.lg.teams[1]
        self.assertEqual((t.full_name, t.abbreviation, t.reputation), ("Gotham Knights", "GTK", 99))
        with self.assertRaises(EditError):
            edit_team(self.lg, 2, {"abbreviation": "GTK"})
        with self.assertRaises(EditError):
            edit_team(self.lg, 2, {"reputation": 0})

    def test_sheet_and_list(self):
        sheet = player_sheet(self.lg, self.p)
        self.assertEqual(sheet["attributes"], self.p.attributes.to_dict())
        self.assertIn("hidden", sheet)
        rows = player_list(self.lg, team_id=1)
        self.assertEqual(len(rows), 13)
        self.assertEqual(rows, sorted(rows, key=lambda r: -r["ca"]))
        self.assertEqual(len(player_list(self.lg, free_agents=True)), 3)
        self.assertEqual([r["name"] for r in player_list(self.lg, query="last12")], ["First12 Last12"])

    def test_edits_survive_save(self):
        edit_player(self.lg, self.p.id, {"attributes": {"three_point": 20}})
        with tempfile.TemporaryDirectory() as d:
            save_league(self.lg, Path(d) / "x.db")
            back = load_league(Path(d) / "x.db")
        self.assertEqual(back.players[self.p.id].attributes.three_point, 20)
        self.assertEqual(edited(back)["count"], 1)

    def test_look(self):
        """The 3D look: optional, checked against what the viewer understands, saved, cleared."""
        self.assertIsNone(self.p.look)
        self.assertIsNone(player_sheet(self.lg, self.p)["look"])
        look = {"hair": "locs", "hairColor": "#1C130D", "beard": False, "band": "team", "sleeve": "L",
                "leg": "both", "wrist": None, "gear": "white", "shoes": "#c8372d"}
        self.assertEqual(edit_player(self.lg, self.p.id, {"look": look}), ["look"])
        self.assertEqual(self.p.look, {**look, "hairColor": "#1c130d"})
        self.assertEqual(edit_player(self.lg, self.p.id, {"look": dict(self.p.look)}), [])   # no change
        with tempfile.TemporaryDirectory() as d:
            save_league(self.lg, Path(d) / "x.db")
            back = load_league(Path(d) / "x.db")
        self.assertEqual(back.players[self.p.id].look, self.p.look)
        for bad in ({"hair": "mohawk"}, {"hairColor": "red"}, {"shoes": "#12345"}, {"beard": "yes"},
                    {"sleeve": "left"}, {"gear": None}, {"cape": True}, ["hair"], {"band": "#zzzzzz"}):
            with self.subTest(bad=bad), self.assertRaises(EditError):
                edit_player(self.lg, self.p.id, {"look": bad, "first_name": "X"})
        self.assertNotEqual(self.p.first_name, "X")                 # an invalid look changes nothing
        self.assertEqual(clean_look({}), None)
        self.assertEqual(clean_look({"hair": "bald"}), {"hair": "bald"})
        self.assertEqual(edit_player(self.lg, self.p.id, {"look": None}), ["look"])   # use default
        self.assertIsNone(self.p.look)
        self.assertNotIn("look", self.p.to_dict())                  # saves without a look are as before

    def test_look_face(self):
        """The face: three weights 0-1 (how far toward each of the model's face shapes),
        checked strictly, saved, and cleared on its own or with the rest of the look."""
        self.assertEqual(clean_look({"face": [1, 0, 0.333]}), {"face": [1.0, 0.0, 0.33]})
        self.assertEqual(clean_look({"face": (0, 0, 0)}), {"face": [0.0, 0.0, 0.0]})      # the model's own even mix
        self.assertEqual(edit_player(self.lg, self.p.id, {"look": {"hair": "fade", "face": [0.2, 0.7, 0]}}), ["look"])
        self.assertEqual(self.p.look, {"hair": "fade", "face": [0.2, 0.7, 0.0]})
        self.assertEqual(player_sheet(self.lg, self.p)["look"]["face"], [0.2, 0.7, 0.0])
        self.assertEqual(player_sheet(self.lg, self.p)["limits"]["look"]["faces"], 3)
        with tempfile.TemporaryDirectory() as d:
            save_league(self.lg, Path(d) / "x.db")
            back = load_league(Path(d) / "x.db")
        self.assertEqual(back.players[self.p.id].look, self.p.look)
        for bad in ([1, 0], [1, 0, 0, 0], [1, 0, 2], [0, 0, -0.1], [True, 0, 0], ["1", 0, 0], [None, 0, 0],
                    [float("nan"), 0, 0], [float("inf"), 0, 0], "abc", 0.5, None, {"a": 1}):
            with self.subTest(bad=bad), self.assertRaises(EditError):
                edit_player(self.lg, self.p.id, {"look": {"face": bad}, "first_name": "X"})
        self.assertNotEqual(self.p.first_name, "X")                 # an invalid face changes nothing
        self.assertEqual(self.p.look["face"], [0.2, 0.7, 0.0])
        # clearing the face alone leaves the rest; clearing the look clears it too
        self.assertEqual(edit_player(self.lg, self.p.id, {"look": {"hair": "fade"}}), ["look"])
        self.assertEqual(self.p.look, {"hair": "fade"})
        edit_player(self.lg, self.p.id, {"look": {"face": [0, 0, 1]}})
        self.assertEqual(edit_player(self.lg, self.p.id, {"look": None}), ["look"])
        self.assertIsNone(self.p.look)

    def test_old_saves_without_face_load(self):
        """A look saved before faces could be set still loads, and stays without one."""
        from hoopsim.models import Player
        d = self.p.to_dict()
        d["look"] = {"hair": "locs", "beard": True}
        p = Player.from_dict(d)
        self.assertEqual(p.look, {"hair": "locs", "beard": True})
        self.assertEqual(clean_look(p.look), p.look)                # and is still a valid look
        self.assertNotIn("face", p.to_dict()["look"])

    def test_old_saves_without_look_load(self):
        d = self.p.to_dict()
        self.assertNotIn("look", d)
        from hoopsim.models import Player
        self.assertIsNone(Player.from_dict(d).look)
        d["look"] = {"hair": "fade"}
        self.assertEqual(Player.from_dict(d).look, {"hair": "fade"})


if __name__ == "__main__":
    unittest.main()


class CareerImportTests(unittest.TestCase):
    def test_career_lines_load_and_show(self):
        data = synthetic_file()
        p = data["players"][0]
        p["draft_year"], p["draft_pick"] = 2019, 3
        p["career"] = [
            {"season": 2019, "playoffs": False, "team": "SEA", "franchise": "T0", "age": 20, "gp": 70, "gs": 50,
             "min": 2100, "pts": 1200, "fgm": 450, "fga": 1000, "tpm": 80, "tpa": 240, "ftm": 220, "fta": 280,
             "orb": 60, "drb": 250, "ast": 200, "stl": 60, "blk": 30, "tov": 150, "pf": 160, "roy": True},
            {"season": 2020, "playoffs": False, "team": "T1", "gp": 80, "pts": 2000, "fgm": 700, "fga": 1400,
             "all_star": True, "mvp": True, "all_nba": 1},
            {"season": 2020, "playoffs": True, "team": "T1", "gp": 12, "pts": 300},
        ]
        lg = load_league_file(data)
        pl = next(x for x in lg.players.values() if x.last_name == "Last0")
        self.assertEqual(len(pl.career), 3)
        self.assertEqual(pl.career[0]["team"], "SEA")                 # the old team code is kept...
        self.assertEqual(pl.career[0]["team_id"], 1)                  # ...linked to today's franchise
        self.assertEqual(pl.career[1]["team_id"], 2)
        self.assertEqual(pl.career[1]["plus_minus"], 0)               # missing numbers are zero
        self.assertEqual(pl.draft_pick, 3)
        from hoopsim.allstar import honours, season_honours
        h = honours(pl)
        self.assertEqual((h["all_star"], h["mvp"], h["roy"], h["all_nba"]), (1, 1, 1, 1))
        self.assertEqual(season_honours(pl.career[1]), ["All-Star", "MVP", "All-NBA 1st"])
        # Track record makes the scouts surer.
        from hoopsim.manager import scouting_error
        other = next(x for x in lg.players.values() if x.last_name == "Last5")
        from datetime import date
        pl.birth_date = other.birth_date = date(2005, 5, 1)    # both 21: youth leaves room to doubt
        pl.team_id = other.team_id = 2                          # neither is yours
        self.assertLess(scouting_error(lg, pl), scouting_error(lg, other))
        with tempfile.TemporaryDirectory() as d:
            save_league(lg, Path(d) / "x.db")
            back = load_league(Path(d) / "x.db")
        self.assertEqual(back.players[pl.id].career, pl.career)

    def test_bad_career_lines(self):
        for line in ({"playoffs": False}, {"season": 2026}, {"season": 2020, "pts": "lots"}):
            data = synthetic_file()
            data["players"][0]["career"] = [line]
            with self.subTest(line=line), self.assertRaises(LeagueFileError):
                load_league_file(data)


class BuilderParsingTests(unittest.TestCase):
    """The NBA builder's parsing of season pages (made-up rows, no network)."""
    @classmethod
    def setUpClass(cls):
        import sys
        tools = str(Path(__file__).resolve().parent.parent / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import build_nba_league
        cls.b = build_nba_league

    def test_honours(self):
        h = self.b._honours("MVP-1,DPOY-3,AS,NBA1,DEF2")
        self.assertEqual(h, {"mvp": True, "all_star": True, "all_nba": 1, "all_defense": 2})
        self.assertEqual(self.b._honours("ROY-1"), {"roy": True})
        self.assertEqual(self.b._honours(""), {})

    def test_traded_player_gets_one_line_under_last_team(self):
        row = lambda team, g, pts, awards="": {"_id": "xx01", "team_name_abbr": team, "games": str(g),  # noqa: E731
                                               "games_started": "0", "mp": "100", "pts": str(pts), "age": "25",
                                               "awards": awards}
        lines = self.b._career_lines([row("2TM", 60, 900, "AS"), row("SEA", 40, 600), row("NJN", 20, 300)],
                                     2007, False)
        line = lines["xx01"]
        self.assertEqual((line["gp"], line["pts"], line["team"], line["franchise"]), (60, 900, "NJN", "BKN"))
        self.assertTrue(line["all_star"])
        self.assertEqual(self.b._career_lines([row("PHO", 82, 2000)], 2010, True)["xx01"]["team"], "PHX")


class BuilderRatingTests(unittest.TestCase):
    """How the NBA builder turns (made-up) stats into ratings and tendencies."""
    setUpClass = classmethod(BuilderParsingTests.setUpClass.__func__)

    LEAGUE = {"share": {"rim": 0.3, "close": 0.17, "mid": 0.12, "three": 0.41},
              "rating": dict.fromkeys(("rim", "close", "mid", "three"), 10.0)}
    AVERAGE = dict.fromkeys(("finishing", "close_range", "mid_range", "three_point", "speed", "ball_handling"), 10)

    @staticmethod
    def stats(usg=18.0, orb=6.0, drb=15.0, ast=12.0, fga=1000.0, mp=2000.0):
        tot = dict.fromkeys(("fg", "fga", "fg3", "fg3a", "ft", "fta", "orb", "drb", "ast", "stl", "blk",
                             "tov", "pf", "pts"), 0.0)
        tot.update(fga=fga, fg=0.46 * fga, fg3a=0.41 * fga, fg3=0.15 * fga, fta=0.25 * fga, ft=0.19 * fga)
        zones = {"00_03": (0.3 * fga, 0.2 * fga), "03_10": (0.17 * fga, 0.07 * fga),
                 "10_16": (0.07 * fga, 0.03 * fga), "16_xx": (0.05 * fga, 0.02 * fga)}
        return {"g": 70, "gs": 70, "mp": mp, "tot": tot, "zones": zones, "dunk_share": 0.05, "ast_fg2": 0.5,
                "per": 15, "ts_pct": 0.57, "orb_pct": orb, "drb_pct": drb, "ast_pct": ast, "stl_pct": 1.5,
                "blk_pct": 1.5, "tov_pct": 12, "usg_pct": usg, "ws_per_48": 0.1, "obpm": 0, "dbpm": 0, "bpm": 0}

    def player(self, height_cm=203, **kw):
        return {"stats": self.stats(**kw), "height_cm": height_cm, "weight_kg": 100, "age": 26, "experience": 5}

    def test_average_shooter_has_neutral_shot_tendencies(self):
        t = self.b.shot_tendencies(self.LEAGUE["share"], 1000, self.LEAGUE, self.AVERAGE)
        self.assertEqual(t, {"drive": 10, "post_up": 10, "mid_range": 10, "three_point": 10})
        self.assertEqual(self.b.shot_profile(self.stats())["three"], 0.41)
        self.assertIsNone(self.b.shot_profile(self.stats(fga=10)))

    def test_shot_tendency_is_what_ratings_dont_explain(self):
        # A great shooter who takes an average share of threes: his rating alone would
        # have him shoot more, so his tendency holds him back.
        sniper = {**self.AVERAGE, "three_point": 18}
        self.assertLess(self.b.shot_tendencies(self.LEAGUE["share"], 1000, self.LEAGUE, sniper)["three_point"], 10)
        # An average shooter who lives behind the line likes threes.
        volume = {"rim": 0.15, "close": 0.08, "mid": 0.07, "three": 0.70}
        t = self.b.shot_tendencies(volume, 1000, self.LEAGUE, self.AVERAGE)
        self.assertGreater(t["three_point"], 13)
        self.assertLess(t["drive"], 10)

    def test_shot_tendencies_reproduce_the_mix_in_the_engine(self):
        import math
        from hoopsim.engine import DEFAULT_TUNING
        a = {**self.AVERAGE, "three_point": 14, "finishing": 8}
        real = {"rim": 0.22, "close": 0.15, "mid": 0.13, "three": 0.50}
        t = self.b.shot_tendencies(real, 1e9, self.LEAGUE, a)       # a huge sample: no shrinking
        neutral = dict.fromkeys(("drive", "post_up", "mid_range", "three_point"), 10)

        def mix(ratings, tend):                  # the engine's zone weights, as shares
            w = {z: self.LEAGUE["share"][z] * math.exp(
                     self.b.ZONE_RATING_SLOPE[z] * DEFAULT_TUNING.skill_impact * (self.b._zone_rating(ratings, z) - 10)
                     + self.b.ZONE_TENDENCY[z][1] * (tend[self.b.ZONE_TENDENCY[z][0]] - 10)) for z in real}
            return {z: v / sum(w.values()) for z, v in w.items()}
        # The average player takes the league's mix; this one takes about his real mix.
        base, mine = mix(self.AVERAGE, neutral), mix(a, t)
        got = {z: self.LEAGUE["share"][z] * mine[z] / base[z] for z in real}
        for z in real:
            self.assertAlmostEqual(got[z] / sum(got.values()), real[z], delta=0.02)

    def test_usage_and_rate_features(self):
        f = self.b.raw_features
        self.assertGreater(f(self.player(usg=30))["_load"], f(self.player(usg=15))["_load"])
        # Same rebound rates: the taller player rates lower (the engines add height themselves).
        tall, short = f(self.player(height_cm=213)), f(self.player(height_cm=196))
        self.assertLess(tall["defensive_rebounding"], short["defensive_rebounding"])
        self.assertLess(tall["offensive_rebounding"], short["offensive_rebounding"])
        # Rates count on a log scale: doubling assists adds the same at any level.
        p = lambda a: f(self.player(ast=a, mp=1e6))["passing"]  # noqa: E731
        self.assertAlmostEqual(p(40) - p(20), p(20) - p(10), places=3)

    def rated(self, fta_rate=0.25, rim=0.3, usg=18.0, fga=1000.0):
        """A made-up player for foul_drawing: average ratings, this free-throw rate and rim share."""
        from hoopsim.models import ABILITY_ATTRIBUTES
        st = self.stats(usg=usg, fga=fga)
        st["tot"]["fta"] = fta_rate * fga
        st["zones"]["00_03"] = (rim * fga, rim * 0.65 * fga)
        st["zones"]["03_10"] = ((0.47 - rim) * fga, (0.47 - rim) * 0.42 * fga)
        return {"stats": st, "attributes": dict.fromkeys(ABILITY_ATTRIBUTES, 10)}

    def test_foul_drawing_is_free_throws_beyond_shot_mix_and_usage(self):
        import math
        from hoopsim.engine import DEFAULT_TUNING
        extra = {"line": self.rated(fta_rate=0.45), "rim": self.rated(fta_rate=0.45, rim=0.45),
                 "star": self.rated(fta_rate=0.45, usg=32), "few": self.rated(fta_rate=0.45, fga=30),
                 "never": self.rated(fta_rate=0.08)}
        out = self.b.foul_drawing([self.rated() for _ in range(40)] + list(extra.values()))
        avg, ratings = out[0], dict(zip(extra, out[40:]))
        self.assertGreater(ratings["line"], avg + 3)
        self.assertLess(ratings["rim"], ratings["line"])      # rim attempts explain some of it
        self.assertLess(ratings["star"], ratings["line"])     # the engine gives a star whistles anyway
        self.assertLess(ratings["few"], ratings["line"])      # a small sample is shrunk
        self.assertLess(ratings["never"], avg - 3)
        # Centred: the shot-weighted rating is about the engine's centre.
        self.assertAlmostEqual(avg, DEFAULT_TUNING.foul_drawing_centre, delta=1.5)
        # The engine with those ratings gives the line-drawer about his real rate again.
        ratio = math.exp(DEFAULT_TUNING.foul_drawing * (ratings["line"] - avg))
        self.assertAlmostEqual(ratio, (0.45 * 1000 + 40) / (0.25 * 1000 + 40), delta=0.25)

    def test_foul_drawing_without_a_sample_follows_the_ratings(self):
        from hoopsim.models import Attributes, derived_drawing_fouls
        rookie = self.rated(fga=0.0)
        rookie["attributes"].update(finishing=18, strength=17)
        got = self.b.foul_drawing([self.rated() for _ in range(10)] + [rookie])[-1]
        self.assertEqual(got, derived_drawing_fouls(Attributes(**rookie["attributes"])))

    def test_scoring_ratings_bend_toward_a_top(self):
        f = self.b.soft_top
        knee, top = self.b.SCORING_Z_KNEE, self.b.SCORING_Z_TOP
        self.assertEqual(f(knee - 1), knee - 1)
        out = [f(knee + i * 0.25) for i in range(40)]
        self.assertTrue(all(b > a for a, b in zip(out, out[1:])))   # the order is kept...
        self.assertLess(max(out), top)                               # ...below the top
        self.assertAlmostEqual(f(knee + 1e-6), knee, places=5)       # continuous at the knee
        # The best scorers keep a gap over the next tier (a hard clamp at 2.8 gave none).
        self.assertGreater(f(6.0) - f(3.5), 0.3)

    def test_pass_first_is_assists_for_his_share_of_the_ball(self):
        a = dict.fromkeys(("finishing", "close_range", "mid_range", "three_point", "free_throw", "passing",
                           "ball_handling", "vision", "speed", "strength", "basketball_iq"), 10)

        def tend(ast, usg):
            return self.b._tendencies({"stats": self.stats(ast=ast, usg=usg), "position": "PG"}, a)["passing"]
        self.assertGreater(tend(30, 16), 14)                  # a pass-first point guard
        self.assertLess(tend(30, 33), tend(30, 16))           # a star who scores and passes
        self.assertLess(tend(10, 30), 8)                      # a scorer

    def test_scoring_ratings_do_not_take_the_impact_level(self):
        # Two players with the same stats but different impact (ability target): the
        # level goes into the other ratings, so the better one doesn't shoot better.
        from hoopsim.models import ABILITY_ATTRIBUTES, Attributes, Position, compute_ability
        z = dict.fromkeys(ABILITY_ATTRIBUTES, 0.0)
        z.update(finishing=1.0, three_point=0.5)
        spread = {n: (10.0, 3.0) for n in ABILITY_ATTRIBUTES}
        lo = self.b.make_attributes(z, spread, Position.SG, 90)
        hi = self.b.make_attributes(z, spread, Position.SG, 120)
        for k in self.b.LOAD:
            self.assertEqual(lo[k], hi[k])
        self.assertGreater(hi["perimeter_defense"], lo["perimeter_defense"])
        for target, a in ((90, lo), (120, hi)):
            self.assertAlmostEqual(compute_ability(Attributes(**a), Position.SG), target, delta=2)

    def test_scoring_accuracy_features_leave_out_volume(self):
        # Free throws are foul drawing's and volume is the shot load's: finishing and
        # close range only see how often the shots go in.
        f = self.b.raw_features
        base, line = self.player(), self.player()
        line["stats"]["tot"]["fta"] *= 2
        self.assertEqual(f(base)["finishing"], f(line)["finishing"])
        many = self.player()
        a, m = many["stats"]["zones"]["03_10"]
        many["stats"]["zones"]["03_10"] = (2 * a, 2 * m)
        self.assertAlmostEqual(f(base)["close_range"], f(many)["close_range"], delta=0.05)   # less shrunk only

    def test_shot_load_also_lowers_the_pass_first_tendency(self):
        a = dict.fromkeys(("finishing", "close_range", "mid_range", "three_point", "free_throw", "passing",
                           "ball_handling", "vision", "speed", "strength", "basketball_iq"), 10)

        def tend(load):
            return self.b._tendencies({"stats": self.stats(), "position": "SF", "_load": load}, a)["passing"]
        step = self.b.LOAD_PASSING
        self.assertEqual(tend(0.0) - tend(2.0), round(2 * step))
        self.assertEqual(tend(-1.0) - tend(0.0), round(step))
        self.assertEqual(tend(50.0), 1)                        # clamped to the scale
