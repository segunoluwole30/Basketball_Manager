"""The match viewer page: 2D / 3D toggle and the bundled 3D library."""
import random
import unittest

from hoopsim import generate_league
from hoopsim.spatial.export import THREE_JS, THREE_PLACEHOLDER, THREE_VERSION, build_html
from hoopsim.spatial import HalfCourtGame

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


if __name__ == "__main__":
    unittest.main()
