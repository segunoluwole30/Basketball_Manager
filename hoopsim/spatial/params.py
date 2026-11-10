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
    closeout_base: float = 4.82          # closeout gap on an 11-rated shooter (ft)
    closeout_slope: float = 0.413        # tighter by this per point of three-point rating
    closeout_min: float = 2.8
    closeout_max: float = 5.5
    closeout_speed: float = 1.033         # top-speed multiplier while closing out on a catch
    contest_reach: float = 1.648          # contests at release stop at hand's length (ft)
    # --- shot model ------------------------------------------------------------
    contest_inside: float = 1.126         # multiplies the contest penalty at the rim and close
    contest_outside: float = 1.602        # ... and on mid-range and three-point jumpers
    rim_make_adj: float = 0.0             # added to the open make chance at the rim
    three_make_adj: float = -0.0204         # added to the open make chance from three
    foul_scale: float = 1.0               # multiplies shooting-foul chances (search chose 1.28; held at 1, see README)
    # --- turnovers and rebounds ------------------------------------------------
    dribble_turnover_per_sec: float = 0.0576   # with a defender in your face
    pass_steal_scale: float = 0.213
    errant_pass: float = 0.0184          # passes thrown away or fumbled (not picked off)
    defensive_rebound_edge: float = 4.576
    # --- rotation lag (the defence falls behind as the ball moves) -------------
    rotation_lag: float = 0.6             # extra reaction (in reactions) per level of scramble
    scramble_bend: float = 4.0            # receiver's man this far beyond his closeout spot = defence bent (ft)
    scramble_decay: float = 0.2           # scramble levels recovered per second
    scramble_max: float = 3.0

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
    "pass_steal_scale": (0.1, 0.5),
    "errant_pass": (0.003, 0.03),
    "defensive_rebound_edge": (3.0, 7.0),
}
