"""The game behind the web interface, with no web framework in it.

One ``GameSession`` holds one league in memory. Its methods return plain
JSON-able dicts (the API's responses) and raise ``UserError`` with a message
the player can read when a request isn't allowed. The FastAPI app in
``app.py`` is a thin layer over this.

What you see is what a general manager would see: potential is your scouts'
estimate with a range, and hidden attributes only appear as scouting notes.
"""
from __future__ import annotations

import random
import re
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional

from ..contracts import cap_room, max_contract, payment_route, payroll_for, team_strength
from ..db import load_league, save_league
from ..engine import clear_healed, play_day, store_result, tick_injuries
from ..generate import generate_league
from ..models import MATCH_ENGINES
from ..lineup import POSITIONS
from ..manager import (
    TACTIC_KEYS, TENDENCY_KEYS, AIManager, choose_team, describe_tendencies, free_agent_ask,
    hidden_traits, offer_contract, propose_trade, release_player, scouted_potential, scouting_error,
    set_auto_lineup, set_minutes, set_player_tendencies, set_starters, set_tactics,
)
from ..market import _salaries_match, evaluate_trade, execute_user_trade
from ..models import ContractOption, DefensiveScheme, League, OffensiveFocus, Phase, Player
from ..playoffs import _HIGH_SEED_HOME, _advance_bracket, current_round, ranked, start_playoffs, team_records
from ..season import (
    OFFSEASON_STEPS, STEP_TITLES, answer_offseason, finish_season, manager_answer,
    offseason_pending, offseason_report, pending_players, play_regular_season,
    refresh_lineups, regular_season_over, season_start, start_offseason, trade_deadline_day,
)
from ..models import Game as GameModel
from ..stats import career_per_game, per_game, season_totals

INBOX_LIMIT = 300


class UserError(Exception):
    """A request the game won't allow; the message is for the player."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------
def height_text(cm: int) -> str:
    inches = round(cm / 2.54)
    return f"{inches // 12}'{inches % 12}\""


def morale_word(m: float) -> str:
    return ("unhappy" if m < 30 else "frustrated" if m < 45 else "content" if m < 60
            else "happy" if m < 75 else "delighted")


def money(n: float) -> str:
    return f"${n / 1e6:.1f}M"


class _DeferredOffers(AIManager):
    """In-season trade pitches wait in the inbox for the user to answer."""

    def __init__(self):
        self.offers: list[dict] = []

    def respond_to_trade(self, league, offer):
        self.offers.append(dict(offer))
        return False


# --------------------------------------------------------------------------
# The session
# --------------------------------------------------------------------------
class GameSession:
    def __init__(self, save_path: str = "hoopsim_save.db", saves_dir: Optional[str] = None):
        self.league: Optional[League] = None
        self.rng = random.Random()
        self.save_path = save_path
        self.saves_dir = Path(saves_dir) if saves_dir else Path.cwd()
        self.lock = threading.RLock()
        self.dirty = False
        self.live: Optional[dict] = None       # a live match in progress (live_start)

    # -- guards -----------------------------------------------------------------
    def _lg(self) -> League:
        if self.league is None:
            raise UserError("no game loaded: start a new game or load a save", 409)
        return self.league

    def _me(self) -> League:
        lg = self._lg()
        if lg.user_team_id is None:
            raise UserError("choose your team first", 409)
        return lg

    def _ok(self, result: tuple[bool, str]) -> dict:
        ok, message = result
        if not ok:
            raise UserError(message)
        self.dirty = True
        return {"ok": True, "message": message}

    # -- new / load / save -------------------------------------------------------
    def new_game(self, seed: Optional[int] = None, num_teams: int = 12,
                 match_engine: str = "statistical") -> dict:
        if not 4 <= num_teams <= 30 or num_teams % 2:
            raise UserError("number of teams must be an even number from 4 to 30")
        seed = random.randrange(1_000_000) if seed is None else int(seed)
        try:
            if match_engine not in MATCH_ENGINES:
                raise ValueError(f"match engine must be one of {', '.join(MATCH_ENGINES)}")
            self.league = generate_league(seed=seed, num_teams=num_teams)
            self.league.rules.match_engine = match_engine
        except ValueError as e:
            raise UserError(str(e))
        self.rng = random.Random(seed)
        self.dirty = True
        return self.team_choices()

    def team_choices(self) -> dict:
        lg = self._lg()
        teams = []
        for t in sorted(lg.teams.values(), key=lambda t: (t.conference, t.abbreviation)):
            top = [p.current_ability for p in lg.roster(t.id)[:8]]
            best = lg.roster(t.id)[0]
            teams.append({
                "id": t.id, "abbr": t.abbreviation, "name": t.full_name, "conference": t.conference,
                "division": t.division, "strength": round(sum(top) / max(1, len(top)), 1),
                "payroll": lg.payroll(t.id), "cap_room": max(0, cap_room(lg, t.id, lg.season)),
                "star": f"{best.name} ({best.position.value}, CA {best.current_ability})",
            })
        return {"teams": teams, "user_team_id": lg.user_team_id}

    def choose(self, team_id: int) -> dict:
        lg = self._lg()
        if lg.user_team_id is not None:
            raise UserError("you already manage a team in this league")
        try:
            choose_team(lg, int(team_id))
        except ValueError as e:
            raise UserError(str(e))
        team = lg.teams[lg.user_team_id]
        self._news("welcome", f"Welcome to the {team.full_name}",
                   [f"The board expects you to build a winner. Your squad, tactics and transfers "
                    f"are yours to run; your assistant handles the lineup until you change it.",
                    f"The {lg.season}-{(lg.season + 1) % 100:02d} season starts on "
                    f"{season_start(lg.season):%B %d}."])
        self.dirty = True
        return self.state()

    def load(self, path: str) -> dict:
        p = self._resolve(path, must_exist=True)
        try:
            league = load_league(p)
        except (ValueError, KeyError) as e:
            raise UserError(f"couldn't load {p.name}: {e}")
        self.league = league
        self.save_path = str(p)
        self.rng = random.Random()
        self.dirty = False
        return self.state()

    def save(self, path: Optional[str] = None) -> dict:
        lg = self._lg()
        p = self._resolve(path or self.save_path, must_exist=False)
        save_league(lg, p)
        self.save_path = str(p)
        self.dirty = False
        return {"ok": True, "message": f"Saved to {p}", "path": str(p)}

    def _resolve(self, path: str, must_exist: bool) -> Path:
        if not path or not str(path).strip():
            raise UserError("give a file name")
        p = Path(str(path).strip()).expanduser()
        if p.suffix != ".db":
            p = p.with_suffix(".db")
        if not p.is_absolute():
            p = self.saves_dir / p
        if must_exist and not p.exists():
            raise UserError(f"no save file {p}", 404)
        return p

    def saves(self) -> dict:
        files = sorted(self.saves_dir.glob("*.db"), key=lambda f: -f.stat().st_mtime)
        return {"dir": str(self.saves_dir), "current": self.save_path,
                "saves": [{"name": f.name, "path": str(f), "modified": f.stat().st_mtime} for f in files[:50]]}

    # -- overview ------------------------------------------------------------------
    def state(self) -> dict:
        lg = self.league
        if lg is None:
            return {"loaded": False}
        out: dict[str, Any] = {
            "loaded": True, "league": lg.name, "season": lg.season, "match_engine": lg.rules.match_engine,
            "live_game": self.live["game_id"] if self.live else None,
            "season_label": f"{lg.season}-{(lg.season + 1) % 100:02d}",
            "date": lg.current_date.isoformat(), "phase": lg.phase.value,
            "user_team_id": lg.user_team_id, "save_path": self.save_path, "unsaved": self.dirty,
            "teams": {t.id: {"id": t.id, "abbr": t.abbreviation, "name": t.full_name,
                             "conference": t.conference} for t in lg.teams.values()},
        }
        if lg.user_team_id is None:
            return out
        me = lg.teams[lg.user_team_id]
        w, l, _ = team_records(lg)[me.id]
        conf = ranked(lg, [t.id for t in lg.teams.values() if t.conference == me.conference])
        out.update({
            "team": {"id": me.id, "abbr": me.abbreviation, "name": me.full_name, "conference": me.conference},
            "record": [w, l], "conference_rank": conf.index(me.id) + 1,
            "stage": self._stage(),
            "open_offers": sum(1 for m in lg.inbox if m.get("kind") == "offer" and m.get("status") == "open"),
            "unread": sum(1 for m in lg.inbox if not m.get("read")),
        })
        if lg.phase is Phase.REGULAR_SEASON:
            played = self._days_played()
            deadline = trade_deadline_day(lg)
            out["day"] = played
            out["deadline_in"] = (deadline - played) if deadline and played < deadline else None
            out["season_over"] = regular_season_over(lg)
        if lg.phase is Phase.OFFSEASON:
            pending = offseason_pending(lg)
            out["offseason_step"] = lg.offseason["step"] if lg.offseason else None
            out["offseason_kind"] = pending["kind"] if pending else None
        return out

    def _stage(self) -> str:
        """Where the season is, for the UI: regular_season, season_over, playoffs,
        awards (season finished, offseason not begun), offseason."""
        lg = self._lg()
        if lg.phase is Phase.REGULAR_SEASON:
            return "season_over" if regular_season_over(lg) else "regular_season"
        if lg.phase is Phase.PLAYOFFS:
            return "playoffs"
        return "offseason" if lg.offseason else "awards"

    def _days_played(self) -> int:
        lg = self._lg()
        return max((g.day for g in lg.games.values() if g.season == lg.season and g.played and not g.playoff),
                   default=0)

    # -- player views ---------------------------------------------------------------
    def _potential(self, p: Player) -> Optional[dict]:
        lg = self._lg()
        if p.age(lg.current_date) >= 29:
            return {"peaked": True}
        est, lo, hi = scouted_potential(lg, p)
        return {"peaked": False, "est": est, "lo": lo, "hi": hi}

    def player_row(self, p: Player, totals: Optional[dict] = None) -> dict:
        lg = self._lg()
        a = p.attributes
        t = (totals or {}).get(p.id)
        team = lg.teams.get(p.team_id) if p.team_id is not None else None
        row = {
            "id": p.id, "name": p.name, "jersey": p.jersey_number, "pos": p.position.value,
            "secondary": [x.value for x in p.secondary_positions],
            "age": p.age(lg.current_date), "height_cm": p.physique.height_cm,
            "height": height_text(p.physique.height_cm), "ca": p.current_ability,
            "potential": self._potential(p),
            "team_id": p.team_id, "team": team.abbreviation if team else None,
            "salary": p.contract.salary if p.contract else None,
            "thru": p.contract.expires_season if p.contract else None,
            "option": p.contract.option.value if p.contract and p.contract.option else None,
            "injury": {"description": p.injury.description, "games": p.injury.games_remaining} if p.injury else None,
            "morale": round(p.morale), "morale_word": morale_word(p.morale),
            "three": a.three_point, "fin": a.finishing, "mid": a.mid_range, "pas": a.passing,
            "hnd": a.ball_handling, "def": max(a.perimeter_defense, a.interior_defense),
            "reb": a.defensive_rebounding, "ath": round((a.speed + a.vertical + a.strength) / 3),
            "iq": a.basketball_iq,
            "retired": p.retired,
        }
        if t:
            row.update({"gp": int(t["gp"]), "mpg": round(per_game(t, "min"), 1),
                        "ppg": round(per_game(t, "pts"), 1), "rpg": round(per_game(t, "trb"), 1),
                        "apg": round(per_game(t, "ast"), 1)})
        else:
            row.update({"gp": 0, "mpg": 0.0, "ppg": 0.0, "rpg": 0.0, "apg": 0.0})
        return row

    def _find_player(self, pid: int) -> Player:
        lg = self._lg()
        p = lg.players.get(pid)
        if p is None:
            p = pending_players(lg).get(pid)               # a draft prospect
        if p is None:
            raise UserError(f"no player {pid}", 404)
        return p

    def player_card(self, pid: int) -> dict:
        lg = self._lg()
        p = self._find_player(int(pid))
        totals = season_totals(lg)
        row = self.player_row(p, totals)
        groups = [("Scoring", ["finishing", "close_range", "mid_range", "three_point", "free_throw"]),
                  ("Playmaking", ["passing", "ball_handling", "vision"]),
                  ("Defence", ["perimeter_defense", "interior_defense", "stealing", "blocking"]),
                  ("Rebounding", ["offensive_rebounding", "defensive_rebounding"]),
                  ("Physical", ["speed", "strength", "vertical", "stamina"]),
                  ("Mental", ["basketball_iq", "composure"])]
        attrs = [{"group": g, "ratings": [{"key": n, "label": n.replace("_", " ").replace("basketball iq", "basketball IQ"), "value": getattr(p.attributes, n)}
                                          for n in names]} for g, names in groups]
        t = totals.get(p.id)
        season = None
        if t:
            season = {"gp": int(t["gp"]), "gs": int(t["gs"]), "mpg": round(per_game(t, "min"), 1),
                      "ppg": round(per_game(t, "pts"), 1), "rpg": round(per_game(t, "trb"), 1),
                      "apg": round(per_game(t, "ast"), 1), "spg": round(per_game(t, "stl"), 1),
                      "bpg": round(per_game(t, "blk"), 1), "tov": round(per_game(t, "tov"), 1),
                      "fg_pct": round(t["fgm"] / max(1, t["fga"]), 3),
                      "three_pct": round(t["tpm"] / max(1, t["tpa"]), 3),
                      "ft_pct": round(t["ftm"] / max(1, t["fta"]), 3)}
        career = []
        for c in p.career:
            pg = career_per_game(c)
            career.append({"season": c["season"], "playoffs": c["playoffs"], "team": c["team"],
                           "age": c.get("age"), "ca": c.get("ca"), "gp": c["gp"],
                           "mpg": round(pg["mpg"], 1), "ppg": round(pg["ppg"], 1),
                           "rpg": round(pg["rpg"], 1), "apg": round(pg["apg"], 1),
                           "fg_pct": round(c["fgm"] / max(1, c["fga"]), 3)})
        draft = None
        if p.draft_year is not None:
            draft = {"year": p.draft_year, "pick": p.draft_pick}
        is_mine = p.team_id is not None and p.team_id == lg.user_team_id
        ask = None
        if p.team_id is None and not p.retired and p.id in lg.players:
            years, salary = free_agent_ask(lg, p)
            ask = {"years": years, "salary": salary}
        return {
            **row,
            "nationality": p.nationality, "weight_kg": p.physique.weight_kg,
            "wingspan_cm": p.physique.wingspan_cm,
            "attributes": attrs,
            "scouting_notes": hidden_traits(p),
            "scouting_certainty": "high" if scouting_error(lg, p) <= 6 else "medium" if scouting_error(lg, p) <= 11 else "low",
            "tendencies": p.tendencies.to_dict(), "tendencies_text": describe_tendencies(p),
            "season": season, "career": career, "draft": draft,
            "is_mine": is_mine, "is_prospect": p.id not in lg.players, "ask": ask,
            "at_positions": {pos.value: p.ability_at(pos) for pos in POSITIONS},
        }

    # -- your team -----------------------------------------------------------------
    def roster(self, team_id: Optional[int] = None) -> dict:
        lg = self._me()
        tid = lg.user_team_id if team_id is None else int(team_id)
        if tid not in lg.teams:
            raise UserError(f"no team {tid}", 404)
        team = lg.teams[tid]
        totals = season_totals(lg)
        starters = team.tactics.starters
        rows = []
        for p in lg.roster(tid):
            r = self.player_row(p, totals)
            r["starter"] = p.id in starters
            r["minutes_target"] = team.tactics.minutes_targets.get(p.id, 0)
            rows.append(r)
        w, l, _ = team_records(lg)[tid]
        return {"team": {"id": tid, "abbr": team.abbreviation, "name": team.full_name,
                         "conference": team.conference, "record": [w, l]},
                "players": rows, "payroll": lg.payroll(tid),
                "cap_room": cap_room(lg, tid, lg.season), "is_mine": tid == lg.user_team_id,
                "strength": round(team_strength(lg, tid), 1)}

    def finances(self) -> dict:
        lg = self._me()
        tid, r = lg.user_team_id, lg.rules
        contracts = [{"id": p.id, "name": p.name, "pos": p.position.value, "salary": p.contract.salary,
                      "thru": p.contract.expires_season,
                      "option": p.contract.option.value if p.contract.option else None}
                     for p in sorted(lg.roster(tid), key=lambda q: -q.contract.salary)]
        return {
            "payroll": payroll_for(lg, tid, lg.season), "cap": r.salary_cap, "tax_line": r.luxury_tax_line,
            "cap_room": cap_room(lg, tid, lg.season), "mle_available": tid not in lg.mle_used,
            "mle": r.mid_level_exception, "min_salary": r.min_salary, "max_salary": max_contract(lg),
            "min_roster": r.min_roster, "max_roster": r.max_roster, "roster_size": len(lg.roster(tid)),
            "committed": [{"season": y, "amount": payroll_for(lg, tid, y)} for y in range(lg.season, lg.season + 4)],
            "contracts": contracts,
        }

    def lineup(self) -> dict:
        lg = self._me()
        team = lg.teams[lg.user_team_id]
        t = team.tactics
        roster = {p.id: p for p in lg.roster(team.id)}
        starters = []
        for pos, pid in zip(POSITIONS, t.starters):
            p = lg.players[pid]
            starters.append({"slot": pos.value, "player_id": pid, "name": p.name, "pos": p.position.value,
                             "ca": p.current_ability, "at_slot": p.ability_at(pos),
                             "available": p.is_available and pid in roster})
        totals = season_totals(lg)
        players = []
        for p in roster.values():
            r = self.player_row(p, totals)
            r["starter"] = p.id in t.starters
            r["minutes_target"] = t.minutes_targets.get(p.id, 0)
            r["at_positions"] = {pos.value: p.ability_at(pos) for pos in POSITIONS}
            players.append(r)
        return {"auto": team.auto_lineup, "starters": starters, "players": players,
                "minutes_total": sum(t.minutes_targets.values()), "tactics": self.tactics()}

    def set_starters(self, ids: list[int]) -> dict:
        self._me()
        return self._ok(set_starters(self.league, [int(i) for i in ids]))

    def set_minutes(self, minutes: dict) -> dict:
        lg = self._me()
        try:
            changes = {int(k): int(v) for k, v in minutes.items()}
        except (TypeError, ValueError):
            raise UserError("minutes must be whole numbers")
        targets = lg.teams[lg.user_team_id].tactics.minutes_targets
        # A full plan (every rotation player) that adds to 240 replaces the old one.
        if sum(changes.values()) == 240:
            for pid in list(targets):
                changes.setdefault(pid, 0)
        return self._ok(set_minutes(lg, changes))

    def set_auto(self, on: bool) -> dict:
        self._me()
        self.dirty = True
        return {"ok": True, "message": set_auto_lineup(self.league, bool(on))}

    def tactics(self) -> dict:
        lg = self._me()
        t = lg.teams[lg.user_team_id].tactics
        return {"pace": t.pace, "three": t.three_point_rate, "crash": t.crash_offensive_glass,
                "depth": t.rotation_depth, "focus": t.offensive_focus.value,
                "defense": t.defensive_scheme.value,
                "choices": {"focus": [f.value for f in OffensiveFocus],
                            "defense": [d.value for d in DefensiveScheme]}}

    def set_tactics(self, changes: dict) -> dict:
        self._me()
        changes = {k: v for k, v in changes.items() if k in TACTIC_KEYS}
        if not changes:
            raise UserError(f"nothing to change (tactics: {', '.join(TACTIC_KEYS)})")
        result = self._ok(set_tactics(self.league, **changes))
        result["tactics"] = self.tactics()
        return result

    def set_instructions(self, pid: int, changes: dict) -> dict:
        self._me()
        changes = {k: v for k, v in changes.items() if k in TENDENCY_KEYS or k in TENDENCY_KEYS.values()}
        if not changes:
            raise UserError(f"nothing to change (instructions: {', '.join(TENDENCY_KEYS)})")
        return self._ok(set_player_tendencies(self.league, int(pid), **changes))

    # -- transfers -------------------------------------------------------------------
    def free_agents(self) -> dict:
        lg = self._me()
        tid = lg.user_team_id
        totals = season_totals(lg)
        rows = []
        for p in lg.free_agents()[:80]:
            r = self.player_row(p, totals)
            years, ask = free_agent_ask(lg, p)
            r["ask"], r["ask_years"] = ask, years
            rows.append(r)
        return {"players": rows, "roster_size": len(lg.roster(tid)), "max_roster": lg.rules.max_roster,
                "cap_room": cap_room(lg, tid, lg.season), "mle_available": tid not in lg.mle_used,
                "mle": lg.rules.mid_level_exception, "min_salary": lg.rules.min_salary,
                "max_salary": max_contract(lg),
                "offseason_free_agency": lg.phase is Phase.OFFSEASON}

    def sign(self, pid: int, salary: int, years: int) -> dict:
        lg = self._me()
        if lg.phase is Phase.OFFSEASON and lg.offseason:
            raise UserError("during the offseason, bid in the free agency rounds instead")
        return self._ok(offer_contract(lg, int(pid), int(salary), int(years), self.rng))

    def release(self, pid: int) -> dict:
        lg = self._me()
        if lg.offseason and lg.offseason.get("pending", {}) and lg.offseason["pending"]["kind"] == "cuts":
            raise UserError("choose your roster cuts in the offseason screen")
        return self._ok(release_player(lg, int(pid)))

    def trade_check(self, give: list[int], team_id: int, get: list[int]) -> dict:
        lg = self._me()
        user, other = lg.user_team_id, int(team_id)
        if other not in lg.teams or other == user:
            raise UserError("pick another team")
        give, get = [int(x) for x in give], [int(x) for x in get]
        for pid in give + get:
            if pid not in lg.players:
                raise UserError(f"no player {pid}")
        uo, oo = [lg.players[i] for i in give], [lg.players[i] for i in get]
        out_salary = sum(p.contract.salary for p in uo if p.contract)
        in_salary = sum(p.contract.salary for p in oo if p.contract)
        ok, message = evaluate_trade(lg, give, other, get)
        mine = all(p.team_id == user for p in uo) and all(p.team_id == other for p in oo)
        match = _salaries_match(lg, uo, oo, lg.season, user, other) if mine else False
        rules = lg.rules
        return {
            "ok": ok, "message": message, "salary_out": out_salary, "salary_in": in_salary,
            "salary_match": match,
            "your_payroll_after": payroll_for(lg, user, lg.season) - out_salary + in_salary,
            "their_payroll_after": payroll_for(lg, other, lg.season) - in_salary + out_salary,
            "your_roster_after": len(lg.roster(user)) - len(give) + len(get),
            "their_roster_after": len(lg.roster(other)) - len(get) + len(give),
            "cap": rules.salary_cap, "max_take_back": int(out_salary * 1.25 + 100_000),
        }

    def trade(self, give: list[int], team_id: int, get: list[int]) -> dict:
        lg = self._me()
        if lg.phase is Phase.REGULAR_SEASON:
            deadline = trade_deadline_day(lg)
            if deadline and self._days_played() > deadline:
                raise UserError("the trade deadline has passed: trades open again in the offseason")
        if lg.phase is Phase.PLAYOFFS:
            raise UserError("no trades during the playoffs")
        result = self._ok(propose_trade(lg, [int(x) for x in give], int(team_id), [int(x) for x in get]))
        self._news("trade", "Trade completed", [result["message"]])
        return result

    # -- inbox ----------------------------------------------------------------------
    def _news(self, kind: str, title: str, body: list[str], **extra) -> dict:
        lg = self._lg()
        next_id = max((m["id"] for m in lg.inbox), default=0) + 1
        item = {"id": next_id, "date": lg.current_date.isoformat(), "season": lg.season,
                "kind": kind, "title": title, "body": body, "read": False, **extra}
        lg.inbox.append(item)
        del lg.inbox[:-INBOX_LIMIT]
        return item

    def inbox(self) -> dict:
        lg = self._me()
        items = list(reversed(lg.inbox))
        return {"items": [self._offer_view(m) if m.get("kind") == "offer" else m for m in items]}

    def mark_read(self, ids: Optional[list[int]] = None) -> dict:
        lg = self._me()
        for m in lg.inbox:
            if ids is None or m["id"] in ids:
                m["read"] = True
        return {"ok": True}

    def _offer_view(self, m: dict) -> dict:
        lg = self._lg()
        offer = m["offer"]
        view = dict(m)
        view["give"] = [self.player_row(lg.players[i]) for i in offer["you_give"] if i in lg.players]
        view["get"] = [self.player_row(lg.players[i]) for i in offer["you_get"] if i in lg.players]
        return view

    def answer_offer(self, item_id: int, accept: bool) -> dict:
        lg = self._me()
        item = next((m for m in lg.inbox if m["id"] == int(item_id) and m.get("kind") == "offer"), None)
        if item is None:
            raise UserError("no such offer", 404)
        if item.get("status") != "open":
            raise UserError(f"that offer is {item.get('status')}")
        offer = item["offer"]
        team = lg.teams[offer["team_id"]]
        item["read"] = True
        if not accept:
            item["status"] = "declined"
            self.dirty = True
            return {"ok": True, "message": f"You turned down the {team.full_name}."}
        ok, reason = evaluate_trade(lg, offer["you_give"], offer["team_id"], offer["you_get"])
        if not ok:
            item["status"] = "expired"
            raise UserError(f"the deal is off: {reason}")
        names_out = ", ".join(lg.players[i].name for i in offer["you_give"])
        names_in = ", ".join(lg.players[i].name for i in offer["you_get"])
        execute_user_trade(lg, offer["you_give"], offer["team_id"], offer["you_get"])
        item["status"] = "accepted"
        message = f"You send {names_out} to {team.abbreviation} for {names_in}"
        self._news("trade", "Trade completed", [message])
        self.dirty = True
        return {"ok": True, "message": message}

    def _expire_offers(self) -> None:
        for m in self._lg().inbox:
            if m.get("kind") == "offer" and m.get("status") == "open":
                m["status"] = "expired"

    # -- dashboard ------------------------------------------------------------------
    def dashboard(self) -> dict:
        lg = self._me()
        me = lg.teams[lg.user_team_id]
        rec = team_records(lg)
        conf_ids = ranked(lg, [t.id for t in lg.teams.values() if t.conference == me.conference])
        leader_w, leader_l, _ = rec[conf_ids[0]]
        standings = []
        for i, tid in enumerate(conf_ids):
            w, l, diff = rec[tid]
            standings.append({"rank": i + 1, "team_id": tid, "abbr": lg.teams[tid].abbreviation,
                              "name": lg.teams[tid].full_name, "w": w, "l": l,
                              "gb": ((leader_w - w) + (l - leader_l)) / 2})
        my_games = sorted((g for g in lg.games.values() if g.season == lg.season
                           and me.id in (g.home_team_id, g.away_team_id)), key=lambda g: (g.day, g.id))
        upcoming = [self._game_view(g) for g in my_games if not g.played][:5]
        recent = [self._game_view(g) for g in my_games if g.played][-5:][::-1]
        totals = season_totals(lg)
        team_leaders = []
        for key, label in (("pts", "Points"), ("trb", "Rebounds"), ("ast", "Assists")):
            mine = [(pid, t) for pid, t in totals.items() if lg.players[pid].team_id == me.id and t["gp"] > 0]
            if mine:
                pid, t = max(mine, key=lambda kv: per_game(kv[1], key))
                team_leaders.append({"stat": label, "player_id": pid, "name": lg.players[pid].name,
                                     "value": round(per_game(t, key), 1)})
        injured = [{"id": p.id, "name": p.name, "description": p.injury.description,
                    "games": p.injury.games_remaining} for p in lg.roster(me.id) if p.injury]
        return {"state": self.state(), "standings": standings, "upcoming": upcoming, "recent": recent,
                "team_leaders": team_leaders, "injured": injured,
                "inbox": self.inbox()["items"][:30], "finances": {
                    "payroll": lg.payroll(me.id), "cap_room": cap_room(lg, me.id, lg.season),
                    "roster_size": len(lg.roster(me.id))}}

    def _game_view(self, g: GameModel) -> dict:
        lg = self._lg()
        me = lg.user_team_id
        home, away = lg.teams[g.home_team_id], lg.teams[g.away_team_id]
        v = {"id": g.id, "day": g.day, "playoff": g.playoff,
             "date": (season_start(g.season) + timedelta(days=g.day)).isoformat(),
             "home_id": home.id, "home": home.abbreviation, "away_id": away.id, "away": away.abbreviation,
             "played": g.played, "has_box": g.box_score is not None,
             "has_replay": g.id in lg.replays}
        if g.result:
            v.update({"home_score": g.result.home_score, "away_score": g.result.away_score,
                      "ot": g.result.overtimes})
        if me in (home.id, away.id):
            at_home = home.id == me
            v["opponent_id"] = away.id if at_home else home.id
            v["opponent"] = (away if at_home else home).abbreviation
            v["at_home"] = at_home
            if g.result:
                mine = g.result.home_score if at_home else g.result.away_score
                theirs = g.result.away_score if at_home else g.result.home_score
                v["won"] = mine > theirs
                v["score"] = f"{mine}-{theirs}"
        return v

    # -- simulation -------------------------------------------------------------------
    def sim(self, mode: str = "day", days: Optional[int] = None) -> dict:
        lg = self._me()
        if lg.phase is not Phase.REGULAR_SEASON:
            raise UserError("the regular season is over" if lg.phase is not Phase.OFFSEASON
                            else "it's the offseason: finish it to start the new season")
        if regular_season_over(lg):
            raise UserError("the regular season is over: on to the playoffs")
        played = self._days_played()
        remaining = sorted({g.day for g in lg.games.values()
                            if g.season == lg.season and not g.playoff and not g.played})
        if mode == "day":
            target = remaining[:1]
        elif mode == "week":
            target = [d for d in remaining if d <= played + 7] or remaining[:1]
        elif mode == "days":
            n = int(days or 1)
            if not 1 <= n <= 200:
                raise UserError("days must be 1-200")
            target = remaining[:n]
        elif mode == "deadline":
            deadline = trade_deadline_day(lg)
            if deadline is None or played >= deadline:
                raise UserError("the trade deadline has passed")
            target = [d for d in remaining if d <= deadline]
        elif mode == "end":
            target = remaining
        else:
            raise UserError(f"unknown sim mode '{mode}' (day, week, days, deadline, end)")
        self._expire_offers()
        me = lg.user_team_id
        before = {g.id for g in lg.games.values() if g.played}
        hurt_before = {p.id: p.injury.description for p in lg.roster(me) if p.injury}
        events: list[str] = []
        manager = _DeferredOffers()
        stopped_for = None
        days_done = 0
        for _ in target:
            events += play_regular_season(lg, self.rng, days=1, manager=manager)
            days_done += 1
            if manager.offers:
                stopped_for = "offers"
                break
        new = sorted((g for g in lg.games.values() if g.played and g.id not in before
                      and me in (g.home_team_id, g.away_team_id)), key=lambda g: g.day)
        if new:
            views = [self._game_view(g) for g in new]
            w = sum(v["won"] for v in views)
            body = [f"{'W' if v['won'] else 'L'} {v['score']} {'vs' if v['at_home'] else '@'} {v['opponent']}"
                    + (" (OT)" if v.get("ot") else "") for v in views[-12:]]
            if len(views) > 12:
                body.insert(0, f"... and {len(views) - 12} earlier games")
            self._news("results", f"{len(views)} game{'s' if len(views) != 1 else ''}: {w}-{len(views) - w}",
                       body, game_ids=[g.id for g in new[-12:]])
        for p in lg.roster(me):
            if p.injury and p.id not in hurt_before:
                self._news("injury", f"Injury: {p.name}",
                           [f"{p.name} has a {p.injury.description} and will miss {p.injury.games_remaining} games."],
                           player_id=p.id)
        trades = [e for e in events if "TRADE" in e]
        if trades:
            self._news("league", "Trade deadline moves", trades)
        for offer in manager.offers:
            team = lg.teams[offer["team_id"]]
            give = ", ".join(lg.players[i].name for i in offer["you_give"])
            get = ", ".join(lg.players[i].name for i in offer["you_get"])
            self._news("offer", f"Trade offer from the {team.full_name}",
                       [f"They want {give} for {get}. The offer stands until you sim again."],
                       offer=offer, status="open")
        if regular_season_over(lg):
            self._news("season", "The regular season is over",
                       ["The playoff bracket is set. Head to the League screen to start the playoffs."])
        self.dirty = True
        return {"ok": True, "days": days_done, "games": [self._game_view(g) for g in new][-20:],
                "stopped_for": stopped_for, "events": events, "state": self.state()}

    # -- playoffs ------------------------------------------------------------------
    def start_playoffs(self) -> dict:
        lg = self._me()
        if lg.phase is not Phase.REGULAR_SEASON or not regular_season_over(lg):
            raise UserError("finish the regular season first")
        self._expire_offers()
        start_playoffs(lg)
        me = lg.user_team_id
        made = any(me in (s.high_id, s.low_id) for s in lg.playoffs)
        self._news("playoffs", "The playoffs begin",
                   ["You're in! Every series is best of seven." if made else
                    "You missed the playoffs this year. You can still watch the bracket."])
        self.dirty = True
        return self.playoffs()

    def _playoff_day(self) -> bool:
        """One day of playoff games (mirrors playoffs.play_playoffs exactly). False when done."""
        opened = self._playoff_day_open()
        if opened is None:
            return False
        day, live = opened
        play_day(self._lg(), day, self.rng)
        self._playoff_day_close(live)
        return True

    def _playoff_day_open(self):
        """Schedule the next playoff day's games: (day, series playing) or None when done."""
        lg = self._lg()
        day = max(g.day for g in lg.games.values() if g.season == lg.season)
        while True:
            live = [s for s in lg.playoffs if s.round == current_round(lg) and not s.over]
            if not live:
                if not _advance_bracket(lg):
                    return None
                continue
            day += 1
            for s in live:
                n = len(s.game_ids)
                home, away = (s.high_id, s.low_id) if n in _HIGH_SEED_HOME else (s.low_id, s.high_id)
                gid = lg.next_game_id()
                lg.add_game(GameModel(gid, lg.season, day, home, away, playoff=True))
                s.game_ids.append(gid)
            return day, live

    def _playoff_day_close(self, live) -> None:
        lg = self._lg()
        for s in live:
            g = lg.games[s.game_ids[-1]]
            if g.winner_id() == s.high_id:
                s.high_wins += 1
            else:
                s.low_wins += 1

    def sim_playoffs(self, mode: str = "day") -> dict:
        lg = self._me()
        if lg.phase is not Phase.PLAYOFFS:
            raise UserError("the playoffs haven't started")
        if mode not in ("day", "round", "all"):
            raise UserError("mode must be day, round or all")
        over_before = {s.id for s in lg.playoffs if s.over}
        rnd = current_round(lg)
        finished = False
        while True:
            if not self._playoff_day():
                finished = True
                break
            if mode == "day":
                break
            if mode == "round" and all(s.over for s in lg.playoffs if s.round == rnd):
                break
        return self._after_playoff_days(over_before, finished)

    def _after_playoff_days(self, over_before: set, finished: bool) -> dict:
        lg = self._lg()
        me = lg.user_team_id
        for s in lg.playoffs:
            if s.over and s.id not in over_before and me in (s.high_id, s.low_id):
                won = s.winner_id == me
                opp = lg.teams[s.loser_id if won else s.winner_id]
                score = f"{max(s.high_wins, s.low_wins)}-{min(s.high_wins, s.low_wins)}"
                stage = "the Finals" if s.conference is None else f"round {s.round}"
                self._news("playoffs", f"{'Series won' if won else 'Eliminated'}: {score} vs {opp.abbreviation}",
                           [f"You {'beat' if won else 'lost to'} the {opp.full_name} {score} in {stage}."])
        if finished:
            summary = finish_season(lg)
            champ = lg.teams[summary["champion"]]
            names = summary["award_names"]
            self._news("season", f"{champ.full_name} win the {summary['season']} title",
                       [f"{champ.full_name} beat {lg.teams[summary['runner_up']].full_name} {summary['finals']}."
                        + (" Congratulations!" if champ.id == me else ""),
                        f"MVP: {names.get('mvp') or '-'}. Finals MVP: {names.get('finals_mvp') or '-'}.",
                        "The offseason is next: retirements, the draft and free agency."])
        self.dirty = True
        return {"ok": True, "finished": finished, "playoffs": self.playoffs(), "state": self.state()}

    def playoffs(self) -> dict:
        lg = self._me()
        series = []
        for s in sorted(lg.playoffs, key=lambda s: (s.round, s.id)):
            series.append({
                "id": s.id, "round": s.round, "conference": s.conference,
                "high_id": s.high_id, "low_id": s.low_id,
                "high": lg.teams[s.high_id].abbreviation, "low": lg.teams[s.low_id].abbreviation,
                "high_seed": s.high_seed, "low_seed": s.low_seed,
                "high_wins": s.high_wins, "low_wins": s.low_wins, "over": s.over,
                "winner_id": s.winner_id, "games": [self._game_view(lg.games[g]) for g in s.game_ids if g in lg.games],
            })
        champion = None
        if series and series[-1]["conference"] is None and series[-1]["over"]:
            champion = series[-1]["winner_id"]
        return {"series": series, "champion": champion, "stage": self._stage()}

    # -- league ------------------------------------------------------------------------
    def standings(self) -> dict:
        lg = self._lg()
        rec = team_records(lg)
        streaks, last10 = {}, {}
        for tid in lg.teams:
            games = sorted((g for g in lg.games.values() if g.season == lg.season and not g.playoff
                            and g.played and tid in (g.home_team_id, g.away_team_id)), key=lambda g: g.day)
            results = ["W" if g.winner_id() == tid else "L" for g in games]
            last10[tid] = f"{results[-10:].count('W')}-{results[-10:].count('L')}"
            streak = ""
            if results:
                n = len(results) - len(("".join(results)).rstrip(results[-1]))
                streak = f"{results[-1]}{n}"
            streaks[tid] = streak
        out = []
        for conf in sorted({t.conference for t in lg.teams.values()}):
            ids = ranked(lg, [t.id for t in lg.teams.values() if t.conference == conf])
            lw, ll, _ = rec[ids[0]]
            rows = []
            for i, tid in enumerate(ids):
                w, l, diff = rec[tid]
                t = lg.teams[tid]
                rows.append({"rank": i + 1, "team_id": tid, "abbr": t.abbreviation, "name": t.full_name,
                             "division": t.division, "w": w, "l": l, "pct": round(w / max(1, w + l), 3),
                             "gb": ((lw - w) + (l - ll)) / 2, "net": round(diff / max(1, w + l), 1),
                             "streak": streaks[tid], "last10": last10[tid],
                             "strength": round(team_strength(lg, tid), 1)})
            out.append({"conference": conf, "teams": rows})
        return {"conferences": out, "season": lg.season, "user_team_id": lg.user_team_id}

    def leaders(self) -> dict:
        lg = self._lg()
        totals = season_totals(lg)
        if not totals:
            return {"categories": [], "min_games": 0}
        most = max(t["gp"] for t in totals.values())
        min_gp = max(1, int(0.6 * most))
        qualified = {pid: t for pid, t in totals.items() if t["gp"] >= min_gp}
        cats = []
        for key, label in (("pts", "Points"), ("trb", "Rebounds"), ("ast", "Assists"),
                           ("stl", "Steals"), ("blk", "Blocks"), ("min", "Minutes")):
            best = sorted(qualified, key=lambda pid: per_game(qualified[pid], key), reverse=True)[:10]
            cats.append({"key": key, "label": label, "rows": [
                {"player_id": pid, "name": lg.players[pid].name,
                 "team": lg.teams[qualified[pid]["team_id"]].abbreviation,
                 "value": round(per_game(qualified[pid], key), 1), "gp": int(qualified[pid]["gp"])} for pid in best]})
        for key, label, made, att, need in (("fg", "FG%", "fgm", "fga", 5), ("tp", "3P%", "tpm", "tpa", 2),
                                            ("ft", "FT%", "ftm", "fta", 2)):
            pool = {pid: t for pid, t in qualified.items() if t[att] / max(1, t["gp"]) >= need}
            best = sorted(pool, key=lambda pid: pool[pid][made] / max(1, pool[pid][att]), reverse=True)[:10]
            cats.append({"key": key, "label": label, "rows": [
                {"player_id": pid, "name": lg.players[pid].name, "team": lg.teams[pool[pid]["team_id"]].abbreviation,
                 "value": round(pool[pid][made] / max(1, pool[pid][att]), 3), "gp": int(pool[pid]["gp"])} for pid in best]})
        return {"categories": cats, "min_games": min_gp}

    def schedule(self, team_id: Optional[int] = None) -> dict:
        lg = self._me()
        tid = lg.user_team_id if team_id is None else int(team_id)
        if tid not in lg.teams:
            raise UserError(f"no team {tid}", 404)
        games = sorted((g for g in lg.games.values() if g.season == lg.season
                        and tid in (g.home_team_id, g.away_team_id)), key=lambda g: (g.day, g.id))
        return {"team_id": tid, "games": [self._game_view(g) for g in games]}

    def results(self, day: Optional[int] = None) -> dict:
        """League-wide results for one day (default: the latest day played)."""
        lg = self._lg()
        played_days = sorted({g.day for g in lg.games.values() if g.season == lg.season and g.played})
        if not played_days:
            return {"day": None, "days": [], "games": []}
        d = played_days[-1] if day is None else int(day)
        games = sorted((g for g in lg.games.values() if g.season == lg.season and g.day == d and g.played),
                       key=lambda g: g.id)
        return {"day": d, "days": played_days, "games": [self._game_view(g) for g in games]}

    # -- live match (coach your team's game as it's played) ---------------------------
    def live_start(self) -> dict:
        """Play on to the user's next game, then hold it as a live match."""
        if self.live is not None:
            return self.live_header()
        lg = self._me()
        me = lg.user_team_id
        if not lg.rules.user_games_spatial:
            raise UserError("live matches need the spatial or hybrid match engine (choose it when starting a new game)")
        mine = lambda g: me in (g.home_team_id, g.away_team_id)
        from ..spatial.live import LiveMatch
        if lg.phase is Phase.REGULAR_SEASON and not regular_season_over(lg):
            nxt = min((g for g in lg.games.values() if g.season == lg.season and not g.playoff
                       and not g.played and mine(g)), key=lambda g: (g.day, g.id), default=None)
            if nxt is None:
                raise UserError("you have no games left this regular season")
            days = len({g.day for g in lg.games.values() if g.season == lg.season and not g.playoff
                        and not g.played and g.day < nxt.day})
            if days:
                r = self.sim("days", days)
                if r["stopped_for"]:
                    return {"ok": True, "started": False, "stopped_for": r["stopped_for"], "state": self.state()}
            refresh_lineups(lg)
            clear_healed(lg)
            match = LiveMatch(lg, nxt, self.rng)
            self.live = {"match": match, "kind": "regular", "game_id": nxt.id, "day": nxt.day}
        elif lg.phase is Phase.PLAYOFFS:
            if not any(me in (s.high_id, s.low_id) and not s.over for s in lg.playoffs):
                raise UserError("your season is over: no playoff games to coach")
            over_before = {s.id for s in lg.playoffs if s.over}
            while True:
                opened = self._playoff_day_open()
                if opened is None:
                    raise UserError("no playoff game to coach")
                day, series = opened
                game = next((g for g in lg.games.values() if g.day == day and g.playoff and not g.played
                             and mine(g)), None)
                if game is not None:
                    break
                play_day(lg, day, self.rng)            # your series is waiting on others
                self._playoff_day_close(series)
            clear_healed(lg)
            match = LiveMatch(lg, game, self.rng)
            self.live = {"match": match, "kind": "playoff", "game_id": game.id, "day": day,
                         "series": series, "over_before": over_before}
        else:
            raise UserError("there are no games to coach right now")
        self.dirty = True
        return self.live_header()

    def _live(self):
        if self.live is None:
            raise UserError("no live game in progress", 409)
        return self.live["match"]

    def live_header(self) -> dict:
        m = self._live()
        return {"ok": True, "started": True, "game_id": self.live["game_id"], "url": "/api/live/page",
                "state": self.state()}

    def live_page(self) -> str:
        from ..spatial.export import build_html
        return build_html(self._live().header())

    def live_next(self, after: int = 0, want: int = 2) -> dict:
        return self._live().next_chunk(after, want)

    def live_command(self, cmd: dict) -> dict:
        from ..spatial.live import LiveError
        try:
            return self._live().command(cmd)
        except LiveError as e:
            raise UserError(str(e))

    def live_finish(self) -> dict:
        """End the live match (playing out anything left) and carry on with the day."""
        match = self._live()
        lg = self._lg()
        live, self.live = self.live, None
        sim = match.finish()
        g = lg.games[live["game_id"]]
        store_result(lg, g.id, sim)
        tick_injuries(lg, {g.home_team_id, g.away_team_id}, {pid for pid, _ in sim.injuries})
        view = self._game_view(g)
        self._news("results", f"Live: {'W' if view['won'] else 'L'} {view['score']} "
                              f"{'vs' if view['at_home'] else '@'} {view['opponent']}",
                   [f"{'W' if view['won'] else 'L'} {view['score']} {'vs' if view['at_home'] else '@'} "
                    f"{view['opponent']}" + (" (OT)" if view.get("ot") else "")], game_ids=[g.id])
        me = lg.user_team_id
        for pid, injury in sim.injuries:
            p = lg.players[pid]
            if p.team_id == me:
                self._news("injury", f"Injury: {p.name}",
                           [f"{p.name} has a {injury.description} and will miss {injury.games_remaining} games."],
                           player_id=p.id)
        self.dirty = True
        if live["kind"] == "regular":
            rest = [x for x in lg.games.values() if x.season == lg.season and not x.playoff
                    and x.day == live["day"] and not x.played]
            if rest:
                self.sim("day")                        # the rest of today's games
            else:
                lg.current_date = season_start(lg.season) + timedelta(days=live["day"])
        else:
            play_day(lg, live["day"], self.rng)
            self._playoff_day_close(live["series"])
            self._after_playoff_days(live["over_before"], False)
        return {"ok": True, "game": self._game_view(g), "final": match.state(), "state": self.state()}

    def watch_next(self) -> dict:
        """Play on to the user's next game (and the rest of that day), so it can be
        watched live. Needs the spatial engine; stops early for a trade offer."""
        lg = self._me()
        me = lg.user_team_id
        if not lg.rules.user_games_spatial:
            raise UserError("watching live needs the spatial or hybrid match engine (choose it when starting a new game)")
        mine = lambda g: me in (g.home_team_id, g.away_team_id)
        before = {g.id for g in lg.games.values() if g.played and mine(g)}
        if lg.phase is Phase.REGULAR_SEASON and not regular_season_over(lg):
            nxt = min((g for g in lg.games.values() if g.season == lg.season and not g.playoff
                       and not g.played and mine(g)), key=lambda g: (g.day, g.id), default=None)
            if nxt is None:
                raise UserError("you have no games left this regular season")
            days = len({g.day for g in lg.games.values() if g.season == lg.season and not g.playoff
                        and not g.played and g.day <= nxt.day})
            r = self.sim("days", days)
            if not lg.games[nxt.id].played:
                return {"ok": True, "game_id": None, "stopped_for": r["stopped_for"], "state": self.state()}
            game_id = nxt.id
        elif lg.phase is Phase.PLAYOFFS:
            if not any(me in (s.high_id, s.low_id) and not s.over for s in lg.playoffs):
                raise UserError("your season is over: no playoff games to watch")
            while True:
                r = self.sim_playoffs("day")
                new = [g for g in lg.games.values() if g.played and mine(g) and g.id not in before]
                if new or r.get("finished"):
                    break
            if not new:
                raise UserError("no game to watch")
            game_id = max(g.id for g in new)
        else:
            raise UserError("there are no games to watch right now")
        return {"ok": True, "game_id": game_id, "url": f"/api/replay/{game_id}?live=1",
                "stopped_for": None, "state": self.state()}

    def replay_page(self, game_id: int, live: bool = False) -> str:
        """The match viewer for a recorded game (spatial engine, the user's team).
        ``live``: spoiler-free (the box score appears at the final buzzer)."""
        from ..spatial.export import build_html
        from ..spatial.season_game import replay_data
        lg = self._lg()
        if int(game_id) not in lg.games:
            raise UserError(f"no game {game_id}", 404)
        data = replay_data(lg, int(game_id))
        if data is None:
            raise UserError("no recording of that game: only your team's most recent games on the "
                            "spatial engine are kept", 404)
        data["live"] = bool(live)
        from ..spatial.highlights import annotate
        annotate(data.get("possessions", []))       # older replays were saved without highlight levels
        return build_html(data)

    def box_score(self, game_id: int) -> dict:
        lg = self._lg()
        g = lg.games.get(int(game_id))
        if g is None:
            raise UserError(f"no game {game_id}", 404)
        view = self._game_view(g)
        if g.box_score is None:
            view["box"] = None
            return view

        def team_box(box):
            lines = []
            for line in box.lines:
                if line.seconds <= 0:
                    continue
                p = lg.players.get(line.player_id)
                lines.append({"player_id": line.player_id, "name": p.name if p else "?",
                              "pos": p.position.value if p else "", "starter": line.starter,
                              "min": round(line.minutes, 1), "pts": line.pts,
                              "fgm": line.fgm, "fga": line.fga, "tpm": line.tpm, "tpa": line.tpa,
                              "ftm": line.ftm, "fta": line.fta, "orb": line.orb, "drb": line.drb,
                              "trb": line.trb, "ast": line.ast, "stl": line.stl, "blk": line.blk,
                              "tov": line.tov, "pf": line.pf, "pm": line.plus_minus})
            t = box.totals()
            totals = {"min": round(t.minutes), "pts": t.pts, "fgm": t.fgm, "fga": t.fga, "tpm": t.tpm,
                      "tpa": t.tpa, "ftm": t.ftm, "fta": t.fta, "orb": t.orb, "drb": t.drb, "trb": t.trb,
                      "ast": t.ast, "stl": t.stl, "blk": t.blk, "tov": t.tov, "pf": t.pf}
            return {"team_id": box.team_id, "abbr": lg.teams[box.team_id].abbreviation,
                    "name": lg.teams[box.team_id].full_name, "periods": box.period_points,
                    "lines": lines, "totals": totals}

        view["box"] = {"home": team_box(g.box_score.home), "away": team_box(g.box_score.away)}
        return view

    def history(self) -> dict:
        lg = self._lg()
        seasons = []
        for h in reversed(lg.history):
            def team(tid):
                t = lg.teams.get(tid)
                return {"id": tid, "abbr": t.abbreviation, "name": t.full_name} if t else None
            standings = [{"team": team(tid), "w": w, "l": l} for tid, w, l in h.get("standings", [])]
            awards = []
            for key, label in (("mvp", "MVP"), ("finals_mvp", "Finals MVP"), ("dpoy", "Defensive POY"),
                               ("roy", "Rookie of the Year"), ("sixth_man", "Sixth Man"), ("mip", "Most Improved")):
                pid = h.get("awards", {}).get(key)
                awards.append({"award": label, "player_id": pid, "name": h["award_names"].get(key)})
            seasons.append({"season": h["season"], "champion": team(h["champion"]),
                            "runner_up": team(h["runner_up"]), "finals": h["finals"],
                            "awards": awards, "standings": standings,
                            "deadline_trades": h.get("deadline_trades", [])})
        return {"seasons": seasons}

    def team_list(self) -> dict:
        lg = self._lg()
        rec = team_records(lg)
        return {"teams": [{"id": t.id, "abbr": t.abbreviation, "name": t.full_name,
                           "conference": t.conference, "record": list(rec[t.id][:2]),
                           "payroll": lg.payroll(t.id), "roster_size": len(lg.roster(t.id)),
                           "strength": round(team_strength(lg, t.id), 1)}
                          for t in sorted(lg.teams.values(), key=lambda t: t.abbreviation)]}

    # -- offseason ----------------------------------------------------------------------
    def offseason(self) -> dict:
        lg = self._me()
        state = lg.offseason
        steps = [{"key": k, "title": STEP_TITLES[k]} for k in OFFSEASON_STEPS]
        if lg.phase is not Phase.OFFSEASON:
            last = lg.last_offseason
            return {"active": False, "can_start": False, "steps": steps,
                    "last_report": self._report_view(last) if last else None}
        if state is None:
            return {"active": False, "can_start": True, "steps": steps, "last_report": None,
                    "season": lg.season}
        pending = offseason_pending(lg)
        current = state["step"]
        return {"active": True, "can_start": False, "steps": steps, "step": current,
                "step_index": OFFSEASON_STEPS.index(current) if current in OFFSEASON_STEPS else len(OFFSEASON_STEPS),
                "pending": self._pending_view(pending) if pending else None,
                "report": self._report_view(offseason_report(lg)), "season": state["season"]}

    def _report_view(self, report: Optional[dict]) -> Optional[dict]:
        if not report:
            return None
        lg = self._lg()
        me = lg.user_team_id
        abbr = lg.teams[me].abbreviation if me is not None else None
        out = {k: report.get(k, []) for k in ("retired", "options", "resigned", "draft", "free_agency",
                                              "trades", "waived", "notes")}
        out["season"] = report.get("season")
        dev = report.get("development", {})
        movers = []
        for p in lg.roster(me) if me is not None else []:
            change = dev.get(p.id, dev.get(str(p.id)))
            if change is not None:
                movers.append({"player_id": p.id, "name": p.name, "change": change, "ca": p.current_ability,
                               "age": p.age(lg.current_date)})
        out["development"] = sorted(movers, key=lambda m: -abs(m["change"]))
        out["user_abbr"] = abbr
        return out

    def _pending_view(self, pending: dict) -> dict:
        lg = self._lg()
        me = lg.user_team_id
        kind = pending["kind"]
        view = dict(pending)
        totals = season_totals(lg)
        rules = lg.rules
        view["finances"] = {"payroll": payroll_for(lg, me, lg.season), "cap": rules.salary_cap,
                            "tax_line": rules.luxury_tax_line, "cap_room": cap_room(lg, me, lg.season),
                            "mle_available": me not in lg.mle_used, "mle": rules.mid_level_exception,
                            "min_salary": rules.min_salary, "max_salary": max_contract(lg),
                            "roster_size": len(lg.roster(me)), "max_roster": rules.max_roster,
                            "min_roster": rules.min_roster}
        if kind == "options":
            view["players"] = [{**self.player_row(lg.players[o["player_id"]], totals),
                                "fair_salary": o["fair_salary"]} for o in pending["options"]]
        elif kind == "resign":
            view["players"] = [{**self.player_row(lg.players[e["player_id"]], totals), "ask": e["ask"],
                                "ask_years": e["years"], "chance": e["chance"]}
                               for e in pending["expiring"] if e["player_id"] in lg.players]
        elif kind == "draft":
            prospects = pending_players(lg)
            board = []
            for pid in pending["available"]:
                p = prospects[pid]
                row = self.player_row(p)
                row["notes"] = hidden_traits(p)[:3]
                est = scouted_potential(lg, p)[0]
                row["board_value"] = round(0.35 * p.current_ability + 0.65 * est, 1)
                board.append(row)
            board.sort(key=lambda r: -r["board_value"])
            for i, r in enumerate(board):
                r["rank"] = i + 1
            view["players"] = board
            view["picks"] = pending["log"]
            del view["available"]
        elif kind == "free_agency":
            rows = []
            for e in pending["pool"][:60]:
                p = lg.players[e["player_id"]]
                rows.append({**self.player_row(p, totals), "ask": e["ask"], "ask_years": e["years"]})
            view["players"] = rows
            view["pool_size"] = len(pending["pool"])
            del view["pool"]
        elif kind == "trade_offer":
            offer = pending["offer"]
            team = lg.teams[offer["team_id"]]
            view["team"] = {"id": team.id, "abbr": team.abbreviation, "name": team.full_name}
            view["give"] = [self.player_row(lg.players[i], totals) for i in offer["you_give"]]
            view["get"] = [self.player_row(lg.players[i], totals) for i in offer["you_get"]]
        elif kind == "cuts":
            view["players"] = sorted((self.player_row(p, totals) for p in lg.roster(me)), key=lambda r: r["ca"])
        return view

    def start_offseason(self) -> dict:
        lg = self._me()
        if lg.phase is not Phase.OFFSEASON:
            raise UserError("the season isn't over yet")
        if lg.offseason is not None:
            raise UserError("the offseason is already under way")
        start_offseason(lg, self.rng)
        self._after_offseason_step()
        self.dirty = True
        return self.offseason()

    def answer(self, answer: Any) -> dict:
        lg = self._me()
        pending = offseason_pending(lg)
        if pending is None:
            raise UserError("no offseason decision is waiting")
        clean = self._validate_answer(pending, answer)
        try:
            answer_offseason(lg, self.rng, clean)
        except ValueError as e:
            raise UserError(str(e))
        self._after_offseason_step()
        self.dirty = True
        return self.offseason()

    def auto(self, scope: str = "one") -> dict:
        """Let the assistant answer: this question, the rest of this step, or everything."""
        lg = self._me()
        pending = offseason_pending(lg)
        if pending is None:
            raise UserError("no offseason decision is waiting")
        if scope not in ("one", "step", "all"):
            raise UserError("scope must be one, step or all")
        kind = pending["kind"]
        assistant = AIManager()
        while pending is not None:
            answer_offseason(lg, self.rng, manager_answer(lg, assistant, pending))
            pending = offseason_pending(lg)
            if scope == "one" or (scope == "step" and (pending is None or pending["kind"] != kind)):
                break
        self._after_offseason_step()
        self.dirty = True
        return self.offseason()

    def _after_offseason_step(self) -> None:
        lg = self._lg()
        if lg.offseason is None and lg.phase is Phase.REGULAR_SEASON and lg.last_offseason:
            report = self._report_view(lg.last_offseason)
            abbr = report["user_abbr"]
            about_me = re.compile(rf"\b{re.escape(abbr)}\b|\byou(r)?\b", re.IGNORECASE) if abbr else None
            mine = [l for k in ("options", "resigned", "draft", "free_agency", "trades", "waived")
                    for l in report.get(k, []) if about_me and about_me.search(l)]
            body = mine[:20] or ["A quiet summer for your team."]
            if report["development"]:
                body.append("Development: " + ", ".join(f"{m['name']} {m['change']:+d}"
                                                        for m in report["development"][:6]))
            body += report.get("notes", [])
            self._news("offseason", f"Summer report: welcome to {lg.season}-{(lg.season + 1) % 100:02d}", body)

    def _validate_answer(self, pending: dict, answer: Any) -> Any:
        lg = self._lg()
        kind = pending["kind"]
        me = lg.user_team_id
        try:
            if kind == "options":
                ids = {o["player_id"] for o in pending["options"]}
                out = {int(k): bool(v) for k, v in dict(answer).items()}
                if set(out) != ids:
                    raise UserError("decide every team option (exercise or decline)")
                return out
            if kind == "resign":
                ids = {e["player_id"] for e in pending["expiring"]}
                out = {}
                for k, v in dict(answer).items():
                    pid, (salary, years) = int(k), v
                    if pid not in ids:
                        raise UserError(f"player {pid} isn't on the list")
                    salary, years = int(salary), int(years)
                    if not 1 <= years <= 5:
                        raise UserError("contracts are 1-5 years")
                    if salary < lg.rules.min_salary:
                        raise UserError(f"the minimum salary is {money(lg.rules.min_salary)}")
                    if salary > max_contract(lg):
                        raise UserError(f"the maximum salary is {money(max_contract(lg))}")
                    out[pid] = (salary, years)
                return out
            if kind == "draft":
                pid = int(answer)
                if pid not in pending["available"]:
                    raise UserError("that player isn't on the board")
                return pid
            if kind == "free_agency":
                pool = {e["player_id"] for e in pending["pool"]}
                out, seen = [], set()
                cap_left = cap_room(lg, me, lg.season)
                mle = me not in lg.mle_used
                for item in list(answer):
                    pid, salary, years = int(item[0]), int(item[1]), int(item[2])
                    p = lg.players.get(pid)
                    if pid not in pool or p is None or p.team_id is not None:
                        raise UserError(f"player {pid} isn't in this round's pool")
                    if pid in seen:
                        raise UserError(f"one offer per player ({p.name})")
                    seen.add(pid)
                    if not 1 <= years <= 5:
                        raise UserError("contracts are 1-5 years")
                    if not payment_route(lg, me, salary, lg.season, mle):
                        raise UserError(f"you can't pay {p.name} {money(salary)}: with "
                                        f"{money(max(0, cap_left))} of cap room you can offer up to that, "
                                        + (f"the mid-level exception (up to {money(lg.rules.mid_level_exception)}) "
                                           if mle else "")
                                        + f"or the minimum ({money(lg.rules.min_salary)})")
                    out.append((pid, salary, years))
                return out
            if kind == "trade_offer":
                if not isinstance(answer, bool):
                    raise UserError("answer true (accept) or false (decline)")
                return answer
            if kind == "cuts":
                ids = [int(x) for x in answer]
                roster = {p.id for p in lg.roster(me)}
                if len(set(ids)) != pending["count"] or not set(ids) <= roster:
                    raise UserError(f"pick exactly {pending['count']} players from your roster")
                return ids
        except UserError:
            raise
        except (TypeError, ValueError, KeyError, IndexError):
            raise UserError(f"couldn't read that answer for {kind}")
        raise UserError(f"unknown decision {kind}")

    # -- stats pages (read-only) ---------------------------------------------------------
    def _stat_seasons(self) -> list[int]:
        lg = self._lg()
        return sorted({lg.season, *(h["season"] for h in lg.history)}, reverse=True)

    def _stat_season(self, season: Optional[int]) -> int:
        lg = self._lg()
        if season is None:
            return lg.season
        season = int(season)
        if season not in self._stat_seasons():
            raise UserError(f"no stats for the {season} season", 404)
        return season

    def _stats_memo(self, kind: str, season: int, playoff: bool, build):
        """Cache a stat table until a game is played (or box scores are pruned)."""
        lg = self._lg()
        games = [g for g in lg.games.values() if g.season == season and g.playoff == playoff]
        key = (kind, season, playoff, lg.current_date.isoformat(), lg.phase.value,
               sum(1 for g in games if g.played), sum(1 for g in games if g.box_score is not None))
        memo = self.__dict__.get("_stats_cache")
        if memo is None or memo[0] is not lg:
            memo = (lg, {})
            self.__dict__["_stats_cache"] = memo
        cache = memo[1]
        if key not in cache:
            if len(cache) > 32:
                cache.clear()
            cache[key] = build()
        return cache[key]

    def _player_totals(self, season: int, playoff: bool) -> dict:
        from ..stats_tables import player_totals
        return self._stats_memo("players", season, playoff,
                                lambda: player_totals(self._lg(), season, playoff))

    @staticmethod
    def _rounded(line: dict) -> dict:
        out = {}
        for k, v in line.items():
            if isinstance(v, float):
                pct = k.endswith(("_pct", "_rate")) or k in ("efg", "ts", "usg", "fga_share", "opp_efg")
                out[k] = round(v, 3 if pct else 1)
            else:
                out[k] = v
        return out

    def stats_leaders(self, playoffs: bool = False, pos: Optional[str] = None,
                      season: Optional[int] = None, limit: int = 10) -> dict:
        """League leaders per game, shooting and advanced, with qualifiers."""
        from ..stats_tables import LEADER_CATEGORIES, min_games, player_line
        lg = self._lg()
        season = self._stat_season(season)
        playoff = bool(playoffs)
        if pos and pos not in {p.value for p in POSITIONS}:
            raise UserError(f"position must be one of {', '.join(p.value for p in POSITIONS)}")
        limit = max(1, min(50, int(limit)))
        totals = self._player_totals(season, playoff)
        need = min_games(lg, season, playoff, totals)
        lines = {}
        for pid, t in totals.items():
            p = lg.players.get(pid)
            if p is None or t["gp"] < need or (pos and p.position.value != pos):
                continue
            lines[pid] = player_line(t)
        cats = []
        for key, label, kind, extra in LEADER_CATEGORIES:
            pool = [pid for pid, line in lines.items() if line[key] is not None
                    and (extra is None or line[extra[0]] >= extra[1])]
            pool.sort(key=lambda pid: (-lines[pid][key], -lines[pid]["gp"], pid))
            rows = []
            for pid in pool[:limit]:
                p, t = lg.players[pid], totals[pid]
                team = lg.teams.get(t["team_id"])
                rows.append({"player_id": pid, "name": p.name, "pos": p.position.value,
                             "team_id": t["team_id"], "team": team.abbreviation if team else "?",
                             "gp": lines[pid]["gp"], "mpg": round(lines[pid]["mpg"], 1),
                             "value": round(lines[pid][key], 3 if kind == "pct" else 1),
                             "mine": t["team_id"] == lg.user_team_id})
            cats.append({"key": key, "label": label, "kind": kind, "rows": rows,
                         "qualifier": f"{need}+ games" + (f", {extra[2]}" if extra else ""),
                         "advanced": key in ("ts", "usg", "fga_share", "pts36", "trb36", "ast36")})
        return {"season": season, "seasons": self._stat_seasons(), "playoffs": playoff, "pos": pos,
                "positions": [p.value for p in POSITIONS], "min_games": need, "categories": cats,
                "from_box_scores": any(t.get("context") for t in totals.values()),
                "user_team_id": lg.user_team_id}

    def stats_teams(self, playoffs: bool = False, season: Optional[int] = None) -> dict:
        """Per-team table: points, pace, ratings and the four factors."""
        from ..stats_tables import team_line, team_totals
        lg = self._lg()
        season = self._stat_season(season)
        playoff = bool(playoffs)
        totals = self._stats_memo("teams", season, playoff, lambda: team_totals(lg, season, playoff))
        rows = []
        for tid, t in totals.items():
            team = lg.teams[tid]
            rows.append({"team_id": tid, "abbr": team.abbreviation, "name": team.full_name,
                         "conference": team.conference, "mine": tid == lg.user_team_id,
                         **self._rounded(team_line(t)),
                         "totals": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in t.items()}})
        rows.sort(key=lambda r: -(r["net"] or 0))
        league = None
        if totals:
            agg: dict = {}
            for t in totals.values():
                for k, v in t.items():
                    agg[k] = agg.get(k, 0) + v
            league = self._rounded(team_line(agg))
        note = None
        if not rows:
            played = any(g.played for g in lg.games.values() if g.season == season and g.playoff == playoff)
            note = ("Team stats come from box scores, which are kept for the current season only."
                    if season != lg.season or played else "No games played yet.")
        return {"season": season, "seasons": self._stat_seasons(), "playoffs": playoff,
                "teams": rows, "league": league, "note": note, "user_team_id": lg.user_team_id}

    def player_history(self, pid: int) -> dict:
        """Season-by-season career table (plus the season in progress) and chart points."""
        from ..stats_tables import player_line
        lg = self._lg()
        p = self._find_player(int(pid))

        def row(t: dict, season: int, playoff: bool, team_id, age, ca, in_progress: bool) -> dict:
            team = lg.teams.get(team_id) if team_id is not None else None
            line = self._rounded(player_line(t))
            return {"season": season, "playoffs": playoff, "team_id": team_id,
                    "team": team.abbreviation if team else "?", "age": age, "ca": ca,
                    "in_progress": in_progress,
                    "totals": {k: int(t[k]) for k in ("pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta",
                                                      "orb", "drb", "ast", "stl", "blk", "tov")},
                    **{k: line[k] for k in ("gp", "gs", "mpg", "pts", "trb", "ast", "stl", "blk", "tov",
                                            "fg_pct", "tp_pct", "ft_pct", "efg", "ts", "pts36")}}

        rows = []
        for c in sorted(p.career, key=lambda c: (c["season"], c["playoffs"])):
            t = {**c, "seconds": c["min"] * 60}
            rows.append(row(t, c["season"], c["playoffs"], c.get("team_id"), c.get("age"), c.get("ca"), False))
        done = {(r["season"], r["playoffs"]) for r in rows}
        if p.id in lg.players:
            for playoff in (False, True):
                if (lg.season, playoff) in done:
                    continue
                t = self._player_totals(lg.season, playoff).get(p.id)
                if t and t.get("context"):
                    rows.append(row(t, lg.season, playoff, t["team_id"], p.age(lg.current_date),
                                    p.current_ability, True))
        rows.sort(key=lambda r: (r["season"], r["playoffs"]))
        chart = [{"season": r["season"], "ppg": r["pts"], "rpg": r["trb"], "apg": r["ast"],
                  "ca": r["ca"], "in_progress": r["in_progress"]} for r in rows if not r["playoffs"]]
        if not chart or chart[-1]["season"] < lg.season:
            chart.append({"season": lg.season, "ppg": None, "rpg": None, "apg": None,
                          "ca": p.current_ability, "in_progress": True})
        return {"player_id": p.id, "name": p.name, "pos": p.position.value, "ca": p.current_ability,
                "seasons": rows, "chart": chart,
                "ability_source": "season-end snapshots; the last point is his ability now"}

    def team_gamelog(self, team_id: Optional[int] = None) -> dict:
        """Every game a team has played this season: result, score, record, top scorer."""
        lg = self._me()
        tid = lg.user_team_id if team_id is None else int(team_id)
        if tid not in lg.teams:
            raise UserError(f"no team {tid}", 404)
        games = sorted((g for g in lg.games.values() if g.season == lg.season
                        and tid in (g.home_team_id, g.away_team_id)), key=lambda g: (g.playoff, g.day, g.id))
        rows, record = [], {False: [0, 0], True: [0, 0]}
        for g in games:
            if not g.played:
                continue
            at_home = g.home_team_id == tid
            mine = g.result.home_score if at_home else g.result.away_score
            theirs = g.result.away_score if at_home else g.result.home_score
            won = mine > theirs
            record[g.playoff][0 if won else 1] += 1
            opp = lg.teams[g.away_team_id if at_home else g.home_team_id]
            top = None
            if g.box_score:
                box = g.box_score.home if at_home else g.box_score.away
                played = [x for x in box.lines if x.seconds > 0]
                if played:
                    best = max(played, key=lambda x: (x.pts, x.trb + x.ast, -x.player_id))
                    pl = lg.players.get(best.player_id)
                    top = {"player_id": best.player_id, "name": pl.name if pl else "?", "pts": best.pts,
                           "trb": best.trb, "ast": best.ast}
            w, l = record[g.playoff]
            rows.append({"id": g.id, "day": g.day, "playoff": g.playoff,
                         "date": (season_start(g.season) + timedelta(days=g.day)).isoformat(),
                         "at_home": at_home, "opponent_id": opp.id, "opponent": opp.abbreviation,
                         "opponent_name": opp.full_name, "team_score": mine, "opp_score": theirs,
                         "won": won, "ot": g.result.overtimes, "margin": mine - theirs,
                         "record": f"{w}-{l}", "top_scorer": top,
                         "has_box": g.box_score is not None, "has_replay": g.id in lg.replays})
        reg = [r for r in rows if not r["playoff"]]
        return {"team_id": tid, "abbr": lg.teams[tid].abbreviation, "name": lg.teams[tid].full_name,
                "season": lg.season, "games": rows, "record": record[False],
                "playoff_record": record[True],
                "remaining": sum(1 for g in games if not g.played and not g.playoff),
                "points_for": sum(r["team_score"] for r in reg),
                "points_against": sum(r["opp_score"] for r in reg)}
