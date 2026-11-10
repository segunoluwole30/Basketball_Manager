"""Spatial half-court simulation (prototype).

Ten players move on a half court at 10 updates a second. Outcomes come from
where they are and what they decide:

* Offence spreads to spacing spots. The ball handler re-evaluates every few
  tenths of a second: shoot, drive, pass, call a pick-and-roll, or hold. Each
  option is scored by expected points given *current positions*, then one is
  chosen with a little randomness (smarter players choose better).
* Defence plays man-to-man: on-ball defenders stay between their man and the
  rim, off-ball defenders sag toward the paint the further they are from the
  ball. When someone drives, a help defender rotates, leaving his man open.
  Defenders react on a delay set by basketball IQ, which is what creates open
  shots after a pass.
* A shot's make chance comes from distance, the shooter's rating and how close
  (and how tall, and how good) the nearest defender is. Blocks, shooting fouls,
  turnovers, steals on passes, and rebounds (with box-outs) are resolved the
  same way.

Only half-court possessions for now: no transition, no substitutions.
"""
from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from ..lineup import plan_rotation
from ..models import League, Player
from . import court as C
from .court import CLOSE_ZONE, MID_ZONE, RIM, RIM_ZONE, THREE_ZONE
from .params import DEFAULT_PARAMS, SpatialParams
from .values import DecisionModel, default_model, hold_curve

ZONES = (RIM_ZONE, CLOSE_ZONE, MID_ZONE, THREE_ZONE)


@dataclass
class SpatialTuning:
    dt: float = 0.1
    # Make chance for an average shooter, wide open / fully contested reduction.
    make_open: dict = field(default_factory=lambda: {RIM_ZONE: 0.78, CLOSE_ZONE: 0.52, MID_ZONE: 0.47, THREE_ZONE: 0.365})
    contest_max: dict = field(default_factory=lambda: {RIM_ZONE: 0.20, CLOSE_ZONE: 0.15, MID_ZONE: 0.13, THREE_ZONE: 0.10})
    skill_per_point: float = 0.009
    three_skill_per_point: float = 0.013     # shooting ability matters most from deep
    shooting_foul: dict = field(default_factory=lambda: {RIM_ZONE: 0.42, CLOSE_ZONE: 0.20, MID_ZONE: 0.06, THREE_ZONE: 0.025})
    block_base: dict = field(default_factory=lambda: {RIM_ZONE: 0.14, CLOSE_ZONE: 0.09, MID_ZONE: 0.02, THREE_ZONE: 0.01})
    free_throw: float = 0.775
    # dribble_turnover_per_sec, pass_steal_scale, errant_pass, defensive_rebound_edge
    # and the help trigger distance live in SpatialParams (params.py).
    pass_speed: float = 40.0                  # ft/s
    charge_chance: float = 0.25               # a help defender set in the driver's path
    charge_on_ball: float = 0.015             # the man guarding him, beaten to the spot and set
    travel_per_sec: float = 0.012             # driving or backing down: travels, carries, push-offs
    # Jump shots right off a pass beat jump shots off the dribble (NBA: ~37% vs ~34% from three).
    catch_shoot_bonus: float = 0.02
    lob_bonus: float = 0.08                   # alley-oops are close to dunks
    cut_rate: float = 0.008                   # chance per tick an off-ball player cuts (scaled)
    post_strength_effect: float = 0.01        # make chance per point of strength edge in the post
    # Style: points of option value per tendency point away from 10 (a 20 tendency = +0.12).
    tendency_weight: float = 0.025
    off_dribble_penalty: float = 0.035
    # Jump shots take time to get off (catch to release / gather and rise); the
    # contest is judged where the defenders are at release, not at the decision.
    release_catch: float = 0.30
    release_dribble: float = 0.36
    # Help defenders swipe at the ball when a driver comes through their reach.
    dig_per_sec: float = 0.35


DEFAULT_TUNING = SpatialTuning()

# Spacing spots by lineup slot (PG, SG, SF, PF, C): four out, one in.
# Perimeter spots sit just outside the arc (23.75 ft; 22 ft in the corners).
SPOTS_4OUT = [(0.0, 30.0), (19.0, 22.0), (-19.0, 22.0), (-22.6, 4.5), (6.0, 7.0)]
FIVE_OUT_C = (22.6, 4.5)
POST_BLOCK = (6.5, 8.0)        # a big who can score inside sets up on the low block
DUNKER = (5.0, 4.0)            # one who can't waits under the rim for dump-offs
LANE_SPOTS = [(-7.5, 7.5), (7.5, 7.5), (-7.5, 11.0), (7.5, 11.0), (-7.5, 14.0), (7.5, 14.0)]


class Agent:
    def __init__(self, player: Player, side: str, slot: int):
        self.p = player
        self.side = side                       # "home" / "away"
        self.slot = slot
        a = player.attributes
        self.max_speed = 13.0 + 0.55 * (a.speed - 10)        # ft/s in half-court action
        self.accel = 22.0 + 1.2 * (a.speed - 10)
        self.reaction = max(0.15, 0.32 - 0.012 * (a.basketball_iq - 10))
        self.height = player.physique.height_cm / 30.48      # feet
        self.x = self.y = 0.0
        self.vx = self.vy = 0.0
        self.tx = self.ty = 0.0
        self.next_think = 0.0
        self.stuck_until = -1.0
        self.helping = False
        self.spot = (0.0, 0.0)
        # How hard to brake approaching a target: offence eases into spots,
        # defenders stay on the gas to keep up with their man.
        self.ease = 3.0
        self.cut_until = -1.0
        self.last_cut = -9.0

    @property
    def pos(self) -> tuple[float, float]:
        return self.x, self.y

    def a(self, name: str) -> int:
        return getattr(self.p.attributes, name)

    def place(self, x: float, y: float) -> None:
        self.x, self.y = x, y
        self.tx, self.ty = x, y
        self.vx = self.vy = 0.0

    def target(self, p: tuple[float, float]) -> None:
        self.tx, self.ty = C.clamp(p)

    def move(self, dt: float, now: float, speed_boost: float = 1.0) -> None:
        if now < self.stuck_until:             # caught on a screen
            self.vx *= 0.4
            self.vy *= 0.4
        else:
            dx, dy = self.tx - self.x, self.ty - self.y
            d = math.hypot(dx, dy)
            top = self.max_speed * speed_boost
            speed = min(top, d * self.ease)    # ease in to the target
            want = (dx / d * speed, dy / d * speed) if d > 0.05 else (0.0, 0.0)
            sx, sy = want[0] - self.vx, want[1] - self.vy
            s = math.hypot(sx, sy)
            limit = self.accel * dt
            if s > limit:
                sx, sy = sx / s * limit, sy / s * limit
            self.vx += sx
            self.vy += sy
        self.x, self.y = C.clamp((self.x + self.vx * dt, self.y + self.vy * dt))


@dataclass
class Flight:
    kind: str                  # "pass", "shot", "rebound"
    start: tuple[float, float]
    end: tuple[float, float]
    t0: float
    duration: float
    apex: float = 0.0          # extra height at the top of the arc (shots)
    z0: float = 4.0
    z1: float = 4.0
    data: dict = field(default_factory=dict)

    def position(self, now: float) -> tuple[float, float, float]:
        f = max(0.0, min(1.0, (now - self.t0) / self.duration))
        x = self.start[0] + (self.end[0] - self.start[0]) * f
        y = self.start[1] + (self.end[1] - self.start[1]) * f
        z = self.z0 + (self.z1 - self.z0) * f + self.apex * 4 * f * (1 - f)
        return x, y, z

    def done(self, now: float) -> bool:
        return now - self.t0 >= self.duration


def _softmax_choice(rng: random.Random, options: list[tuple[float, object]], temperature: float):
    best = max(v for v, _ in options)
    weights = [math.exp((v - best) / temperature) for v, _ in options]
    return rng.choices([o for _, o in options], weights)[0]


class HalfCourtGame:
    """Alternate half-court possessions between two teams' starting fives."""

    def __init__(self, league: League, home_id: int, away_id: int, rng: random.Random,
                 tuning: SpatialTuning = DEFAULT_TUNING, record: bool = True,
                 model: Optional[DecisionModel] = None, explore: float = 0.0,
                 recorder: Optional[dict] = None, params: Optional[SpatialParams] = None):
        """``model`` values the ball handler's options (default: learned values if
        trained, else hand-written). ``explore`` is the chance of a random option and
        ``recorder`` collects (features, possession points) per option, for training."""
        self.league, self.rng, self.t, self.record = league, rng, tuning, record
        self.p = params or DEFAULT_PARAMS      # calibrated defence and shot-model settings
        self.model = model or default_model()
        self.explore = explore
        self.recorder = recorder
        self.teams = {"home": league.teams[home_id], "away": league.teams[away_id]}
        self.agents = {side: self._starters(side) for side in ("home", "away")}
        self.everyone = self.agents["home"] + self.agents["away"]
        self.index = {id(a): i for i, a in enumerate(self.everyone)}
        self.score = {"home": 0, "away": 0}
        self.clock = 0.0                       # running time across possessions
        self.possessions: list[dict] = []      # recorded frames + events
        self.stats = Counter()
        self.box = {id(a): Counter() for a in self.everyone}

    def _starters(self, side: str) -> list[Agent]:
        team = self.teams[side]
        roster = [p for p in self.league.roster(team.id) if p.is_available]
        starters = team.tactics.starters if len(team.tactics.starters) == 5 and all(
            self.league.players[pid].is_available for pid in team.tactics.starters) else plan_rotation(roster, 9)[0]
        return [Agent(self.league.players[pid], side, slot) for slot, pid in enumerate(starters)]

    # ------------------------------------------------------------------ public
    def play(self, possessions: int) -> None:
        side = "home"
        for _ in range(possessions):
            self.play_possession(side)
            side = "away" if side == "home" else "home"

    # ------------------------------------------------------------------ helpers
    def say(self, text: str) -> None:
        if self.record:
            # Live score: include points scored so far this possession.
            live = dict(self.score)
            live[self.side] += self.points
            self.events.append([len(self.frames), text, live["home"], live["away"]])

    def credit(self, agent: Agent, stat: str, n: int = 1) -> None:
        self.box[id(agent)][stat] += n

    def guarded_by(self, off: Agent) -> Agent:
        return self.defense[self.guards[self.offense.index(off)]]

    def man_of(self, d: Agent) -> Agent:
        return self.offense[self.guards.index(self.defense.index(d))]

    def nearest_defender(self, p: tuple[float, float]) -> tuple[Agent, float]:
        best = min(self.defense, key=lambda d: C.dist(d.pos, p))
        return best, C.dist(best.pos, p)

    # ------------------------------------------------------------------ shot model
    def make_prob(self, shooter: Agent, p: tuple[float, float], contest_d: Optional[float] = None,
                  defender: Optional[Agent] = None, moving: bool = False,
                  catch: Optional[bool] = None, post: bool = False, lob: bool = False) -> tuple[float, str]:
        """``catch``: True for a catch-and-shoot, False off the dribble, None if unknown."""
        t = self.t
        z = C.zone(p)
        skill = {RIM_ZONE: "finishing", CLOSE_ZONE: "close_range", MID_ZONE: "mid_range",
                 THREE_ZONE: "three_point"}[z]
        per_point = t.three_skill_per_point if z == THREE_ZONE else t.skill_per_point
        prob = t.make_open[z] + per_point * (shooter.a(skill) - 10)
        prob += self.p.rim_make_adj if z == RIM_ZONE else self.p.three_make_adj if z == THREE_ZONE else 0.0
        prob += 0.003 * (shooter.a("composure") - 10)
        if defender is None:
            defender, contest_d = self.nearest_defender(p)
        level = max(0.0, min(1.0, (6.0 - contest_d) / 5.0))     # 1 = in his face, 0 = 6+ ft away
        d_skill = defender.a("interior_defense" if z in (RIM_ZONE, CLOSE_ZONE) else "perimeter_defense")
        quality = 1 + 0.03 * (d_skill - 10) + 0.06 * (defender.height - shooter.height)
        scale = self.p.contest_inside if z in (RIM_ZONE, CLOSE_ZONE) else self.p.contest_outside
        prob -= t.contest_max[z] * scale * level * max(0.4, quality)
        d_rim = C.dist_to_rim(p)
        if d_rim > 25:
            prob -= 0.015 * (d_rim - 25)                         # deep threes
        if moving:
            prob -= 0.03
        if catch is not None and z in (MID_ZONE, THREE_ZONE):
            prob += t.catch_shoot_bonus if catch else -t.off_dribble_penalty
        if post:
            prob += t.post_strength_effect * (shooter.a("strength") - defender.a("strength"))
        if lob:
            prob += t.lob_bonus + 0.01 * (shooter.a("vertical") - 10)
        return max(0.02, min(0.95, prob)), z

    def shot_value(self, shooter: Agent, p=None, contest_d=None, defender=None, moving=False,
                   catch: Optional[bool] = None, post: bool = False, lob: bool = False) -> float:
        p = p or shooter.pos
        prob, z = self.make_prob(shooter, p, contest_d, defender, moving, catch, post, lob)
        points = 3 if z == THREE_ZONE else 2
        return prob * points

    # ------------------------------------------------------------------ possession
    def play_possession(self, side: str) -> None:
        rng, t = self.rng, self.t
        self.offense = self.agents[side]
        self.defense = self.agents["away" if side == "home" else "home"]
        self.side = side
        self.guards = list(range(5))           # guards[offense slot] = defense slot
        self.frames, self.events = [], []
        self.start_score = dict(self.score)
        self.now = 0.0
        self.shot_clock = 24.0 - rng.uniform(3.5, 6.0)   # time used bringing it up
        self.flight: Optional[Flight] = None
        self.holder: Optional[Agent] = self.offense[0]
        self.mode = "setup"                    # setup, probe, drive, pass, shot, rebound, ft, over
        self.mode_since = 0.0
        self.pnr: Optional[dict] = None
        self.pnr_used = 0
        self.last_passer: Optional[Agent] = None
        self.catch_time = -9.0
        self.orb_time = -9.0
        # Fast-paced teams get into their offence sooner.
        pace = self.teams[side].tactics.pace
        self.next_decision = rng.uniform(1.5, 3.0) * max(0.5, 1 - 0.03 * (pace - 10))
        self.passes = 0
        self.decisions: list[tuple[str, list[float]]] = []
        self.result: Optional[str] = None
        self.points = 0

        mirror = rng.random() < 0.5
        focus = self.teams[side].tactics.offensive_focus.value
        five_out_shooting = {"perimeter": 10, "inside": 15}.get(focus, 12)
        five_out = self.offense[4].a("three_point") >= five_out_shooting
        spots = list(SPOTS_4OUT)
        if five_out:
            spots[4] = FIVE_OUT_C
        else:
            spots[4] = POST_BLOCK if self.can_post(self.offense[4]) else DUNKER
        if focus == "inside" and self.can_post(self.offense[3]):
            spots[3] = (-POST_BLOCK[0], POST_BLOCK[1])          # both bigs inside: three out, two in
        for i, a in enumerate(self.offense):
            sx, sy = spots[i]
            a.spot = (-sx if mirror else sx, sy)
            a.place(a.spot[0] * 0.6 + rng.uniform(-3, 3), min(44.0, a.spot[1] + 14 + rng.uniform(-2, 2)))
            a.next_think = 0.0
            a.helping = False
            a.stuck_until = -1.0
            a.cut_until, a.last_cut = -1.0, -9.0
        self.offense[0].place(rng.uniform(-4, 4), 38.0)
        self.cutter: Optional[Agent] = None
        self.post: Optional[dict] = None
        self.catch_source: Optional[str] = None
        self.closing: set = set()              # defenders closing out on a catch
        for i, d in enumerate(self.defense):
            man = self.offense[i]
            gx, gy = C.toward(man.pos, RIM, 4.0)
            d.place(gx, gy)
            d.ease = 6.0
            d.next_think = rng.uniform(0, d.reaction * self.p.reaction_scale)
            d.helping = False
            d.stuck_until = -1.0
        self.stats["possessions"] += 1
        self.say(f"{self.teams[side].abbreviation} bring it up: {self.holder.p.name}")

        steps = 0
        while self.result is None and steps < 600:
            self.step()
            steps += 1
        if self.result is None:
            self.result = "stalled"
        if self.recorder is not None:
            for kind, feats in self.decisions:
                self.recorder.setdefault(kind, []).append((feats, float(self.points)))
        self.stats["seconds"] += self.now
        self.stats[f"end_{self.result}"] += 1
        self.score[side] += self.points
        self.stats["points"] += self.points
        self.clock += self.now + 2.0
        if self.record:
            self.possessions.append({
                "offense": side, "start_score": [self.start_score["home"], self.start_score["away"]],
                "end_score": [self.score["home"], self.score["away"]],
                "frames": self.frames, "events": self.events, "result": self.result,
            })

    def end(self, result: str, points: int = 0) -> None:
        self.result = result
        self.points += points

    # ------------------------------------------------------------------ one tick
    def step(self) -> None:
        dt = self.t.dt
        self.now += dt
        if self.mode != "ft":
            self.shot_clock -= dt
        self.offense_targets()
        self.defense_targets()
        if self.flight:
            self.advance_flight()
        elif self.holder is not None and self.mode in ("setup", "probe", "drive", "post", "pullup"):
            self.handler_logic()
        elif self.mode == "release" and self.now >= self.release["at"] - 1e-9:
            r = self.release
            self.mode = r["mode"]
            self.take_shot(self.holder, r["moving"], released=r["catch"])
        boost = 1.08 if self.mode == "drive" else 0.35 if self.mode == "post" else 1.0
        for a in self.everyone:
            a.move(dt, self.now, boost if a is self.holder else
                   self.p.closeout_speed if a in self.closing else 1.0)
        self.separate()
        if self.result is None and self.holder is not None and self.mode in ("probe", "drive", "setup", "post", "pullup") \
                and self.shot_clock <= 0:
            self.credit(self.holder, "tov")
            self.stats["tov"] += 1
            self.say("Shot clock violation!")
            self.end("tov")
        self.record_frame()

    def separate(self) -> None:
        people = self.everyone
        for i in range(len(people)):
            a = people[i]
            for j in range(i + 1, len(people)):
                b = people[j]
                dx, dy = a.x - b.x, a.y - b.y
                d2 = dx * dx + dy * dy
                if d2 < 2.56 and d2 > 1e-9:             # 1.6 ft personal space
                    d = math.sqrt(d2)
                    push = (1.6 - d) / 2
                    a.x += dx / d * push
                    a.y += dy / d * push
                    b.x -= dx / d * push
                    b.y -= dy / d * push

    def record_frame(self) -> None:
        if not self.record:
            return
        if self.flight:
            bx, by, bz = self.flight.position(self.now)
            holder = -1
        elif self.holder is not None:
            bx, by, bz = self.holder.x + 0.8, self.holder.y, 3.0
            holder = self.index[id(self.holder)]
        else:
            bx, by, bz, holder = RIM[0], RIM[1], 10.0, -1
        row = [round(self.now, 1), round(max(0.0, self.shot_clock), 1),
               round(bx * 10), round(by * 10), round(bz * 10), holder]
        for a in self.everyone:
            row += [round(a.x * 10), round(a.y * 10)]
        self.frames.append(row)

    # ------------------------------------------------------------------ movement goals
    def offense_targets(self) -> None:
        h = self.holder
        for a in self.offense:
            if a is h:
                continue
            if self.mode in ("rebound",):
                continue                                         # handled in advance_flight
            if self.mode == "ft":
                continue
            if self.pnr and a is self.pnr["screener"]:
                if not self.pnr["set"]:
                    a.target(self.pnr["point"])
                elif self.now > self.pnr["set_time"] + 0.15:
                    side = 1 if self.pnr["point"][0] >= 0 else -1
                    a.target((side * 2.0, 5.5))                  # roll hard to the rim
                continue
            if self.cutter is a:
                if self.now > a.cut_until:
                    self.cutter = None                           # nothing came: clear back out
                    a.target(a.spot)
                else:
                    a.target((math.copysign(2.5, a.spot[0] or 1), 5.0))
                continue
            d = self.guarded_by(a)
            if d.helping and a.a("three_point") < 12 and a.spot[1] < 22:
                a.target((math.copysign(3.5, a.x or 1), 5.5))    # cut behind the help
            else:
                a.target(a.spot)
                self.maybe_cut(a, d)
        if h is not None and self.mode == "setup":
            h.target(h.spot)
        elif h is not None and self.mode == "probe" and self.now - self.mode_since > 1.0:
            # Probe: work around your spot, sometimes attacking the elbow or short wing.
            if self.rng.random() < 0.06:
                elbow = max(0.15, min(0.8, 0.25 + 0.04 * (h.p.tendencies.mid_range - 10)))
                if self.rng.random() < elbow:
                    angle = math.radians(self.rng.uniform(25, 155))
                    r = self.rng.uniform(15, 20)
                    h.target((RIM[0] + r * math.cos(angle), RIM[1] + r * math.sin(angle)))
                else:
                    h.target((h.spot[0] + self.rng.uniform(-4, 4), h.spot[1] + self.rng.uniform(-3, 3)))

    def can_post(self, a: Agent) -> bool:
        """A big who can (and likes to) score on the block."""
        return (a.slot >= 3 and (a.a("close_range") + a.a("strength")) / 2 >= 11
                and a.p.tendencies.post_up >= 8)

    @property
    def scheme(self) -> str:
        """The defending team's scheme: man, switch, zone or press."""
        defending = "away" if self.side == "home" else "home"
        return self.teams[defending].tactics.defensive_scheme.value

    def style_bias(self, h: Agent, kind: str, action: tuple) -> float:
        """How much this player (and his coach's tactics) prefer an option, in points.
        Added to the learned value when choosing; it changes choices, not outcomes."""
        tend = h.p.tendencies
        tac = self.teams[self.side].tactics
        focus = tac.offensive_focus.value
        k = self.t.tendency_weight
        if kind == "shoot":
            zone = action[1]
            if zone == THREE_ZONE:
                return (k * (tend.three_point - 10) + 0.008 * (tac.three_point_rate - 10)
                        + {"perimeter": 0.03, "inside": -0.03}.get(focus, 0.0))
            if zone == MID_ZONE:
                return k * (tend.mid_range - 10)
            return 0.02 if focus == "inside" else 0.0
        if kind in ("drive", "continue"):
            return k * (tend.drive - 10) + (0.02 if focus == "inside" else 0.0)
        if kind == "post":
            return k * (tend.post_up - 10) + {"inside": 0.04, "perimeter": -0.03}.get(focus, 0.0)
        if kind == "pass":
            mate = action[1]
            feed_post = 0.03 if focus == "inside" and self.can_post(mate) and C.dist_to_rim(mate.pos) < 11 else 0.0
            return 0.8 * k * (tend.passing - 10) + feed_post
        if kind == "hold":
            return -0.012 * (tac.pace - 10)                     # fast teams take earlier shots
        return 0.0

    def maybe_cut(self, a: Agent, d: Agent) -> None:
        """Off-ball players cut to the rim when their man loses them. Smart players,
        poor shooters and players whose defender is sagging cut most."""
        if self.cutter is not None or self.mode not in ("probe", "drive") or self.now - a.last_cut < 4.0:
            return
        if C.dist_to_rim(a.pos) < 10 or (self.pnr and a is self.pnr["screener"]):
            return
        gap = C.dist(a.pos, d.pos)
        rate = (self.t.cut_rate * math.exp(0.08 * (a.p.tendencies.cutting - 10))
                * (1 + 0.06 * (a.a("basketball_iq") - 10)) * (2.0 if gap > 6 else 1.0)
                * (1.6 if a.a("three_point") < 11 else 0.7))
        if self.rng.random() < rate:
            self.cutter = a
            a.cut_until, a.last_cut = self.now + 2.0, self.now
            self.stats["cuts"] += 1
            self.say(f"{a.p.name} cuts to the basket")

    def paint_threat(self) -> Optional[Agent]:
        """An offensive player near the rim whose defender has lost him (cutter, roller)."""
        best = None
        for a in self.offense:
            if a is self.holder:
                continue
            dr = C.dist_to_rim(a.pos)
            if dr > self.p.tag_zone or C.dist(self.guarded_by(a).pos, a.pos) < self.p.tag_lost:
                continue
            if best is None or dr < C.dist_to_rim(best.pos):
                best = a
        return best

    def choose_tagger(self, threat: Agent, exclude: Optional[Agent]) -> Optional[Agent]:
        on_ball = self.guarded_by(self.holder) if self.holder is not None else None
        own = self.guarded_by(threat)
        cands = [d for d in self.defense if d not in (on_ball, own, exclude)]
        if not cands:
            return None
        def cost(d):
            return (C.dist(d.pos, threat.pos) - (2 if d.slot >= 3 else 0)
                    + (self.p.help_shooter_penalty if self.man_of(d).a("three_point") >= 14 else 0))
        best = min(cands, key=cost)
        return best if C.dist(best.pos, threat.pos) < self.p.tag_range else None

    def choose_doubler(self, h: Agent) -> Optional[Agent]:
        on = self.guarded_by(h)
        cands = [d for d in self.defense if d is not on]
        if not cands:
            return None
        # Come from the man who hurts you least if left open.
        return min(cands, key=lambda d: C.dist(d.pos, h.pos) + 0.8 * self.man_of(d).a("three_point"))

    def defense_targets(self) -> None:
        now = self.now
        if self.mode == "ft":
            return
        ball = self.flight.position(now)[:2] if self.flight else (self.holder.pos if self.holder else RIM)
        driving = self.mode == "drive" and self.holder is not None
        helper = None
        help_at = self.p.help_trigger + (4.0 if self.scheme == "zone" else 0.0)
        if driving and C.dist_to_rim(self.holder.pos) < help_at:
            helper = self.choose_helper()
        # A perimeter defender stunts at a drive (a step toward the ball), which
        # leaves his own man for a kick-out.
        stunter = None
        if driving and 6 < C.dist_to_rim(self.holder.pos) < 20:
            stunter = self.choose_stunter(exclude=helper)
        tagger = threat = None
        if self.mode in ("probe", "drive", "pass", "post"):
            threat = self.paint_threat()
            if threat is not None:
                tagger = self.choose_tagger(threat, exclude=helper)
        doubler = self.post["double"] if self.mode == "post" and self.post else None
        for d in self.defense:
            d.helping = d is helper or d is tagger or d is doubler or d is stunter
            if now < d.next_think:
                continue
            d.next_think = now + d.reaction * self.p.reaction_scale
            self.closing.discard(d)
            if self.mode == "rebound":
                continue
            man = self.man_of(d)
            if self.mode == "release" and self.holder is not None and (
                    man is self.holder or C.dist(d.pos, self.holder.pos) < 8):
                # Get a hand up: lunge in as far as the shooting motion allows, but
                # contests end at hand's length, not body to body.
                gap = C.dist(d.pos, self.holder.pos)
                d.target(C.toward(self.holder.pos, d.pos, max(self.p.contest_reach, gap - 2.5)))
            elif d is stunter:
                d.target(C.toward(self.holder.pos, d.pos, self.p.stunt_distance))
            elif d is helper:
                # Meet the driver between him and the rim.
                d_rim = C.dist_to_rim(self.holder.pos)
                d.target(C.toward(RIM, self.holder.pos, max(3.0, min(self.p.help_depth, d_rim - 2.5))))
            elif d is doubler and self.holder is not None:
                d.target(C.toward(self.holder.pos, (0.0, 22.0), 2.0))   # double from the top side
            elif d is tagger and threat is not None:
                d.target(C.toward(RIM, threat.pos, min(3.0, C.dist_to_rim(threat.pos))))   # tag the cutter
            elif self.pnr and d is self.guarded_by(self.pnr["screener"]) \
                    and self.holder is not None and not self.pnr.get("switch"):
                # Drop coverage: don't follow the screener out. Wait at the nail
                # (between ball and rim), then sink to protect the rim once he comes off it.
                depth = self.p.drop_depth if self.pnr["set"] else 14.0
                d.target(C.toward(RIM, self.holder.pos, min(depth, C.dist_to_rim(self.holder.pos) - 2)))
            elif self.flight and self.flight.kind == "pass" and self.flight.data.get("to") is man:
                d.target(C.toward(self.flight.end, RIM, self.closeout_gap(man)))   # close out on the catch
                self.closing.add(d)
            elif man is self.holder:
                # About 3.5 ft off a perimeter ball handler: closer on shooters,
                # more room for non-shooters (and far from the basket).
                deep = C.dist_to_rim(man.pos)
                gap = self.closeout_gap(man) + 0.5 if deep >= 22 else 3.0
                if deep >= 27:
                    gap += 2.0
                gap += {"zone": 1.0, "press": -0.8}.get(self.scheme, 0.0)   # zones sit back, presses get up
                # Anticipate: play where he's going, not where he was.
                lead = 0.45 if self.mode == "drive" else 0.2
                ahead = C.clamp((man.x + man.vx * lead, man.y + man.vy * lead))
                d.target(C.toward(ahead, RIM, gap))
            else:
                pass_dist = C.dist(man.pos, ball)
                # One pass away: stay close. Far side: sink into the paint to help.
                sag = (self.p.sag_near if pass_dist < 22 else
                       min(self.p.sag_max, self.p.sag_near + 0.02 + self.p.sag_slope * (pass_dist - 22)))
                if self.scheme == "zone":
                    sag = max(sag, 0.22)                         # a zone packs the paint
                if man.a("three_point") >= 13:
                    sag *= self.p.shooter_sag                    # shade toward shooters
                mx, my = man.pos
                tx = mx + (RIM[0] - mx) * sag + (ball[0] - mx) * 0.08
                ty = my + (RIM[1] - my) * sag + (ball[1] - my) * 0.08
                d.target((tx, ty))

    def closeout_gap(self, man: Agent) -> float:
        """Where a closeout stops: about 3 ft on shooters (hand up, still able to
        slide with a drive), more room for non-shooters so they can't drive past."""
        if C.dist_to_rim(man.pos) < 20:
            return 2.5
        p = self.p
        return max(p.closeout_min, min(p.closeout_max, p.closeout_base - p.closeout_slope * (man.a("three_point") - 11)))

    def choose_stunter(self, exclude: Optional[Agent]) -> Optional[Agent]:
        """The nearest perimeter defender to a drive (not the on-ball or rim help)."""
        on_ball = self.guarded_by(self.holder)
        cands = [d for d in self.defense if d not in (on_ball, exclude)
                 and C.dist_to_rim(self.man_of(d).pos) >= 18]
        if not cands:
            return None
        best = min(cands, key=lambda d: C.dist(d.pos, self.holder.pos))
        return best if C.dist(best.pos, self.holder.pos) < self.p.stunt_range else None

    def choose_helper(self) -> Optional[Agent]:
        h = self.holder
        on_ball = self.guarded_by(h)
        candidates = [d for d in self.defense if d is not on_ball]
        if not candidates:
            return None
        # The rim protector: whoever is closest to the basket, bigs preferred,
        # but don't leave a great shooter.
        def cost(d):
            man = self.man_of(d)
            return (C.dist_to_rim(d.pos) - (3 if d.slot >= 3 else 0)
                    + (self.p.help_shooter_penalty if man.a("three_point") >= 14 else 0))
        return min(candidates, key=cost)

    # ------------------------------------------------------------------ decisions
    def handler_logic(self) -> None:
        h, now, rng = self.holder, self.now, self.rng
        if self.mode == "setup":
            # Bring it up and let the offence get into its spots before running anything.
            if now >= self.next_decision:
                self.mode, self.mode_since = "probe", now
                self.next_decision = now
            return
        if self.dribble_turnover(h):
            return
        if self.mode in ("drive", "post") and self.travel_check(h):
            return
        if self.mode == "pullup":
            if C.dist(h.pos, self.pull_point) < 1.5 or now - self.mode_since > 1.0:
                self.take_shot(h, moving=False)
            return
        if self.mode == "post":
            d_rim = C.dist_to_rim(h.pos)
            h.target(C.toward(RIM, h.pos, max(4.0, d_rim - 3.0)))        # back him down
            if (self.post["double"] is None and now - self.post["since"] > 0.6
                    and self.post_threat(h) >= 2.0):
                doubler = self.choose_doubler(h)
                if doubler is not None:
                    self.post["double"] = doubler
                    self.stats["doubles"] += 1
                    self.say(f"Double team! {doubler.p.name} comes to help")
            if now - self.post["since"] > 4.5:
                self.mode, self.mode_since, self.post = "probe", now, None   # nothing there: reset
                h.spot = h.pos
        if self.mode == "drive":
            if self.dig(h):
                return
            d_rim = C.dist_to_rim(h.pos)
            if d_rim < 4.5:
                self.take_shot(h, moving=False)
                return
            if self.charge_check(h):
                return
            if self.pnr and self.pnr.get("attack") and d_rim <= 15.5:
                self.pnr["attack"] = False
                side = 1 if h.x >= 0 else -1
                h.target((side * 2.5, 6.0))                      # at the nail: keep going if it's there
            if now - self.mode_since > 3.5 or (now - self.mode_since > 1.0 and math.hypot(h.vx, h.vy) < 2.0):
                h.spot = C.clamp(C.toward(RIM, h.pos, max(18.0, d_rim)))
                h.target(h.spot)
                self.mode, self.mode_since = "probe", now
                self.next_decision = now + 0.3
                return
        if self.pnr and not self.pnr["set"]:
            scr = self.pnr["screener"]
            if C.dist(scr.pos, self.pnr["point"]) < 1.5:
                self.set_screen()
            elif now - self.pnr["called"] > 4.0:
                self.pnr = None                      # screen never came
        if now < self.next_decision:
            return
        self.decide(h)

    def decide(self, h: Agent) -> None:
        """Score every option with the decision model, then pick one (smarter players
        pick the best more reliably). Decisions are recorded for training."""
        now, rng, sc = self.now, self.rng, self.shot_clock
        options: list[tuple[str, tuple, list[float]]] = []      # (kind, action, features)
        d_rim = C.dist_to_rim(h.pos)
        moving = self.mode == "drive"
        if d_rim < 30:
            # Judge the contest by where the nearest defender will be at release.
            spot = self.finish_point(h)
            closer, dd = self.nearest_defender(spot)
            catch = self.just_caught(moving)
            rt = self.release_time(h, catch) if C.zone(spot) in (MID_ZONE, THREE_ZONE) else 0.0
            predicted = max(0.0, dd - self.closing_speed(closer, h) * (0.35 + rt))
            ep = self.shot_value(h, p=spot, contest_d=predicted, defender=closer, moving=moving,
                                 catch=catch, post=self.mode == "post")
            options.append(("shoot", ("shoot", C.zone(spot)), [1.0, ep, float(C.is_three(spot)), float(moving),
                                                   max(0.0, 6.0 - sc) / 6.0]))
        if self.mode == "post":
            options.append(("post", ("post",), self.post_features(h)))
        elif self.mode == "probe":
            pull = self.pull_up_option(h)
            if pull is not None:
                options.append(pull)
            options.append(("drive", ("drive",), self.drive_features(h)))
            if self.pnr is None and self.pnr_used < 3 and d_rim > 15:
                bigs = [a for a in (self.offense[3], self.offense[4]) if a is not h]
                scr = min(bigs, key=lambda a: C.dist(a.pos, h.pos))   # the nearer big sets it
                options.append(("pnr", ("pnr", scr), [1.0, hold_curve(sc - 4), h.a("ball_handling") - 10.0,
                                                      scr.a("strength") - 10.0]))
            options.append(("hold", ("hold",), [1.0, hold_curve(sc), sc / 24.0]))
        else:
            options.append(("continue", ("continue",), self.drive_features(h)))
        for mate in self.offense:
            if mate is not h:
                feats = self.pass_features(h, mate)
                if feats is not None:
                    options.append(("pass", ("pass", mate), feats))

        if self.explore and rng.random() < self.explore:
            kind, action, feats = rng.choice(options)              # try something (training only)
        else:
            temperature = 0.03 + 0.006 * (20 - h.a("basketball_iq"))   # smarter = closer to the best option
            scored = [(self.model.value(k, f) + self.style_bias(h, k, a), (k, a, f)) for k, a, f in options]
            kind, action, feats = _softmax_choice(rng, scored, temperature)
        self.decisions.append((kind, feats))
        self.stats[f"choice_{kind}"] += 1
        # Coming off a screen the handler reads the big and the roller every beat.
        reading = self.mode == "drive" and self.pnr is not None and self.pnr["set"]
        self.next_decision = now + h.reaction * (rng.uniform(0.5, 0.9) if reading else rng.uniform(1.2, 2.2))
        if kind == "shoot" and action[0] == "pullup":
            self.mode, self.mode_since = "pullup", now
            self.pull_point = action[2]
            self.catch_time = -9.0                         # it's off the dribble now
            h.target(self.pull_point)
            self.say(f"{h.p.name} takes two dribbles in")
        elif kind == "shoot":
            self.take_shot(h, moving=moving)
        elif kind == "drive":
            self.start_drive(h)
        elif kind in ("continue", "post"):
            if kind == "post":
                self.next_decision = now + rng.uniform(0.4, 0.7)
        elif kind == "pnr":
            self.call_pnr(h, action[1])
        elif kind == "pass":
            self.throw_pass(h, action[1])
        else:
            self.next_decision = now + rng.uniform(0.6, 1.2)

    @staticmethod
    def closing_speed(defender: Agent, target: Agent) -> float:
        """How fast the defender is coming at the target (ft/s, 0 if not)."""
        dx, dy = target.x - defender.x, target.y - defender.y
        d = math.hypot(dx, dy) or 1.0
        return max(0.0, (defender.vx * dx + defender.vy * dy) / d)

    def pull_up_option(self, h: Agent) -> Optional[tuple]:
        """One or two hard dribbles in from the arc into a mid-range jumper."""
        d_rim = C.dist_to_rim(h.pos)
        if not 21.0 <= d_rim <= 29.0:
            return None
        point = C.clamp(C.toward(RIM, h.pos, 16.5))
        if C.zone(point) != MID_ZONE:
            return None
        on = self.guarded_by(h)
        # His man follows him in; he gains a little separation on the stop.
        contest = max(0.0, C.dist(on.pos, h.pos) - 0.5 + 0.02 * (h.a("speed") - on.a("speed")) * 10)
        ep = self.shot_value(h, p=point, contest_d=contest, defender=on, moving=True, catch=False)
        feats = [1.0, ep, 0.0, 1.0, max(0.0, 6.0 - self.shot_clock) / 6.0]
        return ("shoot", ("pullup", MID_ZONE, point), feats)

    def just_caught(self, moving: bool) -> bool:
        """Shooting straight off a pass (no dribble move in between)?"""
        return not moving and self.now - self.catch_time < 1.2

    def drive_features(self, h: Agent) -> list[float]:
        on = self.guarded_by(h)
        gap = C.dist(h.pos, on.pos)
        edge = ((h.a("speed") + h.a("ball_handling")) - (on.a("perimeter_defense") + on.a("speed"))) / 2
        crowd = sum(1 for d in self.defense if d is not on and C.dist_to_rim(d.pos) < 9)
        beaten = C.dist_to_rim(on.pos) > C.dist_to_rim(h.pos) + 1 or self.now < on.stuck_until
        closeout = self.closing_speed(on, h) > 8 and gap < 8                 # flying at you: go past him
        return [1.0, hold_curve(self.shot_clock - 2.5), edge, max(-1.0, min(3.0, gap - 3)), float(crowd),
                float(beaten), float(closeout), C.dist_to_rim(h.pos) / 30.0]

    def pass_target(self, h: Agent, mate: Agent) -> tuple[tuple[float, float], float, bool]:
        """Where to throw it (leading a moving receiver), how long it flies, and whether it's a lob."""
        flight = C.dist(h.pos, mate.pos) / self.t.pass_speed + 0.15
        moving = math.hypot(mate.vx, mate.vy) > 4
        target = C.clamp((mate.x + mate.vx * flight, mate.y + mate.vy * flight)) if moving else mate.pos
        # Lobs go to an athletic cutter or roller heading to the rim.
        rolling = self.pnr is not None and self.pnr["set"] and mate is self.pnr["screener"]
        # Cutters need a long, athletic target; a roller just needs to get over the
        # drop big (a short lob), which tall or bouncy rollers can catch.
        if rolling:
            lob = (moving and C.dist_to_rim(target) < 9 and C.dist(h.pos, target) >= 7
                   and (mate.height >= 6.75 or mate.a("vertical") >= 13))
        else:
            lob = (mate is self.cutter and moving and C.dist_to_rim(target) < 7
                   and C.dist(h.pos, target) >= 12 and mate.height >= 6.75 and mate.a("vertical") >= 12)
        if lob:
            flight = C.dist(h.pos, target) / (self.t.pass_speed * 0.6) + 0.35
        return target, flight, lob

    def finish_point(self, h: Agent) -> tuple[float, float]:
        """A catch near the rim is finished with a gather step toward it."""
        d = C.dist_to_rim(h.pos)
        # A roller or cutter catching on the move goes straight up from further out.
        reach = 11.0 if self.catch_source in ("roll", "cut") else 8.0
        if self.mode != "post" and self.just_caught(False) and d < reach:
            return C.toward(RIM, h.pos, max(1.5, d - (5.0 if reach > 8 else 3.0)))
        return h.pos

    def post_threat(self, h: Agent) -> float:
        on = self.guarded_by(h)
        return ((h.a("strength") + h.a("close_range")) - (on.a("strength") + on.a("interior_defense"))) / 2

    def post_features(self, h: Agent) -> list[float]:
        on = self.guarded_by(h)
        return [1.0, hold_curve(self.shot_clock - 1.5), h.a("strength") - on.a("strength"),
                h.a("close_range") - on.a("interior_defense"), C.dist_to_rim(h.pos) / 10.0,
                float(bool(self.post and self.post["double"]))]

    def pass_features(self, h: Agent, mate: Agent) -> Optional[list[float]]:
        d = C.dist(h.pos, mate.pos)
        if d > 40:
            return None
        target, flight, lob = self.pass_target(h, mate)
        diving = mate is self.cutter or (self.pnr is not None and self.pnr["set"] and mate is self.pnr["screener"])
        risk = 0.0
        for de in self.defense:
            if lob:     # over the top: only someone at the landing spot can get it
                land = C.dist(de.pos, target)
                if land < 2.5:
                    risk += 0.3 * (2.5 - land) / 2.5 * (1 + 0.1 * (de.height - mate.height) * 10 / 3)
            else:
                lane = C.point_segment_distance(de.pos, h.pos, target)
                if lane < 2.5 and C.dist(de.pos, h.pos) > 1.5:
                    risk += 0.25 * (2.5 - lane) / 2.5
        risk += self.errant_chance(h, d, lob)
        guard = self.guarded_by(mate)
        contest_after = max(0.0, C.dist(guard.pos, target) - guard.max_speed * self.p.closeout_speed
                            * max(0.0, flight - guard.reaction * self.p.reaction_scale))
        d_target = C.dist_to_rim(target)
        if lob:
            catch_shoot = self.shot_value(mate, p=target, contest_d=contest_after, defender=guard, lob=True)
        elif d_target < (11.0 if diving else 8.0):
            finish = C.toward(RIM, target, max(1.5, d_target - (5.0 if diving else 3.0)))
            catch_shoot = self.shot_value(mate, p=finish, contest_d=contest_after, defender=guard)
        else:
            catch_shoot = self.shot_value(mate, p=target, contest_d=contest_after, defender=guard, catch=True)
        if d_target > 30:
            catch_shoot = 0.0
        clock = hold_curve(self.shot_clock - flight)
        # Moving the ball keeps the offence alive and makes the defence rotate.
        keep_going = clock + 0.04 + 0.01 * (mate.a("ball_handling") - 10)
        best = max(catch_shoot, keep_going) * (1 - min(0.9, risk))
        back = float(mate is self.last_passer and self.now - self.catch_time < 1.5)
        return [1.0, catch_shoot, clock, risk, back, best, float(d_target < 8), float(lob)]

    # ------------------------------------------------------------------ actions
    def dribble_turnover(self, h: Agent) -> bool:
        on = self.guarded_by(h)
        dd = C.dist(h.pos, on.pos)
        if dd >= 3.5 or self.now < on.stuck_until:
            return False
        rate = (self.p.dribble_turnover_per_sec
                * math.exp(0.15 * (on.a("stealing") - 10) - 0.15 * (h.a("ball_handling") - 10))
                * (3.5 - dd) / 2.5 * (1.6 if self.scheme == "press" else 1.0))
        if self.rng.random() < rate * self.t.dt:
            self.credit(h, "tov")
            self.stats["tov"] += 1
            if self.rng.random() < 0.55:
                self.credit(on, "stl")
                self.stats["steals"] += 1
                self.say(f"{on.p.name} strips {h.p.name}!")
            else:
                self.say(f"{h.p.name} loses it out of bounds")
            self.end("tov")
            return True
        return False

    def dig(self, h: Agent) -> bool:
        """Driving through traffic: any help defender within reach can poke the ball loose."""
        on = self.guarded_by(h)
        for d in self.defense:
            if d is on or self.now < d.stuck_until:
                continue
            dd = C.dist(d.pos, h.pos)
            if dd >= 3.0:
                continue
            rate = (self.t.dig_per_sec * math.exp(0.15 * (d.a("stealing") - 10) - 0.15 * (h.a("ball_handling") - 10))
                    * (3.0 - dd) / 1.5)
            if self.rng.random() < rate * self.t.dt:
                self.credit(h, "tov")
                self.stats["tov"] += 1
                self.stats["digs"] += 1
                if self.rng.random() < 0.7:
                    self.credit(d, "stl")
                    self.stats["steals"] += 1
                    self.say(f"{d.p.name} digs down and pokes it away from {h.p.name}!")
                else:
                    self.say(f"{d.p.name} gets a hand on it: out of bounds off {h.p.name}")
                self.end("tov")
                return True
        return False

    def charge_check(self, h: Agent) -> bool:
        """A defender with his feet set in the driver's path can take a charge: a help
        defender who rotated over, or the on-ball defender who beat him to the spot.
        One chance per defender per drive."""
        on = self.guarded_by(h)
        for d in self.defense:
            if d in self.charge_tried or C.dist(d.pos, h.pos) >= 2.0 or math.hypot(d.vx, d.vy) >= 2.5:
                continue
            if d is on:
                if C.dist_to_rim(d.pos) > C.dist_to_rim(h.pos) - 0.5:
                    continue                                      # not in front of him
                chance = self.t.charge_on_ball
            elif d.helping:
                chance = self.t.charge_chance
            else:
                continue
            self.charge_tried.append(d)
            if self.rng.random() < chance:
                self.credit(h, "tov")
                self.credit(h, "pf")
                self.stats["tov"] += 1
                self.stats["charges"] += 1
                self.stats["charges_on_ball" if d is on else "charges_help"] += 1
                self.say(f"Charge! {d.p.name} takes it from {h.p.name}")
                self.end("tov")
                return True
        return False

    def travel_check(self, h: Agent) -> bool:
        """Driving or backing down under pressure: travels, carries and offensive fouls."""
        rate = self.t.travel_per_sec * math.exp(-0.15 * (h.a("ball_handling") - 10))
        if self.mode == "post":
            on = self.guarded_by(h)
            rate *= math.exp(0.1 * (on.a("strength") - h.a("strength")))
        if self.rng.random() < rate * self.t.dt:
            self.credit(h, "tov")
            self.stats["tov"] += 1
            self.stats["travels"] += 1
            self.say(f"{h.p.name} travels" if self.mode == "drive" else f"Offensive foul on {h.p.name}")
            self.end("tov")
            return True
        return False

    def start_drive(self, h: Agent) -> None:
        on = self.guarded_by(h)
        # Attack the side away from the defender.
        side = 1 if on.x < h.x else -1
        h.target((side * 2.5, 6.0))
        self.mode, self.mode_since = "drive", self.now
        self.charge_tried: list[Agent] = []
        self.stats["drives"] += 1
        self.say(f"{h.p.name} drives {'right' if side > 0 else 'left'}")
        if self.scheme == "press" and self.now >= on.stuck_until and self.rng.random() < 0.35:
            on.stuck_until = self.now + 0.3                      # pressing up, he was caught leaning
            self.say(f"{on.p.name} overplays and gets beaten")
        self.next_decision = self.now + 0.4

    def call_pnr(self, h: Agent, screener: Agent) -> None:
        on = self.guarded_by(h)
        side = 1 if h.x <= 0 else -1                  # screen to send him toward the middle
        perp = (-(RIM[1] - h.y), RIM[0] - h.x)
        n = math.hypot(*perp) or 1.0
        point = (on.x + perp[0] / n * 2.0 * side, on.y + perp[1] / n * 2.0 * side)
        self.pnr = {"screener": screener, "point": C.clamp(point), "set": False, "called": self.now,
                    "set_time": 0.0, "switch": False}
        self.pnr_used += 1
        self.stats["pnr"] += 1
        self.say(f"{screener.p.name} comes up to set a screen")
        self.next_decision = self.now + 4.5             # wait for the screen

    def set_screen(self) -> None:
        h, scr = self.holder, self.pnr["screener"]
        on = self.guarded_by(h)
        scr_d = self.guarded_by(scr)
        self.pnr["set"], self.pnr["set_time"] = True, self.now
        scheme = self.teams["home" if self.side == "away" else "away"].tactics.defensive_scheme.value
        if scheme == "switch":
            i, j = self.offense.index(h), self.offense.index(scr)
            self.guards[i], self.guards[j] = self.guards[j], self.guards[i]
            self.pnr["switch"] = True
            self.say(f"They switch: {scr_d.p.name} picks up {h.p.name}")
        else:
            hold = 0.5 + 0.06 * (scr.a("strength") - on.a("strength")) + 0.03 * (scr.a("basketball_iq") - 10)
            on.stuck_until = self.now + max(0.2, min(1.4, hold))
            self.say(f"{h.p.name} comes off the screen")
        self.start_drive(h)
        # Come off downhill toward the free-throw line and read the big: pull up if
        # he drops, hit the roller if he steps up, keep going if there's a lane.
        h.target(C.toward(RIM, h.pos, min(15.0, C.dist_to_rim(h.pos))))
        self.pnr["attack"] = True
        self.next_decision = self.now + 0.25

    def errant_chance(self, h: Agent, d: float, lob: bool) -> float:
        """Chance a pass is thrown away or fumbled: a defender's hands in the passing
        window, the length of the pass and the passer's skill."""
        on = self.guarded_by(h)
        gap = C.dist(on.pos, h.pos)
        pressure = 0.0 if self.now < on.stuck_until else max(0.0, (3.5 - gap) / 2.0) * math.exp(0.1 * (on.a("stealing") - 10))
        return (self.p.errant_pass * math.exp(-0.1 * (h.a("passing") - 10) - 0.05 * (h.a("vision") - 10))
                * (1 + 1.5 * pressure) * (1 + max(0.0, d - 20) / 15) * (1.5 if lob else 1.0))

    def throw_pass(self, h: Agent, mate: Agent) -> None:
        d = C.dist(h.pos, mate.pos)
        target, flight, lob = self.pass_target(h, mate)
        mate.target(target)                            # stop and show a target, or keep cutting
        source = ("cut" if self.cutter is mate else
                  "roll" if self.pnr and self.pnr["set"] and mate is self.pnr["screener"] else None)
        # Interception? (Lobs go over the top; they're contested at the catch instead.)
        for de in ([] if lob else self.defense):
            lane = C.point_segment_distance(de.pos, h.pos, target)
            if lane < 2.0 and C.dist(de.pos, h.pos) > 1.5:
                p = (self.p.pass_steal_scale * (1.4 if self.scheme == "press" else 1.0)
                     * math.exp(0.12 * (de.a("stealing") - 10))
                     * (2.0 - lane) / 2.0 * (1.3 if d > 25 else 1.0))
                if self.rng.random() < p:
                    self.flight = Flight("pass", h.pos, de.pos, self.now, max(0.2, C.dist(h.pos, de.pos) / self.t.pass_speed),
                                         data={"steal": de, "passer": h})
                    self.holder = None
                    self.mode = "pass"
                    return
        if self.rng.random() < self.errant_chance(h, d, lob):
            away = C.toward(h.pos, target, d + 6.0 + self.rng.uniform(0, 6))
            self.flight = Flight("pass", h.pos, (max(-25.0, min(25.0, away[0])), max(0.0, min(47.0, away[1]))),
                                 self.now, flight, data={"errant": mate, "passer": h})
            self.holder = None
            self.mode = "pass"
            return
        self.flight = Flight("pass", h.pos, target, self.now, flight, apex=6.0 if lob else 0.0,
                             z0=6.0 if lob else 4.0, z1=9.0 if lob else 4.0,
                             data={"to": mate, "passer": h, "lob": lob, "source": source})
        if lob:
            self.stats["lobs"] += 1
            self.say(f"{h.p.name} throws the lob to {mate.p.name}")
        # The receiver's defender reads the pass and breaks on it (half his usual reaction).
        guard = self.guarded_by(mate)
        guard.next_think = min(guard.next_think, self.now + guard.reaction * self.p.reaction_scale * self.p.pass_read)
        self.holder = None
        self.mode = "pass"
        self.passes += 1
        self.stats["passes"] += 1

    def finish_lob(self, mate: Agent, passer: Agent) -> None:
        """A lob is contested at the catch: a defender there can break it up."""
        for de in self.defense:
            land = C.dist(de.pos, mate.pos)
            if land < 2.5:
                p = (0.3 * (2.5 - land) / 2.5 * math.exp(0.1 * (de.a("blocking") - 10))
                     * (1 + 0.1 * (de.height - mate.height) * 10 / 3))
                if self.rng.random() < p:
                    self.credit(passer, "tov")
                    self.credit(de, "stl")
                    self.stats["tov"] += 1
                    self.stats["steals"] += 1
                    self.say(f"{de.p.name} breaks up the lob!")
                    self.end("tov")
                    return
        self.take_shot(mate, moving=False, finish="lob")

    def release_time(self, h: Agent, catch: bool) -> float:
        """Seconds from deciding to shoot a jumper to letting it go; good shooters are quicker."""
        base = self.t.release_catch if catch else self.t.release_dribble
        return max(0.15, base - 0.008 * (h.a("three_point") - 10))

    def begin_release(self, h: Agent, moving: bool) -> None:
        """Start the shooting motion; the shot goes up (and is contested) at release."""
        catch = self.just_caught(moving)
        self.release = {"mode": self.mode, "moving": moving, "catch": catch,
                        "at": self.now + self.release_time(h, catch)}
        self.mode = "release"
        h.target(h.pos)
        for d in self.defense:              # the shot motion is a cue: defenders nearby react quickly
            if C.dist(d.pos, h.pos) < 12:
                d.next_think = min(d.next_think, self.now + d.reaction * self.p.reaction_scale * 0.5)

    def take_shot(self, h: Agent, moving: bool, finish: Optional[str] = None,
                  released: Optional[bool] = None) -> None:
        """``released``: None to start a shot; for a jumper, the catch flag once it's released."""
        rng, t = self.rng, self.t
        lob = finish == "lob"
        post = self.mode == "post"
        spot = C.toward(RIM, h.pos, min(1.5, C.dist_to_rim(h.pos))) if lob else self.finish_point(h)
        if released is None and not lob and not post and C.zone(spot) in (MID_ZONE, THREE_ZONE):
            self.begin_release(h, moving)
            return
        catch = released if released is not None else (None if lob else self.just_caught(moving))
        defender, dd = self.nearest_defender(spot)
        prob, z = self.make_prob(h, spot, dd, defender, moving, catch, post=post, lob=lob)
        points = 3 if z == THREE_ZONE else 2
        level = max(0.0, min(1.0, (6.0 - dd) / 5.0))
        shot_d = C.dist_to_rim(spot)
        verb = {RIM_ZONE: "goes up at the rim", CLOSE_ZONE: "floater", MID_ZONE: "pull-up jumper",
                THREE_ZONE: "fires a three"}[z]
        if lob:
            verb = "rises for the alley-oop"
        elif post:
            verb = "hook shot" if z in (RIM_ZONE, CLOSE_ZONE) else "turnaround jumper"
        elif spot != h.pos:
            verb = "gathers and goes up"
        source = ("lob" if lob else "post" if post else "putback" if self.now - self.orb_time < 2.0
                  else "drive" if self.mode == "drive"
                  else (self.catch_source or ("dunker" if C.dist_to_rim(h.pos) < 8 else "catch"))
                  if (catch if released is not None else self.just_caught(moving)) else "other")
        # Block?
        if dd < 4.0:
            closeness = max(0.0, min(1.0, (4.0 - dd) / 2.5))
            p_block = (t.block_base[z] * math.exp(0.15 * (defender.a("blocking") - 10))
                       * closeness * (1 + 0.15 * (defender.height - h.height)))
            if rng.random() < max(0.0, p_block):
                self.count_attempt(h, z)
                self.credit(defender, "blk")
                self.stats["blocks"] += 1
                self.say(f"{h.p.name} {verb}... BLOCKED by {defender.p.name}!")
                land = C.clamp((h.x + rng.uniform(-6, 6), h.y + rng.uniform(2, 8)))
                self.start_rebound(h.pos, land, 0.6)
                return
        fouled = rng.random() < t.shooting_foul[z] * self.p.foul_scale * (0.3 + 0.7 * level)
        if fouled:
            prob *= 0.45
        made = rng.random() < prob
        if made or not fouled:                 # a missed shot that drew a foul isn't an attempt
            self.count_attempt(h, z)
            bucket = "tight" if dd < 2 else "close" if dd < 4 else "open" if dd < 6 else "wide"
            self.stats[f"contest_{z}_{bucket}"] += 1
            ctx = ("drive" if self.mode == "drive" else "putback" if self.now - self.orb_time < 2.0
                   else "catch" if (catch if released is not None else self.now - self.catch_time < 1.0)
                   else "other")
            self.stats[f"ctx_{z}_{ctx}"] += 1
            self.stats[f"ctxwide_{z}_{ctx}"] += bucket == "wide"
            self.stats[f"shot_clock_{z}"] += self.shot_clock
            self.stats[f"src_{z}_{source}"] += 1
        assisted = (self.last_passer is not None and self.now - self.catch_time < 2.5)
        duration = 0.55 + shot_d / 45.0
        self.flight = Flight("shot", spot, RIM, self.now, duration, apex=6 + shot_d * 0.25, z0=7.5, z1=10.0,
                             data={"shooter": h, "made": made, "points": points, "zone": z, "fouled": fouled,
                                   "defender": defender, "assisted": assisted, "passer": self.last_passer,
                                   "shot_d": shot_d})
        self.holder = None
        self.mode = "shot"
        self.pnr = None
        self.post = None
        self.say(f"{h.p.name} {verb}" + (f" over {defender.p.name}" if level > 0.6 else "")
                 + (" (fouled)" if fouled else ""))

    def count_attempt(self, h: Agent, z: str) -> None:
        self.stats[f"fga_{z}"] += 1
        self.credit(h, "fga")
        if z == THREE_ZONE:
            self.credit(h, "tpa")

    # ------------------------------------------------------------------ ball in the air
    def advance_flight(self) -> None:
        f = self.flight
        if f.kind == "rebound":
            self.rebound_movement(f)
        if not f.done(self.now):
            return
        self.flight = None
        if f.kind == "pass":
            if "steal" in f.data:
                thief, passer = f.data["steal"], f.data["passer"]
                self.credit(passer, "tov")
                self.credit(thief, "stl")
                self.stats["tov"] += 1
                self.stats["steals"] += 1
                self.say(f"Picked off! {thief.p.name} jumps the passing lane")
                self.end("tov")
                return
            if "errant" in f.data:
                self.credit(f.data["passer"], "tov")
                self.stats["tov"] += 1
                self.stats["errant_passes"] += 1
                self.say(f"{f.data['passer'].p.name}'s pass gets away from {f.data['errant'].p.name}: turnover")
                self.end("tov")
                return
            mate = f.data["to"]
            self.holder = mate
            self.pnr = None
            self.post = None
            self.last_passer = f.data["passer"]
            self.catch_time = self.now
            self.catch_source = f.data.get("source")
            if self.cutter is mate:
                self.cutter = None
            self.mode, self.mode_since = "probe", self.now
            if f.data.get("lob"):
                self.finish_lob(mate, f.data["passer"])
                return
            d_rim = C.dist_to_rim(mate.pos)
            if self.can_post(mate) and d_rim < 11 and self.catch_source is None:
                self.mode, self.post = "post", {"since": self.now, "double": None}
                self.stats["post_ups"] += 1
                self.say(f"{mate.p.name} posts up")
                self.next_decision = self.now + 0.6
            else:
                mate.spot = mate.pos if d_rim > 8 else mate.spot
                self.next_decision = self.now + 0.1  # catch and shoot (or finish) if it's there
        elif f.kind == "shot":
            self.resolve_shot(f)
        elif f.kind == "rebound":
            self.resolve_rebound(f)

    def resolve_shot(self, f: Flight) -> None:
        d = f.data
        shooter = d["shooter"]
        if d["made"]:
            self.credit(shooter, "fgm")
            self.credit(shooter, "pts", d["points"])
            self.stats[f"fgm_{d['zone']}"] += 1
            if d["zone"] == THREE_ZONE:
                self.credit(shooter, "tpm")
            assist = ""
            if d["assisted"] and d["passer"] is not None:
                self.credit(d["passer"], "ast")
                self.stats["ast"] += 1
                assist = f" (assist {d['passer'].p.name})"
            self.points += d["points"]
            self.say(f"Good!{assist}" + (" And one!" if d["fouled"] else ""))
            if d["fouled"]:
                self.free_throws(shooter, 1, d["defender"])
            else:
                self.end("score")
        elif d["fouled"]:
            n = d["points"]
            self.say(f"Foul on {d['defender'].p.name}: {n} free throws")
            self.free_throws(shooter, n, d["defender"])
        else:
            self.say("No good")
            # Long shots, long rebounds; often to the far side.
            r = 3.0 + self.rng.uniform(0, 1) * (2 + d["shot_d"] * 0.35)
            side = -1 if shooter.x > 0 else 1
            angle = math.radians(self.rng.uniform(15, 165))
            if self.rng.random() < 0.6:
                angle = math.radians(90 + side * self.rng.uniform(10, 75))
            land = C.clamp((RIM[0] + r * math.cos(angle), RIM[1] + abs(r * math.sin(angle))))
            self.start_rebound(RIM, land, 0.7)

    def start_rebound(self, start, land, duration, edge: float = 1.0) -> None:
        """``edge`` multiplies the defence's rebounding advantage (free throws: they have the inside spots)."""
        self.flight = Flight("rebound", start, land, self.now, duration, z0=10.0, z1=6.0, data={"edge": edge})
        self.holder = None
        self.mode = "rebound"
        self.box_out_until = self.now + 0.7

    def rebound_movement(self, f: Flight) -> None:
        land = f.end
        for d in self.defense:
            man = self.man_of(d)
            if self.now < self.box_out_until and C.dist(man.pos, land) < 15:
                d.target(C.toward(man.pos, RIM, 2.0))          # put a body on him
            else:
                d.target(land)
        for a in self.offense:
            # The big crashes; more bodies (and from further out) when the coach says crash.
            crash_level = self.teams[self.side].tactics.crash_offensive_glass
            crash = (a.slot == 4 or (a.slot == 3 and crash_level >= 13) or (a.slot >= 1 and crash_level >= 18)
                     or C.dist(a.pos, land) < 6 + 0.3 * (crash_level - 10))
            a.target(land if crash else a.spot)

    def resolve_rebound(self, f: Flight) -> None:
        land = f.end
        candidates = []
        for a in self.everyone:
            dd = C.dist(a.pos, land)
            if dd > 8:
                continue
            attr = "defensive_rebounding" if a in self.defense else "offensive_rebounding"
            w = math.exp(0.25 * (a.a(attr) - 10) + (a.p.physique.height_cm - 203) / 24) / (1 + dd * dd / 6)
            if a in self.defense:
                w *= self.p.defensive_rebound_edge * f.data.get("edge", 1.0)
            candidates.append((w, a))
        if not candidates:                                   # nobody near: closest gets it
            a = min(self.everyone, key=lambda q: C.dist(q.pos, land))
            candidates = [(1.0, a)]
        winner = self.rng.choices([a for _, a in candidates], [w for w, _ in candidates])[0]
        if winner in self.defense:
            self.credit(winner, "drb")
            self.stats["drb"] += 1
            self.say(f"Rebound {winner.p.name}")
            self.end("drb")
        else:
            self.credit(winner, "orb")
            self.stats["orb"] += 1
            self.orb_time = self.now
            self.say(f"Offensive rebound {winner.p.name}!")
            winner.place(*land)
            self.holder = winner
            self.last_passer = None
            self.mode, self.mode_since = "probe", self.now
            self.shot_clock = max(self.shot_clock, 14.0)
            winner.spot = C.clamp(C.toward(RIM, winner.pos, max(C.dist_to_rim(winner.pos), 4.0)))
            self.next_decision = self.now + 0.3

    def free_throws(self, shooter: Agent, n: int, fouler: Agent) -> None:
        self.credit(fouler, "pf")
        self.mode = "ft"
        shooter.place(0.0, C.FREE_THROW_Y)
        others = [a for a in self.everyone if a is not shooter]
        lane = list(LANE_SPOTS)
        defenders = [a for a in others if a in self.defense]
        attackers = [a for a in others if a in self.offense]
        for a, spot in zip(defenders[:2] + attackers[:2] + defenders[2:3] + attackers[2:3], lane):
            a.place(*spot)
        rest = defenders[3:] + attackers[3:]
        for i, a in enumerate(rest):
            a.place((-1) ** i * (10 + 3 * i), 24.0)
        pct = max(0.35, min(0.95, self.t.free_throw + 0.018 * (shooter.a("free_throw") - 10)))
        made = 0
        last_missed = False
        for _ in range(n):
            self.credit(shooter, "fta")
            self.stats["fta"] += 1
            for _ in range(8):
                self.record_frame()
                self.now += self.t.dt
            shot = Flight("shot", shooter.pos, RIM, self.now, 0.9, apex=6, z0=7.5, z1=10.0)
            for k in range(9):
                self.flight = shot
                self.record_frame()
                self.now += self.t.dt
            self.flight = None
            if self.rng.random() < pct:
                made += 1
                self.credit(shooter, "ftm")
                self.credit(shooter, "pts")
                self.stats["ftm"] += 1
                self.points += 1
                last_missed = False
                self.say("Free throw good")
            else:
                last_missed = True
                self.say("Free throw missed")
        if last_missed:
            land = C.clamp((self.rng.uniform(-6, 6), RIM[1] + self.rng.uniform(3, 7)))
            self.start_rebound(RIM, land, 0.6, edge=2.2)
        else:
            self.end("score" if made else "ft")

    # ------------------------------------------------------------------ export
    def to_dict(self) -> dict:
        def team(side):
            t = self.teams[side]
            return {"name": t.full_name, "abbr": t.abbreviation,
                    "players": [{"num": a.p.jersey_number, "name": a.p.name, "pos": a.p.position.value,
                                 "ca": a.p.current_ability} for a in self.agents[side]]}
        return {"version": 1, "dt": self.t.dt, "home": team("home"), "away": team("away"),
                "possessions": self.possessions,
                "box": {side: [{"name": a.p.name, **{k: int(v) for k, v in self.box[id(a)].items()}}
                               for a in self.agents[side]] for side in ("home", "away")}}

