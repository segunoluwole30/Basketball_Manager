"""The calibrated settings of the spatial engine's defence and shot model.

These are the knobs behind the rim-versus-arc trade-off: how early and how
deep help comes, how far the stunter and tagger leave their men, how hard
closeouts come, how much the contest takes off a shot, and the turnover and
rebounding rates that set the value of a possession. ``calibrate.py`` searches
them so the engine's output matches the half-court targets in ``measure.py``;
the defaults here are its result.

``HalfCourtGame(params=None)`` uses ``DEFAULT_PARAMS``. ``SEARCH_SPACE`` lists
the searched fields and their ranges; the rest are held fixed.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path


@dataclass(frozen=True)
class SpatialParams:
    # --- help on drives -------------------------------------------------------
    help_trigger: float = 16.498          # a drive this close to the rim draws help (ft; zones +4)
    help_depth: float = 6.887             # furthest from the rim the helper meets the driver (ft)
    help_shooter_penalty: float = 3.649   # reluctance to help off a shooter (ft of extra "cost")
    reaction_scale: float = 1.299         # multiplies every defender's think interval
    pass_read: float = 0.719              # receiver's man re-reads at this fraction of his reaction
    # --- the stunter (a perimeter defender stepping at a drive) ---------------
    stunt_distance: float = 7.59         # how far from the driver the stunt stops (ft; larger = shallower)
    stunt_range: float = 15.231           # only a defender this close to the driver stunts (ft)
    # --- the tagger (picking up a cutter or roller in the paint) --------------
    tag_zone: float = 8.395               # an open man this close to the rim is a threat (ft)
    tag_lost: float = 5.88               # ... if his own man is at least this far away (ft)
    tag_range: float = 8.32             # tagger must be this close to the threat (ft)
    drop_depth: float = 11.634             # drop big's distance from the rim once the screen is set (ft)
    # --- off-ball sag ----------------------------------------------------------
    sag_near: float = 0.175              # one pass away: fraction of the way to the rim
    sag_slope: float = 0.027            # far side: extra sag per foot beyond 22 ft from the ball
    sag_max: float = 0.61
    shooter_sag: float = 0.931           # sag multiplier off good shooters
    # --- closeouts and contests ------------------------------------------------
    # Closeouts brake in time now (defender_brake), so they arrive under control and
    # stop where they aim: the gaps were 4.82 / 2.8 when they overran (README "Defence that recovers").
    closeout_base: float = 5.5           # closeout gap on an 11-rated shooter (ft)
    closeout_slope: float = 0.413        # tighter by this per point of three-point rating
    closeout_min: float = 3.4
    closeout_max: float = 5.5
    closeout_speed: float = 1.033         # top-speed multiplier while closing out on a catch
    contest_reach: float = 1.648          # contests at release stop at hand's length (ft)
    # --- shot model ------------------------------------------------------------
    contest_inside: float = 1.126         # multiplies the contest penalty at the rim and close
    contest_outside: float = 1.7          # ... and on mid-range and three-point jumpers (was 1.602; README "Defence that recovers")
    rim_make_adj: float = -0.015          # added to the open make chance at the rim (README "Patient offence": -0.03; "Defence that recovers": -0.025; "Values that match the engine": rim FG .660 against .666)
    three_make_adj: float = -0.026        # added to the open make chance from three (was -0.0204; README "Shots with room": -0.023; "Values that match the engine")
    short_make_adj: float = -0.02         # ... and in the short paint (README "Values that match the engine": .468 against .449)
    foul_scale: float = 1.0               # multiplies shooting-foul chances (search chose 1.28; held at 1, see README; 1.05 in README "Steadier leagues", 1.0 again in "Values that match the engine")
    # --- turnovers and rebounds ------------------------------------------------
    dribble_turnover_per_sec: float = 0.05    # (0.04 before README "Steadier leagues") with a defender in your face (was 0.0576, then 0.045; on-ball men stay closer since README "Defence that recovers")
    pass_steal_scale: float = 0.037       # was 0.213, when ~5% of passes were lost (NBA ~2-2.5%), then 0.045 (README "Defence that recovers")
    errant_pass: float = 0.0072           # passes thrown away or fumbled (not picked off; was 0.0184, then 0.007, then 0.0062)
    defensive_rebound_edge: float = 2.4   # was 4.576 (full-game ORB% .21 against .26), then 3.2 (README "Steadier leagues": ratings count less)
    # --- rotation lag (the defence falls behind as the ball moves) -------------
    rotation_lag: float = 0.6             # extra reaction (in reactions) per level of scramble
    scramble_bend: float = 4.0            # receiver's man this far beyond his closeout spot = defence bent (ft)
    scramble_decay: float = 0.2           # scramble levels recovered per second
    scramble_max: float = 3.0
    # --- set plays (plays.py) ---------------------------------------------------
    entry_actions: float = 2.0            # entry actions before attacking, at pace 10 (was 3; README "Defence that recovers")
    entry_read: float = 1.12              # a catch worth this many points (wide open) is shot in the entry
    deny_rate: float = 0.45               # chance a set defence denies a swing receiver (scaled by skill)
    entry_pnr: float = 0.5                # weight of a closing pick-and-roll among the set's last actions
    entry_drive_gap: float = 7.0          # ... and drives a freed catch only with this much daylight (ft; was 5)
    routine_pass_risk: float = 0.4        # steal and errant chances on routine perimeter passes
    second_actions: float = 2.0           # resets into a new action after a stalled attack (per possession)
    # Shooting off the dribble and spacing (README "Full-game three-point volume, part 2").
    off_dribble_penalty: float = 0.01     # make chance off the dribble (NBA pull-up ~34% vs catch ~37%)
    pullup_on_the_move: float = 0.0       # 1: value pull-up jumpers as running shots (the old mismatch)
    relocate_shooter: float = 10.0        # shooters this good slide along the arc when their man helps (0: off)
    relocate_degrees: float = 18.0        # ... by this much of the arc
    # Patience (README "Patient offence"): with the defence set and time on the shot
    # clock, a team passes up an ordinary look for a better one. Attacking options (a
    # shot, a drive without an edge, a post-up) lose this many points of value with
    # ``patience_full`` or more on the shot clock, fading to nothing at ``patience_none``.
    # Great looks (worth ``patience_great``+ points) and beaten defenders are taken anyway.
    patience: float = 0.45                # 0.5 before README "Values that match the engine"; was 0.3 (README "Shots with room": closeouts stop further off, so more looks pass the bar)
    patience_full: float = 17.0           # was 14, then 16 (README "Defence that recovers"; "Shots with room")
    patience_none: float = 3.0            # was 4 (README "Defence that recovers")
    patience_great: float = 1.25
    patience_three: float = 1.2           # ... scaled by this for threes (0.25 when closeouts overran; README "Defence that recovers"; then 0.8, 1.0, 0.9 in README "Steadier leagues" with stale values, 1.2 in "Values that match the engine")
    patience_hold: float = 0.3            # ... and standing on the ball by this share of it
    # Off-ball spacing: after a catch, a perimeter man this close (ft) to the ball or a
    # teammate's spot fills the nearest open spot instead (0: off).
    respace_gap: float = 12.0
    respace_corner_cost: float = 20.0     # ... a corner counts as this much further away (ft; README "Defence that recovers")
    corner_lift: float = 22.0             # a corner man lifts to the wing on a drive on his side below this y (ft; 0: off)
    # Defence that recovers (README "Defence that recovers"): closeouts that brake in
    # time, keep coming through the shot and are anticipated as such, off-ball men
    # tracked continuously, and sag set by how well the man shoots. 0 / 1: off.
    defender_brake: float = 0.8           # defenders slow down in time to stop at their spot (share of accel)
    closeout_burst: float = 1.35          # acceleration multiplier while closing out (a straight sprint)
    contest_run: float = 5.0              # a defender this far from a rising shooter runs at him (ft; 0: lunge only)
    contest_stop: float = 4.0             # ... a jump-shot contest stops this far off him, hand up (ft; 0: off; was 3.0, README "Shots with room")
    contest_lunge: float = 1.0            # a defender nearer than that steps in at most this far (ft; was 2.5)
    offball_lead: float = 0.5             # off-ball defenders play where their man will be this soon (s)
    offball_track: float = 1.0            # 1: off-ball defenders track their man between reads
    sag_shooter_slope: float = 0.06       # sag shrinks by this share per point of the man's three rating above 11
    pullup_lead: float = 0.45             # on a pull-up the on-ball man gives ground as on a drive (s of lead; was 0.2)
    # Shots with room (README "Shots with room"); 0 switches each off.
    # Contact: a driver or pull-up shooter picks the ball up before he runs into a man
    # in front of him (when he'd be within ``gather_contact`` ft of body contact, the
    # 1.6 ft personal space, on his next step), instead of finishing through him.
    gather_contact: float = 0.6
    gather_range: float = 6.0             # ... a driver this close to the rim (ft; further out he keeps going)
    finish_room: float = 1.5              # finishes go where there is room: angled gathers taken as real steps, the open side of the rim on drives (ft of preference for his side)
    release_stop: float = 2.0             # a jump shooter plants: he stops at this multiple of his acceleration (0: drifts to a stop)
    contest_spread: float = 0.75          # contests stop within +-this of contest_stop, one defender and shot to the next (ft)
    # Threes above the break.
    step_back: float = 3.0                # a guarded shooter at the arc steps back (or aside) this far into a pull-up three (ft)
    step_back_shooter: float = 12.0       # ... (and rises behind a screen) if his three-point rating is at least this
    pop_shooter: float = 13.0             # a screener this good from three pops to the arc instead of rolling
    pop_recover: float = 0.8              # ... and his man, dropping, recovers to him this long after the screen (s)
    four_high: float = 0.4                # share of four-out sets run with all four above the break, corners empty
    # Early offence: this share of walk-ups (at pace 10; more for fast teams) come up
    # briskly into a quick hitter, taking the first good look while the shot clock is
    # above ``early_until``; then into the set as usual.
    early_offense: float = 0.3
    early_until: float = 17.0
    # Star scoring (README "Star scoring"): nobody carries an offence alone. A player's
    # share of his team's shots while he's on the floor (this game, starting from 20% over
    # ``usage_prior`` shots) costs his own shots and passes to him
    # ``usage_penalty`` * (share - ``usage_knee``)^2 points of value above the knee (a
    # heliocentric offence's ``usage_room`` raises the knee): a 32% share costs 0.13. Not in
    # early offence (shot clock above ``early_until``): the defence isn't set yet; nor
    # for the man a "get him the ball" shout is for.
    # And the go-to scorer draws the fouls: shooting-foul odds
    # x exp(``foul_draw`` * (share - 0.22)). 0 switches each off.
    usage_penalty: float = 20.0
    usage_knee: float = 0.24
    usage_prior: float = 10.0
    foul_draw: float = 2.5
    # Steadier leagues (README "Steadier leagues"): a team's identity moves its numbers
    # about as far as in the NBA, not two or three times as far, so a 12-team league's
    # means don't follow whichever systems it happens to have. 0 (a span) or 1 (a
    # scale) switches each off; LEGACY_PARAMS has them all off.
    # The pace tactic's distance from 10 is read as ``tempo_span`` * tanh(distance /
    # ``tempo_span``): small differences kept, extremes bent (pace 14 counts as 12.7).
    tempo_span: float = 3.0
    # A coaching system's tempo levers (running after rebounds and makes, extra or fewer
    # entry actions, holding or shooting early) count this share of their face value:
    # the system also sets the pace slider, so they came on top of it.
    system_tempo: float = 0.5
    # The coach's standing nudges to one option (sliders, offensive focus and the
    # system's bias together, in points of value) are bent past ``team_bias_span``:
    # they stack (Grit and Grind's threes: -0.074), and against a choice temperature of
    # 0.03-0.12 they swung post-ups, threes and holds far more than any NBA team's
    # identity does. The players' own tendencies are not bent.
    team_bias_span: float = 0.04
    # A rebounder's edge per point of his (offensive or defensive) rebounding rating, in
    # log odds (was 0.25: a 20-rated rebounder had 12x a 10-rated one's chance from the
    # same spot, and league ORB% followed its rosters; height counts as before).
    rebound_rating: float = 0.08
    # A post-up is a contact play: the on-ball man is on him the whole time, so strips at
    # the perimeter handler's rate turned ~30% of post-ups over (NBA ~11%) and made
    # post-heavy teams' turnovers double the league's. Strips while backing down x this.
    post_strip: float = 0.5
    # An inside-focus offence puts its second big on the other block this share of the
    # time (two in, three out); otherwise he spaces like any four. Every catch on a block
    # is a post-up, so both bigs there every time made post-heavy teams post about once a
    # possession, with the turnovers and offensive rebounds that brings.
    inside_pair: float = 0.4
    # Values that match the engine (README "Values that match the engine"): the expected
    # points a decision sees for a shot are the whole shot as it's played, with the block
    # chance and the shooting foul and its free throws, not just make chance x points.
    # Rim attempts are fouled ~23% of the time and blocked ~5%: their make-only value
    # ran 0.3 points over what they made from the floor and 0.13 under what they scored,
    # while catch-and-shoot threes were valued about right. So the learned weight on
    # expected points fell with every retrain (0.94 -> 0.69) and shot quality counted for
    # less and less. 1: whole shot, 0: make chance x points.
    shot_value_whole: float = 1.0
    # A pull-up's contest is predicted from where his man is and how fast he can get to
    # the pull-up point, stopping a stride off as on any jumper (see ``pullup_contest`` in
    # sim.py). 1: on, 0: the old guess (the man's current gap).
    pullup_contest: float = 1.0
    # ... and contested as pull-ups are: the man giving ground on the dribble contests a
    # jumper off the dribble at this share of a closeout's effect. Contested ~3.8 ft off,
    # pull-ups went in at .38 from mid-range and .29 from three (NBA ~.41 and ~.33).
    dribble_contest: float = 0.6

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SpatialParams":
        known = {f.name for f in fields(cls)}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown spatial params: {sorted(unknown)}")
        return cls(**{k: float(v) for k, v in d.items()})

    def with_values(self, **changes) -> "SpatialParams":
        return replace(self, **changes)

    def pace(self, pace: float) -> float:
        """The pace tactic as the spatial engine reads it (``tempo_span``)."""
        return 10.0 + self.bend(pace - 10.0, self.tempo_span)

    @staticmethod
    def bend(x: float, span: float) -> float:
        """``span`` * tanh(``x`` / ``span``): slope 1 near 0, never past ``span`` (0: ``x``)."""
        return span * math.tanh(x / span) if span > 0 else x

    def save(self, path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=1))

    @classmethod
    def load(cls, path) -> "SpatialParams":
        return cls.from_dict(json.loads(Path(path).read_text()))


DEFAULT_PARAMS = SpatialParams()
# The hand-set values before calibration (kept for comparison and regression tests).
LEGACY_PARAMS = SpatialParams(
    help_trigger=22.0,
    help_depth=6.0,
    help_shooter_penalty=8.0,
    reaction_scale=1.0,
    pass_read=0.5,
    stunt_distance=4.5,
    stunt_range=16.0,
    tag_zone=9.0,
    tag_lost=4.0,
    tag_range=15.0,
    drop_depth=9.0,
    sag_near=0.08,
    sag_slope=0.025,
    sag_max=0.55,
    shooter_sag=0.75,
    closeout_base=3.5,
    closeout_slope=0.25,
    closeout_min=2.8,
    closeout_max=5.5,
    closeout_speed=1.0,
    contest_reach=2.5,
    contest_inside=1.0,
    contest_outside=1.0,
    rim_make_adj=0.0,
    three_make_adj=0.0,
    foul_scale=1.0,
    dribble_turnover_per_sec=0.045,
    pass_steal_scale=0.2,
    errant_pass=0.006,
    defensive_rebound_edge=4.6,
    rotation_lag=0.0,
    entry_actions=0.0,
    routine_pass_risk=1.0,
    second_actions=0.0,
    off_dribble_penalty=0.035,
    pullup_on_the_move=1.0,
    relocate_shooter=0.0,
    patience=0.0,
    respace_gap=0.0,
    respace_corner_cost=0.0,
    corner_lift=0.0,
    defender_brake=0.0,
    closeout_burst=1.0,
    contest_run=0.0,
    contest_stop=0.0,
    contest_lunge=2.5,
    offball_lead=0.0,
    offball_track=0.0,
    sag_shooter_slope=0.0,
    pullup_lead=0.2,
    gather_contact=0.0,
    release_stop=0.0,
    finish_room=0.0,
    step_back=0.0,
    pop_shooter=0.0,
    four_high=0.0,
    early_offense=0.0,
    contest_spread=0.0,
    usage_penalty=0.0,
    foul_draw=0.0,
    tempo_span=0.0,
    system_tempo=1.0,
    team_bias_span=0.0,
    rebound_rating=0.25,
    post_strip=1.0,
    shot_value_whole=0.0,
    short_make_adj=0.0,
    pullup_contest=0.0,
    dribble_contest=1.0,
    inside_pair=1.0,
)

# Searched fields: (low, high). Everything else stays at its default.
SEARCH_SPACE = {
    "help_trigger": (12.0, 26.0),
    "help_depth": (3.0, 9.0),
    "help_shooter_penalty": (0.0, 16.0),
    "reaction_scale": (0.7, 1.8),
    "pass_read": (0.25, 1.2),
    "stunt_distance": (3.0, 12.0),
    "stunt_range": (6.0, 20.0),
    "tag_zone": (4.0, 12.0),
    "tag_lost": (2.5, 7.0),
    "tag_range": (5.0, 20.0),
    "drop_depth": (5.0, 14.0),
    "sag_near": (0.0, 0.2),
    "sag_slope": (0.0, 0.05),
    "sag_max": (0.2, 0.7),
    "shooter_sag": (0.4, 1.0),
    "closeout_base": (2.8, 6.0),
    "closeout_slope": (0.0, 0.5),
    "closeout_speed": (0.7, 1.1),
    "contest_reach": (1.5, 4.5),
    "contest_inside": (0.5, 1.5),
    "contest_outside": (0.6, 1.8),
    "rim_make_adj": (-0.08, 0.08),
    "three_make_adj": (-0.05, 0.03),
    "foul_scale": (0.8, 2.0),
    "dribble_turnover_per_sec": (0.02, 0.12),
    "pass_steal_scale": (0.03, 0.5),
    "errant_pass": (0.003, 0.03),
    "defensive_rebound_edge": (2.0, 7.0),
    "rotation_lag": (0.0, 1.2),
    "scramble_decay": (0.1, 0.8),
    "entry_actions": (0.0, 4.5),
    "entry_read": (1.0, 1.35),
    "routine_pass_risk": (0.2, 1.0),
    "entry_drive_gap": (2.0, 8.0),
    "entry_pnr": (0.0, 2.0),
    "deny_rate": (0.0, 1.0),
    "second_actions": (0.0, 3.0),
    "patience": (0.0, 0.8),
    "patience_full": (10.0, 20.0),
    "patience_none": (3.0, 10.0),
}
