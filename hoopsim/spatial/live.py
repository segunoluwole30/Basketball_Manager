"""A live match: the user coaches his team's game as it is played.

The full game runs possession by possession, only as far ahead as the viewer
needs (``next_chunk``), so the coach's commands shape what happens next:

* **timeout**: taken at the next dead ball, or as soon as his team has the ball.
  It uses one of his timeouts, rests everyone a little and stops the other team's
  run.
* **sub**: player in for player out, at the next dead ball (or timeout).
* **auto_subs**: on, the bench coach keeps running the rotation; off, only
  forced changes (a player fouling out) are made for him.
* **tactics**: pace, three-point rate, crashing the glass (1-20), offensive focus
  and defensive scheme. They hold for this game only: the team's saved tactics
  come back when it ends.

``finish`` plays out whatever is left (for a coach who walks away early) and
returns the ``GameSim``; the caller records it like any other game.
"""
from __future__ import annotations

import copy
import random
from typing import Optional

from ..engine import GameSim
from ..models import ATTR_MAX, ATTR_MIN, DefensiveScheme, Game, League, OffensiveFocus
from .fullgame import FullGame
from .season_game import _model, finish_spatial

LIVE_TACTICS = {
    "pace": "pace", "three": "three_point_rate", "crash": "crash_offensive_glass",
    "focus": "offensive_focus", "defense": "defensive_scheme",
}
AHEAD = 2                      # possessions simulated beyond what the viewer has asked for


class LiveError(ValueError):
    pass


class LiveMatch:
    def __init__(self, league: League, game: Game, rng: random.Random):
        me = league.user_team_id
        if me not in (game.home_team_id, game.away_team_id):
            raise LiveError("that isn't your team's game")
        self.league, self.game, self.rng = league, game, rng
        self.user_side = "home" if game.home_team_id == me else "away"
        team = league.teams[me]
        self._saved_tactics = team.tactics
        team.tactics = copy.deepcopy(team.tactics)       # match tactics: changes don't stick
        try:
            self.fg = FullGame(league, game.home_team_id, game.away_team_id, rng, record=True, model=_model())
        except Exception:
            team.tactics = self._saved_tactics
            raise
        self.manager = self.fg.attach_manager(self.user_side)
        self._gen = self.fg.iter_game()
        self.done = False
        self.sim: Optional[GameSim] = None

    # ------------------------------------------------------------------ playing
    def _step(self) -> None:
        try:
            next(self._gen)
        except StopIteration:
            self.done = True

    def next_chunk(self, after: int, want: int = AHEAD) -> dict:
        """Recorded possessions from index ``after`` on, simulating up to ``want``
        beyond it if needed."""
        after = max(0, int(after))
        guard = 0
        while not self.done and len(self.fg.possessions) < after + want and guard < 50:
            self._step()
            guard += 1
        chunk = [dict(p, notes=self._notes(i)) for i, p in
                 enumerate(self.fg.possessions[after:after + want], start=after)]
        return {"possessions": chunk, "next": after + len(chunk),
                "done": self.done and after + len(chunk) >= len(self.fg.possessions), "state": self.state()}

    NOTE_WORDS = ("Sub:", "Timeout", "Start of", "fouls out", "foul to stop")

    def _notes(self, i: int) -> list[str]:
        """Bench news logged since the previous recorded possession (subs, timeouts...)."""
        ps = self.fg.possessions
        lo = ps[i - 1].get("log_at", 0) if i > 0 else 0
        hi = ps[i].get("log_at", lo)
        return [f"{e.team + ': ' if e.team else ''}{e.text}" for e in self.fg.events[lo:hi]
                if any(w in e.text for w in self.NOTE_WORDS)]

    def finish(self) -> GameSim:
        """Play out the rest and return the result (injury rolls, replay included)."""
        if self.sim is None:
            for _ in self._gen:
                pass
            self.done = True
            self.league.teams[self.league.user_team_id].tactics = self._saved_tactics
            self.sim = finish_spatial(self.fg, self.rng, True)
        return self.sim

    # ------------------------------------------------------------------ the coach
    def command(self, cmd: dict) -> dict:
        if self.sim is not None:
            raise LiveError("the game is over")
        kind = cmd.get("type")
        side = self.fg.sides[self.user_side]
        m = self.manager
        if kind == "timeout":
            if side.timeouts - (1 if m["timeout"] else 0) <= 0:
                raise LiveError("no timeouts left")
            m["timeout"] = True
            msg = "Timeout called: at the next dead ball or when you have the ball"
        elif kind == "sub":
            out_id, in_id = int(cmd.get("out", -1)), int(cmd.get("in", -1))
            out, inn = side.seats.get(out_id), side.seats.get(in_id)
            if out is None or inn is None:
                raise LiveError("pick a player on the floor and one on the bench")
            if not out.on_court or inn.on_court:
                raise LiveError(f"{out.p.name} must be on the floor and {inn.p.name} on the bench")
            if self.fg.fouled_out(inn):
                raise LiveError(f"{inn.p.name} has fouled out")
            m["subs"] = [(o, i) for o, i in m["subs"] if o != out_id and i != in_id] + [(out_id, in_id)]
            msg = f"{inn.p.name} will check in for {out.p.name} at the next dead ball"
        elif kind == "cancel_subs":
            m["subs"] = []
            msg = "Pending substitutions cancelled"
        elif kind == "auto_subs":
            m["auto_subs"] = bool(cmd.get("on"))
            msg = "Bench coach runs the rotation" if m["auto_subs"] else "You make the substitutions"
        elif kind == "tactics":
            t = side.tactics
            changed = []
            for key, value in (cmd.get("changes") or {}).items():
                attr = LIVE_TACTICS.get(key)
                if attr is None:
                    raise LiveError(f"unknown tactic '{key}' (try: {', '.join(LIVE_TACTICS)})")
                try:
                    if attr == "offensive_focus":
                        value = OffensiveFocus(str(value))
                    elif attr == "defensive_scheme":
                        value = DefensiveScheme(str(value))
                    else:
                        value = int(value)
                        if not ATTR_MIN <= value <= ATTR_MAX:
                            raise ValueError
                except ValueError:
                    raise LiveError(f"bad value for {key}: {value}")
                setattr(t, attr, value)
                changed.append(key)
            if not changed:
                raise LiveError("nothing to change")
            msg = "Tactics changed for this game: " + ", ".join(changed)
        else:
            raise LiveError("unknown command (timeout, sub, cancel_subs, auto_subs, tactics)")
        return {"ok": True, "message": msg, "state": self.state()}

    # ------------------------------------------------------------------ what the coach sees
    def state(self) -> dict:
        fg = self.fg
        score = fg.hc.score

        def side_state(key):
            side = fg.sides[key]
            players = []
            for s in side.roster:
                c = fg.hc.box[id(s.agent)]
                players.append({
                    "id": s.p.id, "name": s.p.name, "num": s.p.jersey_number, "pos": s.p.position.value,
                    "on_court": s.on_court, "energy": round(s.energy, 3), "pf": fg.pf(s),
                    "fouled_out": fg.fouled_out(s), "min": round(s.seconds / 60, 1),
                    "pts": int(c["pts"]), "reb": int(c["orb"] + c["drb"]), "ast": int(c["ast"]),
                    "fgm": int(c["fgm"]), "fga": int(c["fga"]), "tpm": int(c["tpm"]), "tpa": int(c["tpa"]),
                    "ftm": int(c["ftm"]), "fta": int(c["fta"]), "tov": int(c["tov"]), "pm": s.plus_minus,
                })
            return {"abbr": side.team.abbreviation, "name": side.team.full_name, "score": score[key],
                    "timeouts": side.timeouts, "team_fouls": side.team_fouls,
                    "periods": list(side.period_points), "players": players}

        t = fg.sides[self.user_side].tactics
        m = self.manager
        return {
            "period": fg.period, "clock": round(max(0.0, fg.clock), 1), "done": self.sim is not None,
            "user_side": self.user_side, "home": side_state("home"), "away": side_state("away"),
            "manager": {"timeout_pending": m["timeout"], "auto_subs": m["auto_subs"],
                        "subs_pending": [{"out": o, "in": i} for o, i in m["subs"]]},
            "tactics": {"pace": t.pace, "three": t.three_point_rate, "crash": t.crash_offensive_glass,
                        "focus": t.offensive_focus.value, "defense": t.defensive_scheme.value},
        }

    def header(self) -> dict:
        """The viewer's static data: teams and rosters (in ``Side.roster`` order)."""
        d = self.fg.to_dict()
        return {"version": 2, "fullgame": True, "dt": d["dt"], "home": d["home"], "away": d["away"],
                "possessions": [], "live_client": {"user_side": self.user_side, "game_id": self.game.id}}
