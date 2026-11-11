"""Set plays: the entry actions a half-court offence runs before it attacks.

NBA offences rarely shoot off the first look. They swing the ball, run a hand-off
or a down screen for a shooter, and only then go to a pick-and-roll, drive or
isolation. This module scripts those entries:

* **swing**: the handler moves the ball to the next perimeter man (a reversal).
* **handoff**: the handler dribbles at a wing and hands it to him as he comes
  past; the wing's defender gets caught on the handler's body.
* **downscreen**: a big screens down for the corner man, who pops to the wing
  (the wing replaces him in the corner) and gets the ball with his man trailing.

Each entry pass is a real pass (it can be picked off, and it bends the defence's
rotations, see ``scramble`` in ``sim.py``). At every catch the receiver reads the
defence: wide open, he shoots; his man trailing, he drives; otherwise the play goes
on. When the entry is over, the learned decisions take over.

Patience comes from the team's pace tactic: about ``entry_actions`` actions at
pace 10, fewer for fast teams, more for slow ones. Entries are skipped on short
clocks, in end-of-game situations and in flows out of transition.

A coach can call the set instead (a play out of a timeout): ``g.forced_entry`` =
(kind, target) makes ``plan`` run that action first, end of game or not, with the
target as the screener of a pick-and-roll or the man coming off a down screen.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Optional

from . import court as C
from .court import RIM

if TYPE_CHECKING:            # pragma: no cover
    from .sim import Agent, HalfCourtGame

KINDS = ("swing", "handoff", "downscreen", "pnr")
MIN_CLOCK = 10.0             # don't start an entry action with less than this on the shot clock
ABORT_CLOCK = 7.5            # ... and drop it when the clock gets this low
MAX_ACTIONS = 4               # most entry actions in one possession
ACTION_TIMEOUT = 3.5         # an action that hasn't produced its pass by then is dropped


def plan(g: "HalfCourtGame") -> Optional[dict]:
    """At the end of the bring-up: how many entry actions to run (None for none)."""
    if g.forced_entry is not None:
        return _plan_forced(g)
    p = g.p
    if p.entry_actions <= 0:
        return None
    if g.situation is not None or (g.game_clock is not None and g.game_clock < 35):
        return None
    if g.shot_clock < MIN_CLOCK:
        return None
    pace = g.teams[g.side].tactics.pace
    n = round(p.entry_actions * max(0.3, 1 - 0.06 * (pace - 10)) + g.rng.uniform(-0.75, 0.75))
    n = max(0, min(MAX_ACTIONS, n))
    if n == 0:
        return None
    play = {"left": n, "action": None, "done": 0}
    g.stats["entries"] += 1
    if not start_next(g, play):
        return None
    return play


def _plan_forced(g: "HalfCourtGame") -> Optional[dict]:
    """The coach's called set: that one action (if the clock allows), then the read."""
    kind, target = g.forced_entry
    g.forced_entry = None
    g.stats["called_sets"] += 1
    if g.shot_clock < MIN_CLOCK or g.holder is None:
        return None
    play = {"left": 1, "action": None, "done": 0, "force": (kind, target)}
    if not start_next(g, play) or play["action"]["kind"] != kind:
        g.play = None
        return None
    g.stats["called_sets_run"] += 1
    g.stats[f"called_{kind}"] += 1
    g.say(f"{g.teams[g.side].abbreviation} run the play they drew up")
    return play


def can_reset(g: "HalfCourtGame") -> bool:
    """Is another set action on the table (for the handler's decision)?"""
    return (g.play is None and g.p.second_actions > 0 and g.holder is not None
            and g.second_actions < g.p.second_actions and g.situation is None
            and g.shot_clock >= MIN_CLOCK + 1.0 and C.dist_to_rim(g.holder.pos) >= 19
            and (g.game_clock is None or g.game_clock >= 35))


def reset(g: "HalfCourtGame", why: str) -> None:
    """A stalled attack (drive cut off, screen that never came, nothing in the post):
    reset and run another action instead of forcing something, clock permitting."""
    if (g.play is not None or g.p.second_actions <= 0 or g.holder is None
            or g.second_actions >= g.p.second_actions or g.situation is not None
            or (g.game_clock is not None and g.game_clock < 35)):
        return
    play = {"left": 1, "action": None, "done": 0, "second": True}
    if start_next(g, play):
        g.second_actions += 1
        g.stats["second_actions"] += 1
        g.stats[f"reset_{why}"] += 1
        g.play = play
        g.mode, g.mode_since = "probe", g.now
        g.say(f"{g.teams[g.side].abbreviation} reset and run something else")


def _perimeter(g: "HalfCourtGame", exclude=()) -> list["Agent"]:
    return [a for a in g.offense if a is not g.holder and a not in exclude
            and C.dist_to_rim(a.pos) >= 19 and a.y >= 12]


def _corner(g: "HalfCourtGame") -> list["Agent"]:
    return [a for a in g.offense if a is not g.holder and a.spot[1] < 10 and abs(a.spot[0]) > 15]


def start_next(g: "HalfCourtGame", play: dict) -> bool:
    """Pick and set up the next action. False if none can be run."""
    if play["left"] <= 0 or g.shot_clock < MIN_CLOCK or g.holder is None:
        return False
    h, rng = g.holder, g.rng
    options = []
    swing_to = [a for a in _perimeter(g, exclude=(play.get("last_passer"),)) if C.dist(a.pos, h.pos) >= 10]
    if swing_to:
        options.append(("swing", 0.45))
    wings = [a for a in _perimeter(g) if abs(a.x) >= 10 and C.dist(a.pos, h.pos) < 26]
    if wings and C.dist_to_rim(h.pos) >= 22:
        options.append(("handoff", 0.25))
    corners = _corner(g)
    bigs = [a for a in g.offense if a.slot >= 3 and a is not h and a not in corners]
    if corners and bigs:
        options.append(("downscreen", 0.30))
    # Sets usually finish in a pick-and-roll: the last action calls one.
    screeners = [a for a in g.offense if a.slot >= 3 and a is not h]
    closing = play["left"] == 1 or g.shot_clock < MIN_CLOCK + 4
    if (closing and screeners and g.pnr_used < 3 and C.dist_to_rim(h.pos) > 18
            and g.shot_clock >= MIN_CLOCK + 1):
        options.append(("pnr", g.p.entry_pnr))
    force = play.pop("force", None)
    if force is not None:                    # a called set: that action if it can be run at all
        kind, who = force
        who = who if who in g.offense and who is not h else None
        if kind == "pnr":
            screeners = [who] if who else screeners or [a for a in g.offense if a is not h]
            ok = bool(screeners)
        elif kind == "downscreen":
            corners = [who] if who else corners or _perimeter(g)
            bigs = ([a for a in g.offense if a.slot >= 3 and a is not h and a not in corners]
                    or [a for a in g.offense if a is not h and a not in corners])
            ok = bool(corners and bigs)
        else:
            ok = any(k == kind for k, _ in options)
        if ok:
            options = [(kind, 1.0)]
    if not options:
        return False
    kind = rng.choices([k for k, _ in options], [w for _, w in options])[0]
    a = {"kind": kind, "since": g.now, "phase": "move"}
    if kind == "swing":
        a["to"] = min(swing_to, key=lambda m: C.dist(m.pos, h.pos))
        a["at"] = g.now + rng.uniform(0.6, 1.3)
        _maybe_deny(g, a)
    elif kind == "handoff":
        a["to"] = min(wings, key=lambda m: C.dist(m.pos, h.pos))
    elif kind == "pnr":
        a["to"] = min(screeners, key=lambda m: C.dist(m.pos, h.pos))
    else:
        c = rng.choice(corners)
        side = math.copysign(1.0, c.spot[0] or 1.0)
        scr = min(bigs, key=lambda b: C.dist(b.pos, c.pos))
        wing = [m for m in g.offense if m is not h and m not in (c, scr)
                and abs(m.spot[0]) > 12 and m.spot[1] > 15 and math.copysign(1.0, m.spot[0] or 1.0) == side]
        a.update({"to": c, "screener": scr, "fill": wing[0] if wing else None,
                  "screen_at": (c.spot[0] * 0.85, c.spot[1] + 4.0),
                  "pop": (side * 19.0, 21.0), "home": scr.spot, "corner": c.spot})
    play["action"] = a
    play["left"] -= 1
    g.stats[f"entry_{kind}"] += 1
    return True


def _maybe_deny(g: "HalfCourtGame", a: dict) -> None:
    """A set defence often overplays a shooter one pass away: the swing isn't there
    until he V-cuts free (or the action dies on the shot clock)."""
    if g.p.deny_rate <= 0 or g.scramble > 0:
        return
    mate = a["to"]
    d = g.guarded_by(mate)
    scheme = {"press": 1.5, "zone": 0.0}.get(g.scheme, 1.0)
    p = (g.p.deny_rate * scheme * math.exp(0.06 * (d.a("perimeter_defense") - 10))
         * math.exp(0.05 * (mate.a("three_point") - 10)))
    if g.rng.random() >= min(0.95, p):
        return
    # How long he needs to get free: his quickness and smarts against the defender's.
    edge = ((mate.a("speed") + mate.a("basketball_iq")) - (d.a("speed") + d.a("perimeter_defense"))) / 2
    work = max(0.6, g.rng.uniform(1.0, 2.8) - 0.08 * edge)
    a.update({"deny": d, "free_at": g.now + work, "vcut_turn": g.now + 0.55 * work})
    g.stats["denials"] += 1
    g.say(f"{d.p.name} denies {mate.p.name} the ball")


def defense_target(g: "HalfCourtGame", d: "Agent") -> Optional[tuple[float, float]]:
    """A denying defender sits in the passing lane until his man gets free."""
    act = g.play["action"] if g.play else None
    if act is None or act.get("deny") is not d or g.now >= act["free_at"] or g.holder is None:
        return None
    return C.clamp(C.toward(act["to"].pos, g.holder.pos, 2.0))


def offense_target(g: "HalfCourtGame", a: "Agent") -> Optional[tuple[float, float]]:
    """Where an off-ball player in the current action goes (None: his usual logic)."""
    act = g.play["action"] if g.play else None
    if act is None:
        return None
    kind, h = act["kind"], g.holder
    if kind == "swing" and a is act["to"] and h is not None:
        if act.get("deny") is not None and g.now < act["free_at"]:
            if g.now < act["vcut_turn"]:                               # V-cut: take him toward the rim...
                return C.clamp(C.toward(RIM, a.spot, max(12.0, C.dist_to_rim(a.spot) - 6.0)))
            return C.clamp(C.toward(h.pos, a.spot, 2.0))                # ...then pop back out to the ball
        return C.clamp(C.toward(h.pos, a.spot, 2.0))                # step up and show for it
    if kind == "handoff" and a is act["to"] and h is not None:
        # Come at the handler, then take it going toward the top of the key.
        return C.clamp(C.toward(RIM, h.pos, C.dist_to_rim(h.pos) + 1.5))
    if kind == "downscreen":
        if a is act["screener"]:
            return act["screen_at"] if act["phase"] == "move" else act["home"]
        if a is act["to"]:
            return act["corner"] if act["phase"] == "move" else act["pop"]
        if a is act["fill"] and act["phase"] != "move":
            return act["corner"]                                       # replace him in the corner
    return None


def handler_step(g: "HalfCourtGame") -> None:
    """The ball handler's part of the action; throws the entry pass when it's time."""
    play, h, now = g.play, g.holder, g.now
    act = play["action"]
    if act is None or g.shot_clock < ABORT_CLOCK or now - act["since"] > ACTION_TIMEOUT:
        finish(g)
        return
    kind, mate = act["kind"], act["to"]
    if kind == "pnr":
        # The set's last action: call the screen and let the pick-and-roll play out
        # (the handler reads it with the learned decisions).
        g.play = None
        g.mode, g.mode_since = "probe", now
        g.call_pnr(h, mate)
        return
    if kind == "swing":
        h.target(h.spot)
        if now >= act["at"] and (act.get("deny") is None or now >= act["free_at"]):
            _entry_pass(g, h, mate)
    elif kind == "handoff":
        h.target(C.clamp(C.toward(mate.pos, h.pos, 3.0)))             # dribble at him
        if C.dist(h.pos, mate.pos) < 4.5:
            on = g.guarded_by(mate)
            hold = 0.35 + 0.04 * (h.a("strength") - on.a("strength")) + 0.02 * (h.a("basketball_iq") - 10)
            act["hold"] = max(0.15, min(0.8, hold))
            _entry_pass(g, h, mate)
    else:
        h.target(h.spot)
        scr = act["screener"]
        if act["phase"] == "move":
            if C.dist(scr.pos, act["screen_at"]) < 2.0 or now - act["since"] > 2.5:
                act["phase"], act["set_at"] = "set", now
                on = g.guarded_by(mate)
                if C.dist(on.pos, act["screen_at"]) < 6.0:               # he ran into it
                    hold = 0.45 + 0.06 * (scr.a("strength") - on.a("strength")) + 0.03 * (scr.a("basketball_iq") - 10)
                    on.stuck_until = now + max(0.2, min(1.0, hold))
                g.say(f"{scr.p.name} screens down for {mate.p.name}")
        elif C.dist(mate.pos, act["pop"]) < 3.0 or now - act["set_at"] > 1.4:
            _entry_pass(g, h, mate)


def _entry_pass(g: "HalfCourtGame", h: "Agent", mate: "Agent") -> None:
    g.play["last_passer"] = h
    g.play["action"]["phase"] = "pass"
    g.throw_pass(h, mate)


def on_catch(g: "HalfCourtGame", mate: "Agent") -> None:
    """The receiver of an entry pass reads the defence, then the play goes on (or ends)."""
    play = g.play
    act = play["action"]
    play["done"] += 1
    if act is not None and act["kind"] == "handoff":
        on = g.guarded_by(mate)
        on.stuck_until = max(on.stuck_until, g.now + act.get("hold", 0.3))   # caught on the hand-off
    if act is not None and act["kind"] == "downscreen":
        act["to"].spot = act["pop"]
        if act["fill"] is not None:
            act["fill"].spot = act["corner"]
    mate.spot = mate.pos if C.dist_to_rim(mate.pos) > 19 else mate.spot
    play["action"] = None
    # Read: shoot it if he's wide open, attack a trailing defender, else keep running it.
    on = g.guarded_by(mate)
    spot = mate.pos
    closer, dd = g.nearest_defender(spot)
    rt = g.release_time(mate, True)
    contest = max(0.0, dd - g.closing_speed(closer, mate) * (0.35 + rt))
    ep = g.shot_value(mate, p=spot, contest_d=contest, defender=closer, catch=True)
    read = g.p.entry_read
    if C.is_three(spot):                 # the coach's three-point appetite moves the bar
        tac = g.teams[g.side].tactics
        read -= 0.005 * (tac.three_point_rate - 10) + {"perimeter": 0.025, "inside": -0.025}.get(
            tac.offensive_focus.value, 0.0)
    if ep >= read:
        g.stats["entry_shots"] += 1
        g.play = None
        g.take_shot(mate, moving=False)
        return
    # Attack only if the action really freed him: his man is caught or trailing AND
    # there's daylight between them.
    freed = g.now < on.stuck_until or C.dist_to_rim(on.pos) > C.dist_to_rim(mate.pos) + 1
    if freed and C.dist(on.pos, mate.pos) > g.p.entry_drive_gap:
        g.stats["entry_drives"] += 1
        g.play = None
        g.start_drive(mate)
        return
    if not start_next(g, play):
        finish(g)
    else:
        g.next_decision = g.now + 0.2


def finish(g: "HalfCourtGame") -> None:
    """End the entry: from here the learned decisions run the possession."""
    g.play = None
    g.mode, g.mode_since = "probe", g.now
    g.next_decision = min(g.next_decision, g.now + 0.3)
