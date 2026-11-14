"""The match viewer page: 2D / 3D toggle, the bundled 3D library and the animated players."""
import base64
import json
import random
import re
import struct
import unittest

from hoopsim import generate_league
from hoopsim.spatial.export import (PLAYER_ASSETS, PLAYER_ASSETS_PLACEHOLDER, PLAYER_ASSETS_URL, TEMPLATE,
                                    THREE_JS, THREE_PLACEHOLDER, THREE_VERSION, build_html)
from hoopsim.spatial import HalfCourtGame
from hoopsim.spatial.sim import ACTIONS

LICENSE_LINE = "Copyright 2010-2023 Three.js Authors"


class ViewerPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        league = generate_league(seed=5)
        g = HalfCourtGame(league, 1, 2, random.Random(5))
        g.play(3)
        cls.data = g.to_dict()

    def test_toggle_and_library_once(self):
        page = build_html(self.data)
        for needle in ('id="viewSeg"', 'id="v2d"', 'id="v3d"', 'id="court3d"', 'id="camSeg"'):
            self.assertEqual(page.count(needle), 1, needle)
        self.assertNotIn(THREE_PLACEHOLDER, page)
        self.assertEqual(page.count(LICENSE_LINE), 1)              # inlined exactly once, licence kept
        self.assertEqual(page.count('id="three-src"'), 1)
        self.assertIn(f'const t="{THREE_VERSION.split(".")[1]}"', page)
        # inert until 3D is switched on: carried as text, not a script the browser runs
        self.assertIn('<script type="text/plain" id="three-src">', page)
        between = page.split('<script type="text/plain" id="three-src">', 1)[1].split("</script>", 1)[0]
        self.assertGreater(len(between), 500_000)
        self.assertNotIn("<!--", between)

    def test_size_impact(self):
        with3d = build_html(self.data)
        without = build_html(self.data, three=False)
        extra = len(with3d) - len(without)
        self.assertAlmostEqual(extra, THREE_JS.stat().st_size, delta=1000)
        self.assertNotIn(LICENSE_LINE, without)
        self.assertIn('id="v3d"', without)                        # the page hides the toggle itself

    def test_arena_and_quality_control(self):
        page = build_html(self.data)
        # quality control: Full / Light / Off, shown with the cameras in 3D, remembered safely
        for needle in ('id="arenaSeg"', 'id="arenaFull"', 'id="arenaLight"', 'id="arenaOff"'):
            self.assertEqual(page.count(needle), 1, needle)
        self.assertEqual(page.count('type="radio" name="arena"'), 3)
        self.assertIn('localStorage.getItem("hoopsim-arena")', page)
        for line in page.splitlines():
            if 'localStorage' in line and 'hoopsim-arena' in line:
                self.assertIn("try", line)                         # storage can throw
        # the arena is built lazily from the 3D view only, never by the 2D path
        self.assertEqual(page.count("function createArena("), 1)
        self.assertEqual(page.count("createArena(THREE"), 2)       # definition + one lazy call in setArena
        self.assertIn('if (lv !== "off" && !arena) arena = createArena(', page)
        # cheap crowd: instanced, animated in the shader, no shadows from the stands
        arena = page.split("function createArena(", 1)[1].split("function createView3D(", 1)[0]
        self.assertIn("InstancedMesh", arena)
        self.assertIn("onBeforeCompile", arena)
        self.assertIn("reduceMotion", arena)
        self.assertNotIn("castShadow", arena)
        self.assertNotIn("data:image", arena)                      # procedural: no embedded images
        self.assertEqual(page.count(LICENSE_LINE), 1)              # three.js still inlined exactly once

    def test_arena_is_small(self):
        from hoopsim.spatial.export import TEMPLATE
        src = TEMPLATE.read_text(encoding="utf-8")
        arena = src.split("function createArena(", 1)[1].split("function createView3D(", 1)[0]
        self.assertLess(len(arena.encode("utf-8")), 40_000)

    def test_cameras_labels_and_trail(self):
        page = build_html(self.data)
        # four camera presets, Broadcast the default; Numbers / Names / Off labels, Numbers default
        for needle in ('id="camBroadcast" value="broadcast" checked', 'id="camBaseline"', 'id="camFollow"',
                       'id="camOverhead"', 'id="labelSeg"', 'id="lblNum" value="numbers" checked',
                       'id="lblName"', 'id="lblOff"'):
            self.assertEqual(page.count(needle), 1, needle)
        self.assertEqual(page.count('type="radio" name="cam"'), 4)
        self.assertEqual(page.count('type="radio" name="labels"'), 3)
        self.assertIn('const CAMS = ["broadcast", "baseline", "follow", "overhead"]', page)
        # remembered like the other toggles, and storage can throw
        for key in ("hoopsim-labels", "hoopsim-cam"):
            lines = [ln for ln in page.splitlines() if "localStorage" in ln and key in ln]
            self.assertTrue(lines, key)
            for ln in lines:
                self.assertIn("try", ln)
        # the shot trail and the per-player label textures live in the 3D view only
        view = page.split("function createView3D(", 1)[1]
        self.assertIn("function flights(", view)
        self.assertIn("function updateTrail(", view)
        self.assertIn("function labelTexture(pl, names, holder)", view)
        self.assertNotIn("function updateTrail(", page.split("function createView3D(", 1)[0])
        # hidden segments really hide (the .seg rule would otherwise beat [hidden])
        self.assertIn(".seg[hidden]", page)

    def test_no_diagnostics_global_without_debug(self):
        page = build_html(self.data)
        self.assertEqual(page.count("window.hoopsim3D ="), 1)
        before = page.split("window.hoopsim3D =", 1)[0].rsplit("\n", 3)
        self.assertIn("if (DEBUG)", "\n".join(before[-2:]))
        self.assertIn('new URLSearchParams(location.search).has("debug")', page)

    def test_live_page_still_renders(self):
        page = build_html({"live_client": {"user_side": "home", "game_id": 1}, "live": True, "fullgame": True,
                           "home": self.data["home"], "away": self.data["away"], "possessions": []})
        self.assertIn('"live_client":{"user_side":"home"', page)
        self.assertEqual(page.count(LICENSE_LINE), 1)
        self.assertTrue(page.startswith("<!doctype html>"))


def player_block(page):
    """What the page is told about the animated players (the #player-assets block)."""
    return page.split('<script type="text/plain" id="player-assets">', 1)[1].split("</script>", 1)[0]


def read_glb(path):
    raw = path.read_bytes()
    magic, version, total = struct.unpack("<4sII", raw[:12])
    size, kind = struct.unpack("<I4s", raw[12:20])
    return magic, version, total, len(raw), json.loads(raw[20:20 + size]), kind


class AnimatedPlayersTests(unittest.TestCase):
    """The rigged model and its clips: a control in the page, files beside it, simple bodies
    whenever they are not there."""

    @classmethod
    def setUpClass(cls):
        league = generate_league(seed=5)
        g = HalfCourtGame(league, 1, 2, random.Random(5))
        g.play(3)
        cls.data = g.to_dict()
        cls.template = TEMPLATE.read_text(encoding="utf-8")

    def test_control_remembered_and_shortcut(self):
        page = build_html(self.data)
        for needle in ('id="bodySeg"', 'id="bodyAnim" value="animated" checked', 'id="bodyLight" value="light"',
                       'id="bodySimple" value="simple"'):
            self.assertEqual(page.count(needle), 1, needle)
        self.assertEqual(page.count('type="radio" name="bodies"'), 3)   # Animated | Light | Simple
        self.assertIn('<div class="seg" id="bodySeg" role="radiogroup" aria-label="Players" hidden>', page)
        lines = [ln for ln in page.splitlines() if "localStorage" in ln and "hoopsim-bodies" in ln]
        self.assertEqual(len(lines), 2)                            # read once, written once (by hand)
        for ln in lines:
            self.assertIn("try", ln)                               # storage can throw
        self.assertIn('else if (k === "b" && BODY_CFG) {', page)    # B steps through them, only when there is a choice
        self.assertIn('rememberFallback("light");', page)          # a step down made by itself is remembered too
        # the control only shows in 3D, and only when the page was given the files
        self.assertIn('$("bodySeg").hidden = mode !== "3d" || !BODY_CFG;', page)

    def test_exported_page_keeps_simple_bodies(self):
        page = build_html(self.data)
        self.assertNotIn(PLAYER_ASSETS_PLACEHOLDER, page)
        self.assertEqual(player_block(page).strip(), "")           # nothing to load: simple bodies, no control
        for needle in ("class GLTFLoader", "function retargetClip", "glTF"):
            self.assertNotIn(needle, page)                         # neither the loader nor the model is inlined
        self.assertEqual(len(page), len(build_html(self.data, animation=None)))
        # a 2D-only page says nothing about players either
        self.assertEqual(player_block(build_html(self.data, three=False, animation="embed")).strip(), "")

    def test_web_app_points_at_separate_files(self):
        plain = build_html(self.data)
        page = build_html(self.data, animation=PLAYER_ASSETS_URL)
        self.assertEqual(json.loads(player_block(page)), {"url": "/viewer-assets/"})
        self.assertLess(len(page) - len(plain), 100)               # an address, not the files
        self.assertNotIn("class GLTFLoader", page)
        self.assertEqual(json.loads(player_block(build_html(self.data, animation="/x")))["url"], "/x/")
        # fetched from the 3D switch only: no load at start-up, none from the 2D path
        self.assertEqual(page.count("loadPlayerRig(threeUrl"), 2)    # definition + the one call, in loadBodies
        self.assertEqual(page.count("loadBodies();"), 2)           # on first showing 3D, and on picking a level
        self.assertIn('if (mode === "3d" && BODY_CFG && !wantsSimple()) loadBodies();', page)
        # only the model the level needs is fetched, and the loader modules once
        self.assertIn('const RIG_FILES = { full: "player.glb", light: "player_light.glb" };', page)
        self.assertIn("r.data = await loadPlayerRig(threeUrl, RIG_FILES[lv]);", page)
        self.assertEqual(page.count("if (!rigModules) rigModules = (async () => {"), 1)

    def test_embed_option_carries_the_files(self):
        plain = build_html(self.data)
        page = build_html(self.data, animation="embed")
        files = json.loads(player_block(page))["embed"]
        self.assertEqual(sorted(files), sorted(n for n in PLAYER_ASSETS if n != "player_light.glb"))   # the full model only
        self.assertIn('typeof BODY_CFG.embed["player_light.glb"] === "string"', page)                   # so no Light there
        self.assertEqual(base64.b64decode(files["player.glb"]), PLAYER_ASSETS["player.glb"].read_bytes())
        self.assertEqual(files["GLTFLoader.js"], PLAYER_ASSETS["GLTFLoader.js"].read_text(encoding="utf-8"))
        self.assertNotIn("</script", player_block(page))
        self.assertLess(len(page) - len(plain), 1_900_000)
        self.assertEqual(page.count('id="three-src"'), 1)

    def test_asset_budget_and_model(self):
        total = sum(p.stat().st_size for p in PLAYER_ASSETS.values())
        self.assertLess(total, 5_000_000)                          # the whole set: loader, utils, model + clips
        magic, version, declared, size, doc, kind = read_glb(PLAYER_ASSETS["player.glb"])
        self.assertEqual((magic, version, kind), (b"glTF", 2, b"JSON"))
        self.assertEqual(declared, size)
        self.assertLess(size, 2_000_000)
        self.assertEqual(len(doc["skins"]), 1)                     # one shared rigged model
        self.assertGreaterEqual(len(doc["skins"][0]["joints"]), 20)
        names = [m["name"] for m in doc["materials"]]
        self.assertEqual(names[:4], ["skin", "jersey", "shorts", "kit"])
        self.assertEqual(names[4:], doc["extras"]["hoopsim"]["parts"])   # hair styles, beard, gear: shown per player
        self.assertIn("mocap.cs.cmu.edu", doc["asset"]["copyright"])
        self.assertIn("MakeHuman", doc["asset"]["copyright"])       # the body
        clips = doc["extras"]["hoopsim"]["clips"]
        self.assertEqual(sorted(a["name"] for a in doc["animations"]), sorted(clips))
        for name in ("idle", "stance", "walk", "jog", "sprint", "backpedal", "slide", "slide_m", "dribble_idle",
                     "dribble_walk", "dribble_run", "jumper", "free_throw", "layup", "floater", "pass", "lob_pass",
                     "catch", "contest", "block", "rebound", "dunk", "steal", "plant", "plant_m", "fall", "screen",
                     "triple", "drive", "burst"):
            self.assertIn(name, clips)
            self.assertGreater(clips[name]["duration"], 0.3, name)
            self.assertTrue(clips[name]["src"], name)              # every clip names its mocap source
        for name in ("walk", "jog", "sprint", "backpedal", "slide", "dribble_walk", "dribble_run"):
            self.assertGreater(clips[name]["speed"], 2, name)      # run cycles are scaled to the player's speed
        self.assertLess(clips["walk"]["speed"], clips["jog"]["speed"])
        self.assertLess(clips["jog"]["speed"], clips["sprint"]["speed"])
        self.assertEqual(clips["slide"]["side"], -clips["slide_m"]["side"])
        for name in ("jumper", "free_throw", "layup", "floater", "rebound", "block", "dunk"):
            m = clips[name]["marks"]
            self.assertLess(m["gather"], m["release"], name)
            self.assertLess(m["release"], m["land"], name)
        for name in ("dribble_idle", "dribble_walk", "dribble_run"):
            self.assertTrue(clips[name]["dribble"]["bounces"], name)

    def test_builds_per_player(self):
        """Height, weight and wingspan shape each player: one mesh, a "bulk" shape and arm length."""
        doc = read_glb(PLAYER_ASSETS["player.glb"])[4]
        mesh = doc["meshes"][0]
        self.assertEqual(mesh["extras"]["targetNames"], ["bulk", "face_a", "face_b", "face_c"])
        self.assertEqual(mesh["weights"], [0.0] * 4)               # as designed until the viewer sets them
        for prim in mesh["primitives"]:
            for k, target in enumerate(prim["targets"]):
                acc = doc["accessors"][target["POSITION"]]
                self.assertEqual("sparse" in acc, k > 0)           # faces move the head only: stored sparsely
                if k > 0:
                    self.assertLess(acc["sparse"]["count"], acc["count"] // 2)
        for prim in mesh["primitives"]:
            self.assertEqual(list(prim["targets"][0]), ["POSITION"])
        body = doc["extras"]["hoopsim"]["body"]
        self.assertGreater(body["bulk_girth"], 1.05)
        self.assertGreater(body["span"], 2 * body["arm"])
        src = self.template
        self.assertEqual(src.count("function physique(pl)"), 1)
        # real numbers when the page has them, the position's typical build otherwise
        self.assertIn("const ht = pl.ht > 0 ? pl.ht : b[0]", src)
        self.assertIn("const wt = pl.wt > 0 ? pl.wt : b[1]", src)
        self.assertIn("const ws = pl.ws > 0 ? pl.ws : ht", src)
        self.assertIn('if (body && ph) set("bulk", Math.max(-0.8, Math.min(1, (ph.girth - 1) / (body.bulk_girth - 1))));', src)
        self.assertIn('["face_a", "face_b", "face_c"].forEach((name, k) => set(name,', src)
        self.assertIn('bones[sd + "Arm"].scale.setScalar(arm);', src)
        self.assertIn("function skinOf(pl)", src)                  # a stored skin tone wins over the name's
        # exported pages carry no builds: nothing to look players up in
        self.assertNotIn('"ht":', build_html(self.data))

    def test_jersey_wraps_the_body(self):
        """The jersey is a layer of the body, textured right round: its u runs past 1 at the seam."""
        raw = PLAYER_ASSETS["player.glb"].read_bytes()
        doc = read_glb(PLAYER_ASSETS["player.glb"])[4]
        size = struct.unpack("<I", raw[12:16])[0]
        blob = raw[20 + size + 8:]
        prim = next(p for p in doc["meshes"][0]["primitives"] if doc["materials"][p["material"]]["name"] == "jersey")
        def read(i, fmt, width):
            acc = doc["accessors"][i]; view = doc["bufferViews"][acc["bufferView"]]
            start = view["byteOffset"] + acc.get("byteOffset", 0)
            return struct.unpack(f"<{acc['count'] * width}{fmt}", blob[start:start + acc["count"] * width * struct.calcsize(fmt)])
        idx = sorted(set(read(prim["indices"], "H", 1)))
        self.assertGreater(len(idx), 600)                          # the body's own detail, not a tube of rings
        # its outline is cut along a smooth curve: points on it lie between the body's vertices,
        # so they carry blended skin weights that no body vertex has
        builder = (TEMPLATE.parent.parent.parent / "tools" / "build_player_assets.py").read_text(encoding="utf-8")
        self.assertIn("self.shirt_patch = self.cut(f)", builder)
        self.assertIn("self.shorts_patch = self.cut(self.shorts_f)", builder)   # the shorts are cut the same way
        self.assertIn("if (f[a] > 0) != (f[b] > 0): poly.append(pv(a, b))", builder)
        uv = read(prim["attributes"]["TEXCOORD_0"], "f", 2)
        us = [uv[2 * i] for i in idx]; vs = [uv[2 * i + 1] for i in idx]
        self.assertLess(min(us), 0.05)
        self.assertGreater(max(us), 1.0)                           # wraps: the viewer's texture repeats
        self.assertGreaterEqual(min(vs), -0.05)
        self.assertLessEqual(max(vs), 1.0001)
        self.assertIn("tx.wrapS = THREE.RepeatWrapping;", self.template)

    def test_ball_is_true_size(self):
        src = self.template
        radius = float(src.split("const BALL_R = ", 1)[1].split(";", 1)[0])
        self.assertAlmostEqual(radius * 2 * 12, 9.4, delta=0.2)     # inches across: a size 7 ball
        self.assertIn("new THREE.SphereGeometry(BALL_R, 20, 14)", src)
        view = src.split("function createView3D(", 1)[1]
        self.assertNotIn("Math.max(0.6, ", view.split("// ---- camera", 1)[0].split("// the ball (true size)", 1)[1])
        self.assertIn("bY = Math.max(BALL_R, bY);", view)          # it rests on the floor at its own radius

    def _look_of(self):
        """lookOf and what it needs, lifted out of the viewer to run under node (or None)."""
        import shutil
        if not shutil.which("node"):
            return None
        src = self.template
        hash_fn = "const hash = " + src.split("const hash = ", 1)[1].split("\n", 1)[0]
        body = src.split("  const HAIR_STYLES", 1)[1].split("  // What each action cue plays", 1)[0]
        return hash_fn + "\nconst HAIR_STYLES" + body

    def test_faces_lean_weakly_on_skin_tone(self):
        """Every face shape is common for every skin tone, and the blend varies by player."""
        import subprocess
        src = self.template
        lean = json.loads(re.sub(r"(\w+):", r'"\1":', src.split("const FACE_LEAN = ", 1)[1].split(";", 1)[0]))
        self.assertEqual(set(lean), {"dark", "mid", "light"})
        for odds in lean.values():
            self.assertEqual(sum(odds), 100)
            self.assertGreaterEqual(min(odds), 28)                 # no shape rare for any tone
            self.assertLessEqual(max(odds), 42)                    # a nudge, not a rule
        js = self._look_of()
        if js is None:
            self.skipTest("node not installed")
        js += """
const out = {};
for (let tone = 0; tone < 7; tone++) {
  const lead = [0, 0, 0], mixes = new Set(); let second = 0, n = 600;
  for (let i = 0; i < n; i++) {
    const f = lookOf({ name: "Test Player " + tone + "-" + i * 13 }, tone).face;
    if (!(f.length === 3 && f.every((v) => v >= 0 && v <= 1) && f[0] + f[1] + f[2] <= 1.0001)) throw new Error("bad face " + f);
    lead[f.indexOf(Math.max(...f))]++; mixes.add(f.map((v) => v.toFixed(1)).join(","));
    if ([...f].sort((a, b) => b - a)[1] > 0.15) second++;
  }
  out[tone] = { lead: lead.map((c) => c / n), mixes: mixes.size, second: second / n };
}
const a = lookOf({ name: "Same Name" }, 3).face, b = lookOf({ name: "Same Name" }, 3).face;
const stored = lookOf({ name: "Same Name", look: { face: [0, 1, 0] } }, 3).face;
const junk = ["x", [1, 2, 3], [1, 0], [0.5, "0", 0], null].map((v) => lookOf({ name: "Same Name", look: { face: v } }, 3).face);
console.log(JSON.stringify({ out, same: JSON.stringify(a) === JSON.stringify(b), stored, junk, a }));
"""
        res = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True, timeout=60).stdout)
        for tone, d in res["out"].items():
            for share in d["lead"]:
                self.assertGreater(share, 0.2, (tone, d))          # each shape leads for a real share of players
                self.assertLess(share, 0.5, (tone, d))
            self.assertGreater(d["mixes"], 40, (tone, d))          # many blends, not three types
            self.assertGreater(d["second"], 0.3, (tone, d))        # a second shape is often mixed in
        self.assertTrue(res["same"])                               # the same every time, from his name
        self.assertEqual(res["stored"], [0, 1, 0])                 # a face set in the editor wins
        for face in res["junk"]:
            self.assertEqual(face, res["a"])                       # anything else: back to the name's

    def test_stored_face_and_the_light_model(self):
        """The light model has no face shapes: a stored face is simply not applied there."""
        light = read_glb(PLAYER_ASSETS["player_light.glb"])[4]
        self.assertEqual(light["meshes"][0]["extras"]["targetNames"], ["bulk"])
        src = self.template
        self.assertIn("const shapes = o.morphTargetDictionary, set = (name, v) => { if (shapes && shapes[name] !== undefined)", src)
        self.assertIn("face: validFace(L.face) ? L.face.map(Number) :", src)
        from hoopsim.editor import LOOK_FACES, LOOK_KEYS
        self.assertIn("face", LOOK_KEYS)
        self.assertEqual(LOOK_FACES, len(re.findall(r"\"face_[abc]\"", src.split("if (Array.isArray(lk.face))", 1)[1].split("\n", 1)[0])))

    def test_clip_speeds_and_footwork(self):
        """Each moving loop carries its natural speed and stride, measured in the build; the
        viewer plays the cycle so the stride covers the ground, and holds the feet that are down."""
        for name in ("player.glb", "player_light.glb"):
            meta = read_glb(PLAYER_ASSETS[name])[4]["extras"]["hoopsim"]
            self.assertAlmostEqual(meta["body"]["ankle"], 0.33, delta=0.08)
            moving = {n: c for n, c in meta["clips"].items() if c.get("speed")}
            self.assertEqual(set(moving), {"walk", "jog", "sprint", "backpedal", "slide", "slide_m", "dribble_walk", "dribble_run"})
            for n, c in moving.items():
                self.assertAlmostEqual(c["stride"], c["speed"] * c["duration"], delta=0.01, msg=n)
                self.assertTrue(2 < c["speed"] < 25 and 3 < c["stride"] < 15, (n, c))
            for n in ("idle", "stance", "dribble_idle", "screen"):
                self.assertFalse(meta["clips"][n].get("speed"), n)   # standing clips add no stride
                self.assertNotIn("stride", meta["clips"][n])
        src = self.template
        # the cycle's rate comes from the blend's stride and the replay's speed, not a clamp per clip
        self.assertIn("stride += r.w[name] * CL[name].speed * CL[name].duration;", src)
        self.assertIn("Math.min(sp * sum / stride, STEP_MAX * mov / cycle)", src)
        self.assertNotIn("Math.max(0.55, Math.min(1.5, sp / CL[name].speed))", src)
        # planted feet: after he is placed and turned, for the animated and light models alike
        view = src.split("function createView3D(", 1)[1]
        self.assertIn("if (useRig) anim.plantFeet(fg, dta, fg.fresh);", view)
        self.assertLess(view.index("fg.body.rotation.y = fg.heading;"), view.index("anim.plantFeet(fg, dta, fg.fresh);"))
        self.assertEqual(src.count("function plantFeet(fg, dta, fresh) {"), 1)
        # bones the viewer turns are brought back to the clip's pose first (the mixer skips unchanged values)
        self.assertIn("for (const b of r.adjusted) clipPose(b);", src)

    def test_fluid_movement(self):
        """Animated players follow the replay as bodies with limits, lean into how they move,
        and turn chest and head toward the ball; simple bodies keep the old exact follow."""
        src = self.template
        limits = dict(re.findall(r"(MOVE_\w+|TURN_MAX) = ([\d.]+)", src.split("const MOVE_ACCEL", 1)[1].split(";", 1)[0].join(["MOVE_ACCEL", ""])))
        self.assertTrue(15 <= float(limits["MOVE_ACCEL"]) <= 30)       # ft/s2: quick, but a person's
        self.assertGreater(float(limits["MOVE_BRAKE"]), float(limits["MOVE_ACCEL"]))
        self.assertLessEqual(float(limits["MOVE_LAG"]), 1.5)           # never far from where the replay has him
        self.assertLess(float(limits["MOVE_LAG_TIGHT"]), 0.6)          # closer still around shots, passes, catches
        view = src.split("function createView3D(", 1)[1]
        self.assertIn("} else if (useRig && fluid) {", view)
        self.assertIn("const lim = fg.rig.busy > 0.2 || q === st.holder ? MOVE_LAG_TIGHT : MOVE_LAG;", view)
        self.assertIn("if (live && dtg > 0) {", view)                  # paused: he stays where he is drawn
        self.assertIn("const off = Math.hypot(tx - fg.x, tz - fg.z), lim = 0.6;", view)   # simple bodies: as before
        # his clips and facing follow the drawn movement, so strides still match the ground
        self.assertIn("vX = fg.vx; vZ = fg.vz; sp = Math.hypot(vX, vZ);", view)
        # lean and look happen before the feet are planted, and ease off during a one-shot clip
        self.assertLess(view.index("anim.carry(fg,"), view.index("anim.plantFeet(fg, dta, fg.fresh);"))
        self.assertIn("free = 1 - 0.8 * Math.min(1, r.busy || 0)", src)
        self.assertIn('for (const [name, share] of [["Spine1", LOOK_CHEST], ["Head", LOOK_HEAD]])', src)
        self.assertIn('adjusted: ["Spine1", "Head", "LeftArm"', src)   # brought back to the clip's pose each frame
        self.assertIn("fluid: (on) => { fluid = !!on; dirty = 3; },", src)

    def test_offence(self):
        """Offensive stances and clips, dribbling with either hand, shots out of the run."""
        src = self.template
        self.assertIn('drive: ["drive", "", 0, { w: 0.75, under: true }],', src)
        self.assertIn('cut: ["burst", "", 0, { w: 0.6, under: true }],', src)
        self.assertIn("const rank = (c) => (c.under ? 0 : c.slam ? 2 : 1);", src)   # a shot right after the drive wins
        self.assertIn("tgt.triple = 1 - go;", src)                     # standing with the ball: triple threat
        self.assertIn("tgt.idle = (1 - go) * (1 - READY); tgt.stance = (1 - go) * READY;", src)
        # left-handed dribbles are made in the viewer by mirroring: no extra clips in the file
        clips = read_glb(PLAYER_ASSETS["player.glb"])[4]["extras"]["hoopsim"]["clips"]
        self.assertFalse([n for n in clips if n.startswith("dribble") and n.endswith("_m")])
        self.assertIn('for (const name of ["dribble_idle", "dribble_walk", "dribble_run", ...HANDED]) {', src)
        self.assertIn('CL[c.name].dribble = { ...CL[name].dribble, hand: CL[name].dribble.hand === "L" ? "R" : "L" };', src)
        # the hand away from the nearest defender, not changed more often than HAND_HOLD
        self.assertIn("const wantLeft = side < -0.8 ? true : side > 0.8 ? false : near >= 7 ? r.lefty : r.left;", src)
        self.assertIn("if (wantLeft !== r.left && (r.handT > HAND_HOLD || fresh))", src)
        self.assertIn("if (r.ballHand && r.ballHand !== m.hand) r.cross = HAND_CROSS;", src)   # the ball crosses over
        # a shot on the move is eased in up to its gather
        self.assertIn("if (v > 5) cue.fin = Math.max(0.2, Math.min(0.45, c.marks.gather * 0.9));", src)
        self.assertIn("const fin = smooth01(tl / Math.min(c.fin || 0.2, c.dur / 3));", src)

    def test_hand_to_ball(self):
        """A blocker's (and a contester's) hand goes to the ball: arm IK on top of the clip."""
        src = self.template
        view = src[src.index("function createView3D"):]
        self.assertIn('block: ["block", "release", 0.1, { face: "other", reach: "ball", hand: 1, offArm: true }],', src)
        self.assertIn('contest: ["contest", "release", 0.2, { face: "other", hand: 0.7 }],', src)
        self.assertEqual(src.count("function reachBall(fg, bx, by, bz) {"), 1)
        # only around the top of the jump, and never for a ball behind him or far out of reach
        self.assertIn("smooth01((t + REACH_IN) / 0.22) * smooth01((REACH_OUT - t) / 0.22);", src)
        self.assertIn("const w = r.reachW * smooth01((ahead + 0.6) / 0.5) * (1 - smooth01((dist - 1.5 * len) / len)) * smooth01((v4.y + 0.2 * len) / (0.5 * len));", src)
        # once the ball is where it will be drawn
        self.assertLess(view.index("ball.position.set(bX, bY, bZ);"), view.index("anim.reachBall(f, bX, bY, bZ);"))
        self.assertIn('"LeftForeArm", "RightForeArm",', src)           # brought back to the clip's pose each frame
        self.assertIn("reach: (on) => { handReach = !!on; dirty = 3; },", src)

    def test_dunk_to_the_rim(self):
        """A dunker's hand takes the ball to the rim, holds it, and he drifts within reach."""
        src = self.template
        view = src[src.index("function createView3D"):]
        self.assertEqual(src.count("function slamHand(fg, rimX, at) {"), 1)
        self.assertEqual(src.count("function armTo(fg, sd, T, w) {"), 1)     # the one arm solver, shared with the block
        self.assertIn("const SLAM_IN = 0.35, SLAM_HANG = 0.12, SLAM_NEAR = 1.5;", src)
        # before the ball is placed in his hand; the ball then rides under the palm
        self.assertLess(view.index("anim.slamHand(slamFig, rimX, st.at);"), view.index("if (after < 0) anim.ballInHand(slamFig, gripPos, dta);"))
        self.assertIn("else out.y += slam ? r.slamBall : BALL_R * 0.8;", src)
        self.assertIn("} else v4.set(rimX + dx * 0.72, 10.02, dz * 0.72);", src)   # the palm on the rim
        self.assertIn("m = Math.min(3, Math.max(0, d - SLAM_NEAR * r.k)) * w / d;", src)   # the drift, at most 3 ft
        self.assertIn("r.model.position.x = px; r.model.position.z = pz;", src)

    def test_hook_shot(self):
        """A hook: the one-hand shot's hop, side-on, with the arms posed by the viewer."""
        src = self.template
        view = src[src.index("function createView3D"):]
        self.assertIn('hook: ["floater", "release", 0, { face: "rim", yaw: -1.25, reach: 8.4, sweep: true }],', src)
        self.assertEqual(src.count("function hookArm(fg, rimX) {"), 1)
        # the shooting arm: from his side away from the rim, over the top by the release, then on
        self.assertIn("const phi = t < 0 ? -0.35 + 2.1 * Math.pow(u, 1.5) : Math.min(2.6, 1.75 + 3.5 * t);", src)
        self.assertIn("armTo(fg, guard, v4, w * 0.8);", src)               # the guard arm
        # before the ball is placed in his hand
        self.assertLess(view.index("anim.hookArm(f, rimX);"), view.index("anim.ballInHand(holderFig, gripPos, dta)"))

    def test_handedness(self):
        """Left- and right-handed players: from the name (about one in ten left), or set in the editor."""
        import subprocess
        src = self.template
        self.assertIn('const HANDED = ["jumper", "free_throw", "layup", "floater", "dunk"];', src)
        # a left-hander plays the mirrored clip, holds the ball in his left, dunks and hooks with it
        self.assertIn('const clip = r.lefty && HANDED.includes(c.clip) ? c.clip + "_m" : c.clip;', src)
        self.assertIn('const strong = r.lefty && (slam || (r.ballCue && HANDED.includes(r.ballCue.clip))) ? "Left" : "Right";', src)
        self.assertIn('const sd = r.lefty ? "Left" : "Right";', src)
        self.assertIn('const shoot = r.lefty ? "Left" : "Right", guard = r.lefty ? "Right" : "Left";', src)
        self.assertIn("yaw: r.lefty && c.sweep ? -c.yaw : c.yaw", src)
        # the mirrored shots are made in the viewer: none in the file
        clips = read_glb(PLAYER_ASSETS["player.glb"])[4]["extras"]["hoopsim"]["clips"]
        self.assertFalse([n for n in clips if n[:-2] in ("jumper", "free_throw", "layup", "floater", "dunk") and n.endswith("_m")])
        js = self._look_of()
        if js is None:
            self.skipTest("node not installed")
        js += """
let left = 0; const n = 4000;
for (let i = 0; i < n; i++) if (lookOf({ name: "Hand Test " + i * 7 }, i % 7).hand === "L") left++;
const set = ["L", "R", "x", null].map((v) => lookOf({ name: "Hand Test 3", look: { hand: v } }, 2).hand);
console.log(JSON.stringify({ share: left / n, set, same: lookOf({ name: "A B" }, 1).hand === lookOf({ name: "A B" }, 1).hand }));
"""
        res = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True, timeout=60).stdout)
        self.assertGreater(res["share"], 0.06)
        self.assertLess(res["share"], 0.14)
        self.assertEqual(res["set"][:2], ["L", "R"])                # the editor's choice wins
        self.assertIn(res["set"][2], ("L", "R"))                   # junk falls back to his name
        self.assertTrue(res["same"])

    def test_facing_from_the_replay(self):
        """The viewer faces players as the replay records, when it does; older replays as before."""
        src = self.template
        view = src[src.index("function createView3D"):]
        self.assertIn('fc = typeof p.facing === "string" && p.facing.length === fr.length * np ? p.facing : null;', src)
        self.assertIn("return (c < 58 ? c - 48 : c < 91 ? c - 55 : c - 61) * Math.PI / 30; };", src)   # sim.FACING_CODE, 6 degrees a step
        self.assertIn("q.face = (flip > 0 ? 0.5 : 1.5) * Math.PI - ", src)
        # a clip that turns him (a shooter to the rim, a passer to his man) still wins
        self.assertLess(view.index("if (q.face !== undefined && useFacing) want = q.face;"), view.index("const face = anim.update(fg, q, st, dtg, dta, fg.fresh);"))
        from hoopsim.spatial.sim import FACING_CODE
        code = lambda c: (ord(c) - 48 if ord(c) < 58 else ord(c) - 55 if ord(c) < 91 else ord(c) - 61)
        self.assertEqual([code(c) for c in FACING_CODE], list(range(62)))

    def test_shots_are_found_by_their_cue(self):
        """A close shot peaks barely over the rim now: the trail finds it by the engine's cue."""
        src = self.template
        self.assertIn('const SHOT_CUES = ["jumper_release", "layup", "floater", "hook", "fadeaway", "heave", "free_throw", "dunk", "alley_oop"];', src)
        self.assertIn("const high = cues.has(i) || cues.has(i - 1) || cues.has(i + 1) ? 0 : 110;", src)   # (older replays: over 11 ft, as before)

    def test_light_model(self):
        """The same player with far fewer triangles: same skeleton, clips and parts."""
        full, light = (read_glb(PLAYER_ASSETS[n])[4] for n in ("player.glb", "player_light.glb"))
        count = lambda d: sum(d["accessors"][p["indices"]]["count"] // 3 for p in d["meshes"][0]["primitives"])
        self.assertLess(count(light), count(full) * 0.5)
        self.assertEqual(light["extras"]["hoopsim"]["detail"], "light")
        self.assertEqual(full["extras"]["hoopsim"]["detail"], "full")
        self.assertEqual([n["name"] for n in light["nodes"]], [n["name"] for n in full["nodes"]])
        self.assertEqual(sorted(a["name"] for a in light["animations"]), sorted(a["name"] for a in full["animations"]))
        self.assertEqual({m["name"] for m in light["materials"]}, {m["name"] for m in full["materials"]})
        self.assertEqual(light["meshes"][0]["extras"]["targetNames"], ["bulk"])
        self.assertLess(PLAYER_ASSETS["player_light.glb"].stat().st_size, PLAYER_ASSETS["player.glb"].stat().st_size)
        src = self.template
        self.assertIn('const START_LEVEL = PHONE && HAS_LIGHT ? "light" : "full";', src)   # phones start on it
        self.assertIn("fg.body.remove(fg.rig.model);", src)        # players on the floor are rebuilt when the model changes

    def test_looks_per_player(self):
        """Hair, beard and gear are parts of the one model, switched on per player."""
        doc = read_glb(PLAYER_ASSETS["player.glb"])[4]
        parts = doc["extras"]["hoopsim"]["parts"]
        for name in ("hair_cap", "hair_fade", "hair_afro", "hair_flattop", "hair_locs", "hair_bun", "beard",
                     "headband", "sleeve_L", "sleeve_R", "wrist_L", "wrist_R", "leg_L", "leg_R", "shoes"):
            self.assertIn(name, parts)
        self.assertEqual(len(doc["meshes"][0]["primitives"]), 4 + len(parts))
        src = self.template
        self.assertEqual(src.count("function lookOf(pl, tone)"), 1)
        # every style the viewer can pick is made of hair parts the model has (bald: none)
        styles = re.findall(r'\["(\w+)", \d+\]', src.split("const HAIR_STYLES = [", 1)[1].split(";", 1)[0])
        made_of = json.loads(re.sub(r"(\w+):", r'"\1":', src.split("const HAIR_PARTS = ", 1)[1].split(";", 1)[0]))
        self.assertEqual(set(styles), set(made_of) | {"bald"})
        for pieces in made_of.values():
            for piece in pieces:
                self.assertIn("hair_" + piece, parts)
        # a look stored on the player wins; otherwise it comes from his name, the same every time
        self.assertIn('const L = pl.look && typeof pl.look === "object" ? pl.look : {};', src)
        self.assertIn('hair: has("hair") ? L.hair :', src)
        self.assertIn("} else o.visible = false;", src)            # a part the viewer doesn't know stays hidden
        self.assertNotIn("Math.random", src.split("function lookOf(pl, tone)", 1)[1].split("// What each action cue plays", 1)[0])

    def test_web_pages_carry_real_builds(self):
        try:
            from hoopsim.web.session import GameSession
        except ImportError:
            self.skipTest("web extras not installed")
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        session = GameSession(saves_dir=tmp.name)
        session.new_game(seed=3, num_teams=4)
        known = next(iter(session.league.players.values()))
        data = {"home": {"players": [{"num": known.jersey_number, "name": known.name, "pos": "PG", "ca": 100}]},
                "away": {"players": [{"num": 1, "name": "Nobody Atall", "pos": "C", "ca": 100}]}}
        out = session._viewer_builds(data)
        row = out["home"]["players"][0]
        self.assertEqual((row["ht"], row["wt"], row["ws"]),
                         (known.physique.height_cm, known.physique.weight_kg, known.physique.wingspan_cm))
        self.assertNotIn("ht", out["away"]["players"][0])          # not in the league: sized by position

    def test_player_ids_in_page_data(self):
        """viewer_data lists each player's league id (half-court and full games), and leaves
        what the legacy fingerprint hashes (the possessions) alone."""
        from hoopsim.spatial.export import viewer_data
        from hoopsim.spatial.fullgame import FullGame
        league = generate_league(seed=5)
        g = HalfCourtGame(league, 1, 2, random.Random(5))
        g.play(2)
        d = viewer_data(g)
        self.assertEqual(d["possessions"], g.to_dict()["possessions"])
        for side in ("home", "away"):
            rows = d[side]["players"]
            self.assertEqual(len(rows), 5)
            for row in rows:
                self.assertEqual(league.players[row["id"]].name, row["name"])
        fg = FullGame(league, 1, 2, random.Random(1), record=True)
        fg.run()
        for kw in ({}, {"period": 2}):
            d = viewer_data(fg, **kw)
            for side in ("home", "away"):
                ids = [row["id"] for row in d[side]["players"]]
                self.assertEqual(ids, [s.p.id for s in fg.sides[side].roster])
        self.assertNotIn('"id":', json.dumps(g.to_dict()["home"]))   # to_dict itself unchanged

    def test_web_builds_by_id_with_name_fallback(self):
        try:
            from hoopsim.web.session import GameSession
        except ImportError:
            self.skipTest("web extras not installed")
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        session = GameSession(saves_dir=tmp.name)
        session.new_game(seed=3, num_teams=4)
        a, b = list(session.league.players.values())[:2]
        b.first_name, b.last_name = a.first_name, a.last_name      # two players, one name
        b.physique.height_cm = a.physique.height_cm + 7
        a.look = {"hair": "bald"}
        # by id: the right one, whatever the name and number in the replay say
        data = {"home": {"players": [{"id": b.id, "num": a.jersey_number, "name": "Old Name", "pos": "PG", "ca": 90}]},
                "away": {"players": [{"id": 999999, "num": 1, "name": a.name, "pos": "C", "ca": 90}]}}
        out = session._viewer_builds(data)
        self.assertEqual(out["home"]["players"][0]["ht"], b.physique.height_cm)
        self.assertNotIn("look", out["home"]["players"][0])
        self.assertNotIn("ht", out["away"]["players"][0])        # an id not in the league: no guessing by name
        # an old replay (no ids): name, then number
        data = {"home": {"players": [{"num": a.jersey_number, "name": a.name, "pos": "PG", "ca": 90}]}, "away": {}}
        row = session._viewer_builds(data)["home"]["players"][0]
        self.assertEqual(row["ht"], a.physique.height_cm)
        self.assertEqual(row["look"], {"hair": "bald"})

    def test_auto_fallback_is_not_permanent(self):
        src = self.template
        # an automatic drop to simple bodies is kept for a week, on the same renderer and screen
        self.assertIn('const AUTO_SIMPLE_KEY = "hoopsim-bodies-auto", AUTO_SIMPLE_DAYS = 7;', src)
        self.assertIn("age < AUTO_SIMPLE_DAYS * 864e5 && rec.gl === sig.gl && rec.screen === sig.screen", src)
        self.assertIn("UNMASKED_RENDERER_WEBGL", src)
        fall = src.split("function fallBackToSimple() {", 1)[1].split("\n  }\n", 1)[0]
        self.assertNotIn('"hoopsim-bodies"', fall)              # the hand-made choice is not touched
        self.assertNotIn("setBodyPref", fall)
        # a choice by hand is kept for good, and replaces the automatic one
        pref = src.split("function setBodyPref(v) {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn('localStorage.setItem("hoopsim-bodies", v); localStorage.removeItem(AUTO_SIMPLE_KEY);', pref)
        # every storage access for the players' setting can throw
        lines = [ln for ln in src.splitlines() if "localStorage" in ln and ("hoopsim-bodies" in ln or "AUTO_SIMPLE_KEY" in ln)]
        self.assertEqual(len(lines), 5)                          # read, set by hand, read/drop/write the fallback
        for ln in lines:
            self.assertIn("try", ln, ln)

    def test_licences_recorded(self):
        notes = (PLAYER_ASSETS["player.glb"].parent / "LICENSES.md").read_text(encoding="utf-8")
        for needle in ("mocap.cs.cmu.edu", "NSF EIA-0196217", "GLTFLoader.js", "SkeletonUtils.js",
                       "BufferGeometryUtils.js", "MIT", "Mixamo", "player.glb", "MakeHuman", "CC0"):
            self.assertIn(needle, notes)
        clips = read_glb(PLAYER_ASSETS["player.glb"])[4]["extras"]["hoopsim"]["clips"]
        for src in {c["src"] for c in clips.values()}:
            self.assertIn(src, notes)                              # every mocap trial used is listed
        for name in ("GLTFLoader.js", "SkeletonUtils.js", "BufferGeometryUtils.js"):
            src = PLAYER_ASSETS[name].read_text(encoding="utf-8")
            self.assertIn("from 'three';", src)                    # unmodified r160 modules: the page rewrites the import
        self.assertIn("class GLTFLoader extends Loader", PLAYER_ASSETS["GLTFLoader.js"].read_text(encoding="utf-8"))

    def test_every_action_has_an_animation(self):
        """Each cue the engine records is mapped to a clip (or knowingly left to the run cycles)."""
        src = self.template
        cue_map = src.split("const CUE_CLIPS = {", 1)[1].split("};", 1)[0]
        mapped = {m.group(1): m.group(2) for m in re.finditer(r'^\s*(\w+): \["(\w+)"', cue_map, re.M)}
        poses = dict(re.findall(r'(\w+): "(\w+)"', src.split("const POSE_CUES = {", 1)[1].split("}", 1)[0]))
        from_frames = set()                                        # (running and dribbling come off the frames too)
        self.assertEqual(set(ACTIONS), set(mapped) | set(poses) | from_frames)
        clips = read_glb(PLAYER_ASSETS["player.glb"])[4]["extras"]["hoopsim"]["clips"]
        for action, clip in {**mapped, **poses}.items():
            self.assertIn(clip, clips, action)

    def test_fallbacks(self):
        src = self.template
        # the animator lives outside the arena and the 2D path, and is built only from a loaded rig
        self.assertEqual(src.count("function createAnimator("), 1)
        self.assertLess(src.index("function createAnimator("), src.index("function createArena("))
        self.assertEqual(src.count("createAnimator(THREE"), 2)     # definition + setRig
        # simple bodies stay: shown whenever the rig is not in use
        view = src.split("function createView3D(", 1)[1]
        self.assertIn("fg.simple.visible = !useRig;", view)
        self.assertIn('bodyMode = mode === "animated" && anim ? "animated" : "simple";', view)
        # a failed load: say so, keep simple bodies, disable the choice
        self.assertIn('setText("viewMsg", "Animated players unavailable: using simple bodies.");', src)
        self.assertIn('$(lv === "light" ? "bodyLight" : "bodyAnim").disabled = true;', src)
        # a device that can't keep up drops to simple bodies (unless Animated was picked by hand)
        self.assertIn("Animated players were slow here: switched to simple bodies.", src)
        self.assertIn("if (bodyAuto && slowT >= 3) {", view)        # judged over three seconds, not a count of frames
        self.assertIn("Animated players were slow here: switched to the light model.", src)   # a level at a time
        # replays recorded before the action cues: read shots, passes and rebounds off the frames
        self.assertIn("Array.isArray(p.actions) ? p.actions : inferActions(p)", src)
        # nothing new on window without ?debug
        self.assertEqual(src.count("window.hoopsim3D ="), 1)

    def test_web_route_serves_the_files(self):
        try:
            from fastapi.testclient import TestClient
            from hoopsim.web.app import create_app
            from hoopsim.web.session import GameSession
        except ImportError:
            self.skipTest("web extras not installed")
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        client = TestClient(create_app(GameSession(saves_dir=tmp.name)))
        for name, path in PLAYER_ASSETS.items():
            r = client.get(PLAYER_ASSETS_URL + name)
            self.assertEqual(r.status_code, 200, name)
            self.assertEqual(r.content, path.read_bytes(), name)
        self.assertEqual(client.get(PLAYER_ASSETS_URL + "player.glb").headers["content-type"], "model/gltf-binary")
        self.assertIn("javascript", client.get(PLAYER_ASSETS_URL + "GLTFLoader.js").headers["content-type"])
        for bad in ("nope.js", "export.py", "three.module.min.js"):        # only the four listed files
            self.assertEqual(client.get(PLAYER_ASSETS_URL + bad).status_code, 404, bad)


if __name__ == "__main__":
    unittest.main()
