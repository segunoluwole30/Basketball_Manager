"""Coaching systems: offensive and defensive presets (as in NBA 2K's coach settings).

A system does two things:

* **Sliders.** Choosing it sets the team's tactics (pace, three-point rate, offensive
  focus, crashing the glass; the defensive scheme), which you can still adjust.
* **Style.** It keeps shaping how the team plays in both match engines while it is
  selected: which shots it hunts (zone weights), how the ball is shared (usage,
  assists), how often it runs, which set plays it calls and how many, and the
  small, standing nudges to the half-court players' decisions (the same currency as
  a coach's shouts, smaller).

Offence: Balanced, Pace and Space, Seven Seconds or Less, Motion (Princeton),
Triangle, Grit and Grind, Inside-Out (post-centric), Heliocentric.
Defence: Balanced man, Switch everything, Drop coverage, Zone, Full-court press.

AI teams get the system that suits their roster (``choose_for_team``).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from .models import DefensiveScheme, League, OffensiveFocus, Tactics


@dataclass(frozen=True)
class Style:
    # Statistical engine
    zone: dict = field(default_factory=dict)        # {"rim"|"close"|"mid"|"three": shot-choice multiplier}
    usage_scale: float = 1.0                         # <1 spreads shots around, >1 concentrates them
    star: float = 0.0                                # extra shot share for the best scorer (x(1+star))
    usage_room: float = 0.0                          # raises the shot-share knee and ceiling (engine.usage_ceiling)
    fast_break: float = 1.0                          # fast-break chance multiplier
    assist: float = 1.0                              # assisted-basket chance multiplier
    # Spatial engine
    bias: dict = field(default_factory=dict)         # decision nudges, points of option value
    plays: dict = field(default_factory=dict)        # set-play weights: swing, handoff, downscreen, pnr
    entry: int = 0                                   # extra (or fewer) entry actions before attacking
    push: float = 0.0                                # logit: run after rebounds and steals
    push_after_make: float = 0.0                     # logit: run after makes too
    star_ball: float = 0.0                           # spatial: get the star the ball (pass value)
    # Defence
    gap: float = 0.0                                 # on-ball cushion (ft); + sits back
    sag: float = 0.0                                 # off-ball sag toward the paint
    opp_zone: dict = field(default_factory=dict)     # opponents' shot-choice multipliers
    rim_make: float = 0.0                            # opponents' rim make chance (statistical)


@dataclass(frozen=True)
class System:
    key: str
    name: str
    blurb: str
    sliders: dict
    style: Style = Style()


OFFENSE = {s.key: s for s in [
    System("balanced", "Balanced",
           "No strong identity: a bit of everything, read the defence.",
           {"pace": 10, "three": 10, "focus": "balanced", "crash": 10}),
    System("pace_space", "Pace and Space",
           "Spread the floor, run pick-and-roll and shoot threes: the modern NBA default.",
           {"pace": 13, "three": 12, "focus": "perimeter", "crash": 8},
           Style(zone={"three": 1.0, "mid": 0.75, "close": 0.9, "rim": 1.04}, fast_break=1.1,
                 bias={"three": 0.04, "mid": -0.05, "pnr": 0.03, "post": -0.03, "drive": 0.01},
                 plays={"pnr": 1.6, "handoff": 1.2, "downscreen": 0.7}, entry=0, push=0.2)),
    System("seven_seconds", "Seven Seconds or Less",
           "Push after every rebound, steal and make; shoot early, before the defence sets.",
           {"pace": 14, "three": 12, "focus": "balanced", "crash": 6},
           Style(zone={"three": 1.08, "mid": 0.8, "rim": 1.1}, fast_break=1.45,
                 bias={"early": 0.03, "hold": -0.08, "three": 0.02, "pnr": 0.02},
                 plays={"pnr": 1.5}, entry=-1, push=0.6, push_after_make=0.5)),
    System("motion", "Motion (Princeton)",
           "Ball and player movement: cuts, hand-offs, down-screens; everyone touches it.",
           {"pace": 9, "three": 11, "focus": "balanced", "crash": 9},
           Style(zone={"close": 1.1, "three": 1.04}, usage_scale=0.75, assist=1.12,
                 bias={"pass": 0.03, "pnr": -0.02},
                 plays={"handoff": 1.9, "downscreen": 1.9, "swing": 1.4, "pnr": 0.5}, entry=1)),
    System("triangle", "Triangle",
           "Post entries, cutting off the post and mid-range reads from the triangle's spacing.",
           {"pace": 8, "three": 8, "focus": "balanced", "crash": 11},
           Style(zone={"mid": 1.45, "close": 1.25, "three": 0.9, "rim": 0.95}, usage_scale=0.85, assist=1.08,
                 bias={"post": 0.025, "feed": 0.025, "mid": 0.015, "three": -0.015, "pass": 0.02},
                 plays={"downscreen": 1.6, "swing": 1.3, "handoff": 1.2, "pnr": 0.4}, entry=1)),
    System("grit_grind", "Grit and Grind",
           "Slow it down, pound it inside, crash the offensive glass and win ugly.",
           {"pace": 6, "three": 8, "focus": "inside", "crash": 15},
           Style(zone={"close": 1.2, "rim": 1.1, "three": 0.9, "mid": 1.1}, fast_break=0.75,
                 bias={"post": 0.03, "feed": 0.03, "inside": 0.02, "three": -0.02, "hold": 0.06},
                 plays={"downscreen": 1.3}, entry=1, push=-0.8)),
    System("inside_out", "Inside-Out (post-centric)",
           "Play through the post: feed the big, then kick out to shooters when he draws help.",
           {"pace": 9, "three": 11, "focus": "inside", "crash": 12},
           Style(zone={"close": 1.35, "rim": 1.1, "mid": 0.8, "three": 0.95}, assist=1.04,
                 bias={"post": 0.035, "feed": 0.035, "mid": -0.02, "three": 0.015},
                 plays={"pnr": 0.8})),
    System("heliocentric", "Heliocentric",
           "Everything runs through the star: high pick-and-roll and isolation, the rest space.",
           {"pace": 10, "three": 11, "focus": "balanced", "crash": 9},
           Style(zone={"three": 1.04, "mid": 1.1}, star=1.2, usage_room=0.03, usage_scale=1.1, assist=0.88,
                 bias={"pnr": 0.04, "drive": 0.02, "pass": -0.01},
                 plays={"pnr": 1.8, "handoff": 0.6, "downscreen": 0.5, "swing": 0.6}, entry=0, star_ball=0.06)),
]}

DEFENSE = {s.key: s for s in [
    System("man", "Balanced man", "Man-to-man, help when beaten: the default.", {"defense": "man"}),
    System("switch", "Switch everything",
           "Switch every screen: no open pick-and-roll looks, but mismatches to hunt.", {"defense": "switch"}),
    System("drop", "Drop coverage",
           "The big drops, everyone sags: wall off the rim and live with pull-up threes.", {"defense": "man"},
           Style(gap=0.8, sag=0.06, opp_zone={"rim": 0.88, "close": 0.92, "three": 1.1, "mid": 1.12},
                 rim_make=-0.015)),
    System("zone", "Zone", "Pack the paint, concede perimeter looks.", {"defense": "zone"}),
    System("press", "Full-court press",
           "Pick up full court and trap: turnovers and tired legs, but easy baskets when broken.",
           {"defense": "press"}),
]}

NEUTRAL = Style()


def offense_style(tactics: Tactics) -> Style:
    s = OFFENSE.get(getattr(tactics, "offense_system", "balanced"))
    return s.style if s else NEUTRAL


def defense_style(tactics: Tactics) -> Style:
    s = DEFENSE.get(getattr(tactics, "defense_system", "man"))
    return s.style if s else NEUTRAL


def apply(tactics: Tactics, offense: Optional[str] = None, defense: Optional[str] = None,
          sliders: float = 1.0) -> None:
    """Switch to a system and set its sliders (``sliders`` < 1 moves them only part of
    the way from where they are: AI teams keep some of their own flavour)."""
    if offense is not None:
        if offense not in OFFENSE:
            raise ValueError(f"unknown offence system '{offense}' (try: {', '.join(OFFENSE)})")
        tactics.offense_system = offense
        sl = OFFENSE[offense].sliders
        for key, attr in (("pace", "pace"), ("three", "three_point_rate"), ("crash", "crash_offensive_glass")):
            cur = getattr(tactics, attr)
            setattr(tactics, attr, int(round(cur + (sl[key] - cur) * sliders)))
        tactics.offensive_focus = OffensiveFocus(sl["focus"])
    if defense is not None:
        if defense not in DEFENSE:
            raise ValueError(f"unknown defence system '{defense}' (try: {', '.join(DEFENSE)})")
        tactics.defense_system = defense
        tactics.defensive_scheme = DefensiveScheme(DEFENSE[defense].sliders["defense"])


def describe() -> dict:
    """For the web app: every system with its sliders."""
    return {"offense": [{"key": s.key, "name": s.name, "blurb": s.blurb, "sliders": s.sliders} for s in OFFENSE.values()],
            "defense": [{"key": s.key, "name": s.name, "blurb": s.blurb, "sliders": s.sliders} for s in DEFENSE.values()]}


# --------------------------------------------------------------------------
# AI coaches pick what suits their roster
# --------------------------------------------------------------------------
def _features(league: League, team_id: int) -> dict:
    roster = league.roster(team_id)[:9]
    if len(roster) < 5:
        return {}
    a = lambda p, k: getattr(p.attributes, k)  # noqa: E731
    mean = lambda k: sum(a(p, k) for p in roster) / len(roster)  # noqa: E731
    bigs = [p for p in roster if p.position.value in ("PF", "C")] or roster[-2:]
    cas = sorted((p.current_ability for p in roster), reverse=True)
    return {
        "shoot": mean("three_point"), "speed": mean("speed"), "mid": mean("mid_range"),
        "pass": (mean("passing") + mean("basketball_iq")) / 2, "reb": mean("offensive_rebounding"),
        "def": (mean("perimeter_defense") + mean("interior_defense")) / 2,
        "perim": mean("perimeter_defense"), "steal": mean("stealing"),
        "post": max(0.5 * a(p, "close_range") + 0.3 * a(p, "strength") + 0.2 * a(p, "passing") for p in bigs),
        "rim": max(a(p, "interior_defense") + a(p, "blocking") for p in bigs) / 2,
        "star": cas[0] - cas[1], "handle": a(roster[0], "ball_handling"),
        # How the team already plays (a league file's real tendencies; random in generated leagues).
        "tempo": league.teams[team_id].tactics.pace, "threes": league.teams[team_id].tactics.three_point_rate,
    }


# How well each system suits a roster, from its features as z-scores against the
# league (so every league spreads its teams over the systems): (base, {feature: weight}).
OFFENSE_FIT = {
    "balanced": (0.7, {}),
    "pace_space": (0.4, {"shoot": 1.0, "speed": 0.3, "threes": 0.5}),
    "seven_seconds": (-0.3, {"speed": 0.9, "shoot": 0.6, "tempo": 0.9}),
    "motion": (0.0, {"pass": 1.0, "star": -0.3}),
    "triangle": (-0.3, {"mid": 0.7, "post": 0.5, "pass": 0.2, "threes": -0.3}),
    "grit_grind": (-0.4, {"reb": 0.6, "def": 0.5, "shoot": -0.5, "tempo": -0.6, "threes": -0.6}),
    "inside_out": (-0.1, {"post": 1.0, "shoot": 0.2}),
    "heliocentric": (0.0, {"star": 1.0, "handle": 0.3}),
}
DEFENSE_FIT = {
    "man": (0.8, {}),
    "switch": (-0.1, {"perim": 0.9, "speed": 0.4}),
    "drop": (0.2, {"rim": 1.0}),
    "zone": (-0.7, {"rim": 0.6, "perim": -0.6}),
    "press": (-1.0, {"speed": 0.8, "steal": 0.6}),
}


def _zscores(league: League) -> dict[int, dict]:
    feats = {tid: _features(league, tid) for tid in league.teams}
    feats = {tid: f for tid, f in feats.items() if f}
    out = {tid: {} for tid in feats}
    for k in next(iter(feats.values()), {}):
        xs = [f[k] for f in feats.values()]
        mu = sum(xs) / len(xs)
        sd = (sum((x - mu) ** 2 for x in xs) / len(xs)) ** 0.5 or 1.0
        for tid, f in feats.items():
            out[tid][k] = (f[k] - mu) / sd
    return out


def _pick(fit: dict, z: dict, rng: random.Random) -> str:
    score = {k: base + sum(w * z.get(f, 0.0) for f, w in ws.items()) + rng.gauss(0, 0.45)
             for k, (base, ws) in fit.items()}
    return max(score, key=score.get)


def choose_for_team(league: League, team_id: int, rng: random.Random,
                    z: Optional[dict] = None) -> tuple[str, str]:
    """The (offence, defence) systems that suit a team's roster best (with some taste)."""
    z = (z or _zscores(league)).get(team_id, {})
    return _pick(OFFENSE_FIT, z, rng), _pick(DEFENSE_FIT, z, rng)


def fit_for_team(league: League, team_id: int) -> dict:
    """How well each system suits a team's roster: {"offense": {key: 1-5}, "defense": {...}}
    (5 = made for it), from the same scores the AI coaches use, without their taste."""
    z = _zscores(league).get(team_id, {})
    out = {}
    for side, fit in (("offense", OFFENSE_FIT), ("defense", DEFENSE_FIT)):
        score = {k: base + sum(w * z.get(f, 0.0) for f, w in ws.items()) for k, (base, ws) in fit.items()}
        out[side] = {k: int(max(1, min(5, round(3 + 1.6 * (v - 0.4))))) for k, v in score.items()}
    return out


def assign_ai_systems(league: League, rng: random.Random, sliders: float = 0.5,
                      keep_sliders: frozenset = frozenset()) -> None:
    """Give every AI team the systems that suit its roster (the user's team is left
    alone). Teams in ``keep_sliders`` keep their pace, three-point and glass settings
    (e.g. a league file's real tendencies): only the style, focus and scheme change."""
    z = _zscores(league)
    for tid, team in league.teams.items():
        if tid == league.user_team_id:
            continue
        o, d = choose_for_team(league, tid, rng, z)
        apply(team.tactics, offense=o, defense=d, sliders=0.0 if tid in keep_sliders else sliders)


# --------------------------------------------------------------------------
# Scoring options: 1st, 2nd and 3rd
# --------------------------------------------------------------------------
# Extra shot share by rank (statistical engine: shot-choice weight multiplier) and the
# spatial engine's nudges (points of option value): teammates look for him, and he
# looks for his own shot. Smaller than "get him the ball" so a defence can still
# take him away.
OPTION_SHOTS = (1.45, 1.25, 1.12)
OPTION_PASS = (0.065, 0.04, 0.02)
OPTION_SELF = (0.035, 0.02, 0.01)
LATE_GAME_FIRST = 2.5        # the 1st option's extra share when it's time for a big shot


def option_ranks(tactics: Tactics, on_floor: list[int]) -> dict[int, int]:
    """{player id: 0, 1, 2} for the scoring options on the floor, moved up when someone
    ahead of them is off the floor (the 2nd option is the 1st while the star sits)."""
    ids = [pid for pid in getattr(tactics, "scoring_options", []) if pid in on_floor]
    return {pid: rank for rank, pid in enumerate(ids[:3])}
