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
from .values import DecisionModel, default_model, hold_curve

ZONES = (RIM_ZONE, CLOSE_ZONE, MID_ZONE, THREE_ZONE)


@dataclass
class SpatialTuning:
    dt: float = 0.1
    # Make chance for an average shooter, wide open / fully contested reduction.
    make_open: dict = field(default_factory=lambda: {RIM_ZONE: 0.78, CLOSE_ZONE: 0.52, MID_ZONE: 0.47, THREE_ZONE: 0.355})
    contest_max: dict = field(default_factory=lambda: {RIM_ZONE: 0.20, CLOSE_ZONE: 0.15, MID_ZONE: 0.13, THREE_ZONE: 0.07})
    skill_per_point: float = 0.009
    three_skill_per_point: float = 0.013     # shooting ability matters most from deep
    shooting_foul: dict = field(default_factory=lambda: {RIM_ZONE: 0.42, CLOSE_ZONE: 0.20, MID_ZONE: 0.06, THREE_ZONE: 0.025})
    block_base: dict = field(default_factory=lambda: {RIM_ZONE: 0.14, CLOSE_ZONE: 0.09, MID_ZONE: 0.02, THREE_ZONE: 0.01})
    free_throw: float = 0.775
    dribble_turnover_per_sec: float = 0.045   # with a defender in your face
    pass_steal_scale: float = 0.30
    pass_speed: float = 40.0                  # ft/s
    defensive_rebound_edge: float = 4.6
    help_trigger_distance: float = 22.0       # drive this close to the rim draws help
    charge_chance: float = 0.15
    # Jump shots right off a pass beat jump shots off the dribble (NBA: ~37% vs ~34% from three).
    catch_shoot_bonus: float = 0.02
    off_dribble_penalty: float = 0.035


DEFAULT_TUNING = SpatialTuning()

# Spacing spots by lineup slot (PG, SG, SF, PF, C): four out, one in.
# Perimeter spots sit just outside the arc (23.75 ft; 22 ft in the corners).
SPOTS_4OUT = [(0.0, 30.0), (19.0, 22.0), (-19.0, 22.0), (-22.6, 4.5), (6.0, 7.0)]
FIVE_OUT_C = (22.6, 4.5)
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
                 recorder: Optional[dict] = None):
        """``model`` values the ball handler's options (default: learned values if
        trained, else hand-written). ``explore`` is the chance of a random option and
        ``recorder`` collects (features, possession points) per option, for training."""
        self.league, self.rng, self.t, self.record = league, rng, tuning, record
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
                  catch: Optional[bool] = None) -> tuple[float, str]:
        """``catch``: True for a catch-and-shoot, False off the dribble, None if unknown."""
        t = self.t
        z = C.zone(p)
        skill = {RIM_ZONE: "finishing", CLOSE_ZONE: "close_range", MID_ZONE: "mid_range",
                 THREE_ZONE: "three_point"}[z]
        per_point = t.three_skill_per_point if z == THREE_ZONE else t.skill_per_point
        prob = t.make_open[z] + per_point * (shooter.a(skill) - 10)
        prob += 0.003 * (shooter.a("composure") - 10)
        if defender is None:
            defender, contest_d = self.nearest_defender(p)
        level = max(0.0, min(1.0, (6.0 - contest_d) / 5.0))     # 1 = in his face, 0 = 6+ ft away
        d_skill = defender.a("interior_defense" if z in (RIM_ZONE, CLOSE_ZONE) else "perimeter_defense")
        quality = 1 + 0.03 * (d_skill - 10) + 0.06 * (defender.height - shooter.height)
        prob -= t.contest_max[z] * level * max(0.4, quality)
        d_rim = C.dist_to_rim(p)
        if d_rim > 25:
            prob -= 0.015 * (d_rim - 25)                         # deep threes
        if moving:
            prob -= 0.03
        if catch is not None and z in (MID_ZONE, THREE_ZONE):
            prob += t.catch_shoot_bonus if catch else -t.off_dribble_penalty
        return max(0.02, min(0.95, prob)), z

    def shot_value(self, shooter: Agent, p=None, contest_d=None, defender=None, moving=False,
                   catch: Optional[bool] = None) -> float:
        p = p or shooter.pos
        prob, z = self.make_prob(shooter, p, contest_d, defender, moving, catch)
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
        self.next_decision = rng.uniform(1.5, 3.0)
        self.passes = 0
        self.decisions: list[tuple[str, list[float]]] = []
        self.result: Optional[str] = None
        self.points = 0

        mirror = rng.random() < 0.5
        five_out = self.offense[4].a("three_point") >= 12
        spots = list(SPOTS_4OUT)
        if five_out:
            spots[4] = FIVE_OUT_C
        for i, a in enumerate(self.offense):
            sx, sy = spots[i]
            a.spot = (-sx if mirror else sx, sy)
            a.place(a.spot[0] * 0.6 + rng.uniform(-3, 3), min(44.0, a.spot[1] + 14 + rng.uniform(-2, 2)))
            a.next_think = 0.0
            a.helping = False
            a.stuck_until = -1.0
        self.offense[0].place(rng.uniform(-4, 4), 38.0)
        for i, d in enumerate(self.defense):
            man = self.offense[i]
            gx, gy = C.toward(man.pos, RIM, 4.0)
            d.place(gx, gy)
            d.ease = 6.0
            d.next_think = rng.uniform(0, d.reaction)
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
        elif self.holder is not None and self.mode in ("setup", "probe", "drive"):
            self.handler_logic()
        boost = 1.08 if self.mode == "drive" else 1.0
        for a in self.everyone:
            a.move(dt, self.now, boost if a is self.holder else 1.0)
        self.separate()
        if self.result is None and self.holder is not None and self.mode in ("probe", "drive", "setup") \
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
                elif self.now > self.pnr["set_time"] + 0.4:
                    side = 1 if self.pnr["point"][0] >= 0 else -1
                    a.target((side * 2.5, 6.5))                  # roll to the rim
                continue
            d = self.guarded_by(a)
            if d.helping and a.a("three_point") < 12 and a.spot[1] < 22:
                a.target((math.copysign(3.5, a.x or 1), 5.5))    # cut behind the help
            else:
                a.target(a.spot)
        if h is not None and self.mode == "setup":
            h.target(h.spot)
        elif h is not None and self.mode == "probe" and self.now - self.mode_since > 1.0:
            # Probe: work around your spot, sometimes attacking the elbow or short wing.
            if self.rng.random() < 0.06:
                if self.rng.random() < 0.4:
                    angle = math.radians(self.rng.uniform(25, 155))
                    r = self.rng.uniform(15, 20)
                    h.target((RIM[0] + r * math.cos(angle), RIM[1] + r * math.sin(angle)))
                else:
                    h.target((h.spot[0] + self.rng.uniform(-4, 4), h.spot[1] + self.rng.uniform(-3, 3)))

    def defense_targets(self) -> None:
        now = self.now
        if self.mode == "ft":
            return
        ball = self.flight.position(now)[:2] if self.flight else (self.holder.pos if self.holder else RIM)
        driving = self.mode == "drive" and self.holder is not None
        helper = None
        if driving and C.dist_to_rim(self.holder.pos) < self.t.help_trigger_distance:
            helper = self.choose_helper()
        for d in self.defense:
            d.helping = d is helper
            if now < d.next_think:
                continue
            d.next_think = now + d.reaction
            if self.mode == "rebound":
                continue
            man = self.man_of(d)
            if d is helper:
                # Meet the driver between him and the rim.
                d_rim = C.dist_to_rim(self.holder.pos)
                d.target(C.toward(RIM, self.holder.pos, max(3.0, min(6.0, d_rim - 2.5))))
            elif self.pnr and d is self.guarded_by(self.pnr["screener"]) \
                    and self.holder is not None and not self.pnr.get("switch"):
                # Drop coverage: don't follow the screener out. Wait at the nail
                # (between ball and rim), then sink to protect the rim once he comes off it.
                depth = 9.0 if self.pnr["set"] else 14.0
                d.target(C.toward(RIM, self.holder.pos, min(depth, C.dist_to_rim(self.holder.pos) - 2)))
            elif self.flight and self.flight.kind == "pass" and self.flight.data.get("to") is man:
                d.target(C.toward(man.pos, RIM, 3.0))             # close out on the catch
            elif man is self.holder:
                # Press up on shooters out to 30 ft; give non-shooters room out there.
                deep = C.dist_to_rim(man.pos)
                gap = 3.0 if deep < 27 else 5.0
                # Anticipate: play where he's going, not where he was.
                lead = 0.45 if self.mode == "drive" else 0.2
                ahead = C.clamp((man.x + man.vx * lead, man.y + man.vy * lead))
                d.target(C.toward(ahead, RIM, gap))
            else:
                pass_dist = C.dist(man.pos, ball)
                # One pass away: stay close. Far side: sink into the paint to help.
                sag = 0.08 if pass_dist < 22 else min(0.40, 0.08 + 0.02 * (pass_dist - 22))
                if man.a("three_point") >= 13:
                    sag *= 0.6                                   # stay attached to shooters
                mx, my = man.pos
                tx = mx + (RIM[0] - mx) * sag + (ball[0] - mx) * 0.08
                ty = my + (RIM[1] - my) * sag + (ball[1] - my) * 0.08
                d.target((tx, ty))

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
            return C.dist_to_rim(d.pos) - (3 if d.slot >= 3 else 0) + (8 if man.a("three_point") >= 14 else 0)
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
        if self.mode == "drive":
            d_rim = C.dist_to_rim(h.pos)
            if d_rim < 4.5:
                self.take_shot(h, moving=False)
                return
            if self.charge_check(h):
                return
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
            closer, dd = self.nearest_defender(h.pos)
            predicted = max(0.0, dd - self.closing_speed(closer, h) * 0.35)
            ep = self.shot_value(h, contest_d=predicted, defender=closer, moving=moving,
                                 catch=self.just_caught(moving))
            options.append(("shoot", ("shoot",), [1.0, ep, float(C.is_three(h.pos)), float(moving),
                                                   max(0.0, 6.0 - sc) / 6.0]))
        if self.mode == "probe":
            options.append(("drive", ("drive",), self.drive_features(h)))
            if self.pnr is None and self.pnr_used < 2 and d_rim > 17:
                scr = self.offense[4] if self.offense[4] is not h else self.offense[3]
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
            scored = [(self.model.value(k, f), (k, a, f)) for k, a, f in options]
            kind, action, feats = _softmax_choice(rng, scored, temperature)
        self.decisions.append((kind, feats))
        self.stats[f"choice_{kind}"] += 1
        self.next_decision = now + h.reaction * rng.uniform(1.2, 2.2)
        if kind == "shoot":
            self.take_shot(h, moving=moving)
        elif kind == "drive":
            self.start_drive(h)
        elif kind == "continue":
            pass
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

    def pass_features(self, h: Agent, mate: Agent) -> Optional[list[float]]:
        d = C.dist(h.pos, mate.pos)
        if d > 40:
            return None
        flight = d / self.t.pass_speed + 0.15
        risk = 0.0
        for de in self.defense:
            lane = C.point_segment_distance(de.pos, h.pos, mate.pos)
            if lane < 2.5 and C.dist(de.pos, h.pos) > 1.5:
                risk += 0.25 * (2.5 - lane) / 2.5
        guard = self.guarded_by(mate)
        contest_after = max(0.0, C.dist(guard.pos, mate.pos) - guard.max_speed * max(0.0, flight - guard.reaction))
        catch_shoot = self.shot_value(mate, contest_d=contest_after, defender=guard, catch=True)
        if C.dist_to_rim(mate.pos) > 30:
            catch_shoot = 0.0
        clock = hold_curve(self.shot_clock - flight)
        # Moving the ball keeps the offence alive and makes the defence rotate.
        keep_going = clock + 0.04 + 0.01 * (mate.a("ball_handling") - 10)
        best = max(catch_shoot, keep_going) * (1 - min(0.9, risk))
        back = float(mate is self.last_passer and self.now - self.catch_time < 1.5)
        return [1.0, catch_shoot, clock, risk, back, best]

    # ------------------------------------------------------------------ actions
    def dribble_turnover(self, h: Agent) -> bool:
        on = self.guarded_by(h)
        dd = C.dist(h.pos, on.pos)
        if dd >= 3.5 or self.now < on.stuck_until:
            return False
        rate = (self.t.dribble_turnover_per_sec
                * math.exp(0.15 * (on.a("stealing") - 10) - 0.15 * (h.a("ball_handling") - 10))
                * (3.5 - dd) / 2.5)
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

    def charge_check(self, h: Agent) -> bool:
        for d in self.defense:
            if d.helping and C.dist(d.pos, h.pos) < 2.0 and math.hypot(d.vx, d.vy) < 2.5:
                d.helping = False
                if self.rng.random() < self.t.charge_chance:
                    self.credit(h, "tov")
                    self.credit(h, "pf")
                    self.stats["tov"] += 1
                    self.say(f"Charge! {d.p.name} takes it from {h.p.name}")
                    self.end("tov")
                    return True
        return False

    def start_drive(self, h: Agent) -> None:
        on = self.guarded_by(h)
        # Attack the side away from the defender.
        side = 1 if on.x < h.x else -1
        h.target((side * 2.5, 6.0))
        self.mode, self.mode_since = "drive", self.now
        self.stats["drives"] += 1
        self.say(f"{h.p.name} drives {'right' if side > 0 else 'left'}")
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

    def throw_pass(self, h: Agent, mate: Agent) -> None:
        d = C.dist(h.pos, mate.pos)
        flight = d / self.t.pass_speed + 0.15
        mate.target(mate.pos)                          # stop and show a target
        # Interception?
        for de in self.defense:
            lane = C.point_segment_distance(de.pos, h.pos, mate.pos)
            if lane < 2.0 and C.dist(de.pos, h.pos) > 1.5:
                p = (self.t.pass_steal_scale * math.exp(0.12 * (de.a("stealing") - 10))
                     * (2.0 - lane) / 2.0 * (1.3 if d > 25 else 1.0))
                if self.rng.random() < p:
                    self.flight = Flight("pass", h.pos, de.pos, self.now, max(0.2, C.dist(h.pos, de.pos) / self.t.pass_speed),
                                         data={"steal": de, "passer": h})
                    self.holder = None
                    self.mode = "pass"
                    return
        self.flight = Flight("pass", h.pos, mate.pos, self.now, flight, data={"to": mate, "passer": h})
        # The receiver's defender reads the pass and breaks on it (half his usual reaction).
        guard = self.guarded_by(mate)
        guard.next_think = min(guard.next_think, self.now + guard.reaction * 0.5)
        self.holder = None
        self.mode = "pass"
        self.passes += 1
        self.stats["passes"] += 1

    def take_shot(self, h: Agent, moving: bool) -> None:
        rng, t = self.rng, self.t
        defender, dd = self.nearest_defender(h.pos)
        prob, z = self.make_prob(h, h.pos, dd, defender, moving, self.just_caught(moving))
        points = 3 if z == THREE_ZONE else 2
        level = max(0.0, min(1.0, (6.0 - dd) / 5.0))
        shot_d = C.dist_to_rim(h.pos)
        verb = {RIM_ZONE: "goes up at the rim", CLOSE_ZONE: "floater", MID_ZONE: "pull-up jumper",
                THREE_ZONE: "fires a three"}[z]
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
        fouled = rng.random() < t.shooting_foul[z] * (0.3 + 0.7 * level)
        if fouled:
            prob *= 0.45
        made = rng.random() < prob
        if made or not fouled:                 # a missed shot that drew a foul isn't an attempt
            self.count_attempt(h, z)
            bucket = "tight" if dd < 2 else "close" if dd < 4 else "open" if dd < 6 else "wide"
            self.stats[f"contest_{z}_{bucket}"] += 1
            ctx = ("drive" if self.mode == "drive" else "putback" if self.now - self.orb_time < 2.0
                   else "catch" if self.now - self.catch_time < 1.0 else "other")
            self.stats[f"ctx_{z}_{ctx}"] += 1
            self.stats[f"ctxwide_{z}_{ctx}"] += bucket == "wide"
            self.stats[f"shot_clock_{z}"] += self.shot_clock
        assisted = (self.last_passer is not None and self.now - self.catch_time < 2.5)
        duration = 0.55 + shot_d / 45.0
        self.flight = Flight("shot", h.pos, RIM, self.now, duration, apex=6 + shot_d * 0.25, z0=7.5, z1=10.0,
                             data={"shooter": h, "made": made, "points": points, "zone": z, "fouled": fouled,
                                   "defender": defender, "assisted": assisted, "passer": self.last_passer,
                                   "shot_d": shot_d})
        self.holder = None
        self.mode = "shot"
        self.pnr = None
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
            mate = f.data["to"]
            self.holder = mate
            self.pnr = None
            self.last_passer = f.data["passer"]
            self.catch_time = self.now
            self.mode, self.mode_since = "probe", self.now
            mate.spot = mate.pos if C.dist_to_rim(mate.pos) > 8 else mate.spot
            self.next_decision = self.now + 0.1      # catch and shoot if it's there
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
            # The big crashes; others only if the ball comes right to them.
            crash = a.slot == 4 or C.dist(a.pos, land) < 6
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
                w *= self.t.defensive_rebound_edge * f.data.get("edge", 1.0)
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

