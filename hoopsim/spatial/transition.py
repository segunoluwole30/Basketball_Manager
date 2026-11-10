"""The open-court phase between half-court possessions (transition).

After a steal, a defensive rebound or (sometimes) a made basket, the ten players
are still where the last possession left them, at the far end of the floor. This
module runs them up the court at ten updates a second, in the new offence's
attacking frame (basket at y = 0, the far baseline at y = 94):

* The team with the ball secures it (a rebounder turns and outlets; a thief is
  already running; after a make, a big inbounds from under the far basket) and
  decides whether to push, by tactics (pace), how the ball was won and whether
  anyone is already ahead of the defence.
* Pushing, the handler attacks his lane, wings sprint wide to the corners, one big
  runs to the rim and the other trails. A team that doesn't push walks it up.
* The defence turns and runs back after a reaction delay (longer for the team that
  just lost the ball): the deepest defender protects the rim, the nearest stops
  the ball, the rest pick up the most dangerous men. A handler with a teammate
  ahead of everyone throws it ahead.
* Once the ball is in the frontcourt the phase hands over to the half-court
  engine with everyone's positions and momentum, the shot clock already running
  and scrambled matchups: a clear lane starts as a drive (fast-break layups),
  an advantage as an early decision (early threes and kick-outs), otherwise the
  half-court offence flows in against a defence that isn't set yet.

Players only move here; shots, passes and fouls in the frontcourt are resolved by
``HalfCourtGame``. The open court itself can end a possession only with an
interception of an ahead pass or with a heave at the period buzzer.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional

from . import court as C
from .court import RIM
from .sim import Agent, HalfCourtGame

FULL = C.FULL_LENGTH


@dataclass
class OpenCourtTuning:
    sprint: float = 1.40            # open-court top speed, relative to half-court speed
    dribble_sprint: float = 1.28    # pushing the ball
    jog: float = 0.80               # walking it up
    push_after_steal: float = 0.92
    push_after_drb: float = 0.38
    push_after_score: float = 0.06
    pace_effect: float = 0.05       # push chance (logit) per point of pace tactics
    secure_drb: tuple = (0.35, 0.75)   # land, turn, find the outlet
    secure_steal: tuple = (0.10, 0.30)
    inbound_delay: tuple = (1.0, 1.8)  # retrieve the ball and step out of bounds (clock stopped)
    turn_after_steal: float = 0.35  # extra delay for the team that lost the ball
    turn_after_drb: float = 0.15
    max_seconds: float = 8.0        # hand over to the half court by then whatever happens
    breakaway_distance: float = 7.0    # nobody back: take it this close before handing over
    numbers_distance: float = 9.0      # more attackers than defenders back: attack to here
    attack_distance: float = 30.0      # no advantage: hand over by here at the latest
    walk_up_y: float = 38.0         # walking it up: hand over here
    ahead_margin: float = 9.0       # a teammate this much nearer the rim is "ahead"
    ahead_open: float = 7.0         # ... and needs this much room
    ahead_steal: float = 0.08       # interception chance scale for an ahead pass
    pass_speed: float = 42.0


DEFAULT_OPEN_TUNING = OpenCourtTuning()


@dataclass
class OpenCourtResult:
    pushed: bool
    seconds: float                       # game-clock seconds used
    elapsed: float                       # real seconds shown (includes a stopped-clock inbound)
    start: Optional[dict] = None         # hand-over options for HalfCourtGame.play_possession
    result: Optional[str] = None         # "steal" or "period" if it ended in the open court
    points: int = 0
    carrier: Optional[Agent] = None      # who has the ball next (after a steal)
    frames: list = field(default_factory=list)
    events: list = field(default_factory=list)
    advantage: str = ""                  # "breakaway", "numbers", "none", "walk"


def flip(agent: Agent) -> None:
    """Turn the court around: the previous possession's frame -> the new offence's frame."""
    agent.x, agent.y = -agent.x, FULL - agent.y
    agent.vx, agent.vy = -agent.vx, -agent.vy
    agent.tx, agent.ty = agent.x, agent.y


def matchups(offense: list[Agent], defense: list[Agent]) -> list[int]:
    """Scrambled matchups: the most dangerous attacker (nearest the rim) is picked up
    by the nearest free defender, and so on. Returns guards[offence slot] = defence slot."""
    guards = [-1] * len(offense)
    free = list(range(len(defense)))
    for i in sorted(range(len(offense)), key=lambda i: C.dist_to_rim(offense[i].pos)):
        j = min(free, key=lambda j: C.dist(defense[j].pos, offense[i].pos))
        guards[i] = j
        free.remove(j)
    return guards


class OpenCourt:
    """One open-court phase. ``how`` is how the ball was won: "steal", "drb" or "score"
    (a made basket or free throw: inbound from under the far basket)."""

    def __init__(self, game: HalfCourtGame, side: str, how: str, carrier: Optional[Agent],
                 clock: float, rng: random.Random, tuning: OpenCourtTuning = DEFAULT_OPEN_TUNING,
                 push: Optional[bool] = None, score: Optional[dict] = None,
                 clock_stopped: bool = True):
        """``clock_stopped``: after a make, the game clock waits for the inbound catch
        (free throws, and field goals late in a period); otherwise it runs from the make.
        The shot clock always starts on the catch."""
        self.g, self.side, self.how, self.rng, self.t = game, side, how, rng, tuning
        self.offense = game.agents[side]
        self.defense = game.agents["away" if side == "home" else "home"]
        self.clock = clock                     # game clock left in the period at the start
        self.record = game.record
        self.score = dict(score or game.score)
        self.result = OpenCourtResult(pushed=False, seconds=0.0, elapsed=0.0)
        self.now = 0.0
        self.clock_from = 0.0                  # when the game clock started
        self.shot_from = 0.0                   # when the shot clock started (inbound: the catch)
        self.clock_stopped = clock_stopped
        self.flight: Optional[dict] = None
        self.inbound: Optional[dict] = None
        for a in game.everyone:
            flip(a)
            a.stuck_until = -1.0
            a.helping = False
            a.ease = 4.0
        if how == "score":
            self.setup_inbound()
        else:
            self.holder = carrier if carrier in self.offense else self.offense[0]
            lo, hi = tuning.secure_steal if how == "steal" else tuning.secure_drb
            self.ready_at = rng.uniform(lo, hi)
        turn = {"steal": tuning.turn_after_steal, "drb": tuning.turn_after_drb}.get(how, 0.0)
        for d in self.defense:
            d.next_think = d.reaction + turn + rng.uniform(0.0, 0.2)
        for a in self.offense:
            a.next_think = a.reaction * 0.5 + rng.uniform(0.0, 0.15)
        self.push = self.decide_push() if push is None else push
        self.result.pushed = self.push
        self.lanes = self.assign_lanes()
        self.roles: dict[int, tuple] = {}
        self.next_roles = 0.0
        self.next_look = 0.0
        self.last_passer: Optional[Agent] = None
        self.caught_at = -9.0
        self.kicked = False

    # ------------------------------------------------------------------ set-up
    def setup_inbound(self) -> None:
        """After a make: the nearest big takes it out under the far basket, the point
        guard comes back for it. The clock starts on the catch."""
        t, rng = self.t, self.rng
        far_rim = (0.0, FULL - RIM[1])
        bigs = sorted(self.offense, key=lambda a: (C.dist(a.pos, far_rim) - (6 if a.slot >= 3 else 0)))
        inbounder = bigs[0]
        receiver = self.offense[0] if inbounder is not self.offense[0] else self.offense[1]
        sx = 1 if rng.random() < 0.5 else -1
        inbounder.place(sx * 3.0, FULL + 1.5)
        receiver.tx, receiver.ty = -sx * 9.0, FULL - 9.0
        self.holder = inbounder
        throw = rng.uniform(*t.inbound_delay)
        self.ready_at = throw + 99.0           # the inbounder doesn't run with it
        self.inbound = {"at": throw, "to": receiver, "from": inbounder}

    def decide_push(self) -> bool:
        t = self.t
        base = {"steal": t.push_after_steal, "drb": t.push_after_drb}.get(self.how, t.push_after_score)
        pace = self.g.teams[self.side].tactics.pace
        logit = math.log(base / (1 - base)) + t.pace_effect * (pace - 10)
        # Already ahead of the defence: anyone would run.
        if self.how != "score":
            h = self.holder
            back = sum(1 for d in self.defense if C.dist_to_rim(d.pos) < C.dist_to_rim(h.pos))
            logit += 0.6 * (2 - back)
        if self.clock < 10:
            logit += 3.0                       # end of the period: go now
        return self.rng.random() < 1 / (1 + math.exp(-logit))

    def assign_lanes(self) -> dict[int, tuple]:
        """Where each non-handler runs: wings to the corners, a big to the rim, a trailer."""
        lanes = {}
        mates = [a for a in self.offense if a is not self.holder]
        guards = [a for a in mates if a.slot <= 2]
        bigs = [a for a in mates if a.slot >= 3]
        ball_side = 1 if self.holder.x >= 0 else -1
        if self.push:
            sides = [-ball_side, ball_side]
            for a, s in zip(sorted(guards, key=lambda a: a.x * -ball_side, reverse=True), sides):
                lanes[id(a)] = (s * 21.0, 6.0)
            for i, a in enumerate(sorted(bigs, key=lambda a: C.dist_to_rim(a.pos))):
                lanes[id(a)] = (-ball_side * 3.5, 7.0) if i == 0 else (ball_side * 7.0, 27.0)
            if len(guards) > 2:
                for a in guards[2:]:
                    lanes[id(a)] = (ball_side * 19.0, 22.0)
        else:
            from .sim import SPOTS_4OUT
            for a in mates:
                sx, sy = SPOTS_4OUT[a.slot]
                lanes[id(a)] = (sx * 0.7, sy + 10.0)
        return lanes

    # ------------------------------------------------------------------ run
    @property
    def game_clock(self) -> float:
        return self.clock - max(0.0, self.now - self.clock_from)

    def run(self) -> OpenCourtResult:
        t, dt = self.t, self.g.t.dt
        self.record_frame()
        self.say(f"{self.g.teams[self.side].abbreviation} "
                 + ("push it up the floor" if self.push else "bring it up"))
        while True:
            self.now += dt
            self.update_ball()
            if self.result.result:
                break
            self.offense_targets()
            self.defense_targets()
            for a in self.g.everyone:
                boost = self.speed_for(a)
                a.move(dt, self.now, boost)
            self.g.separate()
            self.record_frame()
            if self.flight is None and self.holder is not None and self.now >= self.ready_at:
                if self.game_clock <= 0.6:
                    self.heave()
                    break
                if self.read():
                    break
            if self.game_clock <= 0:
                self.result.result = "period"
                self.say("End of the period")
                break
        self.result.seconds = max(0.0, min(self.clock, self.now - self.clock_from))
        self.result.elapsed = self.now
        return self.result

    def speed_for(self, a: Agent) -> float:
        if a is self.holder:
            if self.now < self.ready_at:
                return 0.5
            return self.t.dribble_sprint if self.push else self.t.jog
        if a in self.defense:
            return self.t.sprint
        return self.t.sprint if self.push else self.t.jog

    def update_ball(self) -> None:
        if self.inbound and self.now >= self.inbound["at"]:
            inb, to = self.inbound["from"], self.inbound["to"]
            self.inbound = None
            dur = max(0.3, C.dist(inb.pos, to.pos) / self.t.pass_speed + 0.15)
            self.flight = {"from": inb.pos, "to": to, "end": to.pos, "t0": self.now, "dur": dur,
                           "inbound": True, "passer": inb}
            self.holder = None
        f = self.flight
        if f is None or self.now - f["t0"] < f["dur"]:
            return
        self.flight = None
        if f.get("steal") is not None:
            thief, passer = f["steal"], f["passer"]
            self.g.credit(passer, "tov")
            self.g.credit(thief, "stl")
            self.g.stats["tov"] += 1
            self.g.stats["steals"] += 1
            self.g.stats["open_court_steals"] += 1
            self.say(f"Picked off! {thief.p.name} steps in front of the ahead pass")
            self.result.result = "steal"
            self.result.carrier = thief
            return
        to = f["to"]
        self.holder = to
        if f.get("inbound"):
            self.shot_from = self.now          # the shot clock starts on the catch
            if self.clock_stopped:
                self.clock_from = self.now
            inb = f["passer"]
            inb.place(inb.x, FULL - 1.0)       # step back in
            self.ready_at = self.now + 0.2
            self.lanes = self.assign_lanes()
        else:
            self.ready_at = self.now + 0.1
            self.last_passer, self.caught_at = f["passer"], self.now
            self.kicked = bool(f.get("kick"))

    def offense_targets(self) -> None:
        h = self.holder
        for a in self.offense:
            if self.now < a.next_think:
                continue
            if a is h:
                if self.now < self.ready_at:
                    continue
                if self.push:
                    side = 1 if a.x >= 0 else -1
                    goal = (side * 2.0, 6.0)
                else:
                    goal = (a.x * 0.4, self.t.walk_up_y - 6.0)
                self.aim(a, goal)
            elif self.inbound and a is self.inbound["to"]:
                continue
            elif self.flight and self.flight.get("to") is a:
                continue
            else:
                lane = self.lanes.get(id(a))
                if lane is None:
                    lane = (a.x, 20.0)
                # Outlet: a guard comes back toward a rebounder who hasn't turned yet.
                if (self.how == "drb" and self.now < self.ready_at and a.slot <= 1
                        and h is not None and h.slot >= 2):
                    lane = (math.copysign(14.0, a.x or 1.0), max(30.0, h.y - 16.0))
                self.aim(a, lane)

    def aim(self, a: Agent, p: tuple[float, float]) -> None:
        a.tx, a.ty = C.clamp(p, full=True)

    def defense_targets(self) -> None:
        now = self.now
        ball = self.ball_pos()
        if now >= self.next_roles:
            self.next_roles = now + 0.3
            self.assign_roles(ball)
        for d in self.defense:
            if now < d.next_think:
                continue
            role = self.roles.get(id(d))
            if role is None:
                continue
            kind, target = role
            if kind == "rim":
                bx = max(-4.0, min(4.0, ball[0] * 0.2))
                self.aim(d, (bx, 7.0))
            elif kind == "ball":
                self.aim(d, C.toward(ball, RIM, min(6.0, C.dist_to_rim(ball))))
            else:
                self.aim(d, C.toward(target.pos, RIM, min(4.0, C.dist_to_rim(target.pos))))

    def assign_roles(self, ball: tuple[float, float]) -> None:
        """Deepest defender to the rim, nearest (of the rest) to the ball, the others to
        the most dangerous attackers."""
        free = list(self.defense)
        roles: dict[int, tuple] = {}
        rim = min(free, key=lambda d: C.dist_to_rim(d.pos))
        roles[id(rim)] = ("rim", None)
        free.remove(rim)
        stopper = min(free, key=lambda d: C.dist(d.pos, ball) + 0.5 * max(0.0, d.y - ball[1]))
        roles[id(stopper)] = ("ball", None)
        free.remove(stopper)
        threats = sorted((a for a in self.offense if a is not self.holder),
                         key=lambda a: C.dist_to_rim(a.pos))
        for a in threats:
            if not free:
                break
            d = min(free, key=lambda d: C.dist(d.pos, a.pos))
            roles[id(d)] = ("man", a)
            free.remove(d)
        self.roles = roles

    def ball_pos(self) -> tuple[float, float]:
        if self.flight:
            f = self.flight
            k = min(1.0, (self.now - f["t0"]) / f["dur"])
            return (f["from"][0] + (f["end"][0] - f["from"][0]) * k,
                    f["from"][1] + (f["end"][1] - f["from"][1]) * k)
        return self.holder.pos if self.holder else RIM

    # ------------------------------------------------------------------ reads
    def advantage(self, h: Agent) -> tuple[int, int]:
        """(defenders back, attackers level or ahead): who's between the ball and the rim
        (within a couple of feet of the handler's distance) on each side."""
        d_h = C.dist_to_rim(h.pos)
        back = sum(1 for d in self.defense if C.dist_to_rim(d.pos) < d_h + 1.0)
        ahead = sum(1 for a in self.offense if C.dist_to_rim(a.pos) < d_h + 3.0)
        return back, ahead

    def read(self) -> bool:
        """The handler's open-court read. Returns True once the phase is over (handed over).

        Nobody back: take it to the rim. More attackers than defenders back: attack,
        and hit the man the defence leaves (ahead, a drop-off, or a kick-out for an
        early three). No advantage: into the frontcourt and hand over."""
        h, t = self.holder, self.t
        d_rim = C.dist_to_rim(h.pos)
        if self.now >= t.max_seconds:
            return self.hand_over()
        if not self.push:
            return h.y <= t.walk_up_y and self.hand_over()
        if self.kicked and self.now - self.caught_at < 0.3:
            return self.hand_over()            # catch and shoot (or attack the closeout)
        back, ahead = self.advantage(h)
        if back == 0:
            return d_rim <= t.breakaway_distance and self.hand_over()
        if self.now >= self.next_look:
            self.next_look = self.now + 0.2
            if self.look_pass(h):
                return False
        if ahead > back:
            return d_rim <= t.numbers_distance and self.hand_over()
        return (h.y < C.HALF_LENGTH - 2 or d_rim <= t.attack_distance) and self.hand_over()

    def look_pass(self, h: Agent) -> bool:
        """Throw ahead to a man nearer the rim with room, drop it off to a cutter when the
        last defender commits, or kick out to an open shooter."""
        t = self.t
        d_h = C.dist_to_rim(h.pos)
        pressure = min(C.dist(d.pos, h.pos) for d in self.defense)
        best, best_v, best_kick = None, 0.0, False
        for m in self.offense:
            if m is h or C.dist(m.pos, h.pos) > 55:
                continue
            lead = self.lead_point(h, m)
            d_m = C.dist_to_rim(lead)
            room = min(C.dist(d.pos, lead) for d in self.defense)
            kick = False
            if d_m < d_h - t.ahead_margin and room >= t.ahead_open:
                v = 3.0 - d_m / 100                      # ahead of the play
            elif d_m < 9 and room >= 5 and pressure <= 6 and d_h < 22:
                v = 2.5                                  # the defender committed: drop it off
            elif (C.is_three(lead) and d_m < 26 and lead[1] < C.HALF_LENGTH and room >= 9
                  and pressure <= 7 and d_h < 24):
                v, kick = 1.5 + 0.05 * (m.a("three_point") - 10), True   # kick-out for an early three
            else:
                continue
            # A defender in the passing lane: good passers see him.
            if any(C.point_segment_distance(d.pos, h.pos, lead) < 3.0 for d in self.defense) and \
                    self.rng.random() > 0.3 * math.exp(-0.12 * (h.a("vision") - 10)):
                continue
            if v > best_v:
                best, best_v, best_kick = m, v, kick
        if best is None:
            return False
        self.throw(h, best, kick=best_kick)
        return True

    def lead_point(self, h: Agent, m: Agent) -> tuple[float, float]:
        dur = C.dist(h.pos, m.pos) / self.t.pass_speed + 0.15
        return C.clamp((m.x + m.vx * dur, m.y + m.vy * dur), full=True)

    def throw(self, h: Agent, m: Agent, kick: bool = False) -> None:
        dur = C.dist(h.pos, m.pos) / self.t.pass_speed + 0.15
        end = self.lead_point(h, m)
        self.flight = {"from": h.pos, "to": m, "end": end, "t0": self.now, "dur": dur, "passer": h,
                       "kick": kick}
        m.tx, m.ty = end
        for d in self.defense:
            lane = C.point_segment_distance(d.pos, h.pos, end)
            if lane < 2.5 and C.dist(d.pos, h.pos) > 2.0:
                p = self.t.ahead_steal * (2.5 - lane) / 2.5 * math.exp(0.12 * (d.a("stealing") - 10))
                if self.rng.random() < p:
                    self.flight["steal"] = d
                    self.flight["end"] = d.pos
                    self.flight["dur"] = max(0.2, C.dist(h.pos, d.pos) / self.t.pass_speed)
                    break
        self.g.stats["kick_outs" if kick else "ahead_passes"] += 1
        self.say(f"{h.p.name} kicks it out to {m.p.name}" if kick else f"{h.p.name} throws it ahead to {m.p.name}")
        self.holder = None

    # ------------------------------------------------------------------ endings
    def hand_over(self) -> bool:
        h = self.holder
        r = self.result
        used = max(0.0, self.now - self.shot_from)
        back, ahead = self.advantage(h)
        if not self.push:
            mode, decide, r.advantage = "setup", self.rng.uniform(0.8, 1.6), "walk"
        elif back == 0:
            mode, decide, r.advantage = "drive", 0.3, "breakaway"
        elif ahead > back or self.kicked:
            mode, decide, r.advantage = "probe", (0.05 if self.kicked else 0.15), "numbers"
        else:
            mode, decide, r.advantage = "probe", 0.6, "none"
        everyone = self.g.everyone
        r.start = {
            "positions": {a: (a.x, a.y, a.vx, a.vy) for a in everyone},
            "holder": h,
            "shot_clock": 24.0 - used,
            "game_clock": self.game_clock,
            "guards": matchups(self.offense, self.defense),
            "mode": mode,
            "decide_in": decide,
        }
        if self.last_passer is not None and self.now - self.caught_at < 2.0:
            r.start["passer"] = self.last_passer               # for the assist (and catch and shoot)
            r.start["caught"] = self.caught_at - self.now
        return True

    def heave(self) -> None:
        """Out of time in the open court: let it fly."""
        h = self.holder
        d = C.dist_to_rim(h.pos)
        p = max(0.01, min(0.10, 0.10 - 0.003 * (d - 28)))
        made = self.rng.random() < p
        self.g.credit(h, "fga")
        self.g.credit(h, "tpa")
        self.g.stats["heaves"] += 1
        self.say(f"{h.p.name} heaves it from {d:.0f} feet...")
        start, dur = h.pos, 0.6 + d / 45
        steps = int(dur / self.g.t.dt)
        self.holder = None
        for k in range(1, steps + 1):
            self.now += self.g.t.dt
            f = k / steps
            self._ball_override = (start[0] + (RIM[0] - start[0]) * f, start[1] + (RIM[1] - start[1]) * f,
                                   7.0 + 3.0 * f + (6 + d * 0.15) * 4 * f * (1 - f))
            self.record_frame()
        self._ball_override = None
        if made:
            self.g.credit(h, "fgm")
            self.g.credit(h, "tpm")
            self.g.credit(h, "pts", 3)
            self.result.points = 3
            self.score[self.side] += 3
            self.say("GOOD! At the buzzer!")
        else:
            self.say("No good")
        self.result.result = "period"
        self.now = self.clock_from + self.clock   # the buzzer went as it left his hand

    # ------------------------------------------------------------------ recording
    _ball_override = None

    def say(self, text: str) -> None:
        if self.record:
            self.result.events.append([max(0, len(self.result.frames) - 1), text,
                                       self.score["home"], self.score["away"]])

    def record_frame(self) -> None:
        if not self.record:
            return
        g = self.g
        if self._ball_override:
            bx, by, bz = self._ball_override
            holder = -1
        elif self.flight:
            bx, by = self.ball_pos()
            bz, holder = 5.0, -1
        elif self.holder is not None:
            bx, by, bz = self.holder.x + 0.8, self.holder.y, 3.0
            holder = g.index[id(self.holder)]
        else:
            bx, by, bz, holder = 0.0, FULL - RIM[1], 3.0, -1
        used = max(0.0, self.now - self.clock_from)
        shot = max(0.0, self.now - self.shot_from)
        row = [round(self.now, 1), round(max(0.0, min(24.0, 24.0 - shot)), 1),
               round(bx * 10), round(by * 10), round(bz * 10), holder]
        for a in g.everyone:
            row += [round(a.x * 10), round(a.y * 10)]
        row.append(round(max(0.0, self.clock - used), 1))
        self.result.frames.append(row)

