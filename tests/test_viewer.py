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
        self.assertEqual(len(lines), 2)                            # read once, written once
        for ln in lines:
            self.assertIn("try", ln)                               # storage can throw
        self.assertIn('else if (k === "b" && BODY_CFG) {', page)    # B steps through them, only when there is a choice
        self.assertIn('if (bodyPref === "auto-light")', page)      # a step down made by itself is remembered too
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
        self.assertIn('if (mode === "3d" && BODY_CFG) loadBodies();', page)
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
                     "catch", "contest", "block", "rebound", "dunk", "steal", "plant", "plant_m", "fall", "screen"):
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
        uv = read(prim["attributes"]["TEXCOORD_0"], "f", 2)
        us = [uv[2 * i] for i in idx]; vs = [uv[2 * i + 1] for i in idx]
        self.assertLess(min(us), 0.05)
        self.assertGreater(max(us), 1.0)                           # wraps: the viewer's texture repeats
        self.assertGreaterEqual(min(vs), -0.05)
        self.assertLessEqual(max(vs), 1.0001)
        self.assertIn("tx.wrapS = THREE.RepeatWrapping;", self.template)

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
        self.assertIn('let bodyPref = "auto", autoLevel = PHONE && HAS_LIGHT ? "light" : "full", rigShown = null;', src)   # phones start on it
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
        from_frames = {"drive", "cut"}                             # running and dribbling: read off the frames
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
