"""The Three-Point Contest as a page for the match viewer.

``allstar.run_contest`` records every ball: which rack, its value, when it left his hands,
in or out. This turns that record into what the viewer plays: one "possession" per
shooter's round, ten frames a second, the shooter walking the course and the ball going
from the rack to his hands and up to the rim, plus ``contest`` data the viewer's contest
mode draws on top (racks, timer, running score, standings). Nothing here is random: the
same record always gives the same page.

Court coordinates are the viewer's attacking ones: feet, x across the floor (0 in the
middle), y out from the baseline, the rim at (0, 5.25).
"""
from __future__ import annotations

import math
from typing import Optional

from ..allstar import COURSE, ROUND_SECONDS, ball_value

DT = 0.1
RIM = (0.0, 5.25)
INTRO = 2.0          # seconds at the first rack before the clock starts
OUTRO = 2.5          # after his last ball comes down
GATHER = 0.5         # from picking a ball off the rack to letting it go
HELD_Z = 5.0         # a held ball (high enough that the 3D players don't dribble it)
RACK_Z = 3.3         # the top of a rack
STAGE_LABELS = {"first": "Round 1", "final": "Final", "first-shootoff": "Shoot-off for the final",
                "final-shootoff": "Shoot-off for the title"}
RACK_NAMES = ("corner", "wing", "top of the key", "wing", "corner")


def _polar(angle_deg: float, r: float, side: int) -> tuple[float, float]:
    a = math.radians(angle_deg)
    return (side * r * math.sin(a), RIM[1] + r * math.cos(a))


def course_spots() -> dict:
    """Where the shooter stands at each station of the course, where each rack and deep
    ball stand (with the direction the rack runs out from the basket)."""
    shooter = {("rack", 0): (22.6, 4.6), ("rack", 1): _polar(52, 25.0, 1), ("deep", 0): _polar(26, 30.5, 1),
               ("rack", 2): (0.0, RIM[1] + 25.0), ("deep", 1): _polar(26, 30.5, -1),
               ("rack", 3): _polar(52, 25.0, -1), ("rack", 4): (-22.6, 4.6)}

    def frame(spot):
        ux, uy = spot[0] - RIM[0], spot[1] - RIM[1]
        n = math.hypot(ux, uy)
        ux, uy = ux / n, uy / n
        return ux, uy, uy, -ux                      # out from the rim; along the arc, back the way he came
    racks, deep = [], []
    for st in COURSE:
        sx, sy = shooter[st]
        ux, uy, tx, ty = frame((sx, sy))
        if st[0] == "rack":
            racks.append([round(sx + 2.4 * tx + 0.5 * ux, 2), round(sy + 2.4 * ty + 0.5 * uy, 2), round(ux, 3), round(uy, 3)])
        else:
            deep.append([round(sx + 1.9 * tx, 2), round(sy + 1.9 * ty, 2)])
    return {"stations": [[round(shooter[st][0], 2), round(shooter[st][1], 2)] for st in COURSE],
            "kinds": [st[0] for st in COURSE], "racks": racks, "deep": deep}


def _ball_slot(spots: dict, kind: str, spot: int, ball: int) -> tuple[float, float, float]:
    if kind == "deep":
        x, y = spots["deep"][spot]
        return x, y, RACK_Z + 0.3
    x, y, ux, uy = spots["racks"][spot]
    off = (ball - 2) * 0.8
    return x + off * ux, y + off * uy, RACK_Z


def _round_frames(spots: dict, rec: dict, seconds: float, slot: int, name: str, label: str,
                  start_best: int) -> dict:
    """One shooter's round as a viewer possession."""
    stations = [(st, b) for st in COURSE for b in range(5 if st[0] == "rack" else 1)]
    st_index = {st: i for i, st in enumerate(COURSE)}
    shots = []
    for (st, b), t, m in zip(stations, rec["t"], rec["m"]):
        sx, sy = spots["stations"][st_index[st]]
        d = math.hypot(sx - RIM[0], sy - RIM[1])
        rel_f = int(round(INTRO / DT)) + t                       # (whole frames: it reaches the rim on one)
        arr_f = rel_f + int(round((0.5 + d / 50) / DT))
        shots.append({"kind": st[0], "spot": st[1], "station": st_index[st], "ball": b,
                      "value": ball_value(st, b, rec["mr"]), "made": m == "1",
                      "pickup": (rel_f - int(round(GATHER / DT))) * DT, "release": rel_f * DT,
                      "arrive": arr_f * DT, "d": d})
    finished = len(shots) == len(stations)
    last_t = shots[-1]["release"] - INTRO if shots else 0.0
    clock_end = last_t if finished else seconds          # the clock stops on his last ball
    end = max(INTRO + clock_end, shots[-1]["arrive"] if shots else 0.0) + OUTRO
    n = int(round(end / DT)) + 1

    # where he is: at a station from just before his first pickup there until just after his
    # last release, walking the arc between stations
    stays = []                                           # (station, from, to)
    for s in shots:
        if stays and stays[-1][0] == s["station"]:
            stays[-1][2] = s["release"] + 0.35
        else:
            stays.append([s["station"], s["pickup"] - 0.15, s["release"] + 0.35])
    if not stays:
        stays = [[0, 0.0, end]]
    stays[0][1] = 0.0
    stays[-1][2] = max(stays[-1][2], end)

    def polar(p):
        return math.atan2(p[0] - RIM[0], p[1] - RIM[1]), math.hypot(p[0] - RIM[0], p[1] - RIM[1])

    def where(t):
        for i, (si, a, b) in enumerate(stays):
            if t <= b:
                if t >= a or i == 0:
                    return spots["stations"][si]
                pa, pb = polar(spots["stations"][stays[i - 1][0]]), polar(spots["stations"][si])
                k = (t - stays[i - 1][2]) / max(0.05, a - stays[i - 1][2])
                k = k * k * (3 - 2 * k)
                ang, r = pa[0] + (pb[0] - pa[0]) * k, pa[1] + (pb[1] - pa[1]) * k
                return [RIM[0] + r * math.sin(ang), RIM[1] + r * math.cos(ang)]
        return spots["stations"][stays[-1][0]]

    def ball_at(t, me):
        """(x, y, z, holder) at time t."""
        for k, s in enumerate(shots):
            nxt = shots[k + 1]["pickup"] if k + 1 < len(shots) else end + 1
            if t < s["pickup"]:
                if k == 0:                              # waiting on the rack
                    x, y, z = _ball_slot(spots, s["kind"], s["spot"], s["ball"])
                    return x, y, z, -1
                break
            if t < s["release"]:                        # off the rack and up into his hands
                hx, hy = me[0] + 0.7 * (RIM[0] - me[0]) / s["d"], me[1] + 0.7 * (RIM[1] - me[1]) / s["d"]
                return hx, hy, HELD_Z, 0
            if t < s["arrive"]:                         # in the air (the viewer's shot arc)
                f = (t - s["release"]) / (s["arrive"] - s["release"])
                sx, sy = spots["stations"][s["station"]]
                ex, ey = _miss_spot(s) if not s["made"] else RIM
                apex = 6 + s["d"] * 0.25
                return (sx + (ex - sx) * f, sy + (ey - sy) * f, 7.5 + 2.5 * f + apex * 4 * f * (1 - f), -1)
            if t < nxt:                                 # down through the net, or off the rim
                u = t - s["arrive"]
                if s["made"]:                           # through the net, then a couple of bounces
                    v = u - 0.45
                    z = 10 - 9.6 * (u / 0.45) ** 2 if v < 0 else 0.4 + 2.0 * abs(math.sin(v * 5.5)) * max(0.0, 1 - v)
                    return RIM[0], RIM[1] + 0.6 * min(u, 1.0), z, -1
                mx, my = _miss_spot(s)
                ox, oy = mx - RIM[0], my - RIM[1]
                no = math.hypot(ox, oy) or 1
                dist = 9 * min(u, 1.2)
                return mx + ox / no * dist, my + oy / no * dist, max(0.4, 10 + 6 * u - 16 * u * u), -1
        x, y, z = _ball_slot(spots, "rack", 4, 4) if not shots else (RIM[0], RIM[1] + 1, 0.4)
        return x, y, z, -1

    frames, events, actions = [], [], []
    for i in range(n):
        t = i * DT
        me = where(t)
        bx, by, bz, holder = ball_at(t + 1e-9, me)
        clock = seconds if t < INTRO else max(0.0, seconds - min(t - INTRO, clock_end))
        frames.append([round(t, 1), round(clock, 1), round(bx * 10), round(by * 10), round(bz * 10), holder,
                       round(me[0] * 10), round(me[1] * 10)])
    pts, best = 0, start_best
    mr_name = RACK_NAMES[rec["mr"]]
    events.append([0, f"{name}: {label}, {int(seconds)} seconds. Money-ball rack: rack {rec['mr'] + 1} ({mr_name}).", pts, best])
    for k, s in enumerate(shots):
        f = int(math.ceil(s["arrive"] / DT - 1e-6))
        actions.append([int(round(s["pickup"] / DT)), 0, "catch", -1])
        actions.append([int(round(s["release"] / DT)), 0, "jumper_release", -1])
        if s["kind"] == "deep":
            what = "Deep shot"
        elif s["value"] == 2:
            what = f"Rack {s['spot'] + 1}, money ball"
        else:
            what = f"Rack {s['spot'] + 1}, ball {s['ball'] + 1}"
        if s["made"]:
            pts += s["value"]
            text = f"{what}: good (+{s['value']}). {pts} points."
        else:
            text = f"{what}: no good."
        events.append([f, text, pts, best])
    buzz = int(round((INTRO + clock_end) / DT))
    if finished:
        left = seconds - clock_end
        text = f"Through the course with {left:.1f} seconds left: {pts} points."
        buzz = max(buzz, events[-1][0])
    else:
        text = f"Time! {len(stations) - len(shots)} ball{'s' if len(stations) - len(shots) != 1 else ''} left on the racks: {pts} points."
    events.append([buzz, text, pts, best])
    events.sort(key=lambda e: e[0])
    shot_rows = [[0 if s["kind"] == "rack" else 1, s["spot"], s["ball"], s["value"], int(s["made"]),
                  int(round(s["pickup"] / DT)), int(round(s["release"] / DT)), int(math.ceil(s["arrive"] / DT - 1e-6))]
                 for s in shots]
    return {"offense": "home", "period": 1, "start_score": [0, start_best], "end_score": [pts, start_best],
            "frames": frames, "events": events, "actions": actions, "lineups": [[0, [slot]]], "n": 1,
            "result": "contest",
            "contest": {"money_rack": rec["mr"], "shots": shot_rows, "points": pts, "seconds": seconds,
                        "clock_start": int(round(INTRO / DT)), "finished": finished}}


def _miss_spot(s: dict) -> tuple[float, float]:
    """Where a miss meets the rim: short, long or off to a side (the same for the same ball)."""
    k = (s["station"] * 5 + s["ball"]) % 4
    ux, uy = [(0.0, 1.0), (0.0, -1.0), (1.0, 0.0), (-1.0, 0.0)][k]
    return RIM[0] + 0.8 * ux, RIM[1] + 0.8 * uy


def page_data(record: dict, season: Optional[int] = None) -> dict:
    """The viewer's data for a recorded contest (``allstar.run_contest(...)["record"]``)."""
    field = record["field"]
    slot = {row["p"]: i for i, row in enumerate(field)}
    spots = course_spots()
    possessions, stages = [], []
    best_in: dict[str, int] = {}
    results: dict[str, dict] = {}
    for rnd in record["rounds"]:
        stage, seconds = rnd["stage"], float(rnd.get("seconds", ROUND_SECONDS))
        label = STAGE_LABELS.get(stage, stage)
        if stage.endswith("shootoff"):
            same = sum(1 for s in stages if s["stage"] == stage)
            if same:
                label += f" ({same + 1})"
        key = f"{stage}:{len(stages)}"
        stages.append({"key": key, "stage": stage, "label": label, "seconds": seconds, "first": len(possessions),
                       "shooters": [slot[s["p"]] for s in rnd["shooters"]]})
        best = 0
        for k, s in enumerate(rnd["shooters"]):
            p = _round_frames(spots, s, seconds, slot[s["p"]], field[slot[s["p"]]]["name"], label, best)
            p["contest"].update({"stage": key, "label": label, "shooter": slot[s["p"]], "order": k,
                                 "of": len(rnd["shooters"])})
            best = max(best, p["contest"]["points"])
            results.setdefault(key, {})[slot[s["p"]]] = p["contest"]["points"]
            possessions.append(p)
        best_in[key] = best
    winner = slot.get(record.get("winner"))
    if possessions and winner is not None:
        last = possessions[-1]
        w = field[winner]
        last["events"].append([len(last["frames"]) - 1, f"{w['name']} ({w['team']}) wins the Three-Point Contest!",
                               *last["events"][-1][2:]])
    players = [{"id": row["p"], "num": row.get("num"), "name": row["name"], "pos": row.get("pos", "SG"),
                "ca": row.get("ca", 0), "team": row.get("team", "")} for row in field]
    title = "Three-Point Contest" + (f" {season}-{str((season + 1) % 100).zfill(2)}" if season else "")
    return {"version": 1, "dt": DT, "contest": {"title": title, "spots": spots, "stages": stages,
                                                "winner": winner, "results": results, "max_points": 40},
            "home": {"name": title, "abbr": "3PT", "players": players},
            "away": {"name": "To beat", "abbr": "BEST", "players": []},
            "possessions": possessions}
