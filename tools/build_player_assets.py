"""Build the 3D viewer's animated player: one rigged model plus motion-capture clips,
written as a single binary glTF (hoopsim/spatial/assets/player.glb).

    python tools/build_player_assets.py [--cache DIR] [--out FILE]

* Body: MakeHuman's base mesh, default rig weights and shape targets (CC0), shaped into a
  tall athletic man, bound to the mocap skeleton, cut away where the kit covers it and
  simplified. A second shape (the heaviest build) becomes the "bulk" morph target.
* Kit, hair and gear: made here. The jersey, the shorts, socks, the hair cap,
  beard, sleeves and bands are layers lifted off the body's own surface; the shoes and the larger
  hair styles are fitted to it by measuring it.
* Motion: the CMU Graphics Lab Motion Capture Database (free for any use), in Bruce
  Hahne's BVH conversion. Every clip is retargeted to the model's skeleton, cut, turned to
  face +Z, put in place (the viewer moves players from the replay), resampled to 30 Hz and
  stored with quantised rotations. Timing marks the viewer needs (release, take-off,
  natural speed, the dribble's bounces) go in the glTF's top-level ``extras.hoopsim``.

Sources and licences: hoopsim/spatial/assets/LICENSES.md. Neither the MakeHuman files nor
the BVH files are kept in the repository: this script downloads what it needs into
``--cache``. Needs numpy and pyfqmr (mesh simplification; only to build).
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
HEIGHT = 6.5                      # feet, floor to the top of the head in the T-pose
FPS = 30
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
    # hand-keyed on mocap: the jump is captured, the left arm is brought down for a one-hand finish
    "dunk":         ("14_07", 1560, 1676, "shot", dict(marks=dict(gather=1578, release=1624, land=1660), ball="R", pose="dunk",
                                                       desc="jump up to grab, left arm lowered by hand")),
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


# ---------------------------------------------------------------- the MakeHuman body
MH_URL = "https://raw.githubusercontent.com/makehumancommunity/makehuman/master/makehuman/data/"
MH_FILES = {"base.obj": "3dobjs/base.obj", "default.mhskel": "rigs/default.mhskel",
            "default_weights.mhw": "rigs/default_weights.mhw"}
MH_TARGETS = {                        # the shapes mixed into the body (MakeHuman "targets")
    "muscle": "macrodetails/universal-male-young-maxmuscle-averageweight",
    "heavy": "macrodetails/universal-male-young-maxmuscle-maxweight",
    "tall": "macrodetails/height/male-young-maxmuscle-averageweight-maxheight",
    "ideal": "macrodetails/proportions/male-young-maxmuscle-averageweight-idealproportions",
    "african": "macrodetails/african-male-young", "asian": "macrodetails/asian-male-young",
    "caucasian": "macrodetails/caucasian-male-young",
}
# which of the model's joints each bone of MakeHuman's default rig moves with (by name prefix)
MH_BONES = [("root", {"Hips": 1}), ("pelvis", {"Hips": 1}), ("spine05", {"Hips": .5, "LowerBack": .5}),
            ("spine04", {"LowerBack": 1}), ("spine03", {"LowerBack": .4, "Spine": .6}), ("spine02", {"Spine": 1}),
            ("spine01", {"Spine1": .6, "Neck": .4}), ("breast", {"Spine1": 1}), ("clavicle", {"@Shoulder": 1}),
            ("shoulder01", {"@Shoulder": .5, "@Arm": .5}), ("neck01", {"Neck": .4, "Neck1": .6}), ("neck02", {"Neck1": 1}),
            ("neck03", {"Neck1": .5, "Head": .5}), ("upperarm", {"@Arm": 1}), ("lowerarm", {"@ForeArm": 1}),
            ("wrist", {"@Hand": 1}), ("metacarpal", {"@Hand": 1}), ("finger", {"@Hand": 1}),
            ("upperleg", {"@UpLeg": 1}), ("lowerleg", {"@Leg": 1}), ("foot", {"@Foot": 1}), ("toe", {"@ToeBase": 1})]
PARENT = {"Hips": None, "LeftUpLeg": "Hips", "LeftLeg": "LeftUpLeg", "LeftFoot": "LeftLeg", "LeftToeBase": "LeftFoot",
          "RightUpLeg": "Hips", "RightLeg": "RightUpLeg", "RightFoot": "RightLeg", "RightToeBase": "RightFoot",
          "LowerBack": "Hips", "Spine": "LowerBack", "Spine1": "Spine", "Neck": "Spine1", "Neck1": "Neck", "Head": "Neck1",
          "LeftShoulder": "Spine1", "LeftArm": "LeftShoulder", "LeftForeArm": "LeftArm", "LeftHand": "LeftForeArm",
          "RightShoulder": "Spine1", "RightArm": "RightShoulder", "RightForeArm": "RightArm", "RightHand": "RightForeArm"}


class Human:
    """MakeHuman's base body (CC0): mesh, default rig and skin weights, and the shape targets
    that turn the neutral base into a tall, athletic man. Units here are MakeHuman's
    (decimetres); +X is his left, +Y up, +Z his front."""

    def __init__(self, cache: Path):
        cache.mkdir(parents=True, exist_ok=True)

        def get(name, rel):
            f = cache / name
            if not f.exists():
                print("fetching", MH_URL + rel); urllib.request.urlretrieve(MH_URL + rel, f)
            return f

        V, quads, groups, g = [], [], {}, None
        for line in get("base.obj", MH_FILES["base.obj"]).read_text().splitlines():
            if line.startswith("v "): V.append([float(x) for x in line.split()[1:4]])
            elif line.startswith("g "): g = line.split()[1]
            elif line.startswith("f "): groups.setdefault(g, []).append([int(t.split("/")[0]) - 1 for t in line.split()[1:]])
        self.base = np.array(V)
        self.quads = {k: np.array(v) for k, v in groups.items() if not k.startswith("joint")}
        self.nbody = int(self.quads["body"].max()) + 1
        self.rig = json.loads(get("default.mhskel", MH_FILES["default.mhskel"]).read_text())
        wts = json.loads(get("default_weights.mhw", MH_FILES["default_weights.mhw"]).read_text())["weights"]
        self.bone_weights = {b: np.array(w) for b, w in wts.items()}
        self.targets = {}
        for key, rel in MH_TARGETS.items():
            d = np.zeros_like(self.base)
            for line in get(rel.split("/")[-1] + ".target", "targets/" + rel + ".target").read_text().splitlines():
                if line and line[0] != "#":
                    i, x, y, z = line.split(); d[int(i)] = [float(x), float(y), float(z)]
            self.targets[key] = d

    def shape(self, heavy=0.0, face=None):
        """Every vertex (body, helpers and joint markers) of the player's body; heavy: 0..1
        towards MakeHuman's heaviest muscular build; face: one of FACES for that face alone
        (the standard body has an even mix of the three)."""
        t = self.targets
        mix = t[face] if face else (t["african"] + t["asian"] + t["caucasian"]) / 3
        return (self.base + mix + t["tall"] + 0.5 * t["ideal"] + (1 - heavy) * 0.75 * t["muscle"] + heavy * t["heavy"])

    def joint(self, verts, name):
        return verts[self.rig["joints"][name]].mean(0)

    def bone(self, verts, bone, end="head"):
        return self.joint(verts, self.rig["bones"][bone][end])

    def weights(self):
        """Skin weights of the body's vertices on the model's joints: [nbody, len(KEEP)]."""
        W = np.zeros((self.nbody, len(KEEP)))
        for bone, vw in self.bone_weights.items():
            to = next((m for pre, m in MH_BONES if bone.startswith(pre)), {"Head": 1})     # (the face rig)
            side = "Left" if bone.endswith(".L") else "Right"
            sel = vw[:, 0] < self.nbody
            for jn, share in to.items():
                np.add.at(W[:, KEEP.index(jn.replace("@", side))], vw[sel, 0].astype(int), vw[sel, 1] * share)
        return W / np.maximum(W.sum(1, keepdims=True), 1e-9)


# ---------------------------------------------------------------- skeleton
class Skeleton:
    """The model's skeleton: the mocap data's joints, placed where they sit in the MakeHuman
    body and scaled to feet. The bind pose is the body's own rest pose (arms down and out,
    legs apart); every joint's rest rotation is the identity."""

    def __init__(self, hu: Human, verts):
        self.names = KEEP
        self.idx = {n: i for i, n in enumerate(KEEP)}
        self.parent = [self.idx[PARENT[n]] if PARENT[n] else -1 for n in KEEP]
        body = verts[:hu.nbody]
        self.floor = body[:, 1].min()
        self.scale = (HEIGHT - 0.06) / (body[:, 1].max() - self.floor)      # (he stands on a sole)
        P, E, S = self.place(hu, verts)
        self.bindP = np.array([P[n] for n in KEEP]); self.bindW = np.array([np.eye(3)] * len(KEEP))
        self.offset = np.array([P[n] - (P[PARENT[n]] if PARENT[n] else 0) for n in KEEP])
        kids = lambda t: [k for k in range(len(KEEP)) if self.parent[k] == t]
        self.ends = np.array([(E[n] - P[n]) if n in E else (self.offset[kids(i)[0]] if kids(i) else [0, 1, 0])
                              for i, n in enumerate(KEEP)])
        self.second = {self.idx[n]: v / np.linalg.norm(v) for n, v in S.items()}
        self.rest = np.tile([0.0, 0, 0, 1], (len(KEEP), 1))
        self.ankle = float(P["LeftFoot"][1])
        self.hip_height = float(P["Hips"][1])
        self.leg = np.linalg.norm(self.offset[self.idx["LeftLeg"]]) + np.linalg.norm(self.offset[self.idx["LeftFoot"]])

    def place(self, hu, verts):
        """Where the joints sit in a body shape: positions, bone ends and (arms) a second axis."""
        B = lambda bone, end="head": self.to_feet(hu.bone(verts, bone, end))
        P, E, S = {}, {}, {}                                    # joint positions, bone ends, a second axis
        P["Hips"] = P["LowerBack"] = B("spine05")
        P["Spine"] = B("spine03"); P["Spine1"] = P["Neck"] = B("spine01")
        P["Neck1"] = B("neck01"); P["Head"] = B("head"); E["Head"] = B("head", "tail")
        for sd, mh, sx in (("Left", ".L", 1), ("Right", ".R", -1)):
            P[sd + "Shoulder"] = P["Spine1"]
            P[sd + "Arm"] = B("upperarm01" + mh); P[sd + "ForeArm"] = B("lowerarm01" + mh); P[sd + "Hand"] = B("wrist" + mh)
            E[sd + "Hand"] = B("finger3-1" + mh)
            P[sd + "UpLeg"] = B("upperleg01" + mh); P[sd + "Leg"] = B("lowerleg01" + mh); P[sd + "Foot"] = B("foot" + mh)
            toe = (B("toe2-1" + mh) + B("toe4-1" + mh)) / 2
            P[sd + "ToeBase"] = toe; E[sd + "ToeBase"] = toe + (toe - P[sd + "Foot"]) * [0.5, 0, 0.5]
            # which way the outside of the elbow and the back of the hand face: straight up in
            # the mocap's zero pose (arms out, palms down), so the arm's twist carries over
            up, fore = P[sd + "ForeArm"] - P[sd + "Arm"], P[sd + "Hand"] - P[sd + "ForeArm"]
            S[sd + "Arm"] = S[sd + "ForeArm"] = -sx * np.cross(up, fore)
            S[sd + "Hand"] = sx * np.cross(B("finger2-1" + mh) - P[sd + "Hand"], B("finger5-1" + mh) - P[sd + "Hand"])
        for n in KEEP:                                          # exactly symmetric
            if n.startswith("Left"):
                r = "Right" + n[4:]
                for d in (P, E, S):
                    if n in d: m = (d[n] + d[r] * [-1, 1, 1]) / 2; d[n] = m; d[r] = m * [-1, 1, 1]
            elif not n.startswith("Right"):
                P[n] = P[n] * [0, 1, 1]
                if n in E: E[n] = E[n] * [0, 1, 1]
        return P, E, S

    def to_feet(self, v):
        return (np.asarray(v) - [0, self.floor, 0]) * self.scale + [0, 0.06, 0]


def end_of(bv, j):
    """The direction a joint's bone points along: its first child's offset (or the end site)."""
    while True:
        kids = [k for k in range(len(bv.names)) if bv.parent[k] == j]
        if not kids: return np.array(bv.end.get(j, [0, 1, 0]), dtype=np.float64)
        if np.linalg.norm(bv.offset[kids[0]]) > 1e-6: return bv.offset[kids[0]].copy()
        j = kids[0]


def frame(d, s):
    """An orthonormal frame with its first axis along d and its second as near s as it can be."""
    d = d / np.linalg.norm(d); s = s - d * (s @ d); s = s / np.linalg.norm(s)
    return np.stack([d, s, np.cross(d, s)], 1)


def retarget(bv: Bvh, sk: Skeleton, a: int, b: int):
    """Frames a..b of a clip on the model's skeleton: (root positions in feet, local quats).
    The mocap's bones and the model's point different ways at rest (its arms are out, the
    model's down), so each joint's world rotation is taken over with the turn that lines the
    model's bone up with the mocap's; on the arms the twist is lined up too."""
    pos, rot = bv.local()
    W0, _ = fk(bv.parent, bv.offset, pos[:1] * 0, rot[:1] * 1)    # frame 0: the conversion's T-pose, head level
    W0 = W0[0]
    pos, rot = pos[a:b + 1], rot[a:b + 1]
    W, _ = fk(bv.parent, bv.offset, pos, rot)
    Wt = np.zeros((len(pos), len(KEEP), 3, 3))
    for n in KEEP:
        j, t = bv.idx[n], sk.idx[n]
        kids = [k for k in range(len(KEEP)) if sk.parent[k] == t]
        Wt[:, t] = W[:, j]
        if n in ("Neck", "Neck1", "Head"):                      # level when the mocap's T-pose head is
            Wt[:, t] = W[:, j] @ (W0[0].T @ W0[j]).T
        elif len(kids) <= 1 and n != "Hips":
            d_src = end_of(bv, j)
            if kids:
                k = bv.idx[KEEP[kids[0]]]; d_src = bv.offset[k].copy(); p = bv.parent[k]
                while p != j: d_src = d_src + bv.offset[p]; p = bv.parent[p]
            d_tgt = sk.ends[t]
            if np.linalg.norm(d_src) > 1e-6 and np.linalg.norm(d_tgt) > 1e-6:
                if t in sk.second: fix = frame(d_src, np.array([0.0, 1, 0])) @ frame(d_tgt, sk.second[t]).T
                else: fix = rot_between(d_tgt, d_src)
                Wt[:, t] = W[:, j] @ fix
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
        if opt.get("pose") == "dunk":
            g0, rel, land = (marks[k] - a for k in ("gather", "release", "land"))
            for f in range(len(q)):
                w = float(np.clip((f - g0) / max(1, rel - g0), 0, 1)) * float(np.clip((len(q) - 1 - f) / max(1, len(q) - 1 - land), 0, 1))
                if w <= 0: continue
                W, _ = pose_fk(sk, pos[f:f + 1], q[f:f + 1]); W = W[0]
                aim(sk, W, "LeftArm", [0.45, -0.85, 0.3]); aim(sk, W, "LeftForeArm", [0.25, -0.5, 0.83])
                aim(sk, W, "RightArm", [-0.12, 0.95, 0.3]); aim(sk, W, "RightForeArm", [-0.05, 0.9, 0.42])
                q[f] = nlerp(q[f], world_to_local(sk, W), w * w * (3 - 2 * w))
            q = continuous(q)
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


def ellipsoid(mesh, mat, c, r, weights, col=(1, 1, 1), lat0=-90, lat1=90, rows=7, sides=12, shape=None, bulk=False):
    """shape(sin latitude) -> (x scale, z scale, z shift): a jaw, a hairline."""
    rings = []
    for i in range(rows + 1):
        lat = np.radians(lat0 + (lat1 - lat0) * i / rows)
        rr = max(np.cos(lat), 0.02); kx, kz, dz = shape(np.sin(lat)) if shape else (1, 1, 0)
        rings.append((np.asarray(c) + [0, np.sin(lat) * r[1], dz], (1, 0, 0), (0, 0, -1), r[0] * rr * kx, r[2] * rr * kz, weights))
    mesh.loft(mat, rings, lat0 > -89, lat1 < 89, col, sides=sides, bulk=bulk)


SKIN_TRIS = 5200                  # the bare skin left showing, after simplifying
FACES = ("african", "asian", "caucasian")     # MakeHuman's three face (and head) shapes: morph targets face_a, face_b, face_c
OUT_LIGHT = OUT.with_name("player_light.glb")
LIGHT_SKIN_TRIS = 1800            # the light model: for phones and slow devices
LIGHT_KEEP = {"jersey": 0.3, "shorts": 0.4, "socks": 0.3, "sleeve": 0.4, "leg": 0.4, "wrist": 0.6,
              "hair": 0.5, "beard": 0.5, "headband": 0.5}
TORSO = ("Hips", "LowerBack", "Spine", "Spine1", "Neck", "LeftShoulder", "RightShoulder")


def nearest(points, cloud, chunk=512):
    """For each point, the index of the closest point of the cloud."""
    out = np.zeros(len(points), dtype=int)
    for i in range(0, len(points), chunk):
        d = ((points[i:i + chunk, None, :] - cloud[None, :, :]) ** 2).sum(-1)
        out[i:i + chunk] = d.argmin(1)
    return out


def slab(sel, values, at, band):
    """The selected vertices whose value is within ``band`` of ``at``: wider if the mesh is
    too sparse there to measure anything."""
    while True:
        s = sel & (np.abs(values - at) < band)
        if s.sum() >= 8 or band > 2: return s
        band *= 1.5


class Body:
    """The MakeHuman body made ready for the player: in feet on the model's skeleton, with
    what the kit hides cut away and the rest simplified. Built once from the standard shape;
    another shape (the heavy build) reuses its triangles, so it can be a morph target."""

    def __init__(self, hu: Human, sk: Skeleton, verts, skin_tris=SKIN_TRIS):
        import pyfqmr
        self.hu, self.sk = hu, sk
        self.W = hu.weights(); self.dom = self.W.argmax(1)
        self.P0 = self.feet(verts)
        ix = sk.idx; P = self.P0; J = lambda n: sk.bindP[ix[n]]
        self.is_torso = np.isin(self.dom, [ix[n] for n in TORSO])
        # along each limb bone: 0 at its joint, 1 at the next
        self.along = np.zeros(len(P))
        for a, b in (("UpLeg", "Leg"), ("Leg", "Foot"), ("Arm", "ForeArm"), ("ForeArm", "Hand")):
            for sd in ("Left", "Right"):
                pa, pb = J(sd + a), J(sd + b); sel = self.dom == ix[sd + a]
                self.along[sel] = (P[sel] - pa) @ (pb - pa) / ((pb - pa) @ (pb - pa))
        # MakeHuman's face rig tells jaw from lips from the rest of the head
        def rigw(pre):
            w = np.zeros(len(P))
            for bone, vw in hu.bone_weights.items():
                if bone.startswith(pre):
                    sel = vw[:, 0] < len(P); np.add.at(w, vw[sel, 0].astype(int), vw[sel, 1])
            return w
        self.jaw, self.lips = rigw("jaw") + rigw("special04"), rigw("oris")
        head = (self.dom == ix["Head"]) & (P[:, 1] > J("Head")[1] + 0.12)
        self.eye = np.array([sk.to_feet(hu.bone(verts, "eye.L")), sk.to_feet(hu.bone(verts, "eye.R"))])
        ey = self.eye[0][1]; skull = head & (P[:, 1] > ey + 0.1)
        zf, zb = P[skull, 2].max(), P[skull, 2].min()
        self.hc = np.array([0, ey + 0.06, (zf + zb) / 2])     # the middle of the skull, and its half-sizes
        self.hr = np.array([np.abs(P[skull, 0]).max(), P[head, 1].max() - ey - 0.06, (zf - zb) / 2])
        # the scalp: above a hairline that runs from the top of the forehead down to the nape
        line = ey + 0.27 - 0.55 * np.clip((zf - P[:, 2]) / (zf - zb), 0, 1) ** 0.8
        self.is_head = self.dom == ix["Head"]
        self.above_hairline = P[:, 1] - line
        q = hu.quads["body"]; nrm = np.zeros_like(P)
        for a, b, c in ((0, 1, 2), (0, 2, 3)):
            fn = np.cross(P[q[:, b]] - P[q[:, a]], P[q[:, c]] - P[q[:, a]])
            for k in (a, b, c): np.add.at(nrm, q[:, k], fn)
        self.N0 = nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)
        # what the kit covers: the trunk under the jersey, hips and thighs under the shorts, the feet in the shoes
        yH, yS = J("Hips")[1], J("LeftArm")[1]
        # the jersey is a layer of the body's own surface: a tank top from just above the crotch
        # to the shoulders, with round armholes and a scooped neck
        trunk = self.is_torso | np.isin(self.dom, [ix["LeftUpLeg"], ix["RightUpLeg"]])
        self.crotch = P[trunk & (np.abs(P[:, 0]) < 0.06), 1].min()
        self.hem = yH + 0.1                                  # it hangs to the hips, over the top of the shorts
        # Its outline is where a smooth field crosses zero (positive inside the jersey), and the
        # body's triangles are cut along that line: the edge of the cloth is then a clean curve,
        # not the stepped (and at the shoulder, spiky) edge of whichever of the body's own faces
        # happen to fall inside.
        arm, neck = J("LeftArm") + [0.07, -0.1, 0], J("Neck1") + [0, 0.0, 0.08]
        W = self.W; col = lambda names: W[:, [ix[n] for n in names]].sum(1)
        front = np.clip((P[:, 2] - (neck[2] - 0.2)) / 0.1, 0, 1)
        f = np.minimum.reduce([
            P[:, 1] - self.hem,                                                     # the hem
            arm[0] - 0.1 - np.abs(P[:, 0]),                                         # no further out than the shoulder
            np.minimum(np.linalg.norm(P - arm, axis=1), np.linalg.norm(P - arm * [-1, 1, 1], axis=1)) - 0.25,   # armholes
            np.linalg.norm(P - neck, axis=1) - (0.2 + 0.06 * front * front * (3 - 2 * front)),                  # neckline: lower in front
            0.5 - col([sd + j for sd in ("Left", "Right") for j in ("Arm", "ForeArm", "Hand")]),                # not the arms
            0.5 - col(["Neck1", "Head"])])                                          # nor the neck
        self.shirt_f = f
        q = hu.quads["body"]
        self.shirt_patch = self.cut(f)
        on = f > 0
        core = on.copy()                                      # the skin well inside its edge is not needed
        for _ in range(2):
            edge = np.zeros(len(P), dtype=bool); edge[np.unique(q[~core[q].all(1)])] = True
            core &= ~edge
        cov = core | (np.isin(self.dom, [ix["LeftUpLeg"], ix["RightUpLeg"]]) & (self.along < 0.68))
        # the top of the shorts is a layer of the body too: waist to crotch, and the tops of the thighs
        thigh = np.isin(self.dom, [ix["LeftUpLeg"], ix["RightUpLeg"]])
        seat = (P[:, 1] < yH + 0.34) & ((trunk & ~thigh & (P[:, 1] > self.crotch - 0.3)) | (thigh & (self.along < 0.24)))
        cov |= seat & (P[:, 1] < yH + 0.2)
        # The shorts are one layer of the body too, cut the same way: from the waist (under
        # the jersey) down each thigh to a hem above the knee, with a white band at the hem.
        legs = np.where(thigh, 0.8 - self.along, np.where(trunk & (P[:, 1] > self.crotch - 0.3), 1.0, -1.0))
        self.shorts_f = np.minimum(yH + 0.34 - P[:, 1], legs)
        self.shorts_patch = self.cut(self.shorts_f)
        self.band_patch = self.cut(np.minimum(self.shorts_f, np.where(thigh, self.along - 0.755, -1.0)))
        # socks: the lower shin and the ankle
        self.sock = np.zeros(len(P), dtype=bool)
        for sd in ("Left", "Right"):
            self.sock |= ((self.dom == ix[sd + "Leg"]) & (self.along > 0.68)) | (self.dom == ix[sd + "Foot"])
        self.under = np.zeros(len(P), dtype=bool)             # skin under cloth he always wears
        self.under[self.shirt_f > 0.03] = True; self.under[self.shorts_f > 0.03] = True
        self.under |= self.sock
        cov |= np.isin(self.dom, [ix[sd + j] for sd in ("Left", "Right") for j in ("Foot", "ToeBase")]) & (P[:, 1] < sk.ankle + 0.16)
        tris = np.concatenate([hu.quads["body"][:, [0, 1, 2]], hu.quads["body"][:, [0, 2, 3]]])
        tris = tris[~cov[tris].all(1)]
        used = np.unique(tris); remap = np.full(len(P), -1); remap[used] = np.arange(len(used))
        simp = pyfqmr.Simplify(); simp.setMesh(P[used], remap[tris])
        simp.simplify_mesh(target_count=skin_tris, aggressiveness=4, preserve_border=True, verbose=False)
        self.D, self.F, _ = simp.getMesh()
        self.src = used[nearest(self.D, P[used])]             # the body vertex each kept vertex stands for
        nrm = np.zeros_like(self.D)
        fn = np.cross(self.D[self.F[:, 1]] - self.D[self.F[:, 0]], self.D[self.F[:, 2]] - self.D[self.F[:, 0]])
        for k in range(3): np.add.at(nrm, self.F[:, k], fn)
        self.N = nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)

    def cut(self, f):
        """The part of the body's surface where the field f is positive, its triangles cut
        along f = 0: (vertices, faces). A vertex is (a, b, t): the point t of the way from body
        vertex a to b (a body vertex itself: a = b)."""
        q = self.hu.quads["body"]
        tris = np.concatenate([q[:, [0, 1, 2]], q[:, [0, 2, 3]]])
        defs, index, faces = [], {}, []
        def pv(a, b=None):
            if b is None: key, d = (a, a), (a, a, 0.0)
            else:
                lo, hi = (a, b) if a < b else (b, a)
                key, d = (lo, hi), (lo, hi, float(f[lo] / (f[lo] - f[hi])))
            if key not in index: index[key] = len(defs); defs.append(d)
            return index[key]
        for t in tris[(f[tris] > 0).any(1)]:
            poly = []
            for k in range(3):
                a, b = t[k], t[(k + 1) % 3]
                if f[a] > 0: poly.append(pv(a))
                if (f[a] > 0) != (f[b] > 0): poly.append(pv(a, b))
            for k in range(1, len(poly) - 1): faces.append((poly[0], poly[k], poly[k + 1]))
        return defs, np.array(faces)

    def feet(self, verts):
        """A body shape's vertices in feet, on the model's skeleton: where a shape moves the
        joints (a heavier body is a little shorter), the vertices are moved back with them, so
        only the change of shape round each bone is left."""
        P = (verts[:self.hu.nbody] - [0, self.sk.floor, 0]) * self.sk.scale + [0, 0.06, 0]
        return P - self.W @ self.shift(verts)

    def shift(self, verts):
        placed = self.sk.place(self.hu, verts)[0]
        return np.array([placed[n] for n in KEEP]) - self.sk.bindP


def build_model(sk: Skeleton, body: Body, verts) -> Mesh:
    """The player in his rest pose (+X his left, +Z his front) for one body shape. Materials:
    skin (tinted per player), jersey (textured: number, trim), shorts (team colour), kit
    (vertex colours: eyes, brows, socks, soles, trim), and the optional parts the viewer
    switches on per player: hair styles, beard, headband, sleeves, wristbands, leg sleeves,
    shoes."""
    m = Mesh(); ix = sk.idx; hu = body.hu
    P = body.feet(verts)                                      # every body vertex, this shape
    D = body.D + (P[body.src] - body.P0[body.src])            # the simplified skin, this shape
    D = D - body.N * (0.045 * body.under[body.src])[:, None]     # (tucked in under his kit, so it can't show through)
    W, dom, src = body.W, body.dom, body.src
    J = lambda n: sk.bindP[ix[n]]
    X, BACK = (1, 0, 0), (0, 0, -1)
    WHITE, DARK, BROW = (0.85, 0.85, 0.85), (0.006, 0.005, 0.005), (0.012, 0.009, 0.007)   # linear RGB
    wts = lambda i: [(j, W[i, j]) for j in np.nonzero(W[i] > 0.02)[0]]
    mean_w = lambda sel: [(j, w) for j, w in enumerate(W[sel].mean(0)) if w > 0.04] if sel.any() else [(ix["Hips"], 1)]

    # ---- skin
    skin = [m.vert(D[i], wts(src[i])) for i in range(len(D))]
    for f in body.F: m.face("skin", skin[f[0]], skin[f[1]], skin[f[2]])

    def iron(pos, faces, relax):
        """Ease each point of a layer toward its neighbours ``relax`` times: takes the muscles
        out of cloth. Points on its edge only follow the edge, so the outline keeps its place."""
        count = {}
        for fc in faces:
            for a, b in zip(fc, np.roll(fc, -1)): count[(min(a, b), max(a, b))] = count.get((min(a, b), max(a, b)), 0) + 1
        rim = {v for e, c in count.items() if c == 1 for v in e}
        nb = {}
        for (a, b), c in count.items():
            if (a in rim) == (b in rim) and (c == 1 or a not in rim):
                nb.setdefault(a, set()).add(b); nb.setdefault(b, set()).add(a)
            elif a not in rim: nb.setdefault(a, set()).add(b)
            elif b not in rim: nb.setdefault(b, set()).add(a)
        nb = {v: list(u) for v, u in nb.items()}
        for _ in range(relax):
            new = pos.copy()
            for v, u in nb.items(): new[v] = 0.5 * pos[v] + 0.5 * pos[u].mean(0)
            pos = new
        return pos

    def cloth(quads, lift, relax):
        """Where a layer of cloth over part of the body sits: lifted off the surface, then
        ironed. Returns {body vertex: position}."""
        vs = np.unique(quads); at = np.full(len(P), -1); at[vs] = np.arange(len(vs))
        pos = iron(P[vs] + body.N0[vs] * np.array([lift(v) for v in vs])[:, None], at[quads], relax)
        return {v: pos[at[v]] for v in vs}

    def shell0(mat, pick, lift, relax=0, col=(1, 1, 1)):
        """A layer over part of the full-detail body (hair, beard, sleeves, socks: where an
        edge must be clean), ironed ``relax`` times."""
        quads = hu.quads["body"][pick[hu.quads["body"]].all(1)]
        if not len(quads): return
        where = cloth(quads, lambda i: lift, relax); ids = {}
        for qd in quads:
            for i in qd:
                if i not in ids: ids[i] = m.vert(where[i], wts(i), col=col)
            m.face(mat, ids[qd[0]], ids[qd[1]], ids[qd[2]]); m.face(mat, ids[qd[0]], ids[qd[2]], ids[qd[3]])

    # ---- eyes (MakeHuman's own eyeballs) and brows
    for k, grp in enumerate(("helper-l-eye", "helper-r-eye")):
        q = hu.quads[grp]; ids = {}
        ev = (verts[np.unique(q)] - [0, sk.floor, 0]) * sk.scale + [0, 0.06, 0] - body.shift(verts)[ix["Head"]]
        front = ev[:, 2].max(); wH = [(ix["Head"], 1)]
        for i, v in zip(np.unique(q), ev):
            ids[i] = m.vert(v + [0, 0, -0.004], wH, col=DARK if v[2] > front - 0.012 else WHITE)
        for f in q: m.face("kit", ids[f[0]], ids[f[1]], ids[f[2]]); m.face("kit", ids[f[0]], ids[f[2]], ids[f[3]])
        e = body.eye[k]; sx = 1 if k == 0 else -1; face = (dom == ix["Head"]) & (P[:, 2] > e[2] - 0.12)
        pts = []
        for dx in (-0.075, -0.02, 0.04, 0.095):               # along the brow ridge, on the skin
            at = np.array([e[0] + sx * dx, e[1] + 0.085 - 0.02 * abs(dx - 0.01) / 0.08, 0])
            near = np.nonzero(face)[0][np.argmin(((P[face][:, :2] - at[:2]) ** 2).sum(1))]
            pts.append(np.array([at[0], at[1], P[near, 2] + 0.006]))
        m.loft("kit", [(pt, (0, 1, 0), (0, 0, 1), 0.016, 0.008, wH) for pt in (pts if sx > 0 else pts[::-1])],
               cap_start=True, cap_end=True, col=BROW, sides=6, bulk=False)

    # ---- jersey: a layer of the body's own surface, so it fits and moves exactly as he does,
    # let out a little below the chest so it hangs. u runs round him (back at 0.25, front at
    # 0.75), v from the hem to the shoulders: the viewer paints number and trim.
    hem, yS, ax = body.hem, J("LeftArm")[1], J("LeftArm")[0]
    trunk = body.is_torso | np.isin(dom, [ix["LeftUpLeg"], ix["RightUpLeg"]])
    def ring(y, sel, pad, band=0.07, xmax=9.0):
        s = slab(sel & (np.abs(P[:, 0]) < xmax), P[:, 1], y, band)
        z0, z1 = P[s, 2].min(), P[s, 2].max()
        return np.abs(P[s, 0]).max() + pad, (z1 - z0) / 2 + pad, (z0 + z1) / 2, mean_w(s)
    def layer(patch, lift, relax):
        """A cut patch as cloth: lifted off the skin by ``lift`` (feet, per body vertex) and
        ironed. Returns (positions, skin weights, rest positions, faces)."""
        defs, faces = patch
        A, B = np.array([d[0] for d in defs]), np.array([d[1] for d in defs]); T = np.array([d[2] for d in defs])[:, None]
        mixed = lambda arr: arr[A] * (1 - T) + arr[B] * T      # a value at each patch vertex
        return iron(mixed(P + body.N0 * lift[:, None]), faces, relax), mixed(W), mixed(body.P0), faces
    wmix = lambda row: [(j, row[j]) for j in np.nonzero(row > 0.02)[0]]

    # how far off the skin: close on the shoulders, loose from the chest down so it hangs
    # straight, as a basketball jersey does, instead of following the waist
    hang = 0.05 + 0.15 * np.clip((yS - 0.25 - body.P0[:, 1]) / 0.7, 0, 1) ** 1.2
    where, Wm, rest, faces = layer(body.shirt_patch, hang, relax=7)
    zc = rest[:, 2].mean(); top = rest[:, 1].max()
    turn = (np.arctan2(-(rest[:, 2] - zc), rest[:, 0]) / (2 * np.pi)) % 1.0
    ids = {}
    for fc in faces:
        us = turn[fc]; wrap = us.max() - us.min() > 0.5         # (across the seam at his left side)
        corner = []
        for i, u in zip(fc, us):
            key = (i, bool(wrap and u < 0.5))
            if key not in ids:
                ids[key] = m.vert(where[i], wmix(Wm[i]), (u + (1 if key[1] else 0), (rest[i, 1] - hem) / (top - hem)))
            corner.append(ids[key])
        m.face("jersey", *corner)

    # ---- shorts: one loose layer from the waist to above the knee, with a white band at the hem.
    # Looser toward the hem, and ironed hard so it hangs as cloth instead of showing the body;
    # less so on the inside of the thighs, where the two legs would run into each other.
    yH = J("Hips")[1]
    inner = np.clip(-body.N0[:, 0] * np.sign(body.P0[:, 0]), 0, 1) * np.isin(dom, [ix["LeftUpLeg"], ix["RightUpLeg"]])
    loose = (0.06 + 0.2 * np.clip((yH + 0.1 - body.P0[:, 1]) / 1.0, 0, 1)) * (1 - 0.7 * inner)
    for mat, patch, extra, colr in (("shorts", body.shorts_patch, 0.0, (1, 1, 1)), ("kit", body.band_patch, 0.012, WHITE)):
        where, Wm, _, faces = layer(patch, loose + extra, relax=6)
        ids = [m.vert(where[i], wmix(Wm[i]), col=colr) for i in range(len(where))]
        for fc in faces: m.face(mat, *[ids[i] for i in fc])
    for sd, sx in (("Left", 1), ("Right", -1)):
        kn = J(sd + "Leg")
        # ---- sock and shoe, sized from the foot
        an = J(sd + "Foot"); toe = J(sd + "ToeBase"); wF, wT, wK = ix[sd + "Foot"], ix[sd + "ToeBase"], ix[sd + "Leg"]
        shin = (kn - an) / np.linalg.norm(kn - an); sx_, sy_ = frame_for(shin, X)
        def shin_ring(h, pad):
            off = P - an; dist = np.linalg.norm(off - np.outer(off @ shin, shin), axis=1)
            sel = slab(((dom == wK) | (dom == wF)) & (dist < 0.3), off @ shin, h, 0.05)
            return (an + shin * h, sx_, sy_, dist[sel].max() + pad, dist[sel].max() + pad, [(wF, 1)])
        m.loft("shoes", [shin_ring(h, pad) for h, pad in ((0.24, 0.02), (0.12, 0.035), (-0.02, 0.045))], sides=12, bulk=False)
        shell0("socks", body.sock & np.isin(dom, [wK, wF]), 0.016, relax=2)
        foot = np.isin(dom, [wF, wT]) & (P[:, 1] < an[1] + 0.05)
        fd = np.array([toe[0] - an[0], 0, toe[2] - an[2]]); fd /= np.linalg.norm(fd); side = np.cross((0, 1, 0), fd)
        s_ = (P[foot] - an) @ fd; heel, tip = s_.min() - 0.035, s_.max() + 0.045
        half = np.abs((P[foot] - an) @ side).max() + 0.03; mid = ((P[foot] - an) @ side).max() - half + 0.03
        last = ((0.0, 0.55, 0.3, 0), (0.05, 0.82, 0.4, 0), (0.22, 0.92, 0.43, 0), (0.5, 1.0, 0.34, 0.25),
                (0.74, 1.03, 0.26, 0.8), (0.9, 0.95, 0.21, 1), (0.98, 0.7, 0.16, 1), (1.0, 0.35, 0.11, 1))
        for mat, colr, lo, grow in (("shoes", (1, 1, 1), 0.07, 0.0), ("kit", WHITE, 0.0, 0.012)):
            sole = []
            for u, w, hgt, wt in last:
                hi = hgt if mat == "shoes" else 0.085
                c = np.array([an[0], (lo + hi) / 2, an[2]]) + fd * (heel + (tip - heel) * u) + side * mid
                sole.append((c, side, (0, 1, 0), half * w + grow, (hi - lo) / 2, [(wF, 1 - wt), (wT, wt)], 3.0))
            m.loft(mat, sole, cap_start=True, cap_end=True, col=colr, sides=12, bulk=False)

        # ---- gear that hugs the limb: arm sleeve, wristband, leg sleeve
        tag = sd[0]
        al = body.along
        shell0("sleeve_" + tag, ((dom == ix[sd + "Arm"]) & (al > 0.5)) | ((dom == ix[sd + "ForeArm"]) & (al < 0.9)), 0.03, relax=2)
        shell0("wrist_" + tag, (dom == ix[sd + "ForeArm"]) & (al > 0.72) & (al < 0.95), 0.04, relax=1)
        shell0("leg_" + tag, ((dom == ix[sd + "UpLeg"]) & (al > 0.78)) | ((dom == wK) & (al < 0.62)), 0.03, relax=2)

    # ---- hair: one style is shown per player (or none), in his hair colour; beard; headband
    hc, hr = body.hc, body.hr; wH = [(ix["Head"], 1)]
    ears = (np.abs(body.P0[:, 0]) > hr[0] * 0.985) & (body.P0[:, 1] < hc[1] + 0.12)
    scalp = lambda above: body.is_head & (body.above_hairline > above) & ~ears
    shell0("hair_cap", scalp(0.0), 0.02)                      # short hair; also under the flat top, locs and bun
    shell0("hair_fade", scalp(0.17), 0.035)                   # the top only: shaved sides
    ellipsoid(m, "hair_afro", hc + [0, 0.1, -0.03], hr * [1.32, 1.42, 1.22], wH, lat0=-2, lat1=90, rows=8, sides=16,
              shape=lambda sn: (1, 1, -0.3 * hr[2] * (1 - sn)))     # (back off the forehead)
    m.loft("hair_flattop", [((0, hc[1] + y, hc[2] - 0.02), X, BACK, hr[0] * 0.84, hr[2] * 0.8, wH, 3.4)
                            for y in (hr[1] * 0.55, hr[1] * 1.3)], cap_end=True, bulk=False)
    for a in np.radians(np.arange(60, 301, 24)):              # locks hanging round the sides and back
        out = np.array([np.sin(a), 0, np.cos(a)]); base = hc + out * [hr[0] * 0.98, 0, hr[2] * 0.98] + [0, 0.04, 0]
        lx, ly = frame_for((0, -1, 0), out)
        m.loft("hair_locs", [(base + out * o + [0, -dn, 0], lx, ly, r, r, wH) for dn, o, r in
                             ((0, 0, 0.04), (0.2, 0.02, 0.045), (0.42, 0.03, 0.042), (0.58, 0.02, 0.03))],
               cap_end=True, sides=6, bulk=False)
    ellipsoid(m, "hair_bun", hc + [0, hr[1] * 0.75, -hr[2] * 0.9], (0.11, 0.11, 0.11), wH, rows=5, sides=10)
    low = body.P0[:, 1] < body.eye[0][1] - 0.2
    shell0("beard", body.is_head & (body.jaw > 0.3) & (body.lips < 0.25) & low & (body.P0[:, 2] > hc[2] - 0.05), 0.012)
    brow = body.eye[0][1] + 0.13
    shell0("headband", body.is_head & (body.P0[:, 1] > brow) & (body.P0[:, 1] < brow + 0.2), 0.02)
    return m


def lighten(meshes, keep):
    """The same model with fewer triangles: each material named in ``keep`` (by its name or
    the part before "_") is simplified to that fraction. ``meshes`` are the standard shape
    and its other shapes, which share triangles; so do the results."""
    import pyfqmr
    base = meshes[0]; V = [np.array(m.v) for m in meshes]; out = [Mesh() for _ in meshes]
    for mat, tris in base.tri.items():
        t = np.array(tris); used = np.unique(t); remap = np.full(len(base.v), -1); remap[used] = np.arange(len(used))
        frac = keep.get(mat, keep.get(mat.split("_")[0], 1.0))
        if frac < 1 and len(t) > 80:
            simp = pyfqmr.Simplify(); simp.setMesh(V[0][used], remap[t])
            simp.simplify_mesh(target_count=int(len(t) * frac), aggressiveness=4, preserve_border=True, verbose=False)
            D, F, _ = simp.getMesh(); near = used[nearest(D, V[0][used])]
        else:
            D, F, near = V[0][used], remap[t], used
        uv = np.array([base.uv[i] for i in near], dtype=np.float64)
        if mat == "jersey": uv[:, 0] %= 1.0                    # (put back across the seam below)
        for k, o in enumerate(out):
            pos = D + (V[k][near] - V[0][near]); ids = {}
            def vert(i, shift=0):
                if (i, shift) not in ids:
                    ids[(i, shift)] = o.vert(pos[i], list(zip(base.j[near[i]], base.w[near[i]])), (uv[i, 0] + shift, uv[i, 1]), base.col[near[i]])
                return ids[(i, shift)]
            for f in F:
                wrap = mat == "jersey" and np.ptp(uv[f, 0]) > 0.5
                o.face(mat, *[vert(i, 1 if wrap and uv[i, 0] < 0.5 else 0) for i in f])
    return out


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


def write_glb(path, sk, mesh, clips, extras, morphs=()):
    """morphs: [(name, per-vertex offsets, sparse)], the mesh's morph targets in order."""
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
    targets = []
    for _, delta, sparse in morphs:
        delta = np.asarray(delta, dtype=np.float32)
        if not sparse:
            targets.append({"POSITION": g.add(delta, F, "VEC3", minmax=True)}); continue
        rows = np.nonzero(np.abs(delta).max(1) > 1e-4)[0]     # only the vertices that move are stored
        ia = g.add(rows.astype(np.uint16), U16, "SCALAR"); va = g.add(delta[rows], F, "VEC3")
        g.acc.append({"componentType": F, "count": len(delta), "type": "VEC3",
                      "min": [float(x) for x in delta.min(0)], "max": [float(x) for x in delta.max(0)],
                      "sparse": {"count": len(rows), "indices": {"bufferView": g.acc[ia]["bufferView"], "componentType": U16},
                                 "values": {"bufferView": g.acc[va]["bufferView"]}}})
        targets.append({"POSITION": len(g.acc) - 1})
    mats, prims = [], []
    base = ["skin", "jersey", "shorts", "kit"]                # then the optional parts: hair styles, beard, accessories, shoes
    for name in base + [n for n in mesh.tri if n not in base]:
        if not mesh.tri[name]: continue
        idx = g.add(np.array(mesh.tri[name], dtype=np.uint16).reshape(-1), U16, "SCALAR", 34963)
        mats.append({"name": name, "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0, "roughnessFactor": 0.9}})
        prims.append({"attributes": attrs, "indices": idx, "material": len(mats) - 1})
        if targets: prims[-1]["targets"] = targets
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
                     "copyright": "Body: MakeHuman (makehumancommunity.org), CC0. Kit and hair: hoopsim. "
                                  "Motion: CMU Graphics Lab Motion Capture Database (mocap.cs.cmu.edu)"},
           "scene": 0, "scenes": [{"nodes": [len(KEEP) + 1]}], "nodes": nodes,
           "meshes": [dict({"name": "Body", "primitives": prims},
                           **({"weights": [0.0] * len(targets), "extras": {"targetNames": [n for n, _, _ in morphs]}} if targets else {}))],
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

    hu = Human(cache / "makehuman"); verts = hu.shape()
    sk = Skeleton(hu, verts)
    clips = {}
    for name, spec in CLIPS.items():
        clips[name] = build_clip(name, spec, sk, load)
        if name in MIRRORED:
            t, p, q, meta = clips[name]
            mp, mq = mirror_clip(sk, p.astype(np.float64), q)
            mm = dict(meta, mirrored=True)
            if "side" in mm: mm["side"] = -mm["side"]
            clips[name + "_m"] = (t, mp.astype(np.float32), mq, mm)
    body = Body(hu, sk, verts)
    shapes = [verts, hu.shape(heavy=1.0)] + [hu.shape(face=f) for f in FACES]
    mesh, heavy, *faces = [build_model(sk, body, sh) for sh in shapes]
    waist = lambda sh: np.ptp(body.feet(sh)[body.is_torso & (np.abs(body.P0[:, 1] - sk.hip_height - 0.45) < 0.08), 0])
    bulk_girth = float(waist(hu.shape(heavy=1.0)) / waist(verts))             # how much thicker the heavy shape is
    L = lambda a, b: float(np.linalg.norm(sk.bindP[sk.idx[a]] - sk.bindP[sk.idx[b]]))
    arm = L("LeftArm", "LeftForeArm") + L("LeftForeArm", "LeftHand") + 0.62         # shoulder to fingertip
    span = 2 * float(sk.bindP[sk.idx["LeftArm"]][0]) + 2 * arm                     # with the arms straight out
    BASE = ("skin", "jersey", "shorts", "kit")
    extras = {"version": 4, "height": HEIGHT, "fps": FPS, "clips": {n: c[3] for n, c in clips.items()},
              "parts": [n for n in mesh.tri if n not in BASE], "detail": "full",
              "body": {"bulk_girth": round(bulk_girth, 3), "span": round(span, 3), "arm": round(arm, 3)}}
    # morph targets. bulk: MakeHuman's heaviest muscular build (the viewer sets it per player
    # from his weight; a negative weight slims him). face_a/b/c: each of its three face shapes
    # against the even mix the model has: the head only, stored sparsely.
    v0 = mesh.arrays()[0]
    on_head = np.clip(np.array([sum(w for j, w in zip(js, ws) if KEEP[j] in ("Head", "Neck1")) for js, ws in zip(mesh.j, mesh.w)]), 0, 1)
    morphs = [("bulk", heavy.arrays()[0] - v0, False)]
    morphs += [("face_" + "abc"[k], (f.arrays()[0] - v0) * on_head[:, None], True) for k, f in enumerate(faces)]
    size = write_glb(Path(args.out), sk, mesh, clips, extras, morphs)
    # the light model: the same player with about 40% of the triangles and only the bulk shape
    lbody = Body(hu, sk, verts, LIGHT_SKIN_TRIS)
    lmesh, lheavy = lighten([build_model(sk, lbody, verts), build_model(sk, lbody, hu.shape(heavy=1.0))], LIGHT_KEEP)
    lsize = write_glb(Path(args.out).with_name(OUT_LIGHT.name), sk, lmesh, clips, dict(extras, detail="light"),
                      [("bulk", lheavy.arrays()[0] - lmesh.arrays()[0], False)])
    print(f"{OUT_LIGHT.name}: {lsize / 1024:.0f} KB, {len(lmesh.v)} vertices, {sum(len(t) for t in lmesh.tri.values())} triangles")
    tris = sum(len(t) for t in mesh.tri.values())
    print(f"{args.out}: {size / 1024:.0f} KB, {len(mesh.v)} vertices, {tris} triangles, {len(KEEP)} joints, {len(clips)} clips")
    for n, c in clips.items():
        m = c[3]
        print(f"  {n:14s} {m['duration']:5.2f}s {m['src']:7s} " + " ".join(f"{k}={m[k]}" for k in ("speed", "side", "marks", "reach", "dribble") if k in m))


if __name__ == "__main__":
    sys.exit(main())
