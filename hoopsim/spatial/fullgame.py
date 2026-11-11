"""Full games in the spatial engine: four quarters (plus overtime), transition,
substitutions, fatigue, fouls and box scores.

``HalfCourtGame`` plays one half-court possession at a time. This driver wraps
it in a game, following the season engine (``hoopsim/engine.py``) in simplified
form:

* **Clock.** A 12-minute period clock (5 in overtime) runs through every
  possession. The shot clock carries over: 14 after an offensive rebound or a
  non-shooting foul with less on it, and off when the period has less time left
  than the shot clock. The period ends at the buzzer, mid-possession if need be
  (a shot already in the air still counts). Jump ball for the first quarter and
  each overtime, alternating possession for the others.
* **Transition.** After steals, defensive rebounds and (less often) made baskets
  an open-court phase runs first (``transition.py``): the players actually run
  the floor, and the half-court engine picks up from their positions with the
  shot clock already running.
* **Fouls.** Shooting fouls and charges come from the half-court engine.
  Non-shooting fouls (reach-ins, holds) happen during possessions at about NBA
  rates. Team fouls reset each period, and the bonus starts with the 5th team
  foul or the 2nd in the last two minutes. Players foul out at the league's limit (6).
* **Fatigue and substitutions.** Energy drains on court with how much a player
  runs (and against a press) and recovers on the bench, at period breaks and
  timeouts. Tired players are slower, react later and shoot worse. Substitutions
  happen at dead balls (not after made baskets, except late in the game): each
  side follows its rotation plan from ``lineup.plan_rotation`` (or the coach's
  saved minutes), weighs fatigue and foul trouble, and goes to its best players
  in close games and its bench in blowouts, as in engine.py.
* **End of game.** A trailing team fouls to stop the clock, a leading team milks
  the shot clock and dribbles out the last seconds, teams hold for the last shot
  of a quarter and go two-for-one, a team down three looks for a three, and
  trailing teams call a late timeout to advance the ball and set a play.

The result is engine.py's ``GameSim`` (result, box score, play-by-play), so
spatial games can later be stored like season-engine games.

    from hoopsim.spatial.fullgame import FullGame
    sim = FullGame(league, home_id, away_id, random.Random(1)).run()
"""
from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from ..engine import GameSim, PlayEvent
from ..lineup import lineup_is_usable, plan_rotation
from ..models import (BoxScore, DefensiveScheme, GameResult, League, Player, PlayerLine,
                      Position, TeamBox)
from . import court as C
from .sim import DEFAULT_TUNING, Agent, HalfCourtGame, SpatialTuning
from .transition import DEFAULT_OPEN_TUNING, DeadBallInbound, OpenCourt, OpenCourtTuning
from .values import DecisionModel

POSITIONS = list(Position)
SIDES = ("home", "away")
BOX_FIELDS = ("fgm", "fga", "tpm", "tpa", "ftm", "fta", "orb", "drb", "ast", "stl", "blk", "tov", "pf")


@dataclass
class GameTuning:
    # Fatigue (the season engine's rates, with drain scaled by effort).
    drain_per_second: float = 0.00042      # stamina-10 player on court, normal effort
    recover_per_second: float = 0.0011     # on the bench
    period_break_recovery: float = 0.08
    halftime_recovery: float = 0.20
    timeout_recovery: float = 0.03
    min_energy: float = 0.30
    effort_reference: float = 5.0          # ft/s of running that counts as normal effort
    effort_floor: float = 0.45             # standing around still tires you this much
    press_drain: float = 1.15
    # What fatigue does (at energy e): speed x (1 - k (1 - e)), reaction x (1 + k (1 - e)),
    # make chance x (1 - k (1 - e)): proportional, so it doesn't tilt the shot mix
    # (a flat cut took ~11% off threes but ~6% off layups and drove tired teams inside).
    fatigue_speed: float = 0.22
    fatigue_reaction: float = 0.45
    fatigue_shot: float = 0.12
    # Non-shooting defensive fouls (reach-ins, holds, loose balls): a per-second risk
    # during live half-court play, so their number doesn't depend on how long
    # possessions last. ~0.011/s gives ~7.5 a team-game (NBA personal fouls ~20 per 100
    # possessions, of which roughly 9-10 shooting and 2 offensive).
    non_shooting_foul_per_second: float = 0.011
    non_shooting_foul_earliest: float = 0.5
    press_foul_factor: float = 1.25
    bring_up: tuple = (3.5, 6.0)           # half-court starts: shot-clock seconds to get into the offence
    inbound_after_make: tuple = (2.0, 3.5)  # game clock running before the inbound is touched
    # A possession counts as transition if it began with an open-court phase and its
    # first shot, foul or turnover came within this many seconds of gaining the ball.
    transition_window: float = 8.0
    # After a make the court view shows the inbound and the walk-up (open court, not
    # pushed) instead of jumping to half-court positions.
    inbound_walkups: bool = True
    # After a dead ball (a foul away from the ball, a violation, out of bounds, a late
    # timeout) the court view shows the throw-in from where play stopped instead of
    # jumping to half-court positions.
    dead_ball_inbounds: bool = True


DEFAULT_GAME_TUNING = GameTuning()


class Seat:
    """One player's game: his agent on the court, minutes plan, energy and line."""

    def __init__(self, player: Player, agent: Agent, side: str, target_minutes: int):
        self.p = player
        self.agent = agent
        self.side = side
        self.target_seconds = target_minutes * 60
        self.energy = 1.0
        self.on_court = False
        self.starter = False
        self.seconds = 0.0
        self.plus_minus = 0
        self.stint_start = 0.0
        self.left_at = -1e9
        self.fouled_out_at: Optional[float] = None    # game seconds when he fouled out
        self.travel_mark = 0.0
        self.base = (agent.max_speed, agent.accel, agent.reaction)
        self.ca = player.current_ability
        self.fit = [(player.ability_at(pos) - self.ca) / 10 for pos in POSITIONS]

    def __repr__(self) -> str:
        return f"<{self.p.name}>"


class Side:
    def __init__(self, key: str, team, seats: list[Seat], starters: list[int], timeouts: int):
        self.key = key
        self.team = team
        self.tactics = team.tactics
        self.roster = seats                                  # export order
        self.seats = {s.p.id: s for s in seats}
        self.lineup = [self.seats[pid] for pid in starters]  # slot i plays POSITIONS[i]
        for s in self.lineup:
            s.on_court = s.starter = True
        self.period_points: list[int] = []
        self.team_fouls = 0
        self.late_fouls = 0
        self.possessions = 0
        self.timeouts = timeouts
        self.late_timeouts = 0
        self.run_against = 0

    @property
    def bench(self) -> list[Seat]:
        return [s for s in self.roster if not s.on_court]


class FullGame:
    """A full spatial game between two teams of ``league``."""

    def __init__(self, league: League, home_id: int, away_id: int,
                 rng: Optional[random.Random] = None, *, record: bool = False,
                 tuning: SpatialTuning = DEFAULT_TUNING, game_tuning: GameTuning = DEFAULT_GAME_TUNING,
                 open_tuning: OpenCourtTuning = DEFAULT_OPEN_TUNING,
                 model: Optional[DecisionModel] = None, params=None):
        self.league, self.rules = league, league.rules
        self.rng = rng or random.Random()
        self.gt, self.ot = game_tuning, open_tuning
        self.record = record
        self.hc = HalfCourtGame(league, home_id, away_id, self.rng, tuning, record=record, model=model,
                                params=params)
        self.sides = {key: self._side(key, tid) for key, tid in (("home", home_id), ("away", away_id))}
        self.hc.box = {id(s.agent): Counter() for side in self.sides.values() for s in side.roster}
        self._sync()
        self.period = 0
        self.clock = 0.0                       # seconds left in the period
        self.elapsed = 0.0                     # game seconds played
        self.regulation = 4 * self.rules.quarter_minutes * 60
        self.events: list[PlayEvent] = []
        self.possessions: list[dict] = []      # recorded possessions for the viewer
        self.stats = Counter()
        self.sub_log: list[tuple] = []         # (game seconds, side, player in id, player out id)
        self.manager: Optional[dict] = None    # a human coach for one side (see attach_manager)
        self.result: Optional[GameSim] = None

    # ------------------------------------------------------------------ set-up
    def _side(self, key: str, team_id: int) -> Side:
        team = self.league.teams[team_id]
        players = [p for p in self.league.roster(team_id) if p.is_available]
        if len(players) < 5:
            raise ValueError(f"team {team_id} has fewer than 5 healthy players")
        if lineup_is_usable(self.league, team_id):
            starters, minutes = team.tactics.starters, team.tactics.minutes_targets
        else:
            starters, minutes = plan_rotation(players, team.tactics.rotation_depth)
        order = list(starters) + [p.id for p in sorted(players, key=lambda p: -minutes.get(p.id, 0))
                                  if p.id not in starters]
        by_id = {p.id: p for p in players}
        seats = []
        for i, pid in enumerate(order):
            agent = Agent(by_id[pid], key, min(i, 4))
            seats.append(Seat(by_id[pid], agent, key, minutes.get(pid, 0)))
        return Side(key, team, seats, list(starters), self.rules.timeouts_per_game)

    def _sync(self) -> None:
        """Point the half-court engine at the players on the floor."""
        for key, side in self.sides.items():
            for slot, s in enumerate(side.lineup):
                s.agent.slot = slot
            self.hc.agents[key] = [s.agent for s in side.lineup]
        self.hc.everyone = self.hc.agents["home"] + self.hc.agents["away"]
        self.hc.index = {id(a): i for i, a in enumerate(self.hc.everyone)}

    def other(self, side: Side) -> Side:
        return self.sides["away" if side.key == "home" else "home"]

    @property
    def home(self) -> Side:
        return self.sides["home"]

    @property
    def away(self) -> Side:
        return self.sides["away"]

    def score_of(self, side: Side) -> int:
        return self.hc.score[side.key]

    def margin(self) -> int:
        return abs(self.hc.score["home"] - self.hc.score["away"])

    def pf(self, s: Seat) -> int:
        return self.hc.box[id(s.agent)]["pf"]

    def fouled_out(self, s: Seat) -> bool:
        return self.pf(s) >= self.rules.personal_foul_limit

    # ------------------------------------------------------------------ logging
    def log(self, side: Optional[Side], text: str) -> None:
        if self.record:
            self.events.append(PlayEvent(self.period, max(0.0, self.clock),
                                         side.team.id if side else None,
                                         side.team.abbreviation if side else "", text,
                                         self.hc.score["home"], self.hc.score["away"]))

    # ------------------------------------------------------------------ clock & fatigue
    def advance(self, seconds: float) -> None:
        seconds = max(0.0, min(seconds, self.clock))
        gt = self.gt
        for side in self.sides.values():
            press = gt.press_drain if (side.tactics.defensive_scheme is DefensiveScheme.PRESS or
                                       self.other(side).tactics.defensive_scheme is DefensiveScheme.PRESS) else 1.0
            for s in side.roster:
                ran = s.agent.travelled - s.travel_mark
                s.travel_mark = s.agent.travelled
                if seconds <= 0:
                    continue
                if s.on_court:
                    s.seconds += seconds
                    effort = max(gt.effort_floor, min(2.0, ran / seconds / gt.effort_reference))
                    drain = gt.drain_per_second * (1.5 - s.p.attributes.stamina / 20) * effort * press
                    s.energy = max(gt.min_energy, s.energy - drain * seconds)
                else:
                    s.energy = min(1.0, s.energy + gt.recover_per_second * seconds)
                self.apply_fatigue(s)
        self.clock -= seconds
        self.elapsed += seconds

    def apply_fatigue(self, s: Seat) -> None:
        tired = 1.0 - s.energy
        speed, accel, reaction = s.base
        a = s.agent
        a.max_speed = speed * (1 - self.gt.fatigue_speed * tired)
        a.accel = accel * (1 - self.gt.fatigue_speed * tired)
        a.reaction = reaction * (1 + self.gt.fatigue_reaction * tired)
        a.fatigue_shot = self.gt.fatigue_shot * tired

    def rest(self, amount: float) -> None:
        for side in self.sides.values():
            for s in side.roster:
                s.energy = min(1.0, s.energy + amount)
                self.apply_fatigue(s)

    # ------------------------------------------------------------------ substitutions
    def _sub_score(self, s: Seat, crunch: bool, garbage: bool) -> float:
        if self.fouled_out(s):
            return -1e9
        if crunch:
            score = s.ca / 10 + 6 * (s.energy - 0.8)
            if self.clock <= 60:
                lead = self.score_of(self.sides[s.side]) - self.score_of(self.other(self.sides[s.side]))
                if lead > 0:
                    score += 0.25 * (s.p.attributes.free_throw - 10)
                elif lead < 0:
                    score += 0.2 * (s.p.attributes.three_point - 10)
            return score
        if garbage:
            return -s.ca / 10 + 2 * s.energy
        expected = s.target_seconds * min(1.0, self.elapsed / self.regulation)
        deficit = (expected - s.seconds) / 60
        if s.target_seconds == 0:
            deficit -= 6
        score = 0.6 * deficit + 9 * (s.energy - 0.8)
        allowed = {1: 2, 2: 3, 3: 4}.get(self.period, 5)
        if self.pf(s) >= allowed:
            score -= 8                         # foul trouble: sit him
        return score

    def substitutions(self, side: Side, forced_only: bool = False) -> None:
        """Engine.py's substitution logic at a dead ball. ``forced_only``: just replace
        players who fouled out (they can't wait for the next dead ball)."""
        m = self.manager
        if m is not None and m["side"] == side.key and not m["auto_subs"]:
            forced_only = True                 # the coach makes his own changes
        final = self.period >= 4
        crunch = final and self.clock <= 300 and self.margin() <= 8
        garbage = final and self.clock <= 360 and self.margin() >= 20
        bench = [s for s in side.bench if not self.fouled_out(s)]
        if not bench:
            return
        scores = {id(s): self._sub_score(s, crunch, garbage) for s in side.roster}
        order = sorted(range(5), key=lambda i: scores[id(side.lineup[i])])
        changed = False
        for slot in order:
            out = side.lineup[slot]
            forced = self.fouled_out(out)
            if forced_only and not forced:
                continue
            if not forced and self.elapsed - out.stint_start < 240 and not (crunch or garbage):
                continue                       # let a fresh stint breathe
            ready = [s for s in bench if self.elapsed - s.left_at >= 120 or forced or crunch]
            if not ready:
                continue
            best = max(ready, key=lambda s: scores[id(s)] + s.fit[slot])
            if forced or (scores[id(best)] + best.fit[slot]) - (scores[id(out)] + out.fit[slot]) > 2.5:
                self._swap(side, slot, out, best, forced)
                bench.remove(best)
                if not self.fouled_out(out):
                    bench.append(out)
                changed = True
        if changed:
            self._sync()

    def _swap(self, side: Side, slot: int, out: Seat, inn: Seat, forced: bool) -> None:
        side.lineup[slot] = inn
        inn.on_court, inn.stint_start = True, self.elapsed
        out.on_court, out.left_at = False, self.elapsed
        a, b = out.agent, inn.agent
        b.place(a.x, a.y)                      # he takes his man's spot on the floor
        b.vx, b.vy = a.vx, a.vy
        inn.travel_mark = b.travelled
        self.stats["substitutions"] += 1
        self.sub_log.append((self.elapsed, side.key, inn.p.id, out.p.id))
        self.log(side, f"Sub: {inn.p.name} in for {out.p.name}" + (" (fouled out)" if forced else ""))

    # ------------------------------------------------------------------ fouls
    def in_bonus_after_foul(self, defense: Side, clock_at_foul: float) -> bool:
        """Would the defence's next foul give free throws? (5th team foul of the period,
        or the 2nd in its last two minutes.)"""
        return (defense.team_fouls + 1 >= self.rules.team_fouls_bonus
                or (clock_at_foul <= 120 and defense.late_fouls + 1 >= 2))

    def should_foul(self, defense: Side, offense: Side) -> bool:
        """Engine.py's rule: should the trailing defence foul straight away?"""
        if self.period < 4 or self.clock > 45 or self.clock < 0.5:
            return False
        deficit = self.score_of(offense) - self.score_of(defense)
        if deficit <= 0:
            return False
        sc = self.rules.shot_clock_seconds
        if deficit <= 3:
            return self.clock <= sc
        if self.clock > sc + 4:
            return False
        return deficit <= min(12, 3 + self.clock / 5)

    # ------------------------------------------------------------------ clock management
    def plan(self, offense: Side, defense: Side) -> Optional[object]:
        """Clock-management preferences for the half-court engine, "run_out", or None."""
        clock, sc = self.clock, self.rules.shot_clock_seconds
        lead = self.score_of(offense) - self.score_of(defense)
        final = self.period >= 4
        if final and lead > 0:
            if clock <= sc:
                return "run_out"               # the shot clock is off: game over
            if clock <= 2 * sc + 10:
                return {"hold": 0.35, "hold_until": 4.0}     # milk the shot clock
        if final and lead < 0 and clock <= 75:
            need = -lead
            situation = {"hurry": 0.1}
            if need == 3 and clock <= 30:
                situation["three"] = 0.6       # only a three ties it
            elif need >= 4:
                situation["three"] = 0.15
            elif need <= 2 and clock <= 30:
                situation["three"] = -0.1      # play for the tie
            return situation
        if final and lead == 0 and clock <= sc + 2:
            return {"hold": 0.35, "hold_until": 4.0}         # last shot
        if clock <= sc:
            return {"hold": 0.3, "hold_until": 4.0}          # last shot of the quarter
        if clock <= sc + 14 and not final:
            return {"hurry": 0.12}                           # two-for-one
        return None

    def late_timeout(self, side: Side, how: str) -> bool:
        """Trailing (or tied) late: burn a timeout to advance the ball and set a play."""
        deficit = self.score_of(self.other(side)) - self.score_of(side)
        if (self.period >= 4 and self.clock <= 40 and 0 <= deficit <= 6 and side.timeouts > 0
                and side.late_timeouts < 3 and how in ("drb", "score", "ft")
                and self.rng.random() < 0.8):
            side.late_timeouts += 1
            return self.call_timeout(side, "draw up a play")
        return False

    def call_timeout(self, side: Side, reason: str) -> bool:
        if side.timeouts <= 0:
            return False
        side.timeouts -= 1
        self.stats["timeouts"] += 1
        self.log(side, f"Timeout {side.team.abbreviation} ({reason}), {side.timeouts} left")
        self.rest(self.gt.timeout_recovery)
        self.substitutions(self.home)
        self.substitutions(self.away)
        return True

    # ------------------------------------------------------------------ one possession
    def possession(self, offense: Side, how: str, carrier: Optional[Agent]) -> tuple[str, Optional[Agent]]:
        """Play one possession. ``how`` is how the ball was won: "jump", "dead" (inbound
        after a dead ball), "score"/"ft" (after a make), "drb" or "steal".
        Returns (how it ended, who has the ball next for live-ball endings)."""
        defense = self.other(offense)
        offense.possessions += 1
        self.stats[f"poss_after_{how}"] += 1
        before = self.hc.score[offense.key]
        record = {"trans_frames": [], "trans_events": [], "segments": []}
        open_court = None
        first_action: Optional[float] = None
        used = 0.0                              # game seconds since gaining the ball

        if self.late_timeout(offense, how):
            how = "advanced"                    # ball advanced to the frontcourt, defence set

        plan = None if self.should_foul(defense, offense) else self.plan(offense, defense)
        if plan == "run_out":
            self.log(offense, f"{offense.team.abbreviation} dribble out the clock")
            self.stats["run_outs"] += 1
            self.advance(self.clock)
            return "period", None
        situation = plan if isinstance(plan, dict) else None

        # Open court first?
        start: Optional[dict] = None
        live = how in ("drb", "steal")
        if live or how in ("score", "ft"):
            push = None
            if how in ("score", "ft"):
                push = self.clock < 8 or self.rng.random() < self._push_after_make(offense)
            if live or push or self.gt.inbound_walkups:     # a walk-up when not pushing
                oc = OpenCourt(self.hc, offense.key, "score" if how in ("score", "ft") else how,
                               carrier, self.clock, self.rng, self.ot, push=push,
                               clock_stopped=how == "ft" or self.clock_stops_on_make())
                open_court = oc.run()
                self.stats["open_court"] += 1
                self.stats["pushes"] += open_court.pushed
                open_court.how = how
                record["trans_frames"], record["trans_events"] = open_court.frames, open_court.events
                self.advance(open_court.seconds)
                used += open_court.seconds
                self.stats["seconds_open_court"] += open_court.seconds
                if open_court.result is not None:
                    pts = open_court.points
                    if pts:
                        self.hc.score[offense.key] += pts
                    self._after_points(offense, pts)
                    first_action = used
                    self._finish_possession(offense, before, open_court, first_action, record,
                                            open_court.result)
                    if open_court.result == "steal":
                        return "steal", open_court.carrier
                    return "period", None
                start = open_court.start

        ending, carrier_next = "dead", None
        segment = 0
        while True:
            segment += 1
            if self.clock <= 0:
                ending = "period"
                break
            inbound = None
            if start is None:
                inbound = self._dead_inbound(offense, how if segment == 1 else "foul_inbound", record)
            if inbound is not None:
                used += inbound.seconds
                if inbound.result is not None:          # picked off, or out of time
                    if first_action is None:
                        first_action = used
                    if inbound.points:
                        self.hc.score[offense.key] += inbound.points
                    self._after_points(offense, inbound.points)
                    ending, carrier_next = inbound.result, inbound.carrier
                    break
                start = inbound.start
            if start is None:
                start = self._set_start(how if segment == 1 else "foul_inbound")
                bring = start.pop("_bring_up")
                self.advance(bring)
                used += bring
                self.stats["seconds_bring_up"] += bring
                start["game_clock"] = self.clock
            if situation:
                start["situation"] = situation
            self._schedule_foul(offense, defense, start)
            if inbound is not None:
                start["holder"] = inbound.start["holder"]   # they foul whoever caught it
            result, info = self.segment(offense, defense, start, record)
            self._dead_spot = {"period": self.period, "elapsed": self.elapsed, "side": offense.key,
                               "ball": self.hc.ball_spot(), "shot_clock": self.hc.shot_clock}
            if inbound is not None and self.record and record["segments"]:
                seg = record["segments"][-1][0]          # the inbound already brought it in
                seg["events"] = [e for e in seg["events"] if " bring it up: " not in e[1]]
            used_before = used
            used += info["seconds"]
            if first_action is None and info["first_action"] is not None:
                first_action = used_before + info["first_action"]
            start = None
            if result == "foul":
                defense.team_fouls += 1
                if self.clock <= 120:
                    defense.late_fouls += 1
                self.stats["fouls_no_bonus"] += 1
                self._dead_ball()
                how = "foul_inbound"            # inbound and play on (or foul again)
                continue
            ending, carrier_next = self._ending(result, info)
            break
        self._finish_possession(offense, before, open_court, first_action, record, ending)
        return ending, carrier_next

    # Where the last half-court segment left the ball (for the throw-in after a dead ball).
    _dead_spot: Optional[dict] = None

    def _dead_inbound(self, offense: Side, how: str, record: dict):
        """The throw-in after a dead ball (``how`` "dead" or "foul_inbound") or a late
        timeout ("advanced"), from where play stopped: the players start where they are,
        the clocks start on the touch. Returns the ``OpenCourtResult`` (``start`` for the
        half court, or ``result`` "steal"/"period"), or None for a half-court start
        instead (period starts, or ``dead_ball_inbounds`` off)."""
        spot = self._dead_spot
        if (not self.gt.dead_ball_inbounds or how not in ("dead", "foul_inbound", "advanced")
                or spot is None or spot["period"] != self.period or spot["elapsed"] != self.elapsed):
            return None
        turn = spot["side"] != offense.key
        bx, by = spot["ball"]
        ball = (-bx, C.FULL_LENGTH - by) if turn else (bx, by)
        sc = float(self.rules.shot_clock_seconds)
        shot_clock = sc
        if how == "foul_inbound":
            shot_clock = min(sc, max(spot["shot_clock"] if spot["shot_clock"] > 0 else 0.0, 14.0))
        where = self._inbound_spot(how, (bx, by), ball)
        ib = DeadBallInbound(self.hc, offense.key, where, ball, self.clock, self.rng, self.ot,
                             turn=turn, shot_clock=shot_clock, after_timeout=how == "advanced")
        res = ib.run()
        self.stats["inbounds"] += 1
        self.stats["inbounds_baseline" if ib.baseline else
                   "inbounds_frontcourt" if ib.frontcourt else "inbounds_backcourt"] += 1
        self.stats["inbound_steals"] += res.result == "steal"
        self.stats["seconds_bring_up"] += res.seconds
        self.stats["seconds_inbound_setup"] += res.elapsed - res.seconds    # clock stopped
        self.advance(res.seconds)
        if self.record:
            score = [self.hc.score["home"], self.hc.score["away"]]
            record["segments"].append(({"frames": res.frames, "events": res.events, "start_score": score},
                                       self._lineup_indices()))
        return res

    def _inbound_spot(self, how: str, ball_was: tuple[float, float],
                      ball: tuple[float, float]) -> tuple[float, float]:
        """Where the throw-in comes from, in the new offence's frame. ``ball_was``: where it
        went dead in the last segment's frame, ``ball``: the same in the new frame.

        Out under the basket (the ball within 6 ft of the end line): the baseline.
        Otherwise the sideline nearest the ball, no nearer the baseline than the
        free-throw line extended. Out of a late timeout: the frontcourt sideline at the
        28-ft hash mark."""
        x, y = ball
        side = math.copysign(1.0, x) if abs(x) > 0.5 else (1.0 if self.rng.random() < 0.5 else -1.0)
        if how == "advanced":
            return side * 26.0, 28.0
        if how == "dead" and ball_was[1] < 6.0 and abs(ball_was[0]) < 22.0 and y > C.HALF_LENGTH:
            bx = side * max(4.0, min(16.0, abs(x)))         # not from behind the backboard
            return bx, C.FULL_LENGTH + 1.5
        return side * 26.0, max(C.FREE_THROW_Y, min(C.FULL_LENGTH - 3.0, y))

    def clock_stops_on_make(self) -> bool:
        """NBA: the clock stops after a made field goal only in the last minute of the
        first three periods and the last two of the fourth and overtime."""
        return self.clock <= (120 if self.period >= 4 else 60)

    def _push_after_make(self, offense: Side) -> float:
        base = self.ot.push_after_score
        logit = math.log(base / (1 - base)) + self.ot.pace_effect * (offense.tactics.pace - 10)
        return 1 / (1 + math.exp(-logit))

    def _set_start(self, how: str) -> dict:
        """A half-court start with the defence set (dead balls, walk-ups, set plays)."""
        rng, sc = self.rng, float(self.rules.shot_clock_seconds)
        if how == "foul_inbound":
            bring = rng.uniform(1.0, 2.5)
            shot_clock = max(self.hc.shot_clock if self.hc.shot_clock > 0 else 0.0, 14.0)
            shot_clock = min(sc, shot_clock)
        elif how == "advanced":
            bring = rng.uniform(0.5, 1.5)       # inbounded in the frontcourt
            shot_clock = sc - bring
        else:
            bring = rng.uniform(*self.gt.bring_up)
            shot_clock = sc - bring
            if how == "score" and not self.clock_stops_on_make():
                bring += rng.uniform(*self.gt.inbound_after_make)   # the clock runs during the inbound
        bring = min(bring, self.clock * 0.25)
        return {"_bring_up": bring, "shot_clock": shot_clock, "game_clock": self.clock}

    def _schedule_foul(self, offense: Side, defense: Side, start: dict) -> None:
        rng, gt = self.rng, self.gt
        if self.should_foul(defense, offense):
            # Foul a poor free-throw shooter if you can catch one, else whoever has it.
            if rng.random() < 0.35:
                target = min(offense.lineup, key=lambda s: s.p.attributes.free_throw)
                start["holder"] = target.agent
            fouler = min(defense.lineup, key=lambda s: (self.pf(s) >= self.rules.personal_foul_limit - 1, s.ca))
            start["foul_at"] = rng.uniform(0.5, 2.5)
            start["foul_by"] = fouler.agent
            start["bonus"] = self.in_bonus_after_foul(defense, self.clock - 1.5)
            self.stats["intentional_fouls"] += 1
            self.log(defense, f"{defense.team.abbreviation} foul to stop the clock")
            return
        rate = gt.non_shooting_foul_per_second
        if defense.tactics.defensive_scheme is DefensiveScheme.PRESS:
            rate *= gt.press_foul_factor
        at = gt.non_shooting_foul_earliest + rng.expovariate(rate)
        if at < 24.0:                       # it only happens if the ball is still live then
            start["foul_at"] = at
            start["bonus"] = self.in_bonus_after_foul(defense, self.clock - at)

    def segment(self, offense: Side, defense: Side, start: dict, record: dict) -> tuple[str, dict]:
        """One run of the half-court engine; accounts time, points, fouls."""
        hc = self.hc
        snap = {id(a): Counter(hc.box[id(a)]) for a in hc.everyone}
        lineup = self._lineup_indices() if self.record else None
        clock0 = start.get("game_clock", self.clock)
        hc.play_possession(offense.key, start)
        seconds = max(0.0, clock0 - (hc.game_clock if hc.game_clock is not None else clock0))
        if hc.result == "period":
            seconds = self.clock
        self.advance(seconds)
        self.stats["seconds_half_court"] += seconds
        self.stats["segments"] += 1
        diff = {id(a): hc.box[id(a)] - snap[id(a)] for a in hc.everyone}
        d_agents = [s.agent for s in defense.lineup]
        o_agents = [s.agent for s in offense.lineup]
        # Defensive fouls this segment (shooting fouls; the "foul" result is counted by the caller).
        dfouls = sum(diff[id(a)]["pf"] for a in d_agents) - (1 if hc.result == "foul" else 0)
        if dfouls > 0:
            defense.team_fouls += dfouls
            if self.clock <= 120:
                defense.late_fouls += dfouls
        self._after_points(offense, hc.points)
        if self.record and hc.possessions:
            record["segments"].append((hc.possessions[-1], lineup))
        fta = sum(diff[id(a)]["fta"] for a in o_agents)
        thief = next((a for a in d_agents if diff[id(a)]["stl"]), None)
        rebounder = next((a for a in d_agents if diff[id(a)]["drb"]), None)
        first = hc.first_shot_at
        if first is None and hc.result in ("tov", "foul"):
            first = hc.now
        if first is None and fta:
            first = hc.now
        # Players who fouled out leave now (the ball is dead on a foul).
        for side in (offense, defense):
            if any(self.fouled_out(s) for s in side.lineup):
                for s in side.lineup:
                    if self.fouled_out(s) and s.fouled_out_at is None:
                        s.fouled_out_at = self.elapsed
                        self.stats["foul_outs"] += 1
                        self.log(side, f"{s.p.name} fouls out")
                self.substitutions(side, forced_only=True)
        return hc.result, {"seconds": seconds, "fta": fta, "thief": thief, "rebounder": rebounder,
                           "first_action": first}

    def _after_points(self, offense: Side, points: int) -> None:
        if points <= 0:
            return
        offense.period_points[-1] += points
        offense.run_against = 0
        defense = self.other(offense)
        defense.run_against += points
        for s in offense.lineup:
            s.plus_minus += points
        for s in defense.lineup:
            s.plus_minus -= points

    def _ending(self, result: str, info: dict) -> tuple[str, Optional[Agent]]:
        if result == "drb":
            return "drb", info["rebounder"]
        if result == "tov":
            if info["thief"] is not None:
                return "steal", info["thief"]
            return "dead", None
        if result in ("score", "ft"):
            return ("ft" if info["fta"] else "score"), None
        if result == "period":
            return "period", None
        return "dead", None

    def _finish_possession(self, offense: Side, before: int, open_court, first_action,
                           record: dict, ending: str) -> None:
        points = self.hc.score[offense.key] - before
        self.stats["points"] += points
        is_transition = (open_court is not None and open_court.pushed and first_action is not None
                         and first_action <= self.gt.transition_window)
        if open_court is not None:
            tag = open_court.advantage or "ended"
            self.stats[f"adv_{tag}_n"] += 1
            self.stats[f"adv_{tag}_pts"] += points
            self.stats[f"adv_{tag}_trans"] += is_transition
        if is_transition:
            self.stats["transition"] += 1
            self.stats["transition_points"] += points
            self.stats[f"transition_after_{open_court.how}"] += 1
            self.stats[f"transition_after_{open_court.how}_pts"] += points
        else:
            self.stats["halfcourt"] += 1
            self.stats["halfcourt_points"] += points
        if self.record:
            self._record(offense, before, open_court, record, ending, is_transition)

    def _dead_ball(self) -> None:
        self.substitutions(self.home)
        self.substitutions(self.away)

    # ------------------------------------------------------------------ recording
    def _lineup_indices(self) -> list[int]:
        out = []
        for key in SIDES:
            side = self.sides[key]
            out += [side.roster.index(s) for s in side.lineup]
        return out

    def _record(self, offense: Side, before: int, open_court, record: dict, ending: str,
                is_transition: bool) -> None:
        frames, events = list(record["trans_frames"]), list(record["trans_events"])
        start_score = None
        lineups = [[0, self._lineup_at_start]]
        for seg, lineup in record["segments"]:
            if start_score is None and not frames:
                start_score = seg["start_score"]
            shift = len(frames)
            if frames and seg is record["segments"][0][0]:
                # The open court already brought it up.
                seg = dict(seg, events=[e for e in seg["events"] if " bring it up: " not in e[1]])
            if lineup != lineups[-1][1]:
                lineups.append([shift, lineup])
            frames += seg["frames"]
            events += [[e[0] + shift, *e[1:]] for e in seg["events"]]
        if not frames:
            return
        if start_score is None:
            score = dict(self.hc.score)
            score[offense.key] = before
            start_score = [score["home"], score["away"]]
        if not events:
            events = [[0, f"{offense.team.abbreviation} ball", *start_score]]
        self.possessions.append({
            "offense": offense.key, "period": self.period,
            "start_score": start_score, "end_score": [self.hc.score["home"], self.hc.score["away"]],
            "frames": frames, "events": events, "result": ending,
            "transition": is_transition, "lineups": lineups, "log_at": len(self.events),
        })

    # ------------------------------------------------------------------ periods and the game
    def jump_ball(self) -> Side:
        hc_, ac = self.home.lineup[4], self.away.lineup[4]
        a, b = hc_.p, ac.p
        edge = (0.02 * (a.attributes.vertical - b.attributes.vertical)
                + 0.01 * (a.physique.height_cm - b.physique.height_cm))
        return self.home if self.rng.random() < 0.5 + max(-0.3, min(0.3, edge)) else self.away

    def play_period(self, starting: Side) -> None:
        for _ in self._period(starting):
            pass

    def _period(self, starting: Side):
        """One period, pausing (yield) after every possession."""
        self.period += 1
        minutes = self.rules.quarter_minutes if self.period <= 4 else self.rules.overtime_minutes
        self.clock = minutes * 60.0
        for side in self.sides.values():
            side.team_fouls = side.late_fouls = side.late_timeouts = 0
            if self.period > 4:
                side.timeouts = self.rules.timeouts_per_overtime
            side.period_points.append(0)
        if self.period > 1:
            self.rest(self.gt.halftime_recovery if self.period == 3 else self.gt.period_break_recovery)
            self._manager_subs()
            self._dead_ball()
        label = f"Q{self.period}" if self.period <= 4 else f"OT{self.period - 4}"
        self.log(None, f"Start of {label}")
        if self.period == 1 or self.period > 4:
            self.log(starting, f"{starting.team.abbreviation} win the tip")
        offense, how, carrier = starting, "dead", None
        while self.clock > 0:
            self._lineup_at_start = self._lineup_indices()
            how, carrier = self.possession(offense, how, carrier)
            offense = self.other(offense)
            yield
            if self.clock <= 0:
                break
            dead = how in ("dead", "ft") or (how == "score" and self.period >= 4 and self.clock <= 120)
            if self.manager is not None and self._manager_break(dead, offense):
                continue                       # the coach's timeout handled this break
            if dead:
                # Stop the bleeding: timeout on an 8-0 run (saving a few for the end).
                for side in self.sides.values():
                    if side.run_against >= 8 and side.timeouts > 2 and self.clock > 120:
                        side.run_against = 0
                        self.call_timeout(side, "stop the run")
                        break
                else:
                    self._dead_ball()
        self.log(None, f"End of {label}")

    def run(self) -> GameSim:
        for _ in self.iter_game():
            pass
        return self.result

    def iter_game(self):
        """The whole game, pausing (yield) after every possession; ``self.result`` is
        set at the end. ``run()`` plays it straight through."""
        tip = self.jump_ball()
        other = self.other(tip)
        for starting in (tip, other, other, tip):
            yield from self._period(starting)
        while self.hc.score["home"] == self.hc.score["away"]:
            yield from self._period(self.jump_ball())
        self.log(None, "Final")
        boxes = [TeamBox(side.team.id, list(side.period_points), self.lines(side), side.possessions)
                 for side in (self.home, self.away)]
        result = GameResult(self.hc.score["home"], self.hc.score["away"], overtimes=max(0, self.period - 4))
        self.result = GameSim(result, BoxScore(*boxes), self.events, [])

    # ------------------------------------------------------------------ a human coach
    def attach_manager(self, side_key: str) -> dict:
        """Let a human coach ``side_key``: queue commands in the returned dict (or with
        the helpers below); they're applied at the next chance in the game."""
        self.manager = {"side": side_key, "timeout": False, "subs": [], "auto_subs": True}
        return self.manager

    def _manager_break(self, dead: bool, next_offense: Side) -> bool:
        """Between possessions: the coach's timeout (on a dead ball or with the ball) and
        substitutions (dead balls). True if a timeout was taken."""
        m = self.manager
        side = self.sides[m["side"]]
        if m["timeout"] and (dead or next_offense is side):
            m["timeout"] = False
            if self.call_timeout(side, "coach's decision"):
                side.run_against = 0
                self._manager_subs()
                return True
        if dead:
            self._manager_subs()
        return False

    def _manager_subs(self) -> None:
        m = self.manager
        if m is None or not m["subs"]:
            return
        side = self.sides[m["side"]]
        changed = False
        for out_id, in_id in m["subs"]:
            out, inn = side.seats.get(out_id), side.seats.get(in_id)
            if (out is None or inn is None or not out.on_court or inn.on_court
                    or self.fouled_out(inn)):
                continue
            self._swap(side, side.lineup.index(out), out, inn, False)
            changed = True
        m["subs"] = []
        if changed:
            self._sync()

    def lines(self, side: Side) -> list[PlayerLine]:
        out = []
        for s in side.roster:
            c = self.hc.box[id(s.agent)]
            line = PlayerLine(s.p.id, starter=s.starter, seconds=s.seconds, plus_minus=s.plus_minus,
                              **{f: int(c[f]) for f in BOX_FIELDS})
            out.append(line)
        return sorted(out, key=lambda l: (not l.starter, -l.seconds))

    # ------------------------------------------------------------------ export
    def to_dict(self, period: Optional[int] = None) -> dict:
        """Viewer data (see viewer.html). ``period`` keeps just that quarter (5 = OT1...)."""
        def team(key):
            side = self.sides[key]
            t = side.team
            return {"name": t.full_name, "abbr": t.abbreviation,
                    "players": [{"num": s.p.jersey_number, "name": s.p.name, "pos": s.p.position.value,
                                 "ca": s.p.current_ability} for s in side.roster]}

        def box(key):
            side = self.sides[key]
            rows = []
            for s in side.roster:
                c = self.hc.box[id(s.agent)]
                rows.append({"name": s.p.name, "min": round(s.seconds / 60, 1), "starter": s.starter,
                             "pts": int(c["pts"]), "pm": s.plus_minus,
                             **{f: int(c[f]) for f in BOX_FIELDS}})
            return rows

        poss = [p for p in self.possessions if period is None or p["period"] == period]
        return {"version": 2, "fullgame": True, "dt": self.hc.t.dt, "home": team("home"), "away": team("away"),
                "possessions": poss,
                "periods": [{"n": i + 1, "home": self.home.period_points[i], "away": self.away.period_points[i]}
                            for i in range(self.period)],
                "final": [self.hc.score["home"], self.hc.score["away"]],
                "box": {"home": box("home"), "away": box("away")}}


def simulate_spatial_game(league: League, home_id: int, away_id: int,
                          rng: Optional[random.Random] = None, **kwargs) -> GameSim:
    """Play a full spatial game and return engine.py's ``GameSim`` (league unchanged)."""
    return FullGame(league, home_id, away_id, rng, **kwargs).run()
