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
from ..systems import offense_style
from .sim import Agent, HalfCourtGame

FULL = C.FULL_LENGTH
# Transition lanes behind the arc (x mirrored to the side): the corner (22 ft is the
# corner three) and the trailer's spot at the top of the arc (~25 ft from the rim).
CORNER_LANE = (22.6, 4.5)
TRAIL_LANE = (6.0, 29.5)
WING_LANE = (19.5, 20.5)    # the ball-side wing stopping at the 45 instead of the corner


@dataclass
class OpenCourtTuning:
    sprint: float = 1.40            # open-court top speed, relative to half-court speed
    dribble_sprint: float = 1.28    # pushing the ball
    brisk: float = 1.0              # ... bringing it up briskly into early offence (SpatialParams.early_offense)
    jog: float = 0.62               # walking it up (an NBA walk-up: ~4-5 s from baseline to half court; was 0.55, then 0.70, then 0.74; README "Shots with room": early offence brings some up briskly)
    push_after_steal: float = 0.92
    push_after_drb: float = 0.39     # was 0.38 (README "Patient offence"), then 0.43 (README "Defence that recovers")
    push_after_score: float = 0.08   # was 0.06
    pace_effect: float = 0.05       # push chance (logit) per point of pace tactics
    secure_drb: tuple = (0.35, 0.75)   # land, turn, find the outlet
    secure_steal: tuple = (0.10, 0.30)
    inbound_delay: tuple = (1.5, 2.8)  # retrieve the ball and step out of bounds
    turn_after_steal: float = 0.35  # extra delay for the team that lost the ball
    turn_after_drb: float = 0.15
    max_seconds: float = 8.0        # hand over to the half court by then whatever happens
    breakaway_distance: float = 7.0    # nobody back: take it this close before handing over
    numbers_distance: float = 9.0      # more attackers than defenders back: attack to here
    attack_distance: float = 30.0      # no advantage: hand over by here at the latest
    walk_up_y: float = 38.0         # walking it up: hand over here
    early_handover_y: float = 45.0  # ... early offence: the half court takes over as he crosses half court
    ahead_margin: float = 9.0       # a teammate this much nearer the rim is "ahead"
    ahead_open: float = 7.0         # ... and needs this much room
    ahead_steal: float = 0.08       # interception chance scale for an ahead pass
    pass_speed: float = 42.0
    trail_shooter: int = 10         # a trailing big this good from three trails to the arc
    # The ball-side wing stops at the wing this often instead of running to the corner
    # (both wings in the corners made half of early-offence threes corner threes; was 0).
    ball_side_wing: float = 0.7
    # ... and the weak-side wing (half of early-offence threes were still corner threes,
    # NBA ~24% of all threes; README "Shots with room"; was 0).
    weak_side_wing: float = 0.7
    # Early offence: after a push, the half court keeps the runners on their lanes
    # (corners, trail spot) instead of re-forming the set.
    early_spots: bool = True
    # Throw-ins after dead balls (DeadBallInbound).
    dead_inbound_delay: tuple = (1.0, 2.2)   # from the spot until the throw: the official hands it over
    inbound_settle: float = 0.6     # at the spot, the receivers get a moment to get open
    reset_speed: float = 0.6        # walking to the inbound spots (relative to half-court speed)
    timeout_reset_speed: float = 0.85   # out of a timeout: onto the floor for the set play
    ball_return_speed: float = 18.0     # the ball from where it went dead to the inbounder
    inbound_hard_cap: float = 10.0  # throw it in by then whatever happens
    inbound_steal: float = 0.06     # interception chance scale for a throw-in through a denial
    inbound_decide: tuple = (1.5, 3.0)  # after the throw-in: seconds to get into the offence
    # Where the defence picks up (feet from the basket it defends; half court is 47).
    # A man or switching defence meets the ball around half court and matches up in its
    # own half; a zone sits back near the top of the key; only a press picks up full court.
    pickup: dict = field(default_factory=lambda: {
        "man": (50.0, 40.0), "switch": (50.0, 40.0), "zone": (36.0, 30.0)})   # (ball, off the ball)
    # Getting back on a walk-up or a dead-ball throw-in (README "Last audit flags"):
    # defenders brake in time to stop at their spot, decelerating at this share of their
    # acceleration limit from ``brake_range`` ft out, as defenders do in the half court
    # (SpatialParams.defender_brake, from 10 ft: too late to stop a full sprint); 0: ease
    # in only, which ran them 3-6 ft past their spot and back. (On a push they sprint at
    # the ball as before.) And nobody sprints to the rim on a walk-up: the deepest man
    # only protects it on a push (``walkup_rim`` False); otherwise he picks up a man like
    # the others, instead of running under the basket and back out again.
    brake: float = 0.8
    brake_range: float = 30.0
    walkup_rim: bool = False
    # ... and on a walk-up a defender keeps the man he picked up unless another is this
    # much nearer (ft): re-picking every 0.3 s swapped men back and forth on the way
    # back. The half court starts with those matchups.
    role_stick: float = 4.0


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
    facing: list = field(default_factory=list)    # per frame: the ten players' facing, degrees (sim.update_facing)
    events: list = field(default_factory=list)
    actions: list = field(default_factory=list)   # [frame, player, action, other] (see HalfCourtGame.act)
    advantage: str = ""                  # "breakaway", "numbers", "none", "walk"


def flip(agent: Agent) -> None:
    """Turn the court around: the previous possession's frame -> the new offence's frame."""
    agent.x, agent.y = -agent.x, FULL - agent.y
    agent.vx, agent.vy = -agent.vx, -agent.vy
    agent.tx, agent.ty = agent.x, agent.y
    if agent.facing is not None:
        agent.facing += math.pi


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
                 clock_stopped: bool = True, turn: bool = True):
        """``clock_stopped``: after a make, the game clock waits for the inbound catch
        (free throws, and field goals late in a period); otherwise it runs from the make.
        The shot clock always starts on the catch. ``turn``: the ball changed hands, so
        turn the court around (False: same offence, same frame)."""
        self.g, self.side, self.how, self.rng, self.t = game, side, how, rng, tuning
        self.offense = game.agents[side]
        self.defense = game.agents["away" if side == "home" else "home"]
        self.clock = clock                     # game clock left in the period at the start
        self.record = game.record
        self.score = dict(score or game.score)
        self.result = OpenCourtResult(pushed=False, seconds=0.0, elapsed=0.0)
        self.now = 0.0
        # When the game clock started (a stopped clock: not until the inbound is caught).
        self.clock_from = 1e9 if (how == "score" and clock_stopped) else 0.0
        self.shot_from = 0.0                   # when the shot clock started (inbound: the catch)
        self.clock_stopped = clock_stopped
        self.flight: Optional[dict] = None
        self.inbound: Optional[dict] = None
        for a in game.everyone:
            if turn:
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
        # Early offence (SpatialParams.early_offense): a walk-up brought up briskly into
        # a quick hitter before the defence has matched up, by tactics (pace).
        self.early = False
        ep = game.p.early_offense
        if not self.push and ep > 0 and how in ("score", "drb", "steal") and self.clock >= 24:
            pace = game.p.pace(game.teams[side].tactics.pace)
            self.early = rng.random() < ep * max(0.3, 1.0 + 0.1 * (pace - 10))
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
        pace = self.g.p.pace(self.g.teams[self.side].tactics.pace)
        logit = math.log(base / (1 - base)) + t.pace_effect * (pace - 10)
        style = offense_style(self.g.teams[self.side].tactics)
        logit += self.g.p.system_tempo * (style.push_after_make if self.how == "score" else style.push)   # the system: run or walk
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
            # Wings fill the corners behind the arc, one big runs to the rim and the
            # other trails: to the top of the arc for a trail three if he can shoot,
            # to the elbow if he can't.
            sides = [-ball_side, ball_side]
            for a, s in zip(sorted(guards, key=lambda a: a.x * -ball_side, reverse=True), sides):
                wing = self.t.ball_side_wing if s == ball_side else self.t.weak_side_wing
                lane = WING_LANE if wing > 0 and self.rng.random() < wing else CORNER_LANE
                lanes[id(a)] = (s * lane[0], lane[1])
            for i, a in enumerate(sorted(bigs, key=lambda a: C.dist_to_rim(a.pos))):
                if i == 0:
                    lanes[id(a)] = (-ball_side * 3.5, 7.0)
                elif a.a("three_point") >= self.t.trail_shooter:
                    lanes[id(a)] = (ball_side * TRAIL_LANE[0], TRAIL_LANE[1])
                else:
                    lanes[id(a)] = (ball_side * 7.0, 20.0)
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
                if self.out_of_bounds(a):
                    self.walk(a, dt, boost)    # the inbounder: move() would pull him onto the floor
                elif a in self.defense and not self.push:   # getting back on a walk-up: brake in time
                    a.move(dt, self.now, boost, 1.0, self.t.brake, self.t.brake_range)
                else:
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

    @staticmethod
    def out_of_bounds(a: Agent) -> bool:
        return abs(a.x) > C.WIDTH / 2 - 0.5 or a.y > FULL - 0.5 or a.y < 0.5

    def walk(self, a: Agent, dt: float, boost: float) -> None:
        """Agent.move without keeping him on the floor (stepping out to inbound, and back in)."""
        dx, dy = a.tx - a.x, a.ty - a.y
        d = math.hypot(dx, dy)
        speed = min(a.max_speed * boost, d * a.ease)
        want = (dx / d * speed, dy / d * speed) if d > 0.05 else (0.0, 0.0)
        a.steer(want, dt)
        a.travelled += math.hypot(a.vx, a.vy) * dt
        a.x += a.vx * dt
        a.y += a.vy * dt

    def speed_for(self, a: Agent) -> float:
        if a is self.holder:
            if self.now < self.ready_at:
                return 0.5
            return self.t.dribble_sprint if self.push else self.walk_speed
        if a in self.defense:
            return self.t.sprint
        return self.t.sprint if self.push else self.walk_speed

    @property
    def walk_speed(self) -> float:
        return self.t.brisk if self.early else self.t.jog

    def update_ball(self) -> None:
        if self.inbound and self.now >= self.inbound["at"]:
            inb, to = self.inbound["from"], self.inbound["to"]
            self.inbound = None
            dur = max(0.3, C.dist(inb.pos, to.pos) / self.t.pass_speed + 0.15)
            self.flight = {"from": inb.pos, "to": to, "end": to.pos, "t0": self.now, "dur": dur,
                           "inbound": True, "passer": inb}
            self.act(inb, "inbound", to)
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
            self.act(thief, "intercept", passer)
            self.say(f"Picked off! {thief.p.name} steps in front of the ahead pass")
            self.result.result = "steal"
            self.result.carrier = thief
            return
        to = f["to"]
        self.holder = to
        self.act(to, "catch", f["passer"])
        if f.get("inbound"):
            self.shot_from = self.now          # the shot clock starts on the catch
            if self.clock_stopped:
                self.clock_from = self.now
            # The inbounder steps back in on his own (walk): placing him in jumped
            # him back to the baseline if he had already stepped in during the throw.
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

    @property
    def press(self) -> bool:
        return self.defense_scheme == "press"

    @property
    def defense_scheme(self) -> str:
        other = "away" if self.side == "home" else "home"
        return self.g.teams[other].tactics.defensive_scheme.value

    def pickup_lines(self) -> tuple[float, float]:
        """(ball, off the ball): how far from their basket defenders come to meet the
        offence (a press: everywhere)."""
        if self.push:                      # a fast break: sprint back and stop the ball wherever it is
            return (FULL + 10.0, FULL + 10.0)
        return self.t.pickup.get(self.defense_scheme, (FULL + 10.0, FULL + 10.0))

    def held_back(self, goal: tuple[float, float], line: float, d: Agent) -> tuple[float, float]:
        """Don't come out past the pick-up line (but a defender already beyond it keeps
        retreating toward it rather than standing still)."""
        if goal[1] <= line:
            return goal
        x = goal[0] * (0.6 if goal[1] > line + 10 else 0.85)       # stay between the ball and the rim
        return (x, line)

    def defense_targets(self) -> None:
        now = self.now
        ball = self.ball_pos()
        ball_line, off_line = self.pickup_lines()
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
                self.aim(d, self.held_back(C.toward(ball, RIM, min(6.0, C.dist_to_rim(ball))), ball_line, d))
            else:
                self.aim(d, self.held_back(C.toward(target.pos, RIM, min(4.0, C.dist_to_rim(target.pos))),
                                           off_line, d))

    def assign_roles(self, ball: tuple[float, float]) -> None:
        """Deepest defender to the rim (on a push), nearest (of the rest) to the ball, the
        others to the most dangerous attackers. On a walk-up a defender keeps the job he
        had unless another is ``role_stick`` ft better placed for it: re-picking from
        scratch every 0.3 s swapped men back and forth as they ran, and every swap was a
        cut across. (On a push it's a scramble: the nearest man, every time.)"""
        stick = 0.0 if self.push else self.t.role_stick
        old = self.roles

        def keeps(d, role) -> float:
            return stick if old.get(id(d)) == role else 0.0

        free = list(self.defense)
        roles: dict[int, tuple] = {}
        if self.push or self.t.walkup_rim:
            rim = min(free, key=lambda d: C.dist_to_rim(d.pos) - keeps(d, ("rim", None)))
            roles[id(rim)] = ("rim", None)
            free.remove(rim)
        stopper = min(free, key=lambda d: C.dist(d.pos, ball) + 0.5 * max(0.0, d.y - ball[1])
                      - keeps(d, ("ball", None)))
        roles[id(stopper)] = ("ball", None)
        free.remove(stopper)
        threats = sorted((a for a in self.offense if a is not self.holder),
                         key=lambda a: C.dist_to_rim(a.pos))
        for a in threats:
            if not free:
                break
            d = min(free, key=lambda d: C.dist(d.pos, a.pos) - keeps(d, ("man", a)))
            roles[id(d)] = ("man", a)
            free.remove(d)
        self.roles = roles

    def handover_guards(self) -> list[int]:
        """The half court's matchups after a walk-up: whoever each defender picked up on
        the way back (the ball stopper takes the ball), the rest by ``matchups``; after a
        push, ``matchups`` (scrambled)."""
        if self.t.role_stick <= 0 or self.push:
            return matchups(self.offense, self.defense)
        guards = [-1] * len(self.offense)
        taken = set()
        for j, d in enumerate(self.defense):
            role = self.roles.get(id(d))
            if role is None:
                continue
            kind, man = role
            if kind == "ball":
                man = self.holder
            if kind == "rim" or man is None or man not in self.offense:
                continue
            i = self.offense.index(man)
            if guards[i] < 0:
                guards[i] = j
                taken.add(j)
        for i in sorted(range(len(self.offense)), key=lambda i: C.dist_to_rim(self.offense[i].pos)):
            if guards[i] < 0:
                free = [j for j in range(len(self.defense)) if j not in taken]
                j = min(free, key=lambda j: C.dist(self.defense[j].pos, self.offense[i].pos))
                guards[i] = j
                taken.add(j)
        return guards

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
            return h.y <= (t.early_handover_y if self.early else t.walk_up_y) and self.hand_over()
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
        self.act(h, "pass", m)
        self.g.stats["kick_outs" if kick else "ahead_passes"] += 1
        self.say(f"{h.p.name} kicks it out to {m.p.name}" if kick else f"{h.p.name} throws it ahead to {m.p.name}")
        self.holder = None

    # ------------------------------------------------------------------ endings
    def hand_over(self) -> bool:
        h = self.holder
        r = self.result
        used = max(0.0, self.now - self.shot_from)
        back, ahead = self.advantage(h)
        if not self.push and self.early:
            mode, decide, r.advantage = "probe", self.rng.uniform(0.1, 0.3), "early"
        elif not self.push:
            mode, decide, r.advantage = "setup", self.rng.uniform(0.4, 1.0), "walk"
        elif back == 0:
            mode, decide, r.advantage = "drive", 0.3, "breakaway"
        elif ahead > back or self.kicked:
            mode, decide, r.advantage = "probe", (0.05 if self.kicked else 0.15), "numbers"
        else:
            mode, decide, r.advantage = "probe", 0.6, "none"
        everyone = self.g.everyone
        spots = {}
        if self.push and self.t.early_spots and r.advantage in ("none", "numbers"):
            # Early offence: spot up where you ran to (behind the arc), don't re-form the set.
            for a in self.offense:
                lane = self.lanes.get(id(a))
                if a is not h and lane is not None and C.is_three(lane):
                    spots[a] = C.clamp(lane)
        r.start = {
            "positions": {a: (a.x, a.y, a.vx, a.vy) for a in everyone},
            "holder": h,
            "shot_clock": 24.0 - used,
            "game_clock": self.game_clock,
            "guards": self.handover_guards(),
            "mode": mode,
            "decide_in": decide,
        }
        if spots:
            r.start["spots"] = spots
        if self.early and not self.push:
            r.start["early"] = True
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
        self.act(h, "heave")
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

    def act(self, agent: Optional[Agent], action: str, other: Optional[Agent] = None) -> None:
        if self.record and agent is not None:
            idx = self.g.index
            self.result.actions.append([len(self.result.frames), idx[id(agent)], action,
                                        -1 if other is None else idx[id(other)]])

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
        self.result.facing.append(g.face_row(self.offense, self.defense, self.holder, (bx, by)))


class DeadBallInbound(OpenCourt):
    """A throw-in after a dead ball: a foul away from the ball, a violation, a ball out
    of bounds or a turnover that stops play, and the advance out of a late timeout.

    Nobody is teleported: the ten players start where play stopped (turned around if
    the ball changed hands), with the game clock stopped. The official gets the ball
    to the nearest player of the team throwing it in, who walks to the spot out of
    bounds (``spot``, in the new offence's frame: x = +-26 on a sideline, y > 94 on the
    far baseline) while his teammates go to their spots and the set defence denies
    them. Once he has it and they've had a moment to get open he throws it in; a
    defender in the passing lane can pick it off (a turnover). The game and shot
    clocks start on the touch. In the frontcourt the half-court engine takes over on
    the catch (offence in "setup", the defence's own matchups); in the backcourt the
    receiver walks it up first, as after a make."""

    def __init__(self, game: HalfCourtGame, side: str, spot: tuple[float, float],
                 ball: tuple[float, float], clock: float, rng: random.Random,
                 tuning: OpenCourtTuning = DEFAULT_OPEN_TUNING, *, turn: bool = True,
                 shot_clock: float = 24.0, after_timeout: bool = False,
                 score: Optional[dict] = None):
        """``ball``: where it went dead (new offence's frame); ``shot_clock``: what the
        shot clock shows on the touch; ``after_timeout``: the late-game advance."""
        self.spot = spot
        self.ball_at = ball
        self.shot_base = shot_clock
        self.after_timeout = after_timeout
        self.baseline = spot[1] > FULL
        self.frontcourt = spot[1] < C.HALF_LENGTH
        self.caught = False
        self.caught_time = 0.0
        self.in_hands = False
        self.arrived: Optional[float] = None
        super().__init__(game, side, "score", None, clock, rng, tuning, push=False, score=score,
                         clock_stopped=True, turn=turn)
        self.how = "dead"
        self.clock_from = 1e9                  # stopped until the touch
        self.shot_from = 1e9
        for d in self.defense:
            d.next_think = rng.uniform(0.0, d.reaction)
            d.ease = 6.0

    # ------------------------------------------------------------------ set-up
    def setup_inbound(self) -> None:
        """Called by OpenCourt.__init__: pick the inbounder and everyone's spots."""
        t, rng = self.t, self.rng
        # The nearest wing or forward takes it out (a guard only if nobody else is near).
        cands = [a for a in self.offense if a.slot != 0]
        inb = min(cands, key=lambda a: C.dist(a.pos, self.spot) - (5.0 if a.slot in (2, 3) else 0.0))
        self.inbounder = inb
        self.holder = inb
        inb.tx, inb.ty = self.spot
        mates = [a for a in self.offense if a is not inb]          # point guard first
        self.targets = {id(a): C.clamp(p, full=True) for a, p in zip(mates, self.receiver_spots())}
        self.ready_at = 1e9                    # nobody runs with it before the catch
        self.throw_after = rng.uniform(*t.dead_inbound_delay)
        self.inbound = None

    def receiver_spots(self) -> list[tuple[float, float]]:
        """Where the four others set up, best ball handler first: one comes back to the
        ball, one goes long, the others space the floor."""
        x, y = self.spot
        s = 1.0 if x > 0 else -1.0
        if self.baseline:
            return [(-s * 9.0, FULL - 10.0), (s * 14.0, FULL - 14.0),
                    (-s * 16.0, FULL - 30.0), (s * 6.0, FULL - 34.0)]
        if self.frontcourt:
            return [(s * 14.0, min(y + 9.0, 42.0)), (s * 18.0, max(y - 9.0, 4.0)),
                    (-s * 16.0, min(max(y, 14.0), 30.0)), (-s * 5.0, 7.0)]
        return [(s * 13.0, min(y + 7.0, FULL - 4.0)), (s * 16.0, y - 12.0),
                (-s * 8.0, y - 16.0), (-s * 12.0, max(y - 30.0, 30.0))]

    def inside_point(self, depth: float) -> tuple[float, float]:
        """``depth`` feet inside the line, in front of the inbound spot."""
        x, y = self.spot
        if self.baseline:
            return x, FULL - depth
        return math.copysign(C.WIDTH / 2 - depth, x), y

    # ------------------------------------------------------------------ run
    def run(self) -> OpenCourtResult:
        dt = self.g.t.dt
        self.record_frame()
        where = ("the frontcourt after the timeout" if self.after_timeout
                 else "the baseline" if self.baseline else "the sideline")
        self.say(f"{self.g.teams[self.side].abbreviation} inbound from {where}")
        while True:
            self.now += dt
            self.update_ball()
            if self.result.result:
                break
            self.offense_targets()
            self.defense_targets()
            for a in self.g.everyone:
                if self.out_of_bounds(a) or (a is self.inbounder and not self.caught):
                    self.walk(a, dt, self.speed_for(a))     # move() would keep him on the floor
                else:
                    a.move(dt, self.now, self.speed_for(a), 1.0,
                           self.t.brake if a in self.defense else 0.0, self.t.brake_range)
            self.g.separate()
            self.record_frame()
            if self.caught and self.flight is None and self.now >= self.ready_at:
                h = self.holder
                if self.frontcourt:
                    if not self.out_of_bounds(self.inbounder) or self.now - self.caught_time > 1.0:
                        self.hand_over()
                        break
                elif self.game_clock <= 0.6:
                    self.heave()
                    break
                elif h.y <= self.t.walk_up_y or self.now - self.caught_time >= self.t.max_seconds:
                    self.hand_over()
                    break
            if self.game_clock <= 0:
                self.result.result = "period"
                self.say("End of the period")
                break
        self.result.seconds = max(0.0, min(self.clock, self.now - self.clock_from))
        self.result.elapsed = self.now
        return self.result

    def speed_for(self, a: Agent) -> float:
        t = self.t
        if not self.caught:
            if self.arrived is not None and a is not self.inbounder:
                return 1.0                     # the throw is coming: get open, stay with him
            return t.timeout_reset_speed if self.after_timeout else t.reset_speed
        if a in self.defense:
            return 0.9
        return super().speed_for(a)

    def update_ball(self) -> None:
        if self.caught:
            return                             # walking it up: no passes in the backcourt
        t, now, inb = self.t, self.now, self.inbounder
        if self.flight is None:
            if not self.in_hands:              # the ball comes back to the inbounder
                step = t.ball_return_speed * self.g.t.dt
                d = C.dist(self.ball_at, inb.pos)
                self.ball_at = inb.pos if d <= step else C.toward(self.ball_at, inb.pos, step)
                self.in_hands = d <= 1.0 + step
            if self.arrived is None and C.dist(inb.pos, self.spot) < 1.0:
                self.arrived = now
                for a in self.offense:         # cut to get open
                    if a is not inb:
                        tx, ty = self.targets[id(a)]
                        self.targets[id(a)] = C.clamp((tx + self.rng.uniform(-2.5, 2.5),
                                                       ty + self.rng.uniform(-2.5, 2.5)), full=True)
            ready = (self.in_hands and self.arrived is not None
                     and now >= max(self.throw_after, self.arrived + t.inbound_settle))
            if ready or now >= t.inbound_hard_cap:
                self.throw_in()
            return
        f = self.flight
        if now - f["t0"] < f["dur"]:
            return
        self.flight = None
        self.shot_from = self.clock_from = now     # both clocks start on the touch
        if f.get("steal") is not None:
            thief = f["steal"]
            self.g.credit(inb, "tov")
            self.g.credit(thief, "stl")
            self.g.stats["tov"] += 1
            self.g.stats["steals"] += 1
            self.g.stats["inbound_steals"] += 1
            self.act(thief, "intercept", inb)
            self.say(f"Stolen! {thief.p.name} picks off the inbound pass")
            self.result.result = "steal"
            self.result.carrier = thief
            return
        to = f["to"]
        self.holder = to
        self.act(to, "catch", inb)
        self.caught, self.caught_time = True, now
        self.ready_at = now + 0.2
        self.last_passer, self.caught_at = inb, now
        self.lanes = self.assign_lanes()
        if self.frontcourt:
            inb.tx, inb.ty = self.inside_point(3.0)
        self.say(f"{inb.p.name} inbounds to {to.p.name}")

    def throw_in(self) -> None:
        """Throw it to the most open man; a defender in the lane may pick it off."""
        t, rng, inb = self.t, self.rng, self.inbounder
        self.in_hands = True
        noise = 1.2 * math.exp(-0.1 * (inb.a("vision") - 10))
        lane_defenders = [d for d in self.defense if C.dist(d.pos, inb.pos) > 4.5]

        def openness(m: Agent) -> float:
            room = min(C.dist(d.pos, m.pos) for d in self.defense)
            lane = min((C.point_segment_distance(d.pos, inb.pos, m.pos) for d in lane_defenders), default=9.0)
            return (min(room, 6.0) + min(lane, 4.0) - 0.05 * C.dist(inb.pos, m.pos)
                    + (1.0 if m.slot == 0 else 0.0) + rng.gauss(0.0, noise))

        to = max((a for a in self.offense if a is not inb), key=openness)
        end = to.pos
        dur = max(0.3, C.dist(inb.pos, end) / t.pass_speed + 0.15)
        self.flight = {"from": inb.pos, "to": to, "end": end, "t0": self.now, "dur": dur,
                       "inbound": True, "passer": inb}
        for d in sorted(lane_defenders, key=lambda d: C.dist(d.pos, inb.pos)):
            lane = C.point_segment_distance(d.pos, inb.pos, end)
            if lane < 2.0:
                p = (t.inbound_steal * (2.0 - lane) / 2.0 * math.exp(0.12 * (d.a("stealing") - 10))
                     * math.exp(-0.08 * (inb.a("passing") - 10)))
                if rng.random() < p:
                    self.flight["steal"] = d
                    self.flight["end"] = d.pos
                    self.flight["dur"] = max(0.2, C.dist(inb.pos, d.pos) / t.pass_speed)
                    break
        self.act(inb, "inbound", to)
        to.tx, to.ty = end                     # stop and show a target
        self.holder = None

    def offense_targets(self) -> None:
        if self.caught:
            super().offense_targets()
            return
        for a in self.offense:
            if self.now < a.next_think:
                continue
            if a is self.inbounder:
                a.tx, a.ty = self.spot         # out of bounds: not clamped
            elif self.flight is not None and self.flight["to"] is a:
                continue
            else:
                self.aim(a, self.targets[id(a)])

    def defense_targets(self) -> None:
        """A set defence, each man on his own (slot) matchup: deny the throw-in, then pick
        up the ball and get between your man and the rim."""
        now = self.now
        ball = self.ball_pos()
        ball_line, off_line = self.pickup_lines()
        for i, d in enumerate(self.defense):
            if now < d.next_think:
                continue
            d.next_think = now + d.reaction * 0.8
            man = self.offense[i]
            if not self.caught and not self.frontcourt and not self.press:
                # A backcourt throw-in against a set (non-pressing) defence: let them
                # throw it in and match up back in your own half.
                line = ball_line if man is self.inbounder else off_line
                goal = self.held_back(C.toward(man.pos, RIM, min(4.0, C.dist_to_rim(man.pos))), line, d)
            elif not self.caught:
                if man is self.inbounder:
                    goal = self.inside_point(3.5)              # arms up in front of the ball
                elif C.dist(man.pos, self.spot) > 30.0:
                    goal = C.toward(man.pos, RIM, min(4.0, C.dist_to_rim(man.pos)))   # too far to deny
                else:
                    goal = C.toward(man.pos, self.inside_point(3.0), 2.2)            # deny the pass
            elif man is self.holder:
                gap = 4.0 + 0.25 * max(0.0, ball[1] - 50.0)     # pick him up by half court
                goal = self.held_back(C.toward(ball, RIM, min(gap, C.dist(ball, RIM))), ball_line, d)
            else:
                far = 3.0 + 0.12 * C.dist(man.pos, ball)
                goal = self.held_back(C.toward(man.pos, RIM, min(far, C.dist_to_rim(man.pos))), off_line, d)
            self.aim(d, goal)

    def hand_over(self) -> bool:
        super().hand_over()
        r = self.result
        r.start["shot_clock"] = self.shot_base - max(0.0, self.now - self.shot_from)
        r.start["guards"] = list(range(len(self.offense)))  # the set defence keeps its matchups
        # Into the offence against a set defence, as in a half-court start: fast-paced teams sooner.
        pace = self.g.p.pace(self.g.teams[self.side].tactics.pace)
        r.start["mode"] = "setup"
        r.start["decide_in"] = self.rng.uniform(*self.t.inbound_decide) * max(0.5, 1 - 0.03 * (pace - 10))
        r.advantage = "inbound"
        return True

    def record_frame(self) -> None:
        if not self.record:
            return
        if not self.caught and self.flight is None and not self.in_hands:
            self._ball_override = (self.ball_at[0], self.ball_at[1], 3.0)
        super().record_frame()
        self._ball_override = None
        row = self.result.frames[-1]
        shot = max(0.0, self.now - self.shot_from)
        row[1] = round(max(0.0, min(24.0, self.shot_base - shot)), 1)

