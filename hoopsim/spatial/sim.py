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
from ..systems import (LATE_GAME_FIRST, OPTION_PASS, OPTION_SELF, defense_style, offense_style,
                       option_ranks)
from . import court as C
from . import plays
from .court import CLOSE_ZONE, MID_ZONE, RIM, RIM_ZONE, THREE_ZONE
from .params import DEFAULT_PARAMS, SpatialParams
from .values import DecisionModel, default_model, hold_curve

ZONES = (RIM_ZONE, CLOSE_ZONE, MID_ZONE, THREE_ZONE)
ACCEL_BASE = 15.0           # ft/s² for a speed-10 player
ACCEL_PER_POINT = 0.6       # ... per point of speed rating
SCREEN_BRAKE = 1.4          # a screen stops a defender this much harder (body contact)
TRACK_MODES = ("setup", "probe", "drive", "pass", "post", "pullup")   # off-ball defenders track their man
SEPARATE_PASSES = 2         # body-separation passes per tick (was 1)
# Nobody covers more than this in a tick (ft/s): a sprinter shoved by a body he ran
# into was carried past 32 ft/s, a "teleport" (README "Last audit flags").
MAX_STEP_SPEED = 30.0
# Which way each player faces is recorded for the viewer (a possession's "facing"); nothing
# in the simulation reads it. He turns no faster than TURN_RATE (rad/s). Running at more than
# FACE_RUN_OFF / FACE_RUN_DEF ft/s he faces where he is going; otherwise the man with the
# ball faces the rim, his team-mates the ball, and a defender the ball or, off the ball, the
# gap between the ball and his man.
TURN_RATE = 7.0
FACE_RUN_OFF = 5.0
FACE_RUN_DEF = 11.0


FACING_CODE = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def pack_facing(rows: list) -> str:
    """A possession's facing as the replay stores it: one character per player per frame
    (frame by frame, players in frame order), each the angle
    in 6-degree steps (0-59) as FACING_CODE. A list of numbers cost a third of the file."""
    return "".join(FACING_CODE[round(d / 6) % 60] for row in rows for d in row)


def update_facing(offense: list, defense: list, holder, ball: tuple[float, float], dt: float,
                  man_of=None) -> list[int]:
    """Turn each player towards what he should face (``Agent.facing``, radians, atan2(dx, dy)
    in court feet) and return the ten angles in whole degrees, offence first as given."""
    out = []
    for group, is_off in ((offense, True), (defense, False)):
        for a in group:
            speed = math.hypot(a.vx, a.vy)
            limit = FACE_RUN_OFF if is_off else FACE_RUN_DEF
            a.face_run = speed > (limit - 1.5 if a.face_run else limit)     # (no flicker at the line)
            if a.face_run:
                fx, fy = a.vx, a.vy
            elif a is holder:
                fx, fy = RIM[0] - a.x, RIM[1] - a.y
            else:
                fx, fy = ball[0] - a.x, ball[1] - a.y
                man = man_of(a) if (man_of is not None and not is_off) else None
                if man is not None and man is not holder:
                    d1, d2 = math.hypot(fx, fy) or 1.0, math.hypot(man.x - a.x, man.y - a.y) or 1.0
                    fx, fy = fx / d1 + (man.x - a.x) / d2, fy / d1 + (man.y - a.y) / d2
            if math.hypot(fx, fy) > 0.3:
                want = math.atan2(fx, fy)
                if a.facing is None:
                    a.facing = want
                else:
                    turn = (want - a.facing + math.pi) % (2 * math.pi) - math.pi
                    most = TURN_RATE * dt
                    a.facing += max(-most, min(most, turn))
            elif a.facing is None:
                a.facing = 0.0
            out.append(round(math.degrees(a.facing)) % 360)
    return out

# Animation cues in recorded possessions ("actions": [frame, player, action, other]):
# what a player starts doing at that frame, for the viewer; recording them changes
# nothing in the play. Players (and "other", -1 for none) index the frame's ten
# positions. Dribbling, running and defending are left to the frames themselves.
ACTIONS = {
    "pass": "throws a pass (other: the receiver)",
    "lob_pass": "throws a lob (other: the receiver)",
    "inbound": "throws the ball in (other: the receiver)",
    "catch": "catches a pass (other: the passer)",
    "drive": "starts a drive (other: his defender)",
    "cut": "cuts to the basket",
    "screen": "comes up to set a screen (other: the ball handler)",
    "post_up": "posts up",
    "jumper": "starts a jump shot (the gather; the ball leaves at jumper_release)",
    "jumper_release": "the jump shot leaves his hands (other: the closest defender)",
    "layup": "goes up for a layup (other: the closest defender)",
    "dunk": "goes up for a dunk (other: the closest defender)",
    "floater": "floater (other: the closest defender)",
    "hook": "hook shot from the post (other: the closest defender)",
    "fadeaway": "turnaround or fadeaway from the post (other: the closest defender)",
    "alley_oop": "rises to finish a lob (other: the closest defender)",
    "heave": "heaves it at the buzzer",
    "free_throw": "shoots a free throw",
    "contest": "contests a shot, hand up (other: the shooter)",
    "block": "blocks a shot or breaks up a lob (other: the shooter)",
    "steal": "strips or pokes the ball from a dribbler (other: the dribbler)",
    "intercept": "picks off a pass (other: the passer)",
    "take_charge": "takes a charge and falls (other: the driver)",
    "charge": "runs into a set defender (other: the defender)",
    "foul": "commits a foul (other: the fouled player)",
    "rebound": "jumps and secures the rebound",
    "rebound_jump": "jumps for a rebound someone else gets",
}
_PAIRS10 = [(i, j) for i in range(10) for j in range(i + 1, 10)]
LIFT_SPOT = (19.5, 20.5)    # where a lifting corner man goes (the wing)
ROUTINE_PRESSURE = 2.0      # a perimeter pass is routine unless the on-ball man is this close (ft; was 3)


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
    fouled_make: float = 0.45             # a fouled shot goes in at this share of its make chance
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
    # (the off-the-dribble penalty is in SpatialParams)
    # Jump shots take time to get off (catch to release / gather and rise); the
    # contest is judged where the defenders are at release, not at the decision.
    release_catch: float = 0.45          # catch, gather and rise (was 0.30; README "Patient offence")
    release_dribble: float = 0.50
    # Help defenders swipe at the ball when a driver comes through their reach.
    dig_per_sec: float = 0.35


DEFAULT_TUNING = SpatialTuning()

# Spacing spots by lineup slot (PG, SG, SF, PF, C): four out, one in.
# Perimeter spots sit just outside the arc (23.75 ft; 22 ft in the corners).
SPOTS_4OUT = [(0.0, 30.0), (19.0, 22.0), (-19.0, 22.0), (-22.6, 4.5), (6.0, 7.0)]
FIVE_OUT_C = (22.6, 4.5)
# Four out "high": two guards at the slots, the wings at the low 45s just above the
# corners, the four man at the other slot; nobody in the corners (``four_high``).
FOUR_HIGH = [(11.0, 26.5), (21.5, 17.5), (-21.5, 17.5), (-11.0, 26.5)]
POST_BLOCK = (6.5, 8.0)        # a big who can score inside sets up on the low block
DUNKER = (5.0, 4.0)            # one who can't waits under the rim for dump-offs
# Perimeter spots for re-spacing (fill and replace): top, slots, wings, corners.
PERIMETER_SPOTS = [(0.0, 28.5), (12.5, 25.5), (-12.5, 25.5), (19.5, 20.5), (-19.5, 20.5),
                   (22.6, 4.5), (-22.6, 4.5)]
LANE_SPOTS = [(-7.5, 7.5), (7.5, 7.5), (-7.5, 11.0), (7.5, 11.0), (-7.5, 14.0), (7.5, 14.0)]


class Agent:
    def __init__(self, player: Player, side: str, slot: int):
        self.p = player
        self.side = side                       # "home" / "away"
        self.slot = slot
        a = player.attributes
        self.max_speed = 13.0 + 0.55 * (a.speed - 10)        # ft/s in half-court action
        # Acceleration limit (ft/s², speeding up, stopping and changing direction alike).
        # NBA tracking puts the 99th percentile of 0.3 s accelerations at about 22 ft/s²;
        # this was 22 + 1.2 per point of speed (99th percentile 28).
        self.accel = ACCEL_BASE + ACCEL_PER_POINT * (a.speed - 10)
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
        self.track: Optional[tuple] = None    # off-ball defender: (ball, sag) as he last read them
        # Full-game hooks (fullgame.py): make-chance penalty from fatigue, game-day
        # form (a make-chance multiplier from morale and the night), and distance
        # run (drives energy drain).
        self.fatigue_shot = 0.0
        self.form = 1.0
        self.travelled = 0.0
        # Which way he faces (radians, atan2(dx, dy); None: not set yet). Recorded for the
        # viewer only: see update_facing.
        self.facing: Optional[float] = None
        self.face_run = False

    @property
    def pos(self) -> tuple[float, float]:
        return self.x, self.y

    def a(self, name: str) -> int:
        return getattr(self.p.attributes, name)

    def place(self, x: float, y: float) -> None:
        self.x, self.y = x, y
        self.tx, self.ty = x, y
        self.vx = self.vy = 0.0
        self.facing = None                     # (put somewhere new: he faces afresh)

    def target(self, p: tuple[float, float]) -> None:
        self.tx, self.ty = C.clamp(p)

    def steer(self, want: tuple[float, float], dt: float, limit: Optional[float] = None) -> None:
        """Change velocity toward ``want`` no faster than the body allows (``accel``):
        starting, stopping and cutting all take time."""
        sx, sy = want[0] - self.vx, want[1] - self.vy
        s = math.hypot(sx, sy)
        lim = (self.accel if limit is None else limit) * dt
        if s > lim:
            sx, sy = sx / s * lim, sy / s * lim
        self.vx += sx
        self.vy += sy

    def move(self, dt: float, now: float, speed_boost: float = 1.0, accel_boost: float = 1.0,
             brake: float = 0.0, brake_range: float = 10.0) -> None:
        """``accel_boost``: acceleration multiplier (a closeout sprint); ``brake``: slow
        down in time to stop at the target, decelerating at this share of the
        acceleration limit (0: ease in only), from ``brake_range`` ft out (a full
        sprint needs ~20)."""
        if now < self.stuck_until:             # caught on a screen: body contact stops him
            self.steer((0.0, 0.0), dt, SCREEN_BRAKE * self.accel)
        else:
            dx, dy = self.tx - self.x, self.ty - self.y
            d = math.hypot(dx, dy)
            top = self.max_speed * speed_boost
            speed = min(top, d * self.ease)    # ease in to the target
            if brake > 0 and d < brake_range:  # ... braking in time: a body can't stop dead
                speed = min(speed, math.sqrt(2.0 * brake * self.accel * accel_boost * d))
            want = (dx / d * speed, dy / d * speed) if d > 0.05 else (0.0, 0.0)
            self.steer(want, dt, self.accel * accel_boost)
        self.travelled += math.hypot(self.vx, self.vy) * dt
        # A player still in the backcourt (running up in transition) isn't pulled to half court.
        self.x, self.y = C.clamp((self.x + self.vx * dt, self.y + self.vy * dt),
                                 full=self.y > C.HALF_LENGTH - 0.5)


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
        # Shot load (``usage_share``): shots a player took, and his team's shots while he was on.
        self.shots_taken: Counter = Counter()
        self.shots_on_floor: Counter = Counter()
        # A live coach's shouted instructions per side (see ``shout_bias``); empty: none.
        self.shouts: dict = {"home": {}, "away": {}}

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

    def act(self, agent: Optional[Agent], action: str, other: Optional[Agent] = None) -> None:
        """Record what a player starts doing at the next frame, for the viewer's animation:
        ``[frame, player, action, other player or -1]`` (players as in the frames)."""
        if self.record and agent is not None:
            self.actions.append([len(self.frames), self.index[id(agent)], action,
                                 -1 if other is None else self.index[id(other)]])

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
        prob += (self.p.rim_make_adj if z == RIM_ZONE else self.p.three_make_adj if z == THREE_ZONE
                 else self.p.short_make_adj if z == CLOSE_ZONE else 0.0)
        prob += 0.003 * (shooter.a("composure") - 10)
        if defender is None:
            defender, contest_d = self.nearest_defender(p)
        level = max(0.0, min(1.0, (6.0 - contest_d) / 5.0))     # 1 = in his face, 0 = 6+ ft away
        d_skill = defender.a("interior_defense" if z in (RIM_ZONE, CLOSE_ZONE) else "perimeter_defense")
        quality = 1 + 0.03 * (d_skill - 10) + 0.06 * (defender.height - shooter.height)
        scale = self.p.contest_inside if z in (RIM_ZONE, CLOSE_ZONE) else self.p.contest_outside
        if catch is False and z in (MID_ZONE, THREE_ZONE):
            scale *= self.p.dribble_contest           # a man giving ground contests a pull-up less
        prob -= t.contest_max[z] * scale * level * max(0.4, quality)
        d_rim = C.dist_to_rim(p)
        if d_rim > 25:
            prob -= 0.015 * (d_rim - 25)                         # deep threes
        if moving:
            prob -= 0.03
        if catch is not None and z in (MID_ZONE, THREE_ZONE):
            prob += t.catch_shoot_bonus if catch else -self.p.off_dribble_penalty
        if post:
            prob += t.post_strength_effect * (shooter.a("strength") - defender.a("strength"))
        if lob:
            prob += t.lob_bonus + 0.01 * (shooter.a("vertical") - 10)
        prob *= 1.0 - shooter.fatigue_shot           # tired legs: the same fraction off every shot
        prob *= shooter.form                         # game-day form (morale), also proportional
        return max(0.02, min(0.95, prob)), z

    def shot_value(self, shooter: Agent, p=None, contest_d=None, defender=None, moving=False,
                   catch: Optional[bool] = None, post: bool = False, lob: bool = False) -> float:
        """Expected points of a shot from ``p`` with the nearest defender (or ``defender``)
        ``contest_d`` ft away. With ``shot_value_whole`` it's the whole shot as
        ``take_shot`` plays it: the block chance, and the shooting foul with its free
        throws (an and-one or two or three shots); otherwise make chance x points."""
        p = p or shooter.pos
        if defender is None:
            defender, contest_d = self.nearest_defender(p)
        prob, z = self.make_prob(shooter, p, contest_d, defender, moving, catch, post, lob)
        points = 3 if z == THREE_ZONE else 2
        w = self.p.shot_value_whole
        if w <= 0:
            return prob * points
        ft = self.free_throw_pct(shooter)
        fouled = self.foul_chance(shooter, z, contest_d)
        made_fouled = prob * self.t.fouled_make
        whole = ((1.0 - fouled) * prob * points
                 + fouled * (made_fouled * (points + ft) + (1.0 - made_fouled) * points * ft))
        whole *= 1.0 - self.block_chance(shooter, defender, z, contest_d)
        return prob * points + w * (whole - prob * points)

    def block_chance(self, shooter: Agent, defender: Agent, z: str, dd: float) -> float:
        """The chance the nearest defender, ``dd`` ft away, blocks the shot."""
        if dd >= 4.0:
            return 0.0
        closeness = max(0.0, min(1.0, (4.0 - dd) / 2.5))
        return (self.t.block_base[z] * math.exp(0.15 * (defender.a("blocking") - 10))
                * closeness * (1 + 0.15 * (defender.height - shooter.height)))

    def foul_chance(self, shooter: Agent, z: str, dd: float, share: Optional[float] = None) -> float:
        """The chance a shot is fouled, the nearest defender ``dd`` ft away (the go-to
        scorer draws more: ``foul_draw``; ``share``: his shot share, default as of now; and
        so does a good foul drawer: ``foul_drawing``)."""
        level = max(0.0, min(1.0, (6.0 - dd) / 5.0))
        if share is None and self.p.foul_draw:
            share = self.usage_share(shooter)
        draw = math.exp(self.p.foul_draw * (share - 0.22)) if self.p.foul_draw else 1.0
        if self.p.foul_drawing:                # his foul-drawing rating, as in the statistical engine
            draw *= math.exp(self.p.foul_drawing * (shooter.a("drawing_fouls") - self.p.foul_drawing_centre))
            return min(0.6, self.t.shooting_foul[z] * self.p.foul_scale * (0.3 + 0.7 * level) * draw)
        return self.t.shooting_foul[z] * self.p.foul_scale * (0.3 + 0.7 * level) * draw

    def free_throw_pct(self, shooter: Agent) -> float:
        return max(0.35, min(0.95, self.t.free_throw + 0.018 * (shooter.a("free_throw") - 10))
                   * (1.0 - shooter.fatigue_shot))

    # ------------------------------------------------------------------ possession
    def play_possession(self, side: str, start: Optional[dict] = None) -> None:
        """``start``: optional possession-start options (see ``apply_start``)."""
        rng, t = self.rng, self.t
        self.offense = self.agents[side]
        self.defense = self.agents["away" if side == "home" else "home"]
        self.side = side
        self.guards = list(range(5))           # guards[offense slot] = defense slot
        self.frames, self.events, self.actions = [], [], []
        self.facing: list = []                 # per frame: the ten players' facing, degrees (see update_facing)
        self.start_score = dict(self.score)
        self.now = 0.0
        self.shot_clock = 24.0 - rng.uniform(3.0, 4.5)   # time used bringing it up (fullgame bring_up)
        self.flight: Optional[Flight] = None
        self.last_flight: Optional[Flight] = None   # where the last ball in the air ended up
        self.holder: Optional[Agent] = self.offense[0]
        self.mode = "setup"                    # setup, probe, drive, pass, shot, rebound, ft, over
        self.mode_since = 0.0
        self.pnr: Optional[dict] = None
        self.pnr_used = 0
        self.last_passer: Optional[Agent] = None
        self.catch_time = -9.0
        self.orb_time = -9.0
        # Fast-paced teams get into their offence sooner.
        pace = self.p.pace(self.teams[side].tactics.pace)
        self.next_decision = rng.uniform(0.8, 2.0) * max(0.5, 1 - 0.03 * (pace - 10))
        self.passes = 0
        self.scramble = 0.0                    # how far behind the defence's rotations are
        self.play: Optional[dict] = None        # the entry action being run (plays.py)
        self.second_actions = 0
        self.decisions: list[tuple[str, list[float]]] = []
        self.explored: Optional[int] = None    # index of the last random (exploring) decision
        self.result: Optional[str] = None
        self.points = 0

        mirror = rng.random() < 0.5
        focus = self.teams[side].tactics.offensive_focus.value
        if "post" in self.shouts.get(side, {}):
            focus = "inside"                 # "feed the post": get a big on the block first
        five_out_shooting = {"perimeter": 10, "inside": 15}.get(focus, 12)
        five_out = self.offense[4].a("three_point") >= five_out_shooting
        spots = list(SPOTS_4OUT)
        if five_out:
            spots[4] = FIVE_OUT_C
        else:
            spots[4] = POST_BLOCK if self.can_post(self.offense[4]) else DUNKER
        if focus == "inside" and self.can_post(self.offense[3]) and (
                self.p.inside_pair >= 1 or rng.random() < self.p.inside_pair):
            spots[3] = (-POST_BLOCK[0], POST_BLOCK[1])          # both bigs inside: three out, two in
        elif not five_out and self.p.four_high > 0 and rng.random() < self.p.four_high:
            spots[:4] = FOUR_HIGH                               # four out, all above the break
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
            d.track = None
        self.game_clock: Optional[float] = None
        self.foul_at: Optional[float] = None
        self.foul_by: Optional[Agent] = None
        self.bonus = False
        self.situation: Optional[dict] = None
        self.forced_entry: Optional[tuple] = None   # a called set (plays.plan), from ``start["entry"]``
        self.first_shot_at: Optional[float] = None
        self.set_defense = True                # a set half-court defence (not a flow out of transition)
        self.early = False                     # early offence: the first good look (``early_offense``)
        if start:
            self.apply_start(start)
        pg = self.offense[0]
        if (self.p.respace_gap > 0 and self.mode == "setup" and self.holder is not None
                and self.holder is not pg and C.dist_to_rim(self.holder.spot) >= 17):
            self.holder.spot, pg.spot = pg.spot, self.holder.spot   # whoever brings it up runs the point
        self.stats["possessions"] += 1
        self.say(f"{self.teams[side].abbreviation} bring it up: {self.holder.p.name}")

        steps = 0
        while self.result is None and steps < 600:
            self.step()
            steps += 1
        if self.result is None:
            self.result = "stalled"
        if self.recorder is not None:
            for kind, feats in self.on_policy_decisions():
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
                "frames": self.frames, "events": self.events, "actions": self.actions,
                "facing": pack_facing(self.facing), "result": self.result,
            })

    def end(self, result: str, points: int = 0) -> None:
        self.result = result
        self.points += points

    # ------------------------------------------------------------------ one tick
    def step(self) -> None:
        dt = self.t.dt
        self.now += dt
        if self.scramble > 0:
            self.scramble = max(0.0, self.scramble - self.p.scramble_decay * dt)   # the defence re-sets
        if self.mode != "ft":
            self.shot_clock -= dt
            if self.game_clock is not None:
                self.game_clock -= dt
                self.shot_clock = min(self.shot_clock, self.game_clock)   # shot clock off
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
        p, now, holder, closing = self.p, self.now, self.holder, self.closing
        planting = self.mode == "release" and p.release_stop > 0
        for a in self.offense:
            if planting and a is holder:
                a.move(dt, now, 1.0, p.release_stop, 1.0)      # the jump stop: plant and rise
            else:
                a.move(dt, now, boost if a is holder else 1.0)
        for a in self.defense:                 # defenders brake in time to stop where they aim
            if a in closing:
                a.move(dt, now, p.closeout_speed, p.closeout_burst, p.defender_brake)
            else:
                a.move(dt, now, 1.0, 1.0, p.defender_brake)
        self.separate()
        if self.result is None and (self.game_clock is not None or self.foul_at is not None):
            self.clock_hooks()
        if self.result is None and self.holder is not None and self.mode in ("probe", "drive", "setup", "post", "pullup") \
                and self.shot_clock <= 0:
            self.credit(self.holder, "tov")
            self.stats["tov"] += 1
            self.stats["shot_clock_violations"] += 1
            self.say("Shot clock violation!")
            self.end("tov")
        self.record_frame()

    def separate(self) -> None:
        """Keep 1.6 ft of personal space between bodies. A second pass settles bodies
        that the first pushed into a third (only pairs involving a pushed body)."""
        people = self.everyone
        n = len(people)
        xs = [a.x for a in people]
        ys = [a.y for a in people]
        pairs = [(i, j) for i in range(n) for j in range(i + 1, n)] if n != 10 else _PAIRS10
        moved = set()
        for _ in range(SEPARATE_PASSES):
            pushed = set()
            for i, j in pairs:
                dx, dy = xs[i] - xs[j], ys[i] - ys[j]
                d2 = dx * dx + dy * dy
                if d2 < 2.56 and d2 > 1e-9:             # 1.6 ft personal space
                    d = math.sqrt(d2)
                    push = (1.6 - d) / 2 / d
                    xs[i] += dx * push
                    ys[i] += dy * push
                    xs[j] -= dx * push
                    ys[j] -= dy * push
                    pushed.add(i)
                    pushed.add(j)
            if not pushed:
                break
            moved |= pushed
            pairs = [(i, j) for i, j in pairs if i in pushed or j in pushed]
        half = C.WIDTH / 2
        cap = MAX_STEP_SPEED * self.t.dt
        for i in moved:                       # nobody is shoved off the floor
            a = people[i]
            x, y = max(-half, min(half, xs[i])), max(0.0, ys[i])
            # ... nor faster than a body can go: the shove only takes what his own
            # step this tick leaves (the overlap left over is settled next tick).
            sx, sy = a.x - a.vx * self.t.dt, a.y - a.vy * self.t.dt
            step = math.hypot(x - sx, y - sy)
            if step > cap:
                x, y = sx + (x - sx) * cap / step, sy + (y - sy) * cap / step
            a.x, a.y = x, y

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
        if self.game_clock is not None:
            row.append(round(max(0.0, self.game_clock), 1))
        self.frames.append(row)
        self.facing.append(self.face_row(self.offense, self.defense, self.holder, (bx, by), self.man_of))

    def face_row(self, offense: list, defense: list, holder, ball: tuple[float, float], man_of=None) -> list[int]:
        """The ten players' facing for the frame just recorded, in ``everyone``'s order."""
        deg = update_facing(offense, defense, holder, ball, self.t.dt, man_of)
        at = {id(a): d for a, d in zip(offense + defense, deg)}
        return [at.get(id(a), 0) for a in self.everyone]

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
            if self.play is not None:
                tgt = plays.offense_target(self, a)
                if tgt is not None:
                    a.target(tgt)
                    continue
            if self.pnr and a is self.pnr["screener"]:
                if not self.pnr["set"]:
                    a.target(self.pnr["point"])
                elif self.now > self.pnr["set_time"] + 0.15:
                    if self.pnr.get("pop") is not None:
                        a.target(self.pnr["pop"])                # pop to the arc
                    else:
                        side = 1 if self.pnr["point"][0] >= 0 else -1
                        a.target((side * 2.0, 5.5))              # roll hard to the rim
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
            elif self.lifts(a, h):
                a.target((math.copysign(LIFT_SPOT[0], a.spot[0]), LIFT_SPOT[1]))   # baseline drive: lift
            elif (reloc := self.relocate_spot(a, d)) is not None:
                a.target(reloc)                                  # slide to an open spot on the arc
            else:
                a.target(a.spot)
                self.maybe_cut(a, d)
        if h is not None and self.mode == "setup":
            h.target(h.spot)
        elif h is not None and self.mode == "probe" and self.play is None and self.now - self.mode_since > 1.0:
            # Probe: work around your spot, sometimes attacking the elbow or short wing.
            if self.rng.random() < 0.06:
                elbow = max(0.15, min(0.8, 0.25 + 0.04 * (h.p.tendencies.mid_range - 10)))
                if self.rng.random() < elbow:
                    angle = math.radians(self.rng.uniform(25, 155))
                    r = self.rng.uniform(15, 20)
                    h.target((RIM[0] + r * math.cos(angle), RIM[1] + r * math.sin(angle)))
                else:
                    h.target((h.spot[0] + self.rng.uniform(-4, 4), h.spot[1] + self.rng.uniform(-3, 3)))

    def respace(self) -> None:
        """Fill and replace: after a catch, perimeter players whose spot is now crowded
        (too near the ball or a teammate's spot) move to the nearest open spot on the
        floor, at least ``respace_gap`` (12 ft) from the ball and each other. Players in
        an action (screener, cutter, the man coming off a screen) and bigs inside keep
        theirs."""
        if self.p.respace_gap <= 0 or self.holder is None:
            return
        h = self.holder
        busy = set()
        if self.pnr:
            busy.add(id(self.pnr["screener"]))
        if self.cutter is not None:
            busy.add(id(self.cutter))
        act = self.play["action"] if self.play else None
        if act is not None:
            for k in ("to", "screener", "fill"):
                if act.get(k) is not None:
                    busy.add(id(act[k]))
        movers, taken = [], [h.pos]
        for a in self.offense:
            if a is h:
                continue
            if id(a) in busy or C.dist_to_rim(a.spot) < 17:
                taken.append(a.spot)                     # stays: in an action, or inside
            else:
                movers.append(a)
        gap = self.p.respace_gap
        mirror = 1.0 if h.x >= 0 else -1.0
        for a in sorted(movers, key=lambda m: C.dist(m.spot, h.pos)):
            if min(C.dist(a.spot, q) for q in taken) >= gap:
                taken.append(a.spot)                     # his spot is fine
                continue
            free = [sp for sp in PERIMETER_SPOTS
                    if min(C.dist((sp[0] * mirror, sp[1]), q) for q in taken) >= gap]
            if not free:
                taken.append(a.spot)
                continue
            # The nearest open spot; a corner only when it's clearly the nearest (fill and
            # replace lifts men out of the corners more often than it sends them there).
            best = min(free, key=lambda sp: C.dist((sp[0] * mirror, sp[1]), a.pos)
                       + (self.p.respace_corner_cost if sp[1] < 10 else 0.0))
            a.spot = (best[0] * mirror, best[1])
            taken.append(a.spot)
            self.stats["respaced"] += 1

    def lifts(self, a: Agent, h: Optional[Agent]) -> bool:
        """Drive baseline, the corner lifts: a man in the corner on the side of a drive
        heading along the baseline moves up to the wing behind it (clearing the corner
        and giving the driver a pass back) instead of standing in his path."""
        return (self.p.corner_lift > 0 and self.mode == "drive" and h is not None
                and a.spot[1] < 10 and abs(a.spot[0]) > 15 and h.x * a.spot[0] > 0
                and abs(h.x) > 6.0 and h.y < self.p.corner_lift)

    def relocate_spot(self, a: Agent, d: Agent) -> Optional[tuple[float, float]]:
        """A shooter whose man has left him to help on a drive doesn't stand still: he
        slides along the arc away from his defender (drift and lift), into the
        driver's sight line for the kick-out. None: stay on his spot."""
        if (self.p.relocate_shooter <= 0 or self.mode != "drive" or self.holder is None
                or not d.helping or a.a("three_point") < self.p.relocate_shooter
                or not C.is_three(a.spot)):
            return None
        rx, ry = a.spot[0] - RIM[0], a.spot[1] - RIM[1]
        ang = math.atan2(ry, rx)
        dang = math.atan2(d.y - RIM[1], d.x - RIM[0])
        step = math.radians(self.p.relocate_degrees)
        ang += -step if math.sin(dang - ang) > 0 else step      # away from where his man went
        ang = max(math.radians(2), min(math.radians(178), ang))
        r = 24.6
        x, y = RIM[0] + r * math.cos(ang), RIM[1] + r * math.sin(ang)
        if abs(x) > C.CORNER_THREE_X - 0.5 and y < C.CORNER_THREE_Y + 1:
            x = math.copysign(22.6, x)                          # the corner is straight below
        return C.clamp((x, max(1.5, y)))

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
        Added to the learned value when choosing; it changes choices, not outcomes.
        With ``team_bias_span`` the coach's part (sliders, offensive focus and the
        coaching system's nudges together) is bent past that many points (see
        ``team_style``); ``system_bias`` then leaves the system's nudges to it."""
        if self.p.team_bias_span > 0:
            player, team = self.team_style(h, kind, action)
            return player + self.p.bend(team + self.system_style(kind, action), self.p.team_bias_span)
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
        if kind in ("hold", "reset"):
            return -0.012 * (self.p.pace(tac.pace) - 10)        # fast teams take earlier shots
        return 0.0

    def team_style(self, h: Agent, kind: str, action: tuple) -> tuple[float, float]:
        """``style_bias`` split in two: (the player's tendencies, his coach's sliders and
        offensive focus)."""
        tend = h.p.tendencies
        tac = self.teams[self.side].tactics
        focus = tac.offensive_focus.value
        k = self.t.tendency_weight
        if kind == "shoot":
            zone = action[1]
            if zone == THREE_ZONE:
                return (k * (tend.three_point - 10),
                        0.008 * (tac.three_point_rate - 10) + {"perimeter": 0.03, "inside": -0.03}.get(focus, 0.0))
            if zone == MID_ZONE:
                return k * (tend.mid_range - 10), 0.0
            return 0.0, 0.02 if focus == "inside" else 0.0
        if kind in ("drive", "continue"):
            return k * (tend.drive - 10), 0.02 if focus == "inside" else 0.0
        if kind == "post":
            return k * (tend.post_up - 10), {"inside": 0.04, "perimeter": -0.03}.get(focus, 0.0)
        if kind == "pass":
            mate = action[1]
            feed_post = 0.03 if focus == "inside" and self.can_post(mate) and C.dist_to_rim(mate.pos) < 11 else 0.0
            return 0.8 * k * (tend.passing - 10), feed_post
        if kind in ("hold", "reset"):
            return 0.0, -0.012 * (self.p.pace(tac.pace) - 10)
        return 0.0, 0.0

    def system_style(self, kind: str, action: tuple) -> float:
        """The coaching system's standing nudges to an option (``Style.bias``), without
        the star's ball (``system_bias``)."""
        bias = offense_style(self.teams[self.side].tactics).bias
        if not bias:
            return 0.0
        b = 0.0
        if kind == "shoot":
            zone = action[1]
            b += bias.get("three" if zone == THREE_ZONE else "mid" if zone == MID_ZONE else "inside", 0.0)
        elif kind in ("drive", "pnr", "post"):
            b += bias.get(kind, 0.0)
        elif kind == "pass":
            b += bias.get("pass", 0.0)
            if "feed" in bias and action[1].slot >= 3 and C.dist_to_rim(action[1].pos) < 11:
                b += bias["feed"]
        if self.shot_clock > 10:
            if kind in ("hold", "reset"):
                b += self.p.system_tempo * bias.get("hold", 0.0)
            elif kind in ("shoot", "drive", "pnr"):
                b += self.p.system_tempo * bias.get("early", 0.0)
        return b

    def patience_bias(self, h: Agent, kind: str, feats: list[float]) -> float:
        """Shot selection against a set defence: with time on the shot clock an ordinary
        look is passed up for a better one later (a swing, a reset, another action).
        Attacking options lose up to ``patience`` points of value, fully with
        ``patience_full`` seconds or more on the shot clock and not at all by
        ``patience_none``. A great look (a layup, a wide-open shot by a shooter) and a
        drive past a beaten defender are taken whenever they come; flows out of
        transition and end-of-game situations play on their own clock. Standing on the
        ball costs ``patience_hold`` of it too, so the ball moves instead. Slow teams are
        more patient, fast ones less."""
        p = self.p
        if p.patience <= 0 or kind not in ("shoot", "drive", "post", "hold") or self.situation is not None \
                or not self.set_defense:
            return 0.0
        if self.early and self.shot_clock > p.early_until:
            return 0.0                                   # early offence: take the first good look
        span = max(0.5, p.patience_full - p.patience_none)
        f = max(0.0, min(1.0, (self.shot_clock - p.patience_none) / span))
        if f <= 0.0:
            return 0.0
        if kind == "shoot":
            f *= max(0.0, min(1.0, (p.patience_great - feats[1]) / 0.25))
            if feats[2] > 0:
                f *= p.patience_three              # a catch-and-shoot three in the flow is a shot the offence wants
        elif kind == "drive" and feats[5] > 0:
            return 0.0                                   # his man is beaten: go
        elif kind == "hold":
            f *= p.patience_hold                         # ... and don't stand on it: move the ball
        pace = p.pace(self.teams[self.side].tactics.pace)
        return -p.patience * f * max(0.3, 1.0 - 0.06 * (pace - 10))

    def on_policy_decisions(self) -> list[tuple[str, list[float]]]:
        """The decisions whose possession points say what they were worth: the last
        random (exploring) one and everything after it. Earlier decisions were followed by
        a random choice the players wouldn't make, which would teach them that keeping
        the ball alive is worth less than it is."""
        return self.decisions[self.explored or 0:]

    def maybe_cut(self, a: Agent, d: Agent) -> None:
        """Off-ball players cut to the rim when their man loses them. Smart players,
        poor shooters and players whose defender is sagging cut most."""
        if (self.cutter is not None or self.play is not None or self.mode not in ("probe", "drive")
                or self.now - a.last_cut < 4.0):
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
            self.act(a, "cut")
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
        gap_shift, sag_shift = self.shout_defense()
        zone = self.scheme == "zone"
        track_now = round(now * 10) % 2 == 0           # off-ball tracking: every other tick (5 Hz)
        for d in self.defense:
            d.helping = d is helper or d is tagger or d is doubler or d is stunter
            if now < d.next_think:
                if track_now and d.track is not None and not d.helping and self.mode in TRACK_MODES:
                    # Between reads he keeps his eyes on his man and moves with him;
                    # where the ball is he only takes in at his next read.
                    man = self.man_of(d)
                    if man is not self.holder and man.vx * man.vx + man.vy * man.vy > 1.0:
                        d.target(self.off_ball_spot(man, d.track[0], d.track[1]))
                continue
            d.next_think = now + d.reaction * self.p.reaction_scale
            d.track = None
            self.closing.discard(d)
            if self.mode == "rebound":
                continue
            man = self.man_of(d)
            if self.mode == "release" and self.holder is not None and (
                    man is self.holder or C.dist(d.pos, self.holder.pos) < 8):
                # Get a hand up: lunge in as far as the shooting motion allows, but
                # contests end at hand's length, not body to body. On a gathered
                # finish, where he will go up (the defence reads the gather).
                at = self.release.get("spot") or self.holder.pos
                gap = C.dist(d.pos, at)
                p = self.p
                stop = self.contest_stop(d)
                if p.contest_run > 0 and gap > p.contest_run:
                    # Still closing out: keep sprinting at him to get a hand up (not
                    # easing off when he rises), stopping a stride off him.
                    d.target(C.toward(at, d.pos, max(p.contest_reach, stop)))
                    self.closing.add(d)
                else:
                    # Already there: a step in with the hand up (no further in than the
                    # contest stop unless he was already that tight).
                    stop = min(gap, stop)
                    d.target(C.toward(at, d.pos, max(p.contest_reach, gap - p.contest_lunge, stop)))
            elif self.play is not None and (deny := plays.defense_target(self, d)) is not None:
                d.target(deny)                                   # denying the entry pass
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
                    and self.holder is not None and not self.pnr.get("switch") \
                    and not (self.pnr.get("pop") is not None
                             and now > self.pnr["set_time"] + self.p.pop_recover):
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
                gap += gap_shift if deep >= 22 or gap_shift < 0 else 0.0     # the coach's shout
                # Anticipate: play where he's going, not where he was.
                lead = 0.45 if self.mode == "drive" else self.p.pullup_lead if self.mode == "pullup" else 0.2
                ahead = C.clamp((man.x + man.vx * lead, man.y + man.vy * lead))
                d.target(C.toward(ahead, RIM, gap))
            else:
                sag = self.off_ball_sag(man, ball, sag_shift, zone)
                d.target(self.off_ball_spot(man, ball, sag))
                if self.p.offball_track > 0:
                    d.track = (ball, sag)

    def contest_stop(self, d: Agent) -> float:
        """How far off a rising shooter this defender's contest stops (``contest_stop``):
        each closeout is a little different (``contest_spread``, fixed per defender and
        shot), so contests are spread over a range rather than piled at one distance."""
        p = self.p
        if p.contest_spread <= 0 or self.mode != "release":
            return p.contest_stop
        u = (hash((self.index[id(d)], round(self.release["at"] * 10))) % 997) / 996.0
        return p.contest_stop + p.contest_spread * (2.0 * u - 1.0)

    def off_ball_sag(self, man: Agent, ball: tuple[float, float], sag_shift: float = 0.0,
                     zone: Optional[bool] = None) -> float:
        """How far toward the rim (share of the way) a defender plays off an off-ball
        man with the ball at ``ball``: one pass away close to him, on the far side sunk
        toward the paint to help; tight to good shooters, well off poor ones."""
        p = self.p
        pass_dist = C.dist(man.pos, ball)
        # One pass away: stay close. Far side: sink into the paint to help.
        sag = (p.sag_near if pass_dist < 22 else
               min(p.sag_max, p.sag_near + 0.02 + p.sag_slope * (pass_dist - 22)))
        if zone if zone is not None else self.scheme == "zone":
            sag = max(sag, 0.22)                         # a zone packs the paint
        shooting = man.p.attributes.three_point
        if shooting >= 13:
            sag *= p.shooter_sag                         # shade toward shooters
        if p.sag_shooter_slope > 0:                      # ... and away from poor ones
            sag *= max(0.4, min(1.4, 1.0 - p.sag_shooter_slope * (shooting - 11)))
        return sag + sag_shift

    def off_ball_spot(self, man: Agent, ball: tuple[float, float], sag: float) -> tuple[float, float]:
        """The spot ``sag`` of the way from the man to the rim, a little toward the ball,
        and where he's going rather than where he was (``offball_lead``)."""
        lead = self.p.offball_lead
        if lead > 0 and (man.vx or man.vy):
            mx, my = C.clamp((man.x + man.vx * lead, man.y + man.vy * lead))
        else:
            mx, my = man.x, man.y
        return (mx + (RIM[0] - mx) * sag + (ball[0] - mx) * 0.08,
                my + (RIM[1] - my) * sag + (ball[1] - my) * 0.08)

    def closeout_gap(self, man: Agent) -> float:
        """Where a closeout stops: about 3 ft on shooters (hand up, still able to
        slide with a drive), more room for non-shooters so they can't drive past."""
        p = self.p
        d_rim = C.dist_to_rim(man.pos)
        if d_rim < p.closeout_paint_range and p.closeout_paint > 0:
            return p.closeout_paint      # a catch in the paint: wall up a step off him, rim-side
        if d_rim < 20:
            return 2.5
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
                self.play = plays.plan(self)
            return
        if self.dribble_turnover(h):
            return
        if self.early and self.shot_clock <= self.p.early_until and self.mode == "probe":
            # No good early look: into the half-court set after all.
            self.early = False
            if self.play is None and self.pnr is None:
                self.play = plays.plan(self)
        if self.play is not None and self.mode == "probe":
            plays.handler_step(self)                 # running the entry
            return
        if self.mode in ("drive", "post") and self.travel_check(h):
            return
        if self.mode == "pullup":
            if C.dist(h.pos, self.pull_point) < 1.5 or now - self.mode_since > 1.0:
                self.take_shot(h, moving=False)
            elif self.contact_ahead(h, plant=True):
                self.stats["pullup_stops"] += 1
                self.take_shot(h, moving=False)              # stop short of him and rise
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
                plays.reset(self, "post")
        if self.mode == "drive":
            if self.dig(h):
                return
            d_rim = C.dist_to_rim(h.pos)
            if d_rim < 4.5:
                self.take_shot(h, moving=False)
                return
            if self.charge_check(h):
                return
            if d_rim < self.p.gather_range and self.contact_ahead(h):
                self.stats["gathers"] += 1
                self.take_shot(h, moving=False, finish="runner")   # pick it up before the contact
                return
            if self.pnr and self.pnr.get("attack") and d_rim <= 15.5:
                self.pnr["attack"] = False
                side = 1 if h.x >= 0 else -1
                h.target((side * 2.5, 6.0))                      # at the nail: keep going if it's there
            elif self.p.finish_room > 0 and d_rim < 14.0:
                self.open_side(h)
            if now - self.mode_since > 3.5 or (now - self.mode_since > 1.0 and math.hypot(h.vx, h.vy) < 2.0):
                h.spot = C.clamp(C.toward(RIM, h.pos, max(18.0, d_rim)))
                h.target(h.spot)
                self.mode, self.mode_since = "probe", now
                self.next_decision = now + 0.3
                plays.reset(self, "drive")
                return
        if self.pnr and not self.pnr["set"]:
            scr = self.pnr["screener"]
            if C.dist(scr.pos, self.pnr["point"]) < 1.5:
                self.set_screen()
            elif now - self.pnr["called"] > 4.0:
                self.pnr = None                      # screen never came
                plays.reset(self, "pnr")
        if now < self.next_decision:
            return
        self.decide(h)

    def decide(self, h: Agent) -> None:
        """Score every option with the decision model, then pick one (smarter players
        pick the best more reliably). Decisions are recorded for training."""
        now, rng, sc = self.now, self.rng, self.shot_clock
        options: list[tuple[str, tuple, list[float]]] = []      # (kind, action, features)
        d_rim = C.dist_to_rim(h.pos)
        moving = self.mode == "drive" and not self.behind_screen(h)
        if d_rim < 30:
            # Judge the contest by where the nearest defender will be at release.
            spot = self.finish_point(h)
            closer, dd = self.nearest_defender(spot)
            catch = self.just_caught(moving)
            rt = self.release_time(h, catch) if C.zone(spot) in (MID_ZONE, THREE_ZONE) else 0.0
            predicted = self.predicted_contest(closer, h, dd, rt)
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
            step = self.step_back_option(h)
            if step is not None:
                options.append(step)
            options.append(("drive", ("drive",), self.drive_features(h)))
            if self.pnr is None and self.pnr_used < 3 and d_rim > 15:
                bigs = [a for a in (self.offense[3], self.offense[4]) if a is not h]
                scr = min(bigs, key=lambda a: C.dist(a.pos, h.pos))   # the nearer big sets it
                options.append(("pnr", ("pnr", scr), [1.0, hold_curve(sc - 4), h.a("ball_handling") - 10.0,
                                                      scr.a("strength") - 10.0]))
            options.append(("hold", ("hold",), [1.0, hold_curve(sc), sc / 24.0, float(not self.set_defense)]))
            if plays.can_reset(self):
                options.append(("reset", ("reset",), [1.0, hold_curve(sc - 2.5),
                                                      self.scramble / self.p.scramble_max]))
        else:
            options.append(("continue", ("continue",), self.drive_features(h)))
        for mate in self.offense:
            if mate is not h:
                feats = self.pass_features(h, mate)
                if feats is not None:
                    options.append(("pass", ("pass", mate), feats))

        if self.explore and rng.random() < self.explore:
            kind, action, feats = rng.choice(options)              # try something (training only)
            self.explored = len(self.decisions)
        else:
            temperature = 0.03 + 0.006 * (20 - h.a("basketball_iq"))   # smarter = closer to the best option
            scored = [(self.model.value(k, f) + self.style_bias(h, k, a) + self.situation_bias(k, a)
                       + self.shout_bias(h, k, a) + self.patience_bias(h, k, f)
                       + self.usage_bias(h, k, a), (k, a, f))
                      for k, a, f in options]
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
        elif kind == "reset":
            plays.reset(self, "choice")
            if self.play is None:
                self.next_decision = now + rng.uniform(0.6, 1.2)
        else:
            self.next_decision = now + rng.uniform(0.6, 1.2)

    def usage_share(self, a: Agent) -> float:
        """His share of his team's shots while he's been on the floor this game, starting
        from 20% (``usage_prior`` shots' worth) so a hot first few minutes don't count much."""
        k = self.p.usage_prior
        return (self.shots_taken[id(a)] + 0.2 * k) / (self.shots_on_floor[id(a)] + k)

    def usage_bias(self, h: Agent, kind: str, action: tuple) -> float:
        """Nobody carries an offence alone: past a knee, a player's share of the shots
        costs his own shots (the defence loads up on him) and passes to him (his
        teammates look elsewhere), so a star's share levels off (``usage_penalty``).
        Drives, post-ups and pick-and-rolls are left alone: they make shots for others
        too, and taxing them costs the offence more than it spreads the scoring."""
        pen = self.p.usage_penalty
        if not pen or self.shot_clock > self.p.early_until:   # early: the defence isn't set yet
            return 0.0
        if kind == "shoot":
            who = h
        elif kind == "pass":
            who = action[1]
        else:
            return 0.0
        if (self.shouts.get(self.side) or {}).get("ball_to") is who:
            return 0.0                    # the coach is shouting for him to get it
        knee = self.p.usage_knee + offense_style(self.teams[self.side].tactics).usage_room
        over = max(0.0, self.usage_share(who) - knee)
        return -pen * over * over

    def predicted_contest(self, closer: Agent, h: Agent, dd: float, rt: float) -> float:
        """How close the nearest defender will be when a shot goes up ``rt`` seconds
        after the decision: he keeps coming at his closing speed, but on a jump shot
        (``rt`` > 0) a closeout stops a stride off the shooter (``contest_stop``)
        rather than running through him."""
        arrive = dd - self.closing_speed(closer, h) * (0.35 + rt)
        if rt > 0 and self.p.contest_stop > 0:
            return max(min(dd, max(self.p.contest_reach, self.p.contest_stop)), arrive)
        return max(0.0, arrive)

    @staticmethod
    def closing_speed(defender: Agent, target: Agent) -> float:
        """How fast the defender is coming at the target (ft/s, 0 if not)."""
        dx, dy = target.x - defender.x, target.y - defender.y
        d = math.hypot(dx, dy) or 1.0
        return max(0.0, (defender.vx * dx + defender.vy * dy) / d)

    def pullup_contest(self, h: Agent, on: Agent, point: tuple[float, float], guess: float) -> float:
        """How close his man will be when a pull-up from ``point`` goes up (``pullup_contest``):
        the man starts from where he is now, not from the shooter's side, moves with the
        ball, and like any jump-shot contest stops a stride off the shooter. The
        old guess (his gap now, give or take) is the most room he can hope for. Pull-ups
        valued with the old guess were expected 6-9 ft open and went up ~3.8 ft
        contested: valued 0.2-0.28 points above what they made."""
        p = self.p
        if p.pullup_contest <= 0:
            return guess
        d0 = C.dist(on.pos, point)
        time = C.dist(h.pos, point) / max(1.0, h.max_speed) + self.release_time(h, False)
        arrive = d0 - on.max_speed * time               # the man on the ball moves with him
        stop = max(p.contest_reach, p.contest_stop) if p.contest_stop > 0 else p.contest_reach
        honest = max(min(d0, stop), arrive, 0.0)
        return min(guess, guess + p.pullup_contest * (honest - guess))

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
        contest = self.pullup_contest(h, on, point, contest)
        # Valued as it is shot: a set-and-rise jumper off the dribble (not on the move).
        ep = self.shot_value(h, p=point, contest_d=contest, defender=on, catch=False,
                             moving=self.p.pullup_on_the_move > 0)
        feats = [1.0, ep, 0.0, 1.0, max(0.0, 6.0 - self.shot_clock) / 6.0]
        return ("shoot", ("pullup", MID_ZONE, point), feats)

    def step_back_option(self, h: Agent) -> Optional[tuple]:
        """A shooter guarded up close at the arc creates his own three: a step back (or
        a side step) away from his man into a pull-up three above the break."""
        p = self.p
        if p.step_back <= 0 or h.a("three_point") < p.step_back_shooter:
            return None
        d_rim = C.dist_to_rim(h.pos)
        if not 19.0 <= d_rim <= 26.0 or h.y < C.CORNER_THREE_Y:
            return None                                   # above the break only
        on = self.guarded_by(h)
        gap = C.dist(on.pos, h.pos)
        if gap > 6.0:
            return None                                   # nobody to step away from: just shoot it
        ax, ay = h.x - on.x, h.y - on.y
        n = math.hypot(ax, ay) or 1.0
        point = (h.x + ax / n * p.step_back, h.y + ay / n * p.step_back)
        r = C.dist_to_rim(point)
        if r < 24.25:                                     # ... drifting out to the arc
            point = C.toward(RIM, point, 24.25)
        point = C.clamp(point)
        if not C.is_three(point) or C.dist_to_rim(point) > 26.5 or point[1] < C.CORNER_THREE_Y:
            return None
        # His man has to react before he can follow: the step buys about half of itself.
        contest = gap + 0.5 * C.dist(point, h.pos) + 0.03 * (h.a("ball_handling") - on.a("perimeter_defense"))
        contest = self.pullup_contest(h, on, point, contest)
        ep = self.shot_value(h, p=point, contest_d=max(0.0, contest), defender=on, catch=False)
        feats = [1.0, ep, 1.0, 1.0, max(0.0, 6.0 - self.shot_clock) / 6.0]
        return ("shoot", ("pullup", THREE_ZONE, point), feats)

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
        """A catch near the rim is finished with a gather step toward it. With
        ``finish_room`` the gather is angled (a euro step, a reverse, a runner off one
        foot) to the release point with the best shot, never into a defender's body,
        and taken as real steps (``begin_gather``)."""
        d = C.dist_to_rim(h.pos)
        # A roller or cutter catching on the move goes straight up from further out.
        reach = 11.0 if self.catch_source in ("roll", "cut") else 8.0
        room = self.p.finish_room
        if self.mode != "post" and self.just_caught(False) and d < reach:
            step = min(5.0 if reach > 8 else 3.0, d - 1.5)
            if room <= 0 or step <= 0:
                return C.toward(RIM, h.pos, max(1.5, d - (5.0 if reach > 8 else 3.0)))
            return self.best_finish(h, step, (0.0, 30.0, -30.0, 60.0, -60.0))
        return h.pos

    def open_side(self, h: Agent) -> None:
        """Near the basket a driver heads for the side of the rim with more room (a
        change of direction, a euro step) rather than into the man in his path."""
        tx = h.tx
        best, best_room = None, -1.0
        for side in (-1.0, 1.0):
            goal = (side * 2.5, 6.0)
            room = min(C.point_segment_distance(d.pos, h.pos, goal) for d in self.defense)
            if side * tx > 0:
                room += self.p.finish_room          # keep going his way unless the other side is clearly better
            if room > best_room:
                best, best_room = goal, room
        h.target(best)

    def best_finish(self, h: Agent, step: float, angles: tuple) -> tuple[float, float]:
        """The release point a gather of up to ``step`` ft can reach (headed toward the
        rim, turned by one of ``angles`` degrees) with the best shot, valued with the
        nearest defender where he will be when it goes up. Points inside a defender's
        body (1.6 ft) are out."""
        bx, by = RIM[0] - h.x, RIM[1] - h.y
        n = math.hypot(bx, by) or 1.0
        bx, by = bx / n, by / n
        cands = []
        for a in angles:
            c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
            ux, uy = bx * c - by * s, bx * s + by * c
            for f in (1.0, 0.5):
                p = C.clamp((h.x + ux * step * f, h.y + uy * step * f))
                if p[1] >= 1.0:
                    cands.append(p)
        best, best_v = None, -1.0
        for p in cands:
            # Where the defenders will be when he gets there (they keep coming).
            t = max(0.15, min(0.35, C.dist(h.pos, p) / 10.0))
            defender = min(self.defense, key=lambda d: C.dist((d.x + d.vx * t, d.y + d.vy * t), p))
            dd = C.dist((defender.x + defender.vx * t, defender.y + defender.vy * t), p)
            if dd < 1.6:
                continue                                  # he'd be stepping into the man
            v = self.shot_value(h, p=p, contest_d=dd, defender=defender)
            if v > best_v:
                best, best_v = p, v
        if best is None:                                  # boxed in: the old straight step
            return C.toward(RIM, h.pos, max(1.5, C.dist_to_rim(h.pos) - step))
        return best

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
        safe = self.p.routine_pass_risk if not lob and self.routine(h, mate, target) else 1.0
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
        risk = safe * (risk + self.errant_chance(h, d, lob))
        guard = self.guarded_by(mate)
        scramble = 0.0
        if self.p.rotation_lag > 0:      # he reads the pass, late if the defence is scrambled
            scramble = self.scramble_after(mate)
            delay = guard.reaction * self.p.reaction_scale * (self.p.pass_read + self.p.rotation_lag * scramble)
        else:
            delay = guard.reaction * self.p.reaction_scale
        contest_after = max(0.0, C.dist(guard.pos, target) - guard.max_speed * self.p.closeout_speed
                            * max(0.0, flight - delay))
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
        return [1.0, catch_shoot, clock, risk, back, best, float(d_target < 8), float(lob),
                scramble / self.p.scramble_max]

    # ------------------------------------------------------------------ actions
    def dribble_turnover(self, h: Agent) -> bool:
        on = self.guarded_by(h)
        dd = C.dist(h.pos, on.pos)
        if dd >= 3.5 or self.now < on.stuck_until:
            return False
        rate = (self.p.dribble_turnover_per_sec
                * math.exp(0.15 * (on.a("stealing") - 10) - 0.15 * (h.a("ball_handling") - 10))
                * (3.5 - dd) / 2.5 * (1.6 if self.scheme == "press" else 1.0))
        if self.mode == "post":
            rate *= self.p.post_strip            # backing down, the ball is behind his body
        if self.rng.random() < rate * self.t.dt:
            self.credit(h, "tov")
            self.stats["tov"] += 1
            self.stats["strips"] += 1
            if self.rng.random() < 0.55:
                self.credit(on, "stl")
                self.stats["steals"] += 1
                self.act(on, "steal", h)
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
                    self.act(d, "steal", h)
                    self.say(f"{d.p.name} digs down and pokes it away from {h.p.name}!")
                else:
                    self.say(f"{d.p.name} gets a hand on it: out of bounds off {h.p.name}")
                self.end("tov")
                return True
        return False

    def contact_ahead(self, h: Agent, plant: bool = False) -> bool:
        """Is a defender in front of the ball handler about to be in his body (within
        ``gather_contact`` of the 1.6 ft personal space on the next step)? A driver
        picks the ball up then (layup, runner or floater from where he is) and a
        pull-up shooter stops short, rather than finishing through the man. ``plant``:
        a jump shot, which still carries him the length of his stop."""
        reach = self.p.gather_contact
        if reach <= 0:
            return False
        speed = math.hypot(h.vx, h.vy)
        if speed > 2.0:
            hx, hy = h.vx / speed, h.vy / speed
        else:
            hx, hy = RIM[0] - h.x, RIM[1] - h.y
            n = math.hypot(hx, hy) or 1.0
            hx, hy = hx / n, hy / n
        dt = self.t.dt
        for d in self.defense:
            dx, dy = d.x - h.x, d.y - h.y
            gap = math.hypot(dx, dy)
            if gap > 5.0 or gap < 1.6 + 0.4 or dx * hx + dy * hy < 0.5 * gap:
                continue                     # not within 60 degrees of his path, or already on him
            closing = max(0.0, ((h.vx - d.vx) * dx + (h.vy - d.vy) * dy) / (gap or 1.0))
            if plant:
                stop = h.accel * max(1.0, self.p.release_stop)
                closing_by = closing * dt + speed * speed / (2.0 * stop)
            else:
                closing_by = closing * 2 * dt              # a help defender can step in quickly
            if gap - closing_by < 1.6 + reach:
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
                self.act(d, "take_charge", h)
                self.act(h, "charge", d)
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
        self.act(h, "drive", on)
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
        self.act(screener, "screen", h)
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
        self.pnr["pop"] = self.pop_spot(h, scr)
        if self.pnr["pop"] is not None:
            self.stats["pops"] += 1
        self.start_drive(h)
        # Come off downhill toward the free-throw line and read the big: pull up if
        # he drops, hit the roller if he steps up, keep going if there's a lane.
        h.target(C.toward(RIM, h.pos, min(15.0, C.dist_to_rim(h.pos))))
        self.pnr["attack"] = True
        self.next_decision = self.now + (0.1 if self.behind_screen(h) else 0.25)

    def behind_screen(self, h: Agent) -> bool:
        """Coming off a screen still behind the arc, a shooter can stop and rise (the
        pull-up three when his man goes under or is caught): a set shot off the
        dribble, not one on the move (``step_back`` on, shooters of
        ``step_back_shooter``+)."""
        pnr = self.pnr
        return (self.p.step_back > 0 and pnr is not None and pnr["set"] and not pnr.get("switch")
                and self.now - pnr["set_time"] < 0.6 and h.a("three_point") >= self.p.step_back_shooter
                and C.is_three(h.pos))

    def pop_spot(self, h: Agent, scr: Agent) -> Optional[tuple[float, float]]:
        """A screener who can shoot pops to the arc above the break (on the side the
        handler left) instead of rolling: the pick-and-pop. None: he rolls."""
        p = self.p
        if p.pop_shooter <= 0 or scr.a("three_point") < p.pop_shooter:
            return None
        # Bigger shooters pop more often; the rest still roll some of the time.
        if self.rng.random() >= min(0.9, 0.5 + 0.08 * (scr.a("three_point") - p.pop_shooter)):
            return None
        sx = scr.x + (scr.x - h.x) * 2.0
        ang = math.atan2(max(scr.y, 16.0) - RIM[1], sx - RIM[0])
        ang = max(math.radians(40), min(math.radians(140), ang))
        return C.clamp((RIM[0] + 24.6 * math.cos(ang), RIM[1] + 24.6 * math.sin(ang)))

    def routine(self, h: Agent, mate: Agent, target: tuple[float, float]) -> bool:
        """A routine perimeter pass: short, from a passer under no pressure, to a man
        who isn't being denied. These are rarely lost; turnovers come from passes
        into traffic."""
        if C.dist_to_rim(h.pos) < 19 or C.dist_to_rim(target) < 19 or C.dist(h.pos, target) > 25:
            return False
        on = self.guarded_by(h)
        if C.dist(on.pos, h.pos) < ROUTINE_PRESSURE and self.now >= on.stuck_until:
            return False
        guard = self.guarded_by(mate)
        return C.point_segment_distance(guard.pos, h.pos, target) > 3.0

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
        safe = self.p.routine_pass_risk if not lob and self.routine(h, mate, target) else 1.0
        self.act(h, "lob_pass" if lob else "pass", mate)
        source = ("cut" if self.cutter is mate else
                  "roll" if self.pnr and self.pnr["set"] and mate is self.pnr["screener"] else None)
        # Interception? (Lobs go over the top; they're contested at the catch instead.)
        for de in ([] if lob else self.defense):
            lane = C.point_segment_distance(de.pos, h.pos, target)
            if lane < 2.0 and C.dist(de.pos, h.pos) > 1.5:
                p = (safe * self.p.pass_steal_scale * (1.4 if self.scheme == "press" else 1.0)
                     * math.exp(0.12 * (de.a("stealing") - 10))
                     * (2.0 - lane) / 2.0 * (1.3 if d > 25 else 1.0))
                if self.rng.random() < p:
                    self.flight = Flight("pass", h.pos, de.pos, self.now, max(0.2, C.dist(h.pos, de.pos) / self.t.pass_speed),
                                         data={"steal": de, "passer": h})
                    self.holder = None
                    self.mode = "pass"
                    return
        if self.rng.random() < safe * self.errant_chance(h, d, lob):
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
        rs = self.p.reaction_scale
        if self.p.rotation_lag > 0:
            # A defence that was bent (helping, stunting, sagged far off) falls a step
            # further behind with every pass; one that was set re-sets a little.
            self.scramble = self.scramble_after(mate)
            lag = self.p.rotation_lag * self.scramble
            guard.next_think = self.now + guard.reaction * rs * (self.p.pass_read + lag)
            for de in self.defense:            # the rest watch the ball before they rotate
                if de is not guard:
                    de.next_think = max(de.next_think, self.now + de.reaction * rs * 0.5 * lag)
        else:
            guard.next_think = min(guard.next_think, self.now + guard.reaction * rs * self.p.pass_read)
        self.holder = None
        self.mode = "pass"
        self.passes += 1
        self.stats["passes"] += 1

    def scramble_after(self, mate: Agent) -> float:
        """The defence's scramble level once the ball goes to ``mate``."""
        guard = self.guarded_by(mate)
        out = C.dist(guard.pos, mate.pos) - self.closeout_gap(mate)
        if guard.helping or out > self.p.scramble_bend:
            return min(self.p.scramble_max, self.scramble + 1.0)
        return max(0.0, self.scramble - 1.0)

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
                    self.act(de, "block", mate)
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
        self.act(h, "jumper")
        h.target(h.pos)
        for d in self.defense:              # the shot motion is a cue: defenders nearby react quickly
            if C.dist(d.pos, h.pos) < 12:
                d.next_think = min(d.next_think, self.now + d.reaction * self.p.reaction_scale * 0.5)

    def begin_gather(self, h: Agent, moving: bool, spot: tuple[float, float]) -> None:
        """A finish with a gather (``finish_room``): he takes his steps to the release
        point before going up, and the defence keeps moving meanwhile; the shot (and
        the contest) is where he really is when it goes up."""
        self.release = {"mode": self.mode, "moving": moving, "catch": self.just_caught(moving),
                        "at": self.now + max(0.15, min(0.35, C.dist(h.pos, spot) / 10.0)), "spot": spot}
        self.mode = "release"
        h.target(spot)
        for d in self.defense:
            if C.dist(d.pos, h.pos) < 12:
                d.next_think = min(d.next_think, self.now + d.reaction * self.p.reaction_scale * 0.5)

    def take_shot(self, h: Agent, moving: bool, finish: Optional[str] = None,
                  released: Optional[bool] = None) -> None:
        """``released``: None to start a shot; for a jumper (or a gathered finish), the
        catch flag once it's released."""
        rng, t = self.rng, self.t
        if self.first_shot_at is None:
            self.first_shot_at = self.now
        lob = finish == "lob"
        post = self.mode == "post"
        gathered = released is not None and "spot" in self.release
        runner = finish == "runner"
        if gathered or runner:
            spot = h.pos                       # he took his steps (or goes up off them now): from where he is
        else:
            spot = C.toward(RIM, h.pos, min(1.5, C.dist_to_rim(h.pos))) if lob else self.finish_point(h)
        if released is None and not lob and not post and not runner and C.zone(spot) in (MID_ZONE, THREE_ZONE):
            self.begin_release(h, moving)
            return
        if (released is None and not lob and not post and not runner and self.p.finish_room > 0
                and C.dist(spot, h.pos) > 0.25):
            self.begin_gather(h, moving, spot)
            return
        if (self.record and released is None and not lob and self.catch_time == self.now
                and self.frames and self.frames[-1][5] != self.index[id(h)]):
            # Caught and shot in the same tick (a set play's read on the catch): show
            # him with the ball first. Recording only: the play is the same either way.
            self.record_frame()
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
        elif spot != h.pos or gathered:
            verb = "gathers and goes up"
        source = ("lob" if lob else "post" if post else "putback" if self.now - self.orb_time < 2.0
                  else "drive" if self.mode == "drive"
                  else (self.catch_source or ("dunker" if C.dist_to_rim(h.pos) < 8 else "catch"))
                  if (catch if released is not None else self.just_caught(moving)) else "other")
        share = self.usage_share(h)
        self.shots_taken[id(h)] += 1
        for a in self.offense:
            self.shots_on_floor[id(a)] += 1
        # Block?
        if dd < 4.0:
            p_block = self.block_chance(h, defender, z, dd)
            if rng.random() < max(0.0, p_block):
                self.count_attempt(h, z)
                self.credit(defender, "blk")
                self.stats["blocks"] += 1
                self.act(h, self.finish_action(h, z, spot, dd, lob, post, None if gathered else released), defender)
                self.act(defender, "block", h)
                self.say(f"{h.p.name} {verb}... BLOCKED by {defender.p.name}!")
                land = C.clamp((h.x + rng.uniform(-6, 6), h.y + rng.uniform(2, 8)))
                self.start_rebound(h.pos, land, 0.6)
                return
        fouled = rng.random() < self.foul_chance(h, z, dd, share)
        if fouled:
            prob *= t.fouled_make
        made = rng.random() < prob
        bucket = "tight" if dd < 2 else "close" if dd < 4 else "open" if dd < 6 else "wide"
        ctx = ("drive" if self.mode == "drive" else "putback" if self.now - self.orb_time < 2.0
               else "catch" if (catch if released is not None else self.now - self.catch_time < 1.0)
               else "other")
        if made or not fouled:                 # a missed shot that drew a foul isn't an attempt
            self.count_attempt(h, z)
            self.stats[f"contest_{z}_{bucket}"] += 1
            self.stats[f"ctx_{z}_{ctx}"] += 1
            self.stats[f"ctxwide_{z}_{ctx}"] += bucket == "wide"
            self.stats[f"shot_clock_{z}"] += self.shot_clock
            self.stats[f"src_{z}_{source}"] += 1
        assisted = (self.last_passer is not None and self.now - self.catch_time < 2.5)
        duration = 0.55 + shot_d / 45.0
        self.flight = Flight("shot", spot, RIM, self.now, duration, apex=6 + shot_d * 0.25, z0=7.5, z1=10.0,
                             data={"shooter": h, "made": made, "points": points, "zone": z, "fouled": fouled,
                                   "defender": defender, "assisted": assisted, "passer": self.last_passer,
                                   "shot_d": shot_d, "bucket": bucket, "ctx": ctx})
        self.act(h, self.finish_action(h, z, spot, dd, lob, post, None if gathered else released), defender)
        if dd < 6.0:
            self.act(defender, "contest", h)
        self.holder = None
        self.mode = "shot"
        self.pnr = None
        self.post = None
        self.say(f"{h.p.name} {verb}" + (f" over {defender.p.name}" if level > 0.6 else "")
                 + (" (fouled)" if fouled else ""))

    @staticmethod
    def finish_action(h: Agent, z: str, spot, dd: float, lob: bool, post: bool, released) -> str:
        """The shooting motion for the viewer (no effect on the result)."""
        if released is not None:
            return "jumper_release"                 # the jumper begun earlier goes up now
        if lob:
            return "alley_oop"
        if post:
            return "hook" if z in (RIM_ZONE, CLOSE_ZONE) else "fadeaway"
        if z == RIM_ZONE:
            spring = h.a("vertical") + (h.p.physique.height_cm - 198) / 4
            return "dunk" if C.dist_to_rim(spot) < 3.5 and spring >= 14 and dd >= 1.5 else "layup"
        if z == CLOSE_ZONE:
            return "floater"
        return "jumper_release"

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
        self.last_flight = f
        if f.kind == "pass":
            if "steal" in f.data:
                thief, passer = f.data["steal"], f.data["passer"]
                self.credit(passer, "tov")
                self.credit(thief, "stl")
                self.stats["tov"] += 1
                self.stats["steals"] += 1
                self.stats["pass_steals"] += 1
                self.act(thief, "intercept", passer)
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
            self.act(mate, "catch", f.data["passer"])
            self.holder = mate
            self.pnr = None
            self.post = None
            self.last_passer = f.data["passer"]
            self.catch_time = self.now
            self.catch_source = f.data.get("source")
            if self.cutter is mate:
                self.cutter = None
            self.mode, self.mode_since = "probe", self.now
            if C.dist_to_rim(mate.pos) > 8:
                mate.spot = mate.pos
            self.respace()
            if self.play is not None:
                plays.on_catch(self, mate)
                return
            if f.data.get("lob"):
                self.finish_lob(mate, f.data["passer"])
                return
            d_rim = C.dist_to_rim(mate.pos)
            if self.can_post(mate) and d_rim < 11 and self.catch_source is None:
                self.mode, self.post = "post", {"since": self.now, "double": None}
                self.stats["post_ups"] += 1
                self.act(mate, "post_up")
                self.say(f"{mate.p.name} posts up")
                self.next_decision = self.now + 0.6
            else:
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
            self.stats[f"contest_made_{d['zone']}_{d.get('bucket')}"] += 1
            self.stats[f"ctx_made_{d['zone']}_{d.get('ctx')}"] += 1
            if d["zone"] == THREE_ZONE:
                self.credit(shooter, "tpm")
            assist = ""
            if d["assisted"] and d["passer"] is not None:
                self.credit(d["passer"], "ast")
                self.stats["ast"] += 1
                assist = f" (assist {d['passer'].p.name})"
            self.points += d["points"]
            if d["fouled"]:
                self.act(d["defender"], "foul", shooter)
            self.say(f"Good!{assist}" + (" And one!" if d["fouled"] else ""))
            if d["fouled"]:
                self.free_throws(shooter, 1, d["defender"])
            else:
                self.end("score")
        elif d["fouled"]:
            n = d["points"]
            self.act(d["defender"], "foul", shooter)
            self.say(f"Foul on {d['defender'].p.name}: {n} free throws")
            self.free_throws(shooter, n, d["defender"])
        else:
            self.say("No good")
            self.miss_zone = d["zone"]
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
            w = math.exp(self.p.rebound_rating * (a.a(attr) - 10) + (a.p.physique.height_cm - 203) / 24) / (1 + dd * dd / 6)
            if a in self.defense:
                w *= self.p.defensive_rebound_edge * f.data.get("edge", 1.0)
            candidates.append((w, a))
        if not candidates:                                   # nobody near: closest gets it
            a = min(self.everyone, key=lambda q: C.dist(q.pos, land))
            candidates = [(1.0, a)]
        winner = self.rng.choices([a for _, a in candidates], [w for w, _ in candidates])[0]
        for _, a in candidates:
            self.act(a, "rebound" if a is winner else "rebound_jump")
        zone = getattr(self, "miss_zone", None)
        self.miss_zone = None
        self.stats[f"reb_{zone}_{'d' if winner in self.defense else 'o'}"] += 1
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
            self.chase_loose_ball(winner, land)
            self.holder = winner
            self.last_passer = None
            self.mode, self.mode_since = "probe", self.now
            self.shot_clock = max(self.shot_clock, 14.0)
            winner.spot = C.clamp(C.toward(RIM, winner.pos, max(C.dist_to_rim(winner.pos), 4.0)))
            self.next_decision = self.now + 0.3

    def walk_to(self, spots: dict, max_seconds: float = 8.0) -> None:
        """A dead ball (clock stopped): everyone walks, or jogs if it's far, to his spot,
        recorded frame by frame, then stands on it."""
        dt = self.t.dt
        waited = 0.0
        while waited < max_seconds:
            moving = False
            for a, (x, y) in spots.items():
                dx, dy = x - a.x, y - a.y
                d = math.hypot(dx, dy)
                if d > 0.25 or math.hypot(a.vx, a.vy) > 0.5:
                    moving = True
                    v = min(max(5.0, min(15.0, d / 1.6)), 3.0 * d)   # far: jog there; near: walk, and stop
                    a.steer((dx / d * v, dy / d * v) if d > 0.05 else (0.0, 0.0), dt)
                    a.x, a.y = C.clamp((a.x + a.vx * dt, a.y + a.vy * dt), full=a.y > C.HALF_LENGTH - 0.5)
            if not moving:
                break
            self.record_frame()
            self.now += dt
            waited += dt
        for a, (x, y) in spots.items():
            a.place(x, y)

    def chase_loose_ball(self, winner: Agent, land: tuple[float, float]) -> None:
        """The ball comes down a step or two from the rebounder: he goes and gets it
        (everyone keeps moving) instead of appearing on it."""
        dt = self.t.dt
        loose = Flight("rebound", land, land, self.now, 1.0, z0=3.0, z1=3.0)
        winner.target(land)
        for _ in range(10):
            if C.dist(winner.pos, land) <= 1.5:
                break
            for a in self.everyone:
                a.move(dt, self.now, 1.25 if a is winner else 1.0)
            self.flight, saved = loose, self.flight
            self.record_frame()
            self.flight = saved
            self.now += dt
            if self.game_clock is not None:
                self.game_clock -= dt

    def free_throws(self, shooter: Agent, n: int, fouler: Agent) -> None:
        self.credit(fouler, "pf")
        self.mode = "ft"
        others = [a for a in self.everyone if a is not shooter]
        lane = list(LANE_SPOTS)
        defenders = [a for a in others if a in self.defense]
        attackers = [a for a in others if a in self.offense]
        spots = {shooter: (0.0, C.FREE_THROW_Y)}
        for a, spot in zip(defenders[:2] + attackers[:2] + defenders[2:3] + attackers[2:3], lane):
            spots[a] = spot
        rest = defenders[3:] + attackers[3:]
        for i, a in enumerate(rest):
            spots[a] = ((-1) ** i * (10 + 3 * i), 24.0)
        held, self.holder = self.holder, shooter          # the official hands him the ball
        self.walk_to(spots)                                # line up for the free throws (no teleport)
        pct = self.free_throw_pct(shooter)
        made = 0
        last_missed = False
        for _ in range(n):
            self.credit(shooter, "fta")
            self.stats["fta"] += 1
            for _ in range(8):
                self.record_frame()
                self.now += self.t.dt
            self.act(shooter, "free_throw")
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
        self.holder = held
        if last_missed:
            land = C.clamp((self.rng.uniform(-6, 6), RIM[1] + self.rng.uniform(3, 7)))
            self.start_rebound(RIM, land, 0.6, edge=2.2)
        else:
            self.end("score" if made else "ft")

    # ------------------------------------------------------------------ full-game hooks
    def apply_start(self, start: dict) -> None:
        """Possession-start options from the full-game driver (fullgame.py, transition.py):

        * ``positions``: {agent: (x, y, vx, vy)}, e.g. players still running in transition
          (y beyond half court is allowed);
        * ``holder``, ``shot_clock``, ``guards`` (matchups: guards[offence slot] = defence slot);
        * ``spots``: {agent: (x, y)} spacing spots to use instead of the set's (early
          offence: stay in the corner or trail spot you ran to);
        * ``passer`` and ``caught`` (seconds ago, <= 0): the pass the holder just caught,
          for assists and catch-and-shoot;
        * ``game_clock``: seconds left in the period. The shot clock is off below it and
          the possession ends at the buzzer (result "period");
        * ``mode``: "setup", "probe" (early offence) or "drive" (attack the rim), with
          ``decide_in`` seconds to the first decision;
        * ``foul_at``: a non-shooting foul this many seconds in (by ``foul_by`` if given,
          an intentional foul), two free throws if ``bonus`` else result "foul";
        * ``situation``: clock-management preferences (see ``situation_bias``);
        * ``entry``: (kind, target agent or None), a set play to run first (``plays.plan``).
        """
        for a, (x, y, vx, vy) in start.get("positions", {}).items():
            a.x, a.y, a.vx, a.vy = x, y, vx, vy
            a.tx, a.ty = C.clamp((x, y))
        if "holder" in start:
            self.holder = start["holder"]
        if "shot_clock" in start:
            self.shot_clock = start["shot_clock"]
        if "guards" in start:
            self.guards = list(start["guards"])
        for a, spot in start.get("spots", {}).items():
            a.spot = spot
        if start.get("passer") is not None:
            self.last_passer = start["passer"]
            self.catch_time = start.get("caught", 0.0)
        self.game_clock = start.get("game_clock")
        self.foul_at = start.get("foul_at")
        self.foul_by = start.get("foul_by")
        self.bonus = start.get("bonus", False)
        self.situation = start.get("situation")
        self.forced_entry = start.get("entry")
        self.early = bool(start.get("early"))   # early offence out of a brisk walk-up
        mode = start.get("mode")
        if mode in ("probe", "drive") and not self.early:
            self.set_defense = False           # early offence: attack before the defence sets
        if mode is not None:
            self.next_decision = start.get("decide_in", 0.0)
            self.mode, self.mode_since = ("probe" if mode == "drive" else mode), 0.0
            if mode == "drive":
                self.start_drive(self.holder)

    def ball_spot(self) -> tuple[float, float]:
        """Where the ball is, or where it went dead: with the holder, in the air, or where
        the last pass or shot ended up (a pass thrown out of bounds)."""
        if self.holder is not None:
            return self.holder.pos
        if self.flight is not None:
            return self.flight.position(self.now)[:2]
        if self.last_flight is not None:
            return self.last_flight.end
        return RIM

    def clock_hooks(self) -> None:
        """The period buzzer and scheduled non-shooting fouls (full games only)."""
        if self.game_clock is not None and self.game_clock <= 1e-6 \
                and not (self.flight and self.flight.kind == "shot"):
            self.say("End of the period")
            self.end("period")
            return
        if (self.foul_at is not None and self.now >= self.foul_at and self.holder is not None
                and self.mode in ("setup", "probe", "drive", "post", "pullup")):
            self.foul_at = None
            fouler = self.foul_by
            if fouler is None:
                fouler = self.guarded_by(self.holder) if self.rng.random() < 0.6 else self.rng.choice(self.defense)
            self.stats["non_shooting_fouls"] += 1
            self.act(fouler, "foul", self.holder)
            if self.bonus:
                self.say(f"Foul on {fouler.p.name}: in the penalty, two shots")
                # This tick's movement is done: show it before the walk to the lane
                # (otherwise the first walk frame carries two ticks of running).
                self.record_frame()
                self.now += self.t.dt
                self.free_throws(self.holder, 2, fouler)
            else:
                self.credit(fouler, "pf")
                self.say(f"Foul on {fouler.p.name}")
                self.end("foul")

    def situation_bias(self, kind: str, action: tuple) -> float:
        """Clock management from the full-game driver, in points of option value:
        ``hold`` (milk the clock or play for the last shot while the shot clock is above
        ``hold_until``), ``hurry`` (two-for-one, chasing) and ``three`` (need a three)."""
        s = self.situation
        if not s:
            return 0.0
        holding = self.shot_clock > s.get("hold_until", 0.0)
        hold = s.get("hold", 0.0) if holding else 0.0
        if kind == "hold":
            return hold - s.get("hurry", 0.0)
        if kind == "shoot":
            three = s.get("three", 0.0)
            return (three if action[1] == THREE_ZONE else -three) - hold
        if kind in ("drive", "pnr"):
            return -0.5 * hold
        return 0.0

    # A live coach's shouts (fullgame.py), in points of option value. Small next to the
    # learned values: they tilt close calls, they don't override the read.
    SHOUTS = {
        "attack_rim": {"drive": 0.05, "continue": 0.04, "pnr": 0.02, "inside": 0.04, "jumper": -0.05},
        "threes": {"three": 0.09, "mid": -0.04, "inside": -0.01, "drive": -0.02},   # three was 0.06 before patience
        "post": {"post": 0.06, "feed": 0.06, "inside": 0.02},
        "slow": {"hold": 0.20, "early": -0.04, "hold_until": 6.0},
        "push": {"hold": -0.10, "early": 0.03, "hold_until": 14.0},
        "ball_to": {"pass": 0.08, "self": 0.03},
    }
    DEFENSE_SHOUTS = {"pressure": (-1.2, -0.03), "paint": (1.2, 0.08)}   # (on-ball gap ft, sag)

    def shout_bias(self, h: Agent, kind: str, action: tuple) -> float:
        """The offence's coach shouting from the bench (``self.shouts``): attack the rim,
        look for threes, slow it down, push the pace, feed the post, get X the ball."""
        b = self.system_bias(h, kind, action)
        s = self.shouts.get(self.side)
        if not s:
            return b
        sh = self.SHOUTS
        if kind == "shoot":
            zone = action[1]
            inside = zone in (RIM_ZONE, CLOSE_ZONE)
            if "attack_rim" in s:
                b += sh["attack_rim"]["inside"] if inside else sh["attack_rim"]["jumper"]
            if "threes" in s:
                b += (sh["threes"]["three"] if zone == THREE_ZONE else sh["threes"]["mid"] if zone == MID_ZONE
                      else sh["threes"]["inside"])
            if "post" in s and inside:
                b += sh["post"]["inside"]
        elif kind in ("drive", "continue", "pnr"):
            if "attack_rim" in s:
                b += sh["attack_rim"][kind]
            if "threes" in s and kind == "drive":
                b += sh["threes"]["drive"]
        elif kind == "post" and "post" in s:
            b += sh["post"]["post"]
        elif kind == "pass":
            mate = action[1]
            if "post" in s and mate.slot >= 3 and C.dist_to_rim(mate.pos) < 11:
                b += sh["post"]["feed"]
            if s.get("ball_to") is mate:
                b += sh["ball_to"]["pass"]
        if s.get("ball_to") is h and kind in ("shoot", "drive", "post"):
            b += sh["ball_to"]["self"]
        for tempo in ("slow", "push"):
            if tempo in s and self.shot_clock > sh[tempo]["hold_until"]:
                if kind in ("hold", "reset"):
                    b += sh[tempo]["hold"]
                elif kind in ("shoot", "drive", "pnr"):
                    b += sh[tempo]["early"]
        return b

    def system_bias(self, h: Agent, kind: str, action: tuple) -> float:
        """The offence's coaching system (systems.py): standing nudges to its players'
        choices, smaller than a shout (more threes, post feeds, pass the ball...)."""
        tactics = self.teams[self.side].tactics
        st = offense_style(tactics)
        b = self.option_bias(h, kind, action, tactics) if tactics.scoring_options else 0.0
        if self.p.team_bias_span > 0:                   # the nudges are in style_bias, bent
            if st.star_ball:
                if kind == "pass" and action[1] is self.star(self.side):
                    b += st.star_ball
                if h is self.star(self.side) and kind in ("shoot", "drive", "post", "pnr"):
                    b += 0.5 * st.star_ball
            return b
        if not st.bias and not st.star_ball:
            return b
        bias = st.bias
        if kind == "shoot":
            zone = action[1]
            b += bias.get("three" if zone == THREE_ZONE else "mid" if zone == MID_ZONE else "inside", 0.0)
        elif kind in ("drive", "pnr", "post"):
            b += bias.get(kind, 0.0)
        elif kind == "pass":
            mate = action[1]
            b += bias.get("pass", 0.0)
            if "feed" in bias and mate.slot >= 3 and C.dist_to_rim(mate.pos) < 11:
                b += bias["feed"]
            if st.star_ball and mate is self.star(self.side):
                b += st.star_ball
        if st.star_ball and h is self.star(self.side) and kind in ("shoot", "drive", "post", "pnr"):
            b += 0.5 * st.star_ball
        if self.shot_clock > 10:
            if kind in ("hold", "reset"):
                b += self.p.system_tempo * bias.get("hold", 0.0)
            elif kind in ("shoot", "drive", "pnr"):
                b += self.p.system_tempo * bias.get("early", 0.0)
        return b

    def option_bias(self, h: Agent, kind: str, action: tuple, tactics) -> float:
        """The coach's 1st, 2nd and 3rd scoring options: teammates look for them and they
        look for their own shot; the 1st option most, and all the more when the team is
        holding for the last shot of a period."""
        ranks = option_ranks(tactics, [a.p.id for a in self.agents[self.side]])
        if not ranks:
            return 0.0
        late = LATE_GAME_FIRST if self.situation is not None else 1.0
        b = 0.0
        if kind == "pass":
            r = ranks.get(action[1].p.id)
            if r is not None:
                b += OPTION_PASS[r] * (late if r == 0 else 1.0)
        r = ranks.get(h.p.id)
        if r is not None and kind in ("shoot", "drive", "post", "pnr"):
            b += OPTION_SELF[r] * (late if r == 0 else 1.0)
        return b

    def star(self, side: str) -> Agent:
        """The best player of ``side`` on the floor (heliocentric offences feed him), or
        the coach's 1st scoring option when he's on it."""
        ranks = option_ranks(self.teams[side].tactics, [a.p.id for a in self.agents[side]])
        first = next((a for a in self.agents[side] if ranks.get(a.p.id) == 0), None)
        return first or max(self.agents[side], key=lambda a: a.p.current_ability)

    def shout_defense(self) -> tuple[float, float]:
        """The defence's shout: (extra on-ball cushion in ft, extra sag toward the paint).
        Pressure the ball gets up on him and stays home; protect the paint sits back.
        A drop-coverage system sits back too, all game."""
        defending = "away" if self.side == "home" else "home"
        st = defense_style(self.teams[defending].tactics)
        s = self.shouts.get(defending)
        if not s:
            return st.gap, st.sag
        gap, sag = st.gap, st.sag
        for key, (g, k) in self.DEFENSE_SHOUTS.items():
            if key in s:
                gap, sag = gap + g, sag + k
        return gap, sag

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

