"""A live match: the user coaches his team's game as it is played.

The full game runs possession by possession, only as far ahead as the viewer
needs (``next_chunk``), so the coach's commands shape what happens next:

* **timeout**: taken at the next dead ball, or as soon as his team has the ball.
  It uses one of his timeouts, rests everyone a little and stops the other team's
  run.
* **sub**: player in for player out, at the next dead ball (or timeout).
* **auto_subs**: on, the bench coach keeps running the rotation; off, only
  forced changes (a player fouling out or getting hurt) are made for him.
  A hurt player leaves at the dead ball where it happened and can't come back;
  queued subs involving him are dropped.
* **tactics**: pace, three-point rate, crashing the glass (1-20), offensive focus
  and defensive scheme. They hold for this game only: the team's saved tactics
  come back when it ends.
* **shout**: a touchline instruction that tilts the players' close calls (a small
  bias on the learned option values, ``HalfCourtGame.shout_bias``). Offence:
  ``attack_rim``, ``threes``, ``slow`` (use the clock, walk it up), ``push`` (early
  shots, run after rebounds and makes), ``post`` (feed the bigs inside), ``ball_to``
  (get ``player`` the ball). Defence: ``pressure`` (up on the ball: more turnovers
  and reach-ins), ``paint`` (sag in: give up threes, not layups), ``foul`` (foul at
  once to stop the clock whenever trailing in the last period). One per group
  (attack_rim/threes/post, slow/push, pressure/paint): a new one replaces the old.
  A shout lasts until cancelled (``cancel_shout``), or with ``possessions`` N for
  the team's next N possessions on that end ("foul" counts the fouls it made).
* **timeout** with ``play`` (and ``ato`` while one is pending): the set to run out
  of it on the team's next possession, one of the entry actions in ``plays.py``
  (``swing``, ``handoff``, ``downscreen``, ``pnr``), with ``target`` as the man
  coming off the down screen or the pick-and-roll screener.
* **endgame**: overrule the bench coach's late-game calls for his team:
  ``last_shot`` (hold for the last shot of a period), ``two_for_one`` and ``foul``
  (foul to stop the clock when trailing late), each ``auto`` (the bench coach
  decides, the default), ``on`` or ``off``.

``finish`` plays out whatever is left (for a coach who walks away early) and
returns the ``GameSim``; the caller records it like any other game.
"""
from __future__ import annotations

import copy
import random
from typing import Optional

from ..engine import GameSim
from ..models import ATTR_MAX, ATTR_MIN, DefensiveScheme, Game, League, OffensiveFocus
from .fullgame import ATO_PLAYS, ENDGAME_AUTO, OFFENSE_SHOUTS, FullGame
from .season_game import _model, finish_spatial

LIVE_TACTICS = {
    "pace": "pace", "three": "three_point_rate", "crash": "crash_offensive_glass",
    "focus": "offensive_focus", "defense": "defensive_scheme",
}
SHOUTS = {
    "attack_rim": "Attack the rim", "threes": "Look for threes", "slow": "Slow it down",
    "push": "Push the pace", "post": "Feed the post", "ball_to": "Get {} the ball",
    "pressure": "Pressure the ball", "paint": "Protect the paint", "foul": "Foul now",
}
SHOUT_GROUPS = (("attack_rim", "threes", "post"), ("slow", "push"), ("pressure", "paint"))
ENDGAME_CHOICES = ("auto", "on", "off")
MAX_SHOUT_POSSESSIONS = 20
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

    NOTE_WORDS = ("Sub:", "Timeout", "Start of", "fouls out", "foul to stop", "is hurt", "Out of the timeout")

    def _notes(self, i: int) -> list[str]:
        """Bench news logged since the previous recorded possession (subs, timeouts...)."""
        ps = self.fg.possessions
        lo = ps[i - 1].get("log_at", 0) if i > 0 else 0
        hi = ps[i].get("log_at", lo)
        return [f"{e.team + ': ' if e.team else ''}{e.text}" for e in self.fg.events[lo:hi]
                if any(w in e.text for w in self.NOTE_WORDS)]

    def finish(self) -> GameSim:
        """Play out the rest and return the result (injuries, replay included)."""
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
            ato = self._ato(cmd) if cmd.get("play") else None
            m["timeout"] = True
            m["ato"] = ato
            msg = "Timeout called: at the next dead ball or when you have the ball"
            if ato is not None:
                msg += f"; then {self._ato_text(ato)}"
        elif kind == "ato":
            if not m["timeout"]:
                raise LiveError("call a timeout first: the play is drawn up in it")
            m["ato"] = self._ato(cmd) if cmd.get("play") else None
            msg = (f"Out of the timeout: {self._ato_text(m['ato'])}" if m["ato"]
                   else "No set play: the players run their usual offence")
        elif kind == "shout":
            msg = self._shout(cmd)
        elif kind == "cancel_shout":
            name = cmd.get("shout")
            if name is None:
                m["shouts"].clear()
                msg = "All instructions cancelled"
            elif name not in m["shouts"]:
                raise LiveError(f"'{name}' isn't one of your instructions now")
            else:
                label = self._shout_label(name, m["shouts"].pop(name))
                msg = f"Cancelled: {label}"
        elif kind == "endgame":
            changed = []
            for key, value in cmd.items():
                if key == "type":
                    continue
                if key not in ENDGAME_AUTO:
                    raise LiveError(f"unknown end-of-game setting '{key}' (try: {', '.join(ENDGAME_AUTO)})")
                if value not in ENDGAME_CHOICES:
                    raise LiveError(f"{key} must be one of {', '.join(ENDGAME_CHOICES)}")
                m["endgame"][key] = value
                changed.append(f"{key.replace('_', ' ')} {value}")
            if not changed:
                raise LiveError("nothing to change (last_shot, two_for_one, foul)")
            msg = "End of game: " + ", ".join(changed)
        elif kind == "sub":
            out_id, in_id = int(cmd.get("out", -1)), int(cmd.get("in", -1))
            out, inn = side.seats.get(out_id), side.seats.get(in_id)
            if out is None or inn is None:
                raise LiveError("pick a player on the floor and one on the bench")
            if not out.on_court or inn.on_court:
                raise LiveError(f"{out.p.name} must be on the floor and {inn.p.name} on the bench")
            if inn.injury is not None:
                raise LiveError(f"{inn.p.name} is injured ({inn.injury.description})")
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
            raise LiveError("unknown command (timeout, ato, sub, cancel_subs, auto_subs, tactics, "
                            "shout, cancel_shout, endgame)")
        return {"ok": True, "message": msg, "state": self.state()}

    def _player(self, pid, what: str):
        """One of the coach's players, by id, who can still play."""
        side = self.fg.sides[self.user_side]
        try:
            seat = side.seats.get(int(pid))
        except (TypeError, ValueError):
            seat = None
        if seat is None:
            raise LiveError(f"pick one of your players for {what}")
        if self.fg.unavailable(seat):
            why = f"injured ({seat.injury.description})" if seat.injury is not None else "fouled out"
            raise LiveError(f"{seat.p.name} is {why}")
        return seat

    def _ato(self, cmd: dict) -> dict:
        play = cmd.get("play")
        if play not in ATO_PLAYS:
            raise LiveError(f"unknown play '{play}' (try: {', '.join(ATO_PLAYS)})")
        target = cmd.get("target")
        if target is not None and target != "":
            if play not in ("pnr", "downscreen"):
                raise LiveError("only the pick-and-roll and the down screen take a target player")
            target = self._player(target, "the play").p.id
        else:
            target = None
        return {"play": play, "target": target}

    def _ato_text(self, ato: dict) -> str:
        text = ATO_PLAYS[ato["play"]]
        if ato.get("target") is not None:
            name = self.fg.sides[self.user_side].seats[ato["target"]].p.name
            text += f" ({name} {'screens' if ato['play'] == 'pnr' else 'comes off the screen'})"
        return text

    def _shout_label(self, name: str, sh: dict) -> str:
        label = SHOUTS[name]
        if name == "ball_to":
            label = label.format(self.fg.sides[self.user_side].seats[sh["player"]].p.name)
        return label

    def _shout(self, cmd: dict) -> str:
        m = self.manager
        name = cmd.get("shout")
        if name not in SHOUTS:
            raise LiveError(f"unknown instruction '{name}' (try: {', '.join(SHOUTS)})")
        n = cmd.get("possessions")
        if n is not None and n != "":
            try:
                n = int(n)
            except (TypeError, ValueError):
                n = 0
            if not 1 <= n <= MAX_SHOUT_POSSESSIONS:
                raise LiveError(f"possessions must be 1-{MAX_SHOUT_POSSESSIONS} (or leave it out: until cancelled)")
        else:
            n = None
        sh = {"left": n}
        if name == "ball_to":
            sh["player"] = self._player(cmd.get("player"), "'get him the ball'").p.id
        replaced = []
        for group in SHOUT_GROUPS:
            if name in group:
                for other in group:
                    if other != name and other in m["shouts"]:
                        replaced.append(self._shout_label(other, m["shouts"].pop(other)))
        m["shouts"][name] = sh
        msg = f"{self._shout_label(name, sh)}: " + (f"for the next {n} possessions" if n else "until you cancel it")
        if replaced:
            msg += f" (instead of {', '.join(replaced)})"
        if name == "foul":
            msg += "; your team fouls whenever it trails in the 4th quarter or overtime"
        return msg

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
                    "fouled_out": fg.fouled_out(s), "injured": s.injury is not None,
                    "injury": s.injury.description if s.injury is not None else None,
                    "min": round(s.seconds / 60, 1),
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
                        "subs_pending": [{"out": o, "in": i} for o, i in m["subs"]],
                        "shouts": [{"name": k, "label": self._shout_label(k, sh), "left": sh.get("left"),
                                    "player": sh.get("player"),
                                    "end": "offense" if k in OFFENSE_SHOUTS else "defense"}
                                   for k, sh in m["shouts"].items()],
                        "ato": dict(m["ato"], label=self._ato_text(m["ato"])) if m["ato"] else None,
                        "ato_next": (dict(m["ato_next"], label=self._ato_text(m["ato_next"]))
                                     if m["ato_next"] else None),
                        "endgame": dict(m["endgame"])},
            "tactics": {"pace": t.pace, "three": t.three_point_rate, "crash": t.crash_offensive_glass,
                        "focus": t.offensive_focus.value, "defense": t.defensive_scheme.value},
        }

    def header(self) -> dict:
        """The viewer's static data: teams and rosters (in ``Side.roster`` order)."""
        d = self.fg.to_dict()
        return {"version": 2, "fullgame": True, "dt": d["dt"], "home": d["home"], "away": d["away"],
                "possessions": [], "live_client": {"user_side": self.user_side, "game_id": self.game.id}}
