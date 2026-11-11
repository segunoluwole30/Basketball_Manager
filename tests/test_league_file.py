"""League files (load_league_file) and the in-game editor."""
import json
import random
import tempfile
import unittest
from pathlib import Path

from hoopsim import load_league, save_league
from hoopsim.editor import EditError, edit_player, edit_team, edited, player_list, player_sheet
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


if __name__ == "__main__":
    unittest.main()
