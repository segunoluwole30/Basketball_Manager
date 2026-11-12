"""Build the 3D viewer's animated player: one rigged low-poly model plus motion-capture clips,
written as a single binary glTF (hoopsim/spatial/assets/player.glb).

    python tools/build_player_assets.py [--cache DIR] [--out FILE]

* Motion: the CMU Graphics Lab Motion Capture Database (free for any use, see
  hoopsim/spatial/assets/LICENSES.md), in Bruce Hahne's BVH conversion. The BVH files are
  not kept in the repository: this script downloads the ones it needs into ``--cache``.
* Model: generated here (tubes and ellipsoids skinned to the mocap skeleton), so the clips
  need no retargeting to a foreign rig and the mesh is the project's own.

Every clip is retargeted to one symmetric skeleton, cut, turned to face +Z, put in place
(the viewer moves players from the replay), resampled to 30 Hz and stored with quantised
rotations. Timing marks the viewer needs (release, take-off, natural speed, the dribble's
bounces) go in the glTF's top-level ``extras.hoopsim``.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "hoopsim" / "spatial" / "assets" / "player.glb"
MIRROR = "https://raw.githubusercontent.com/una-dinosauria/cmu-mocap/master/data/{sub:03d}/{name}.bvh"
SKELETON_FROM = "124_05"          # the subject whose proportions the model takes
HEIGHT = 6.5                      # feet, floor to the top of the head in the T-pose
FPS = 30
BULK_GIRTH = 1.25                 # the "bulk" morph target: the body this many times as thick
SRC_FPS = 120

# name: (file, first, last source frame (120 Hz), kind, options)
#   kind "loop": a cycle is found inside the range and closed; "shot": played once.
#   marks are source frames, turned into seconds from the clip's start.
CLIPS = {
    # -- standing and moving (loops) --
    "idle":         ("77_02", 100, 520, "loop", dict(still=True, desc="standing")),
    "stance":       ("102_28", 468, 500, "loop", dict(still=True, pingpong=True, desc="defensive stance (between slides)")),
    "walk":         ("07_01", 60, 300, "loop", dict(desc="walk")),
    "jog":          ("16_35", 10, 160, "loop", dict(desc="run/jog")),
    "sprint":       ("102_10", 4, 96, "loop", dict(desc="basketball: running, no ball")),
    "backpedal":    ("76_11", 110, 340, "loop", dict(desc="quick large steps backwards")),
    "slide":        ("102_27", 50, 170, "loop", dict(lateral=True, desc="basketball: defensive move side to side")),
    "dribble_idle": ("86_14", 2020, 2640, "loop", dict(still=True, dribble=True, period=100, desc="bouncing a basketball, standing")),
    "dribble_walk": ("06_02", 270, 610, "loop", dict(dribble=True, desc="basketball: forward dribble")),
    "dribble_run":  ("102_05", 4, 108, "loop", dict(dribble=True, desc="basketball: running straight with the ball")),
    # -- one-shots --
    "jumper":       ("124_05", 340, 480, "shot", dict(marks=dict(gather=361, release=402, land=451), ball="both", desc="basketball jump shot")),
    "free_throw":   ("124_04", 425, 640, "shot", dict(marks=dict(gather=496, release=532, land=560), ball="both", desc="basketball free throw")),
    "layup":        ("124_06", 310, 475, "shot", dict(marks=dict(gather=361, release=410, land=462), ball="R", desc="basketball lay-up")),
    "floater":      ("124_03", 436, 625, "shot", dict(marks=dict(gather=466, release=500, land=526), ball="R", desc="basketball shot (one hand, small hop)")),
    "pass":         ("15_12", 2040, 2165, "shot", dict(marks=dict(release=2096), ball="both", desc="basketball: chest pass")),
    "lob_pass":     ("15_12", 2500, 2700, "shot", dict(marks=dict(release=2625), ball="both", desc="basketball: overhead pass")),
    "catch":        ("34_01", 52, 132, "shot", dict(marks=dict(release=86), ball="both", desc="catching a thrown ball")),
    "contest":      ("13_10", 470, 670, "shot", dict(marks=dict(release=545), desc="reach up on tiptoe (one hand)")),
    "block":        ("14_07", 1700, 1800, "shot", dict(marks=dict(gather=1708, release=1744, land=1780), desc="jump up to grab")),
    "rebound":      ("14_07", 1560, 1676, "shot", dict(marks=dict(gather=1578, release=1624, land=1660), ball="both", desc="jump up to grab")),
    "steal":        ("15_06", 110, 290, "shot", dict(marks=dict(release=181), desc="lean forward, reach for")),
    "plant":        ("127_05", 40, 170, "shot", dict(marks=dict(release=110), desc="run to quick stop")),
    "fall":         ("90_18", 78, 215, "shot", dict(marks=dict(release=128), hold=True, desc="fall backwards (rug pull)")),
    # hand-keyed on a mocap pose
    "screen":       ("102_28", 468, 500, "loop", dict(still=True, pingpong=True, pose="screen", desc="defensive stance, arms folded in by hand")),
}
# clips that also get a left/right mirrored twin
MIRRORED = ("slide", "plant")

KEEP = ["Hips", "LeftUpLeg", "LeftLeg", "LeftFoot", "LeftToeBase", "RightUpLeg", "RightLeg", "RightFoot",
        "RightToeBase", "LowerBack", "Spine", "Spine1", "Neck", "Neck1", "Head", "LeftShoulder", "LeftArm",
        "LeftForeArm", "LeftHand", "RightShoulder", "RightArm", "RightForeArm", "RightHand"]


# ---------------------------------------------------------------- BVH and rotations
class Bvh:
    def __init__(self, path):
        toks = Path(path).read_text(encoding="latin-1").split()
        self.names, self.parent, self.offset, self.channels, self.end = [], [], [], [], {}
        stack, i, cur, in_end = [], 0, -1, False
        while toks[i] != "MOTION":
            t = toks[i]
            if t in ("ROOT", "JOINT"):
                self.names.append(toks[i + 1]); self.parent.append(stack[-1] if stack else -1)
                self.offset.append(None); self.channels.append([])
                cur = len(self.names) - 1; i += 2
            elif t == "End":
                in_end = True; i += 2
            elif t == "{":
                stack.append(-2 if in_end else cur); i += 1
            elif t == "}":
                if stack.pop() == -2: in_end = False
                i += 1
            elif t == "OFFSET":
                v = [float(x) for x in toks[i + 1:i + 4]]
                if in_end: self.end[stack[-2]] = v
                else: self.offset[cur] = v
                i += 4
            elif t == "CHANNELS":
                n = int(toks[i + 1]); self.channels[cur] = toks[i + 2:i + 2 + n]; i += 2 + n
            else:
                i += 1
        nf = int(toks[toks.index("Frames:") + 1])
        start = toks.index("Time:") + 2
        nch = sum(len(c) for c in self.channels)
        self.data = np.array(toks[start:start + nf * nch], dtype=np.float64).reshape(nf, nch)
        self.offset = np.array(self.offset)
        self.idx = {n: k for k, n in enumerate(self.names)}

    def local(self):
        F = len(self.data)
        rot = np.zeros((F, len(self.names), 3, 3)); pos = None; c = 0
        for j, ch in enumerate(self.channels):
            R = np.broadcast_to(np.eye(3), (F, 3, 3)).copy(); p = np.zeros((F, 3))
            for name in ch:
                v = self.data[:, c]; c += 1
                if name.endswith("position"): p[:, "XYZ".index(name[0])] = v
                else: R = R @ axis_rot(name[0], np.radians(v))
            rot[:, j] = R
            if j == 0: pos = p
        return pos, rot


def axis_rot(ax, a):
    a = np.asarray(a, dtype=np.float64)
    c, s, o, z = np.cos(a), np.sin(a), np.ones_like(a), np.zeros_like(a)
    if ax == "X": m = [[o, z, z], [z, c, -s], [z, s, c]]
    elif ax == "Y": m = [[c, z, s], [z, o, z], [-s, z, c]]
    else: m = [[c, -s, z], [s, c, z], [z, z, o]]
    return np.moveaxis(np.array(m), [0, 1], [-2, -1])


def fk(parent, offset, pos, rot):
    F, J = rot.shape[:2]
    W = np.zeros_like(rot); P = np.zeros((F, J, 3))
    for j in range(J):
        pj = parent[j]
        if pj < 0:
            W[:, j] = rot[:, j]; P[:, j] = pos
        else:
            W[:, j] = W[:, pj] @ rot[:, j]
            P[:, j] = P[:, pj] + W[:, pj] @ offset[j]
    return W, P


def rot_between(a, b):
    """The smallest rotation taking direction a to direction b (3x3)."""
    a = a / np.linalg.norm(a); b = b / np.linalg.norm(b)
    v = np.cross(a, b); c = float(a @ b)
    if c < -0.999999:
        ax = np.cross(a, [1, 0, 0]); ax = ax if np.linalg.norm(ax) > 1e-6 else np.cross(a, [0, 1, 0])
        ax /= np.linalg.norm(ax); return 2 * np.outer(ax, ax) - np.eye(3)
    K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + K + K @ K / (1 + c)


def mat_to_quat(m):
    """[...,3,3] -> [...,4] (x, y, z, w), w >= 0."""
    m = np.asarray(m); out = np.zeros(m.shape[:-2] + (4,))
    t = m[..., 0, 0] + m[..., 1, 1] + m[..., 2, 2]
    q = np.stack([1 + m[..., 0, 0] - m[..., 1, 1] - m[..., 2, 2], 1 - m[..., 0, 0] + m[..., 1, 1] - m[..., 2, 2],
                  1 - m[..., 0, 0] - m[..., 1, 1] + m[..., 2, 2], 1 + t], -1)
    k = np.argmax(q, -1)
    for kk in range(4):
        sel = k == kk
        if not sel.any(): continue
        mm = m[sel]; s = 2 * np.sqrt(np.maximum(q[sel][:, kk], 1e-12))
        if kk == 3: v = [(mm[:, 2, 1] - mm[:, 1, 2]) / s, (mm[:, 0, 2] - mm[:, 2, 0]) / s, (mm[:, 1, 0] - mm[:, 0, 1]) / s, s / 4]
        elif kk == 0: v = [s / 4, (mm[:, 0, 1] + mm[:, 1, 0]) / s, (mm[:, 0, 2] + mm[:, 2, 0]) / s, (mm[:, 2, 1] - mm[:, 1, 2]) / s]
        elif kk == 1: v = [(mm[:, 0, 1] + mm[:, 1, 0]) / s, s / 4, (mm[:, 1, 2] + mm[:, 2, 1]) / s, (mm[:, 0, 2] - mm[:, 2, 0]) / s]
        else: v = [(mm[:, 0, 2] + mm[:, 2, 0]) / s, (mm[:, 1, 2] + mm[:, 2, 1]) / s, s / 4, (mm[:, 1, 0] - mm[:, 0, 1]) / s]
        out[sel] = np.stack(v, -1)
    out /= np.linalg.norm(out, axis=-1, keepdims=True)
    return out


def quat_to_mat(q):
    x, y, z, w = np.moveaxis(np.asarray(q), -1, 0)
    return np.moveaxis(np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]), [0, 1], [-2, -1])


def nlerp(a, b, w):
    b = np.where((a * b).sum(-1, keepdims=True) < 0, -b, b)
    q = a * (1 - w) + b * w
    return q / np.linalg.norm(q, axis=-1, keepdims=True)


def yaw_mat(a):
    return axis_rot("Y", a)


# ---------------------------------------------------------------- skeleton
class Skeleton:
    """The model's skeleton: the kept joints of one subject, made left/right symmetric and
    scaled to feet. ``rest`` is the bind pose (the BVH's T-pose frame)."""

    def __init__(self, bv: Bvh):
        self.names = KEEP
        self.idx = {n: i for i, n in enumerate(KEEP)}
        kept_parent, offs, ends = [], [], []
        for n in KEEP:
            j = bv.idx[n]; off = bv.offset[j].copy(); p = bv.parent[j]
            while p >= 0 and bv.names[p] not in self.idx:      # fold dropped joints (they don't move)
                off = off + bv.offset[p]; p = bv.parent[p]
            kept_parent.append(self.idx[bv.names[p]] if p >= 0 else -1)
            offs.append(off)
            ends.append(end_of(bv, j))
        offs, ends = np.array(offs), np.array(ends)
        _, rot = bv.local()
        rest = mat_to_quat(rot[0, [bv.idx[n] for n in KEEP]])
        # symmetric: average each left joint with its mirrored right twin
        for n in KEEP:
            if not n.startswith("Left"): continue
            a, b = self.idx[n], self.idx["Right" + n[4:]]
            for arr in (offs, ends):
                m = (arr[a] + arr[b] * [-1, 1, 1]) / 2
                arr[a] = m; arr[b] = m * [-1, 1, 1]
            q = nlerp(rest[a], rest[b] * [1, -1, -1, 1], 0.5)
            rest[a] = q; rest[b] = q * [1, -1, -1, 1]
        for n in ("Hips", "LowerBack", "Spine", "Spine1", "Neck", "Neck1", "Head"):
            j = self.idx[n]; offs[j][0] = 0; ends[j][0] = 0
            rest[j] = nlerp(rest[j], rest[j] * [1, -1, -1, 1], 0.5)
        self.parent = kept_parent
        self.rest = rest; self.rest[0] = [0, 0, 0, 1]
        W, P = fk(self.parent, offs, np.zeros((1, 3)), quat_to_mat(self.rest)[None])
        top = (P[0, self.idx["Head"]] + W[0, self.idx["Head"]] @ ends[self.idx["Head"]])[1] + 0.9   # scalp above the joint chain
        foot = P[0, self.idx["LeftFoot"]][1]
        self.ankle = 0.27                                    # feet: ankle joint above the floor
        self.scale = (HEIGHT - self.ankle) / (top - foot)
        self.offset = offs * self.scale; self.ends = ends * self.scale
        self.leg = np.linalg.norm(self.offset[self.idx["LeftLeg"]]) + np.linalg.norm(self.offset[self.idx["LeftFoot"]])
        W, P = fk(self.parent, self.offset, np.zeros((1, 3)), quat_to_mat(self.rest)[None])
        self.hip_height = self.ankle - P[0, self.idx["LeftFoot"]][1]
        self.bindW = W[0]; self.bindP = P[0] + [0, self.hip_height, 0]


def end_of(bv, j):
    """The direction a joint's bone points along: its first child's offset (or the end site)."""
    while True:
        kids = [k for k in range(len(bv.names)) if bv.parent[k] == j]
        if not kids: return np.array(bv.end.get(j, [0, 1, 0]), dtype=np.float64)
        if np.linalg.norm(bv.offset[kids[0]]) > 1e-6: return bv.offset[kids[0]].copy()
        j = kids[0]


def retarget(bv: Bvh, sk: Skeleton, a: int, b: int):
    """Frames a..b of a clip on the model's skeleton: (root positions in feet, local quats)."""
    pos, rot = bv.local()
    pos, rot = pos[a:b + 1], rot[a:b + 1]
    W, _ = fk(bv.parent, bv.offset, pos, rot)
    Wt = np.zeros((len(pos), len(KEEP), 3, 3))
    for n in KEEP:
        j, t = bv.idx[n], sk.idx[n]
        kids = [k for k in range(len(KEEP)) if sk.parent[k] == t]
        Wt[:, t] = W[:, j]
        if len(kids) <= 1 and n != "Hips":                   # line the bone up with the model's
            d_src = end_of(bv, j)
            if kids:
                k = bv.idx[KEEP[kids[0]]]; d_src = bv.offset[k].copy(); p = bv.parent[k]
                while p != j: d_src = d_src + bv.offset[p]; p = bv.parent[p]
            d_tgt = sk.offset[kids[0]] if kids else sk.ends[t]
            if np.linalg.norm(d_src) > 1e-6 and np.linalg.norm(d_tgt) > 1e-6:
                Wt[:, t] = W[:, j] @ rot_between(d_tgt, d_src)
    loc = np.zeros_like(Wt)
    for t in range(len(KEEP)):
        p = sk.parent[t]
        loc[:, t] = Wt[:, t] if p < 0 else np.swapaxes(Wt[:, p], -1, -2) @ Wt[:, t]
    src_leg = sum(np.linalg.norm(bv.offset[bv.idx[n]]) for n in ("LeftLeg", "LeftFoot"))
    return pos * (sk.leg / src_leg), mat_to_quat(loc)


def pose_fk(sk, pos, q):
    return fk(sk.parent, sk.offset, pos, quat_to_mat(q))


def continuous(q):
    for f in range(1, len(q)):
        flip = (q[f] * q[f - 1]).sum(-1) < 0
        q[f][flip] *= -1
    return q


def mirror_clip(sk, pos, q):
    pos = pos * [-1, 1, 1]; out = q.copy()
    for n in KEEP:
        a = sk.idx[n]
        b = sk.idx["Right" + n[4:]] if n.startswith("Left") else sk.idx["Left" + n[5:]] if n.startswith("Right") else a
        out[:, a] = q[:, b] * [1, -1, -1, 1]
    return pos, out


def turn(pos, q, R):
    """Rotate a clip about the vertical axis (R: 3x3 or [F,3,3])."""
    pos = np.einsum("...ab,...b->...a", R, pos)
    q = q.copy(); q[:, 0] = mat_to_quat(R @ quat_to_mat(q[:, 0]))
    return pos, q


def facing(q):
    f = quat_to_mat(q[:, 0])[:, :, 2]
    return np.arctan2(f[:, 0], f[:, 2])


def aim(sk, W, joint, direction):
    """Turn a joint (world rotations W[J,3,3], in place) so its bone points along ``direction``;
    everything below it turns with it."""
    j = sk.idx[joint]
    kids = [k for k in range(len(KEEP)) if sk.parent[k] == j]
    rest = sk.offset[kids[0]] if kids else sk.ends[j]
    Q = rot_between(W[j] @ rest, np.asarray(direction, dtype=np.float64))
    todo = [j]
    while todo:
        k = todo.pop(); W[k] = Q @ W[k]
        todo += [c for c in range(len(KEEP)) if sk.parent[c] == k]


def world_to_local(sk, W):
    return mat_to_quat(np.stack([W[t] if sk.parent[t] < 0 else W[sk.parent[t]].T @ W[t] for t in range(len(KEEP))]))


# ---------------------------------------------------------------- clips
def build_clip(name, spec, sk, load):
    src, a, b, kind, opt = spec
    bv = load(src)
    lead = min(24, a) if kind == "loop" and not opt.get("pingpong") else 0
    pos, q = retarget(bv, sk, a - lead, b)
    q = continuous(q)
    meta = {"src": src, "desc": opt.get("desc", ""), "loop": kind == "loop"}
    if kind == "loop" and not opt.get("pingpong"):
        s, e = find_cycle(sk, pos, q, lead, opt)
        n, blend = e - s, min(s, 16, (e - s) // 3)
        cp, cq = pos[s:e].copy(), q[s:e].copy()
        drift = (pos[e] - pos[s]) / n                         # what one cycle travels
        for i in range(blend):                               # close the loop: ease into what came before the start
            w = (i + 1) / (blend + 1); f = n - blend + i
            cq[f] = nlerp(cq[f], q[s - blend + i], w)
            cp[f] = cp[f] * (1 - w) + (pos[s - blend + i] + drift * n) * w
        pos, q = cp, cq
        step = np.arange(n)[:, None] * drift
        travel = np.array([drift[0], 0, drift[2]]) * SRC_FPS
        pos = pos - step * [1, 0, 1]
        yaw = np.arctan2(travel[0], travel[2]) if (np.hypot(travel[0], travel[2]) > 1.5 and not opt.get("lateral")
                                                    and name != "backpedal") else circ_mean(facing(q))
        if name == "backpedal": yaw = np.arctan2(-travel[0], -travel[2])
        pos, q = turn(pos, q, yaw_mat(-yaw))
        travel = yaw_mat(-yaw) @ travel
        meta["speed"] = 0.0 if opt.get("still") else float(np.hypot(travel[0], travel[2]))
        if opt.get("lateral"): meta["side"] = float(np.sign(travel[0]))     # +1: towards the model's left (+X)
    elif kind == "loop":                                     # a held pose: there and back
        pos, q = turn(pos, q, yaw_mat(-circ_mean(facing(q))))
        if opt.get("pose") == "screen":
            for f in range(len(q)):
                W, _ = pose_fk(sk, pos[f:f + 1], q[f:f + 1]); W = W[0]
                for sd, sx in (("Left", 1), ("Right", -1)):
                    aim(sk, W, sd + "Arm", [sx * 0.25, -1, 0.35])
                    aim(sk, W, sd + "ForeArm", [-sx * 0.8, 0.5, 0.6])
                    aim(sk, W, sd + "Hand", [-sx * 0.8, 0.5, 0.3])
                q[f] = world_to_local(sk, W)
            q = continuous(q)
        pos = np.concatenate([pos, pos[-2:0:-1]]); q = np.concatenate([q, q[-2:0:-1]])
        pos = np.concatenate([pos, pos]); q = np.concatenate([q, q])         # long enough to cross-fade
        meta["speed"] = 0.0
    else:
        marks = opt.get("marks", {})
        key = marks.get("release", (a + b) // 2) - a
        pos, q = turn(pos, q, yaw_mat(-facing(q)[key]))
        n = len(pos) - 1
        line = pos[0] + (pos[-1] - pos[0]) * (np.arange(n + 1) / n)[:, None]
        if opt.get("hold"): line = np.repeat(pos[:1], n + 1, 0)
        pos = pos - line * [1, 0, 1]
        meta["marks"] = {k: round((v - a) / SRC_FPS, 3) for k, v in marks.items()}
        if opt.get("ball"): meta["ball"] = opt["ball"]
        if opt.get("hold"): meta["hold"] = True
    pos[:, [0, 2]] -= pos[:, [0, 2]].mean(0) if kind == "loop" else pos[0, [0, 2]]
    # the floor: where the lower ankle sits when a foot is down
    _, P = pose_fk(sk, pos, q)
    low = np.minimum(P[:, sk.idx["LeftFoot"], 1], P[:, sk.idx["RightFoot"], 1])
    pos[:, 1] += sk.ankle - np.percentile(low, 8 if not opt.get("hold") else 2)
    if opt.get("hold"):                                      # lying down: don't sink into the floor
        _, P = pose_fk(sk, pos, q)
        pos[:, 1] += np.maximum(0, 0.38 - P[:, :, 1].min(1))
    if kind == "loop" and not opt.get("still") and not opt.get("pingpong"):
        pos, q = roll_to_contact(sk, pos, q, opt)
    if opt.get("dribble"): meta["dribble"] = dribble_marks(sk, pos, q)
    # 120 Hz -> 30 Hz
    stepf = SRC_FPS // FPS
    idxs = np.arange(0, len(pos), stepf)
    pos, q = pos[idxs], continuous(q[idxs].copy())
    if kind == "loop":
        dur = len(idxs) * stepf / SRC_FPS                    # the loop's length; the last key repeats the first
        times = np.append(idxs / SRC_FPS, dur)
        pos = np.concatenate([pos, pos[:1]]); q = continuous(np.concatenate([q, q[:1]]))
    else:
        times = idxs / SRC_FPS; dur = float(times[-1])
    meta["duration"] = round(float(dur), 4)
    # how high the hands get at the key moment (for lifting a jump to the ball)
    if "marks" in meta and "release" in meta["marks"]:
        f = min(len(pos) - 1, int(round(meta["marks"]["release"] * FPS)))
        _, P = pose_fk(sk, pos[f:f + 1], q[f:f + 1])
        meta["reach"] = round(float(max(P[0, sk.idx["LeftHand"], 1], P[0, sk.idx["RightHand"], 1])) + 0.35, 3)
    return times.astype(np.float32), pos.astype(np.float32), q, meta


def circ_mean(a):
    return float(np.arctan2(np.sin(a).mean(), np.cos(a).mean()))


def find_cycle(sk, pos, q, lead, opt):
    """The best start and end (source frames) of one cycle: the two frames, a stride apart,
    whose poses and pose changes match best."""
    n = len(q)
    if opt.get("still") and not opt.get("period"):
        return lead, n - 1
    _, P = pose_fk(sk, pos, q)
    rel = P - P[:, :1]                                        # joints about the hips
    R = yaw_mat(-facing(q))
    rel = np.einsum("fab,fjb->fja", R, rel)
    feat = np.concatenate([rel.reshape(n, -1), P[:, :1, 1] * 3], 1)
    vel = np.gradient(feat, axis=0) * 12
    feat = np.concatenate([feat, vel], 1)
    best = None
    lo, hi = (int(opt["period"] * 0.8), int(opt["period"] * 1.2)) if opt.get("period") else (60, 200)
    for s in range(lead, n - lo):
        for e in range(s + lo, min(n - 1, s + hi) + 1):
            d = np.linalg.norm(feat[s] - feat[e]) / np.sqrt(e - s)
            if best is None or d < best[0]: best = (d, s, e)
    return best[1], best[2]


def roll_to_contact(sk, pos, q, opt):
    """Start every moving loop at the same point of the stride (left foot furthest ahead, or
    for the slide furthest out), so they can be blended in step."""
    _, P = pose_fk(sk, pos, q)
    ax = 0 if opt.get("lateral") else 2
    lead = P[:, sk.idx["LeftFoot"], ax] - P[:, sk.idx["RightFoot"], ax]
    k = int(np.argmax(np.convolve(np.concatenate([lead[-6:], lead, lead[:6]]), np.ones(13) / 13, "valid")))
    return np.roll(pos, -k, 0), np.roll(q, -k, 0)


def dribble_marks(sk, pos, q):
    """Which hand bounces the ball, and when (seconds into the loop) it leaves the hand
    (lowest point of the push) and comes back to it."""
    _, P = pose_fk(sk, pos, q)
    n = len(pos)
    hands = {s: P[:, sk.idx[s + "Hand"], 1] for s in ("Left", "Right")}
    side = max(hands, key=lambda s: np.ptp(hands[s]))
    h = np.convolve(np.concatenate([hands[side][-8:], hands[side], hands[side][:8]]), np.ones(17) / 17, "valid")
    lows = [i for i in range(n) if h[i] <= h[i - 1] and h[i] < h[(i + 1) % n] and h[i] < h.mean() - 0.12 * np.ptp(h)]
    lows = [i for k, i in enumerate(lows) if k == 0 or i - lows[k - 1] > 30]
    out = []
    for i in lows:
        j = i
        for step in range(1, n):                              # the next high point of the hand
            j = (i + step) % n
            if h[j] >= h[j - 1] and h[j] > h[(j + 1) % n] and h[j] > h.mean(): break
        gap = (j - i) % n
        out.append([round(i / SRC_FPS, 3), round(((i + max(12, int(gap * 0.8))) % n) / SRC_FPS, 3)])
    return {"hand": side[0], "bounces": out}


# ---------------------------------------------------------------- the model
class Mesh:
    def __init__(self, girth=1.0):
        self.v, self.n, self.uv, self.col, self.j, self.w, self.tri = [], [], [], [], [], [], {}
        self.girth = girth                                    # how thick the body is drawn (1: the model as designed)

    def vert(self, p, weights, uv=(0, 0), col=(1, 1, 1)):
        ws = sorted(((float(w), int(j)) for j, w in weights if w > 1e-4), reverse=True)[:4]
        tot = sum(w for w, _ in ws)
        ws = [(w / tot, j) for w, j in ws] + [(0.0, 0)] * (4 - len(ws))
        self.v.append(p); self.uv.append(uv); self.col.append(col)
        self.j.append([j for _, j in ws]); self.w.append([w for w, _ in ws])
        return len(self.v) - 1

    def face(self, mat, a, b, c):
        self.tri.setdefault(mat, []).append((a, b, c))

    def loft(self, mat, rings, cap_start=False, cap_end=False, col=(1, 1, 1), uv=None, sides=16, bulk=True):
        """rings: [(centre, x axis, y axis, rx, ry, weights[, squareness])], each a ring of
        ``sides`` points: an ellipse, or with squareness > 2 a rounded box."""
        N = sides; rows = []
        for ri, ring in enumerate(rings):
            c, ax, ay, rx, ry, wts = ring[:6]; e = 2 / (ring[6] if len(ring) > 6 else 2)
            if bulk: rx, ry = rx * self.girth, ry * self.girth        # (head, hands and feet stay as they are)
            row = []
            for k in range(N + 1):                            # the seam is doubled for the texture
                ang = 2 * np.pi * k / N; cs, sn = np.cos(ang), np.sin(ang)
                p = (np.asarray(c) + np.asarray(ax) * (np.sign(cs) * abs(cs) ** e * rx)
                     + np.asarray(ay) * (np.sign(sn) * abs(sn) ** e * ry))
                row.append(self.vert(p, wts, (k / N, uv(ri) if uv else ri / max(1, len(rings) - 1)), col))
            rows.append(row)
        for r in range(len(rows) - 1):
            for k in range(N):
                a, b, c, d = rows[r][k], rows[r][k + 1], rows[r + 1][k], rows[r + 1][k + 1]
                self.face(mat, a, b, c); self.face(mat, b, d, c)          # outward: the ring runs anticlockwise about its axis
        for cap, row, ring, flip in ((cap_start, rows[0], rings[0], False), (cap_end, rows[-1], rings[-1], True)):
            if not cap: continue
            mid = self.vert(ring[0], ring[5], (0.5, 0.5), col)
            for k in range(N):
                if flip: self.face(mat, mid, row[k], row[k + 1])
                else: self.face(mat, mid, row[k + 1], row[k])

    def arrays(self):
        v = np.array(self.v, dtype=np.float64)
        nrm = np.zeros_like(v)
        for tris in self.tri.values():
            t = np.array(tris)
            fn = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
            for k in range(3): np.add.at(nrm, t[:, k], fn)
        # weld normals across the texture seam and between rings that share a position
        key = {}
        for i, p in enumerate(np.round(v, 4)):
            key.setdefault(tuple(p), []).append(i)
        for ids in key.values():
            if len(ids) > 1: nrm[ids] = nrm[ids].sum(0)
        nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)
        return v, nrm


def frame_for(d, hint=(0, 0, 1)):
    d = np.asarray(d, dtype=np.float64); d = d / np.linalg.norm(d)
    x = np.cross(hint, d)
    if np.linalg.norm(x) < 1e-3: x = np.cross((1, 0, 0), d)
    x /= np.linalg.norm(x)
    return x, np.cross(d, x)


def limb(mesh, mat, sk, chain, radii, col=(1, 1, 1), hint=(0, 0, 1), cap_start=False, cap_end=False, flat=1.0,
         sides=14):
    """A tube along a chain of joints. radii: per segment, a list of (t, r[, push]) along it
    (push: the ring's centre moved along its second axis, for a calf or a biceps). The skin
    blends from one bone to the next round each joint."""
    P = sk.bindP; rings = []
    for s in range(len(chain) - 1):
        a, b = sk.idx[chain[s]], sk.idx[chain[s + 1]]
        pa, pb = P[a], P[b]
        ax, ay = frame_for(pb - pa, hint)
        for t, r, *push in radii[s]:
            w = [(a, 1.0)]
            if t > 0.7 and (s + 1 < len(chain) - 1 or chain[s].endswith("Shoulder")):
                k = smooth((t - 0.7) / 0.6); w = [(a, 1 - k), (b, k)]
            if t < 0.3 and s > 0:
                prev = sk.idx[chain[s - 1]]; k = smooth(0.5 + t / 0.6); w = [(prev, 1 - k), (a, k)]
            rings.append((pa + (pb - pa) * t + np.asarray(ay) * (push[0] if push else 0), ax, ay, r, r * flat, w))
    mesh.loft(mat, rings, cap_start, cap_end, col, sides=sides)


def smooth(x):
    x = min(1.0, max(0.0, x)); return x * x * (3 - 2 * x)


def ellipsoid(mesh, mat, c, r, weights, col=(1, 1, 1), lat0=-90, lat1=90, rows=7, sides=12, shape=None, bulk=False):
    """shape(sin latitude) -> (x scale, z scale, z shift): a jaw, a hairline."""
    rings = []
    for i in range(rows + 1):
        lat = np.radians(lat0 + (lat1 - lat0) * i / rows)
        rr = max(np.cos(lat), 0.02); kx, kz, dz = shape(np.sin(lat)) if shape else (1, 1, 0)
        rings.append((np.asarray(c) + [0, np.sin(lat) * r[1], dz], (1, 0, 0), (0, 0, -1), r[0] * rr * kx, r[2] * rr * kz, weights))
    mesh.loft(mat, rings, lat0 > -89, lat1 < 89, col, sides=sides, bulk=bulk)


def build_model(sk: Skeleton, girth: float = 1.0) -> Mesh:
    """The player, in the T-pose: +X is his left, +Z his front. Four materials: skin (tinted
    per player), jersey (textured: number, trim), shorts (team colour) and kit (vertex
    colours: hair, eyes, socks, shoes, trim)."""
    m = Mesh(girth); P = sk.bindP; ix = sk.idx
    J = lambda n: P[ix[n]]
    SKIN, JERSEY, SHORTS, KIT = "skin", "jersey", "shorts", "kit"
    WHITE, SHOE, HAIR, EYE = (0.85, 0.85, 0.85), (0.02, 0.02, 0.026), (0.008, 0.006, 0.005), (0.004, 0.004, 0.004)   # linear RGB
    hips, head = J("Hips"), J("Head")
    sh = J("LeftArm"); X, BACK = (1, 0, 0), (0, 0, -1)
    wH, wL, wS, wC, wN = ix["Hips"], ix["LowerBack"], ix["Spine"], ix["Spine1"], ix["Neck"]
    # torso: a sleeveless jersey from below the waist to the collar. u runs round it (back at
    # 0.25, front at 0.75), v from the hem to the collar: the viewer paints number and trim.
    hem, top = hips[1] - 0.36, sh[1] + 0.2
    k = sh[0] / 0.84
    tor = [(hem, 0.62, 0.45, 2.2, [(wH, 1)]), (hips[1] - 0.1, 0.61, 0.44, 2.2, [(wH, 1)]),
           (hips[1] + 0.22, 0.51, 0.35, 2.2, [(wH, .5), (wL, .5)]), (hips[1] + 0.48, 0.52, 0.35, 2.3, [(wL, .4), (wS, .6)]),
           (hips[1] + 0.74, 0.6, 0.39, 2.4, [(wS, .6), (wC, .4)]), (sh[1] - 0.36, 0.68, 0.42, 2.5, [(wS, .2), (wC, .8)]),
           (sh[1] - 0.16, 0.71, 0.41, 2.5, [(wC, 1)]), (sh[1] - 0.02, 0.69, 0.36, 2.4, [(wC, 1)]),
           (sh[1] + 0.1, 0.5, 0.29, 2.2, [(wC, .75), (wN, .25)]), (top, 0.25, 0.22, 2, [(wC, .6), (wN, .4)])]
    lean = lambda y: 0.03 + 0.05 * smooth((y - hips[1]) / 0.9) - 0.07 * smooth((y - sh[1] + 0.3) / 0.5)
    m.loft(JERSEY, [((0, y, hips[2] + lean(y)), X, BACK, rx * k, rz, w, sq) for y, rx, rz, sq, w in tor],
           uv=lambda ri: (tor[ri][0] - hem) / (top - hem), sides=20)
    # neck and head: a skull with a jaw, ears, nose, eyes, and a cap of short hair
    nk = J("Neck1")
    m.loft(SKIN, [((0, y, z), X, BACK, r, r * 0.95, w) for y, z, r, w in
                  ((top - 0.12, nk[2] + 0.03, 0.2, [(wC, .5), (wN, .5)]), (nk[1] + 0.1, nk[2] + 0.02, 0.175, [(ix["Neck1"], 1)]),
                   (head[1] + 0.02, head[2] + 0.04, 0.165, [(ix["Neck1"], .4), (ix["Head"], .6)]),
                   (head[1] + 0.2, head[2] + 0.07, 0.17, [(ix["Head"], 1)]))], sides=12)
    hc = head + [0, 0.3, 0.1]; wHd = [(ix["Head"], 1)]
    jaw = lambda sn: (1 - 0.3 * (-sn) ** 1.5, 1 - 0.12 * (-sn) ** 1.5, 0.07 * -sn) if sn < 0 else (1, 1, 0)
    ellipsoid(m, SKIN, hc, (0.3, 0.385, 0.35), wHd, rows=11, sides=16, shape=jaw)
    ellipsoid(m, KIT, hc + [0, 0.02, -0.03], (0.325, 0.405, 0.378), wHd, HAIR, lat0=16, lat1=90, rows=6, sides=16,
              shape=lambda sn: (1, 1, -0.05 * (1 - sn)))
    for sx in (1, -1):
        ellipsoid(m, SKIN, hc + [sx * 0.295, -0.03, -0.03], (0.035, 0.085, 0.055), wHd, rows=4, sides=8)
        ellipsoid(m, KIT, hc + [sx * 0.115, 0.045, 0.312], (0.034, 0.02, 0.018), wHd, EYE, rows=3, sides=8)
    ellipsoid(m, SKIN, hc + [0, -0.06, 0.335], (0.036, 0.065, 0.04), wHd, rows=4, sides=8)
    for sd, sx in (("Left", 1), ("Right", -1)):
        # arm: a rounded bare shoulder out of the jersey's armhole, biceps, forearm
        limb(m, SKIN, sk, [sd + "Shoulder", sd + "Arm", sd + "ForeArm", sd + "Hand"],
             [[(0.8, 0.1), (0.9, 0.21)],
              [(0.0, 0.265), (0.1, 0.26), (0.24, 0.235), (0.42, 0.21, 0.015), (0.62, 0.19), (0.82, 0.165), (0.95, 0.155)],
              [(0.0, 0.155), (0.14, 0.17), (0.38, 0.165), (0.66, 0.135), (0.9, 0.105), (1.0, 0.1)]],
             hint=(0, 1, 0), cap_start=True, flat=0.92)
        ellipsoid(m, SKIN, J(sd + "Arm") + [sx * -0.02, -0.01, 0], (0.275, 0.27, 0.26),
                  [(ix[sd + "Shoulder"], .5), (ix[sd + "Arm"], .5)], rows=6, sides=12, bulk=True)
        # hand: palm, fingers curling down a little, thumb forward (the T-pose's palms face down)
        wr = J(sd + "Hand"); d = sk.bindW[ix[sd + "Hand"]] @ sk.ends[ix[sd + "Hand"]]; d = d / np.linalg.norm(d)
        ax, ay = frame_for(d, (0, 1, 0)); wHn = [(ix[sd + "Hand"], 1)]
        m.loft(SKIN, [(wr + d * t + np.asarray(ay) * dy, ax, ay, w, th, wHn, 2.6) for t, w, th, dy in
                      ((-0.03, 0.1, 0.085, 0), (0.1, 0.14, 0.07, 0), (0.3, 0.165, 0.06, -0.005), (0.42, 0.16, 0.05, -0.015),
                       (0.58, 0.15, 0.04, -0.05), (0.72, 0.13, 0.034, -0.1), (0.8, 0.08, 0.025, -0.135))],
               cap_end=True, sides=10, bulk=False)
        tb = wr + d * 0.14 + np.array([0, -0.01, 0.13]); td = np.array([sx * 0.55, -0.25, 0.8]); td /= np.linalg.norm(td)
        tx, ty = frame_for(td, (0, 1, 0))
        m.loft(SKIN, [(tb + td * t, tx, ty, r, r, wHn) for t, r in ((0, 0.05), (0.1, 0.048), (0.2, 0.04), (0.25, 0.02))],
               cap_end=True, sides=6, bulk=False)
        # leg: thigh, knee, a calf that bulges behind
        limb(m, SKIN, sk, [sd + "UpLeg", sd + "Leg", sd + "Foot"],
             [[(0.25, 0.33), (0.5, 0.3), (0.75, 0.25), (0.92, 0.22)],
              [(0.0, 0.215, 0.01), (0.1, 0.205), (0.3, 0.225, -0.035), (0.48, 0.2, -0.03), (0.68, 0.15, -0.01), (0.84, 0.125)]],
             hint=X)
        # sock, then a high-top shoe on a white sole
        an = J(sd + "Foot"); wF, wT, wK = ix[sd + "Foot"], ix[sd + "ToeBase"], ix[sd + "Leg"]
        kn = J(sd + "Leg"); up = (kn - an) / np.linalg.norm(kn - an)
        ax, ay = frame_for(up, X)
        m.loft(KIT, [(an + up * t, ax, ay, r, r, [(wF, w), (wK, 1 - w)]) for t, r, w in
                     ((0.7, 0.132, 0.1), (0.66, 0.142, 0.15), (0.4, 0.135, 0.45), (0.12, 0.14, 0.9))], col=WHITE, sides=12)
        toe = J(sd + "ToeBase"); fd = np.array([toe[0] - an[0], 0, toe[2] - an[2]]); fl = np.linalg.norm(fd); fd /= fl
        side = np.cross((0, 1, 0), fd); sole = an[1] - sk.ankle
        m.loft(KIT, [(an + up * t, ax, ay, r, r * 1.05, [(wF, 1)]) for t, r in ((0.3, 0.17), (0.12, 0.175), (-0.02, 0.18))],
               col=SHOE, sides=12, bulk=False)
        last = ((-0.34, 0.11, 0.3, 0), (-0.26, 0.165, 0.4, 0), (0.0, 0.185, 0.42, 0), (0.45, 0.2, 0.33, 0.25),
                (0.9, 0.215, 0.25, 0.8), (1.25, 0.2, 0.2, 1), (1.47, 0.15, 0.15, 1), (1.56, 0.07, 0.1, 1))
        for colr, lo, grow in ((SHOE, 0.07, 0.0), (WHITE, 0.0, 0.012)):
            rings = []
            for t, w, hgt, wt in last:
                hi = hgt if colr is SHOE else 0.085
                rings.append((np.array([an[0], sole + (lo + hi) / 2, an[2]]) + fd * (t * fl), side, (0, 1, 0), w + grow,
                              (hi - lo) / 2, [(wF, 1 - wt), (wT, wt)], 3.0))
            m.loft(KIT, rings, cap_start=True, cap_end=True, col=colr, sides=12, bulk=False)
        # shorts leg: a loose tube over the thigh, with a white hem
        hp = J(sd + "UpLeg")
        ax, ay = frame_for(kn - hp, X); wU = ix[sd + "UpLeg"]
        leg = lambda t, r, wh: (hp + (kn - hp) * t, ax, ay, r, r, [(wH, wh), (wU, 1 - wh)])
        m.loft(SHORTS, [leg(*a) for a in ((-0.05, 0.3, 0.7), (0.08, 0.375, 0.5), (0.3, 0.395, 0.15), (0.55, 0.4, 0), (0.7, 0.405, 0))])
        m.loft(KIT, [leg(*a) for a in ((0.7, 0.407, 0), (0.745, 0.407, 0), (0.75, 0.33, 0))], col=WHITE)
    # shorts: the seat, from under the jersey down to the crotch
    m.loft(SHORTS, [((0, hips[1] + y, hips[2] + 0.04), X, BACK, rx, rz, [(wH, 1)], 2.3) for y, rx, rz in
                    ((-0.52, 0.14, 0.2), (-0.36, 0.45, 0.34), (-0.12, 0.57, 0.39), (0.06, 0.52, 0.36), (0.12, 0.46, 0.32))],
           cap_start=True, cap_end=True)
    return m


# ---------------------------------------------------------------- glTF
class Glb:
    def __init__(self):
        self.bin = bytearray(); self.views, self.acc = [], []

    def add(self, arr, ctype, kind, target=None, normalized=False, minmax=False):
        arr = np.ascontiguousarray(arr)
        while len(self.bin) % 4: self.bin.append(0)
        v = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": arr.nbytes}
        if target: v["target"] = target
        self.bin += arr.tobytes(); self.views.append(v)
        a = {"bufferView": len(self.views) - 1, "componentType": ctype, "count": len(arr), "type": kind}
        if normalized: a["normalized"] = True
        if minmax:
            a["min"] = [float(x) for x in np.atleast_2d(arr.reshape(len(arr), -1)).min(0)]
            a["max"] = [float(x) for x in np.atleast_2d(arr.reshape(len(arr), -1)).max(0)]
        self.acc.append(a)
        return len(self.acc) - 1


def write_glb(path, sk, mesh, clips, extras, heavy=None):
    g = Glb(); F, U8, U16, I16 = 5126, 5121, 5123, 5122
    v, nrm = mesh.arrays()
    nodes = []
    for i, n in enumerate(KEEP):
        node = {"name": n, "rotation": [float(x) for x in sk.rest[i]],
                "translation": [float(x) for x in (sk.offset[i] if i else [0, sk.hip_height, 0])]}
        kids = [k for k in range(len(KEEP)) if sk.parent[k] == i]
        if kids: node["children"] = kids
        nodes.append(node)
    inv = np.zeros((len(KEEP), 4, 4), dtype=np.float32)
    for i in range(len(KEEP)):
        M = np.eye(4); M[:3, :3] = sk.bindW[i]; M[:3, 3] = sk.bindP[i]
        inv[i] = np.linalg.inv(M).T                           # column-major
    ibm = g.add(inv.reshape(-1, 16), F, "MAT4")
    attrs = {"POSITION": g.add(v.astype(np.float32), F, "VEC3", 34962, minmax=True),
             "NORMAL": g.add(nrm.astype(np.float32), F, "VEC3", 34962),
             "TEXCOORD_0": g.add(np.array(mesh.uv, dtype=np.float32), F, "VEC2", 34962),
             "COLOR_0": g.add(np.c_[(np.array(mesh.col) * 255).round(), np.full(len(mesh.col), 255)].astype(np.uint8),
                              U8, "VEC4", 34962, normalized=True),
             "JOINTS_0": g.add(np.array(mesh.j, dtype=np.uint8), U8, "VEC4", 34962),
             "WEIGHTS_0": g.add(np.array(mesh.w, dtype=np.float32), F, "VEC4", 34962)}
    # one morph target, "bulk": the same mesh drawn BULK_GIRTH times as thick (the viewer sets
    # it per player from his weight; a negative weight slims him)
    bulk = None
    if heavy is not None:
        bulk = g.add((heavy.arrays()[0] - v).astype(np.float32), F, "VEC3", minmax=True)
    mats, prims = [], []
    for name in ("skin", "jersey", "shorts", "kit"):
        idx = g.add(np.array(mesh.tri[name], dtype=np.uint16).reshape(-1), U16, "SCALAR", 34963)
        mats.append({"name": name, "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0, "roughnessFactor": 0.9}})
        prims.append({"attributes": attrs, "indices": idx, "material": len(mats) - 1})
        if bulk is not None: prims[-1]["targets"] = [{"POSITION": bulk}]
    nodes.append({"name": "Body", "mesh": 0, "skin": 0})
    nodes.append({"name": "Player", "children": [0, len(KEEP)]})
    anims = []
    for name, (times, pos, q, meta) in clips.items():
        t = g.add(times, F, "SCALAR", minmax=True)
        samplers = [{"input": t, "output": g.add(pos, F, "VEC3"), "interpolation": "LINEAR"}]
        channels = [{"sampler": 0, "target": {"node": 0, "path": "translation"}}]
        for j in range(len(KEEP)):
            qi = np.round(q[:, j] * 32767).astype(np.int16)
            samplers.append({"input": t, "output": g.add(qi, I16, "VEC4", normalized=True), "interpolation": "LINEAR"})
            channels.append({"sampler": len(samplers) - 1, "target": {"node": j, "path": "rotation"}})
        anims.append({"name": name, "samplers": samplers, "channels": channels})
    doc = {"asset": {"version": "2.0", "generator": "hoopsim tools/build_player_assets.py",
                     "copyright": "Model: hoopsim. Motion: CMU Graphics Lab Motion Capture Database (mocap.cs.cmu.edu)"},
           "scene": 0, "scenes": [{"nodes": [len(KEEP) + 1]}], "nodes": nodes,
           "meshes": [dict({"name": "Body", "primitives": prims},
                           **({"weights": [0.0], "extras": {"targetNames": ["bulk"]}} if bulk is not None else {}))],
           "materials": mats,
           "skins": [{"joints": list(range(len(KEEP))), "inverseBindMatrices": ibm, "skeleton": 0}],
           "animations": anims, "buffers": [{"byteLength": len(g.bin)}], "bufferViews": g.views, "accessors": g.acc,
           "extras": {"hoopsim": extras}}
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    while len(g.bin) % 4: g.bin.append(0)
    total = 12 + 8 + len(js) + 8 + len(g.bin)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<4sII", b"glTF", 2, total))
        fh.write(struct.pack("<I4s", len(js), b"JSON")); fh.write(js)
        fh.write(struct.pack("<I4s", len(g.bin), b"BIN\0")); fh.write(bytes(g.bin))
    return total


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache", default=str(ROOT / ".mocap-cache"), help="where the downloaded BVH files are kept")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    cache = Path(args.cache); cache.mkdir(parents=True, exist_ok=True)
    loaded = {}

    def load(name):
        if name not in loaded:
            f = cache / f"{name}.bvh"
            if not f.exists():
                url = MIRROR.format(sub=int(name.split("_")[0]), name=name)
                print("fetching", url); urllib.request.urlretrieve(url, f)
            loaded[name] = Bvh(f)
        return loaded[name]

    sk = Skeleton(load(SKELETON_FROM))
    clips = {}
    for name, spec in CLIPS.items():
        clips[name] = build_clip(name, spec, sk, load)
        if name in MIRRORED:
            t, p, q, meta = clips[name]
            mp, mq = mirror_clip(sk, p.astype(np.float64), q)
            mm = dict(meta, mirrored=True)
            if "side" in mm: mm["side"] = -mm["side"]
            clips[name + "_m"] = (t, mp.astype(np.float32), mq, mm)
    mesh, heavy = build_model(sk), build_model(sk, BULK_GIRTH)
    span = 2 * float(sk.bindP[sk.idx["LeftHand"]][0]) + 2 * 0.8          # fingertip to fingertip in the T-pose
    arm = float(sk.bindP[sk.idx["LeftHand"]][0] - sk.bindP[sk.idx["LeftArm"]][0]) + 0.8
    extras = {"version": 2, "height": HEIGHT, "fps": FPS, "clips": {n: c[3] for n, c in clips.items()},
              "body": {"bulk_girth": BULK_GIRTH, "span": round(span, 3), "arm": round(arm, 3)}}
    size = write_glb(Path(args.out), sk, mesh, clips, extras, heavy)
    tris = sum(len(t) for t in mesh.tri.values())
    print(f"{args.out}: {size / 1024:.0f} KB, {len(mesh.v)} vertices, {tris} triangles, {len(KEEP)} joints, {len(clips)} clips")
    for n, c in clips.items():
        m = c[3]
        print(f"  {n:14s} {m['duration']:5.2f}s {m['src']:7s} " + " ".join(f"{k}={m[k]}" for k in ("speed", "side", "marks", "reach", "dribble") if k in m))


if __name__ == "__main__":
    sys.exit(main())
