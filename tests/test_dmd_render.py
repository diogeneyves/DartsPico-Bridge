# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests du rendu du DMD : texte (§ 9.9, 46e complément, sous-phase SR-b),
GIF et PPM (48e complément, sous-phase SR-c).

Demandent Pillow (11.1.0 dans la CI, comme sur le Pi) : sautés s'il manque
(cas du Python du Mac de l'auteur). Sans médias : police intégrée de Pillow.
Les GIF et PPM d'essai sont fabriqués ici par Pillow, dans un dossier temporaire
(en attendant tools/make_test_gifs.py, commit 5 de SR-c).
"""

import itertools
import os
import random
import struct
import tempfile
import time
import unittest

import _paths  # noqa: F401
import dmd_core as C
import dmd_gif as G
import dmd_proto as D
import dmd_service as S

try:
    import PIL
    from PIL import ImageFont, features
    import dmd_render as R
    import make_test_gifs as MG
except ImportError:                         # Pillow absent
    PIL = None

W, H = D.WIDTH, D.HEIGHT
BLUE = D.rgb565(0, 0, 255)


def pixels(frame):
    return struct.unpack("<%dH" % (W * H), frame)


def px(frame, x, y):
    return pixels(frame)[y * W + x]


def ink(frame, background=0):
    """Boîte (x0, y0, x1, y1) des pixels différents du fond, ou None."""
    pts = [(i % W, i // W) for i, v in enumerate(pixels(frame)) if v != background]
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def run(scene, limit=10000):
    """Toutes les images d'une scène animée, jusqu'à l'arrêt de l'animation."""
    frames = []
    while scene.animated and len(frames) < limit:
        frames.append(scene.next_frame())
    return frames


@unittest.skipIf(PIL is None, "Pillow absent : tests du rendu sautés")
class PillowTest(unittest.TestCase):
    def test_load_default_accepts_size(self):
        # Pillow 10.1.0 et plus : load_default(size=…) rend une police FreeType (Aileron)
        self.assertTrue(features.check("freetype2"))
        font = ImageFont.load_default(size=20)
        self.assertIsInstance(font, ImageFont.FreeTypeFont)
        self.assertEqual(font.size, 20)

    @unittest.skipUnless(os.environ.get("CI"), "version figée vérifiée dans la CI seulement")
    def test_pillow_version_of_the_pi(self):
        self.assertEqual(PIL.__version__, "11.1.0")


@unittest.skipIf(PIL is None, "Pillow absent : tests du rendu sautés")
class RenderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with self.assertLogs("dmd", "WARNING"):
            self.r = R.Renderer(self.tmp.name)  # pas de Fonts/Impact.ttf

    # Conversion

    def test_rgb565_little_endian(self):
        from PIL import Image
        img = Image.new("RGB", (W, H), (255, 0, 0))
        img.putpixel((1, 0), (0, 255, 0))
        img.putpixel((2, 0), (0, 0, 255))
        img.putpixel((3, 0), (255, 255, 255))
        img.putpixel((0, 1), (12, 200, 77))
        frame = R.to_rgb565(img)
        self.assertEqual(len(frame), D.PIXELS_BYTES)
        self.assertEqual(frame[0:2], b"\x00\xF8")
        self.assertEqual(px(frame, 1, 0), 0x07E0)
        self.assertEqual(px(frame, 2, 0), 0x001F)
        self.assertEqual(px(frame, 3, 0), 0xFFFF)
        self.assertEqual(px(frame, 0, 1), D.rgb565(12, 200, 77))

    def test_rgb565_wrong_size(self):
        from PIL import Image
        with self.assertRaises(ValueError):
            R.to_rgb565(Image.new("RGB", (64, 32)))

    # Police

    def test_builtin_font_without_media(self):
        self.assertTrue(self.r.builtin_font)
        self.assertIsInstance(self.r.font(30), ImageFont.FreeTypeFont)

    def test_unreadable_impact_falls_back(self):
        os.makedirs(os.path.join(self.tmp.name, "Fonts"))
        with open(os.path.join(self.tmp.name, "Fonts", "Impact.ttf"), "wb") as f:
            f.write(b"pas une police")
        with self.assertLogs("dmd", "WARNING") as logs:
            r = R.Renderer(self.tmp.name)
        self.assertTrue(r.builtin_font)
        self.assertIn("unreadable", logs.output[0])

    def test_size_at_most_30_and_fits(self):
        font = self.r.fit("RASPYDARTS", W, H)
        self.assertLessEqual(font.size, R.MAX_SIZE)
        left, top, right, bottom = font.getbbox("RASPYDARTS")
        self.assertLessEqual(right - left, W)
        self.assertLessEqual(bottom - top, H)

    def test_short_text_at_size_30(self):
        self.assertEqual(self.r.fit("180", W, H).size, 30)

    def test_long_text_smaller(self):
        text = "A" * 22                      # 22 caractères par ligne fixe (§ 9.9)
        font = self.r.fit(text, W, H)
        self.assertLess(font.size, 30)
        left, _, right, _ = font.getbbox(text)
        self.assertTrue(right - left <= W or font.size == R.MIN_SIZE)

    # Texte fixe

    def test_welcome_blue_and_centered(self):
        scene = self.r.scene(C.Welcome())
        self.assertFalse(scene.animated)
        frame = scene.next_frame()
        self.assertEqual(len(frame), D.PIXELS_BYTES)
        box = ink(frame)
        self.assertIsNotNone(box)
        x0, y0, x1, y1 = box
        self.assertLessEqual(abs(x0 - (W - 1 - x1)), 2)
        self.assertLessEqual(abs(y0 - (H - 1 - y1)), 2)
        for v in set(pixels(frame)) - {0}:
            self.assertEqual(v & 0xFFE0, 0)  # rouge et vert nuls : bleu seul
        self.assertIn(BLUE, set(pixels(frame)))

    def test_msg(self):
        cmds = C.DmdCore(clock=lambda: 0).message(C.TOPIC_DMD, "msg|Bravo")
        frame = self.r.scene(cmds[0]).next_frame()
        self.assertIsNotNone(ink(frame))
        self.assertEqual(frame, self.r.fixed_text("Bravo").next_frame())

    def test_empty_text_black(self):
        self.assertEqual(self.r.fixed_text("  ").next_frame(), bytes(D.PIXELS_BYTES))

    def test_static_scene_repeats(self):
        scene = self.r.fixed_text("Bravo")
        self.assertEqual(scene.next_frame(), scene.next_frame())

    def test_msgcolor_fixed(self):
        cmds = C.DmdCore(clock=lambda: 0).message(C.TOPIC_DMD, "msgcolor|Joueur|255;0;0|0;255;0||")
        scene = self.r.scene(cmds[0])
        self.assertFalse(scene.animated)
        frame = scene.next_frame()
        green = D.rgb565(0, 255, 0)
        self.assertEqual(px(frame, 0, 0), green)
        self.assertEqual(px(frame, W - 1, H - 1), green)
        self.assertIn(D.rgb565(255, 0, 0), set(pixels(frame)))

    def test_msgcolor_default_colours(self):
        cmd = C.ShowText("Joueur", C.DEFAULT_TEXT_COLOR, None)
        self.assertEqual(self.r.scene(cmd).next_frame(), self.r.fixed_text("Joueur").next_frame())

    # Défilement

    def test_msgmove_left_one_pass_then_black(self):
        cmds = C.DmdCore(clock=lambda: 0).message(C.TOPIC_DMD, "msgmove|Un texte plus long que vingt-deux|left")
        scene = self.r.scene(cmds[0])
        self.assertTrue(scene.animated)
        frames = run(scene)
        self.assertEqual(ink(frames[0]), None)            # entre par le bord droit
        self.assertEqual(ink(frames[-1]), None)           # sorti par le bord gauche
        lefts = list(itertools.takewhile(lambda x: x > 0, [ink(f)[0] for f in frames if ink(f)]))
        self.assertGreater(len(lefts), 10)
        self.assertTrue(all(b - a == -R.SCROLL_STEP for a, b in zip(lefts, lefts[1:])))
        self.assertFalse(scene.animated)
        self.assertEqual(scene.next_frame(), bytes(D.PIXELS_BYTES))
        self.assertEqual(scene.next_frame(), bytes(D.PIXELS_BYTES))

    def test_scroll_size_limited_by_height_only(self):
        text = "Un texte plus long que vingt-deux caracteres"
        scene = self.r.scrolling_text(text, "left", 1)
        self.assertGreater(scene.strip.width, W)
        self.assertLessEqual(scene.strip.height, H)
        self.assertGreater(self.r.fit(text, None, H).size, self.r.fit(text, W, H).size)

    def test_msgmovebcl_passes(self):
        one = len(run(self.r.scrolling_text("Bravo", "left", 1)))
        three = len(run(self.r.scrolling_text("Bravo", "left", 3)))
        self.assertEqual(three, 3 * one)
        cmds = C.DmdCore(clock=lambda: 0).message(C.TOPIC_DMD, "msgmovebcl|Bravo|left|3")
        self.assertEqual(len(run(self.r.scene(cmds[0]))), three)

    def test_msgcolor_scroll(self):
        cmds = C.DmdCore(clock=lambda: 0).message(C.TOPIC_DMD, "msgcolor|Bravo|255;0;0|0;0;0|left|")
        scene = self.r.scene(cmds[0])
        self.assertEqual(len(run(scene)), len(run(self.r.scrolling_text("Bravo", "left", 1))))
        cmds = C.DmdCore(clock=lambda: 0).message(C.TOPIC_DMD, "msgcolor|Bravo|255;0;0|0;0;0|left|2")
        self.assertEqual(len(run(self.r.scene(cmds[0]))), 2 * len(run(self.r.scrolling_text("Bravo", "left", 1))))

    def _first_visible(self, direction):
        frames = [f for f in run(self.r.scrolling_text("Bravo", direction, 1)) if ink(f)]
        return ink(frames[0]), ink(frames[1])

    def test_directions(self):
        a, b = self._first_visible("right")
        self.assertGreater(b[2], a[2])                    # vers la droite
        a, b = self._first_visible("up")
        self.assertLess(b[1], a[1])                       # vers le haut
        a, b = self._first_visible("down")
        self.assertGreater(b[3], a[3])                    # vers le bas
        a, b = self._first_visible("LEFT")
        self.assertLess(b[0], a[0])

    def test_unknown_direction_left_logged_once(self):
        with self.assertLogs("dmd", "INFO") as logs:
            s1 = self.r.scrolling_text("Bravo", "flip", 1)
            self.r.scrolling_text("Bravo", "flip", 1)
            self.r.scrolling_text("Bravo", "zigzag", 1)
        self.assertEqual(s1.direction, "left")
        self.assertEqual(len(logs.output), 2)

    def test_scroll_background(self):
        scene = self.r.scrolling_text("Bravo", "left", 1, (255, 0, 0), (0, 0, 255))
        frames = run(scene)
        self.assertEqual(px(frames[0], 0, 0), BLUE)
        self.assertEqual(set(pixels(scene.next_frame())), {BLUE})

    # Mire, score, provisoire SR-c

    def test_pattern(self):
        cmds = C.DmdCore(clock=lambda: 0).message(C.TOPIC_RASPY2DMD, "pico|test_rgb")
        frame = self.r.scene(cmds[0]).next_frame()
        self.assertEqual(px(frame, 0, 0), 0xF800)
        self.assertEqual(px(frame, 31, 15), 0xF800)
        self.assertEqual(px(frame, 32, 0), 0x07E0)
        self.assertEqual(px(frame, 64, 0), 0x001F)
        self.assertEqual(px(frame, 127, 15), 0xFFFF)
        self.assertEqual(px(frame, 0, 16), 0)
        self.assertEqual(px(frame, 127, 31), 0xFFFF)
        self.assertEqual(px(frame, 64, 31), D.rgb565(128, 128, 128))

    def test_score_text_in_player_colour(self):
        cmds = C.DmdCore(clock=lambda: 0).message(C.TOPIC_DMD, "score|T20 - X - X||255;0;0|N")
        frame = self.r.scene(cmds[0]).next_frame()
        self.assertEqual(frame, self.r.fixed_text("T20 - X - X", (255, 0, 0)).next_frame())

    def test_media_provisional(self):
        # Score : provisoire jusqu'au commit 3 de SR-c (texte de repli)
        cmds = C.DmdCore(clock=lambda: 0).message(C.TOPIC_DMD, "score|T20 - X - X||255;0;0|O")
        self.assertIsInstance(cmds[0], C.ShowMedia)
        self.assertEqual(self.r.scene(cmds[0]).next_frame(),
                         self.r.fixed_text("T20 - X - X", (255, 0, 0)).next_frame())

    def test_unknown_command(self):
        with self.assertRaises(TypeError):
            self.r.scene(object())


RED, GREEN, WHITE = D.rgb565(255, 0, 0), D.rgb565(0, 255, 0), 0xFFFF
MS = 1000
STEP = 1000000 // D.TARGET_FPS


def save_gif(path, size, colors, durations=None, loop=None):
    """GIF d'essai : une image unie par couleur (couleurs différentes : Pillow fusionne
    les images identiques) ; durations en millisecondes (Pillow écrit /10) ; loop : NETSCAPE."""
    from PIL import Image
    frames = [Image.new("RGB", size, c) for c in colors]
    kw = {"save_all": True, "append_images": frames[1:]}
    if durations is not None:
        kw["duration"] = durations
    if loop is not None:
        kw["loop"] = loop
    frames[0].save(path, **kw)


@unittest.skipIf(PIL is None, "Pillow absent : tests du rendu sautés")
class MediaTest(unittest.TestCase):
    FOLDER = "Tests"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with self.assertLogs("dmd", "WARNING"):
            self.r = R.Renderer(self.tmp.name)
        for folder in (self.FOLDER, C.ATTRACT_FOLDER):
            os.makedirs(os.path.join(self.tmp.name, folder))

    def path(self, name, folder=FOLDER):
        return os.path.join(self.tmp.name, folder, name)

    def scene(self, name, folder=FOLDER, fallback=None, token=7):
        scene = self.r.scene(C.ShowMedia(folder, name, fallback, token))
        self.addCleanup(scene.close)
        return scene

    def gif(self, name, size, colors, durations=None, folder=FOLDER, loop=None):
        save_gif(self.path(name, folder), size, colors, durations, loop)
        return name

    # Mise à l'échelle et centrage

    def first(self, name):
        scene = self.scene(name)
        self.assertIsInstance(scene, R.MediaScene)
        return scene.next_frame(0)

    def test_larger_reduced_nearest(self):
        from PIL import Image
        image = Image.new("RGB", (256, 64), (255, 0, 0))
        image.paste((0, 255, 0), (128, 0, 256, 64))
        image.save(self.path("big.gif"))
        frame = self.first("big.gif")
        self.assertEqual(px(frame, 63, 10), RED)
        self.assertEqual(px(frame, 64, 10), GREEN)
        self.assertEqual(set(pixels(frame)), {RED, GREEN})          # voisin le plus proche : pas de mélange

    def test_larger_proportional_no_stretch(self):
        self.gif("wide.gif", (200, 20), [(255, 255, 255)])
        self.assertEqual(ink(self.first("wide.gif")), (0, 9, 127, 21))     # 128 × 13, centré
        self.gif("square.gif", (64, 64), [(255, 255, 255)])
        self.assertEqual(ink(self.first("square.gif")), (48, 0, 79, 31))   # 32 × 32, centré

    def test_ppm_upper_case_extension(self):
        from PIL import Image
        Image.new("RGB", (128, 32), (255, 0, 0)).save(self.path("A.PPM"), format="PPM")
        self.assertEqual(set(pixels(self.scene("A.PPM").next_frame(0))), {RED})

    # Cadence : durée réelle, images sautées

    def test_real_time_frames_skipped(self):
        colors = [(25 * i, 0, 0) for i in range(1, 11)]
        self.gif("fast.gif", (128, 32), colors, [20] * 10)        # 10 images de 2/100 s
        scene = self.scene("fast.gif")
        t0 = 5_000_000
        shown = []
        for n in range(4):
            shown.append(px(scene.next_frame(t0 + n * STEP), 0, 0))
        self.assertEqual(shown, [D.rgb565(*colors[i]) for i in (0, 3, 6, 9)])
        self.assertFalse(scene.is_done(t0 + 199 * MS))
        self.assertTrue(scene.is_done(t0 + 200 * MS))               # un passage hors Attract Mode
        self.assertEqual(px(scene.next_frame(t0 + 300 * MS), 0, 0), D.rgb565(*colors[9]))
        self.assertFalse(scene.animated)

    def test_attract_short_gif_replayed_to_5_s(self):
        self.gif("s.gif", (128, 32), [(255, 0, 0), (0, 255, 0)], [100, 100], folder=C.ATTRACT_FOLDER)
        scene = self.scene("s.gif", C.ATTRACT_FOLDER)
        self.assertEqual(px(scene.next_frame(0), 0, 0), RED)
        self.assertEqual(px(scene.next_frame(150 * MS), 0, 0), GREEN)
        self.assertEqual(px(scene.next_frame(250 * MS), 0, 0), RED)          # 2e passage
        self.assertEqual(px(scene.next_frame(4950 * MS), 0, 0), GREEN)
        self.assertFalse(scene.is_done(4999 * MS))
        self.assertTrue(scene.is_done(5000 * MS))

    def play_until_done(self, scene, step_us=100 * MS, limit_us=200_000 * MS):
        """Horloge qui avance de step_us : images publiées jusqu'à la fin du média ;
        rend (instant de la fin, couleurs du pixel central (64, 16) vues dans l'ordre, sans doublons)."""
        t = 0
        seen = []
        while not scene.is_done(t):
            color = px(scene.next_frame(t), 64, 16)     # centre : la petite image y est centrée
            if not seen or seen[-1] != color:
                seen.append(color)
            t += step_us
            self.assertLess(t, limit_us)
        return t, seen

    @staticmethod
    def distinct(i):
        """Couleur n° i, distincte des autres même en RGB565 (rouge sur 5 bits, vert sur 6)."""
        return ((i % 32) * 8, (i // 32) * 4 + 4, 0)

    def test_attract_long_gif_cut_at_60_s(self):
        # 50e complément : coupé à 60 s (attract_gif_max_s, défaut), plus à 30 s (48e complément)
        colors = [self.distinct(i) for i in range(1, 71)]
        self.gif("l.gif", (16, 8), colors, [1000] * 70, folder=C.ATTRACT_FOLDER)     # 70 s
        scene = self.scene("l.gif", C.ATTRACT_FOLDER)
        end, seen = self.play_until_done(scene)
        self.assertEqual(end, 60_000 * MS)
        self.assertFalse(scene.is_done(30_000 * MS))                    # plus coupé à 30 s
        self.assertEqual(seen, [D.rgb565(*c) for c in colors[:60]])     # 60 images d'1 s

    def test_attract_max_from_configuration(self):
        # attract_gif_max_s = 30 dans dmd.toml : Renderer(attract_max_s=30)
        colors = [self.distinct(i) for i in range(1, 41)]
        self.gif("l.gif", (16, 8), colors, [1000] * 40, folder=C.ATTRACT_FOLDER)     # 40 s
        with self.assertLogs("dmd", "WARNING"):
            r = R.Renderer(self.tmp.name, attract_max_s=30)
        scene = r.scene(C.ShowMedia(C.ATTRACT_FOLDER, "l.gif", None, 7))
        self.addCleanup(scene.close)
        end, seen = self.play_until_done(scene)
        self.assertEqual(end, 30_000 * MS)
        self.assertEqual(seen, [D.rgb565(*c) for c in colors[:30]])

    # Fichiers manquants ou corrompus

    def test_missing_attract_gif(self):
        with self.assertLogs("dmd", "WARNING") as logs:
            scene = self.scene("absent.gif", C.ATTRACT_FOLDER)
        self.assertIn("missing", logs.output[0])
        self.assertIsInstance(scene, R.FailedMediaScene)
        self.assertTrue(scene.is_done(0))
        self.assertEqual(scene.next_frame(0), self.r.welcome().next_frame())
        with self.assertNoLogs("dmd", "WARNING"):                   # une fois par fichier
            self.scene("absent.gif", C.ATTRACT_FOLDER)

    def test_corrupt_file_fallback_text_logged_once(self):
        with open(self.path("bad.gif"), "wb") as f:
            f.write(b"GIF89a" + bytes(3))
        text = C.ShowText("T20 - X - X", (255, 0, 0))
        with self.assertLogs("dmd", "WARNING") as logs:
            scene = self.scene("bad.gif", fallback=text, token=3)
        self.assertIn("unreadable", logs.output[0])
        self.assertIn(self.path("bad.gif"), self.r.bad_media)
        self.assertEqual(scene.token, 3)
        self.assertTrue(scene.is_done(0))
        self.assertEqual(scene.next_frame(0), self.r.fixed_text("T20 - X - X", (255, 0, 0)).next_frame())
        with self.assertNoLogs("dmd", "WARNING"):
            self.scene("bad.gif")

    def test_not_an_image(self):
        for name, data in (("x.gif", b"bonjour"), ("x.ppm", b"P6\n128 32\n255\n"), ("x.png", b"")):
            with open(self.path(name), "wb") as f:
                f.write(data)
            with self.assertLogs("dmd", "WARNING"):
                self.assertIsInstance(self.scene(name), R.FailedMediaScene)

    def test_error_during_playback(self):
        self.gif("m.gif", (128, 32), [(255, 0, 0), (0, 255, 0)], [100, 100])
        scene = self.scene("m.gif")
        first = scene.next_frame(0)

        def broken(index):
            raise OSError("image tronquée")
        scene.source.frame = broken
        with self.assertLogs("dmd", "WARNING"):
            frame = scene.next_frame(150 * MS)
        self.assertEqual(frame, first)                               # dernière image bonne gardée
        self.assertTrue(scene.is_done(150 * MS))
        self.assertIn(self.path("m.gif"), self.r.bad_media)

    def test_close_releases_file(self):
        self.gif("c.gif", (128, 32), [(255, 0, 0), (0, 255, 0)], [100, 100])
        scene = self.scene("c.gif")
        scene.close()
        self.assertIsNone(scene.source._image)

    # Bout en bout : rendu réel, fin de média relayée au cœur

    def test_service_next_gif_after_end(self):
        self.gif("a.gif", (128, 32), [(255, 0, 0), (128, 0, 0)], [1000, 1000], folder=C.ATTRACT_FOLDER)
        self.gif("b.gif", (128, 32), [(0, 255, 0), (0, 128, 0)], [1000, 1000], folder=C.ATTRACT_FOLDER)
        clk = [0]
        core = C.DmdCore(clock=lambda: clk[0], attract_delay_s=1, gifs=["a.gif", "b.gif"])
        out = []
        service = S.DmdService(core, self.r, out.append, clock=lambda: clk[0])
        service.start()
        colours = []
        while clk[0] < 12_900_000:              # avant la fin du 2e GIF (vers 13 s)
            service.step()
            if out and (not colours or colours[-1] != px(out[-1], 0, 0)):
                colours.append(px(out[-1], 0, 0))
            clk[0] += 5_000
        self.addCleanup(service.scene.close)
        # accueil, puis un GIF de 2 s joué 3 fois (6 s), puis l'autre (tiré « en sac »)
        reds = {RED, D.rgb565(128, 0, 0)}
        greens = {GREEN, D.rgb565(0, 128, 0)}
        played = [c for c in colours if c in reds | greens]
        groups = [played[0] in reds]
        for c in played[1:]:
            if (c in reds) != groups[-1]:
                groups.append(c in reds)
        self.assertEqual(len(groups), 2)
        self.assertEqual(len(played), 2 * 6)                         # 3 passages de 2 images chacun
        self.assertEqual(core.state, C.ATTRACT_GIF)


@unittest.skipIf(PIL is None, "Pillow absent : tests du rendu sautés")
class ScoreMediaTest(unittest.TestCase):
    """Médias de Scores décodés d'avance au démarrage, tirés au hasard (48e complément)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.scores = os.path.join(self.tmp.name, C.SCORES_FOLDER)
        self.gif("T20", "a.gif", [(255, 0, 0), (0, 255, 0)])
        self.gif("T20", ".cache.gif", [(0, 0, 255)])                 # caché : exclu
        self.write("T20", "notes.txt", b"texte")                     # autre extension : exclu
        self.gif("SB", "rouge.gif", [(255, 0, 0)])
        self.gif("SB", "vert.GIF", [(0, 255, 0)])
        self.gif("DB", "bon.gif", [(255, 255, 255)])
        self.write("DB", "mauvais.gif", b"GIF89a" + bytes(3))
        self.write("D1", "mauvais.gif", b"bonjour")                  # aucun fichier lisible
        os.makedirs(os.path.join(self.scores, "D20"))                # vide
        self.gif("Autre", "a.gif", [(255, 0, 0)])                    # pas un code de fléchette
        from PIL import Image
        Image.new("RGB", (117, 32), (255, 255, 255)).save(self.path("S1", "a.ppm"))
        with self.assertLogs("dmd", "INFO") as logs:
            self.r = R.Renderer(self.tmp.name, rng=random.Random(0))
        self.logs = logs.output
        self.core = C.DmdCore(clock=lambda: 0)

    def path(self, folder, name):
        os.makedirs(os.path.join(self.scores, folder), exist_ok=True)
        return os.path.join(self.scores, folder, name)

    def gif(self, folder, name, colors, durations=None):
        save_gif(self.path(folder, name), (128, 32), colors, durations or [100] * len(colors))

    def write(self, folder, name, data):
        with open(self.path(folder, name), "wb") as f:
            f.write(data)

    def score(self, darts, flag="O", colour="255;0;0"):
        out = self.core.message(C.TOPIC_DMD, "score|%s||%s|%s" % (darts, colour, flag))
        self.assertEqual(len(out), 1)
        return out[0]

    def scene(self, command):
        scene = self.r.scene(command)
        self.addCleanup(scene.close)
        return scene

    def test_preloaded_at_start(self):
        self.assertEqual(set(self.r.scores), {"T20", "SB", "DB", "S1"})
        self.assertEqual([os.path.basename(m.path) for m in self.r.scores["T20"]], ["a.gif"])
        t20 = self.r.scores["T20"][0]
        self.assertEqual(len(t20.frames), 2)
        self.assertTrue(all(len(f) == D.PIXELS_BYTES for f in t20.frames))
        self.assertEqual(t20.delays_us, (100 * MS, 100 * MS))

    def test_changes_after_start_need_restart(self):
        os.remove(self.path("T20", "a.gif"))
        self.gif("D20", "nouveau.gif", [(255, 0, 0)])
        self.assertIsInstance(self.scene(self.score("T20 - X - X")), R.MediaScene)   # cache
        self.assertNotIsInstance(self.scene(self.score("D20 - X - X")), R.MediaScene)

    def test_gif_played_once(self):
        scene = self.scene(self.score("S5 - X - T20"))               # dernière fléchette : T20
        self.assertIsInstance(scene, R.MediaScene)
        self.assertEqual(px(scene.next_frame(1000), 0, 0), RED)
        self.assertEqual(px(scene.next_frame(1000 + 150 * MS), 0, 0), GREEN)
        self.assertFalse(scene.is_done(1000 + 199 * MS))
        self.assertTrue(scene.is_done(1000 + 200 * MS))              # ni 5 s minimum ni rejeu

    def test_ppm_2_s(self):
        scene = self.scene(self.score("S1 - X - X"))
        self.assertEqual(ink(scene.next_frame(0)), (5, 0, 121, 31))
        self.assertFalse(scene.is_done(1999 * MS))
        self.assertTrue(scene.is_done(2000 * MS))

    def test_random_draw(self):
        seen = {px(self.scene(self.score("SB - X - X")).next_frame(0), 0, 0) for _ in range(40)}
        self.assertEqual(seen, {RED, GREEN})

    def test_corrupt_logged_once_and_never_drawn(self):
        bad = [line for line in self.logs if "DMD : media" in line and "unreadable" in line]
        self.assertEqual(len(bad), 2)                                # DB/mauvais.gif, D1/mauvais.gif
        self.assertIn(self.path("DB", "mauvais.gif"), self.r.bad_media)
        for _ in range(20):
            self.assertEqual(set(pixels(self.scene(self.score("DB - X - X")).next_frame(0))), {WHITE})

    def test_text_only_without_readable_media(self):
        for darts in ("D1 - X - X", "D20 - X - X", "D5 - X - X"):    # corrompu, vide, absent
            cmd = self.score(darts)
            scene = self.scene(cmd)
            self.assertNotIsInstance(scene, (R.MediaScene, R.FailedMediaScene))
            self.assertIsNone(getattr(scene, "token", None))
            self.assertEqual(scene.next_frame(0), self.r.fixed_text(darts, (255, 0, 0)).next_frame())

    def test_flag_n_text_only(self):
        cmd = self.score("T20 - X - X", "N")
        self.assertEqual(cmd, C.ShowText("T20 - X - X", (255, 0, 0)))

    def test_missing_scores_folder_logged_once(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertLogs("dmd", "WARNING") as logs:
                r = R.Renderer(empty)
            self.assertEqual(len([line for line in logs.output if "scores folder" in line]), 1)
            self.assertEqual(r.scores, {})
            self.assertEqual(r.scene(self.score("T20 - X - X")).next_frame(0),
                             r.fixed_text("T20 - X - X", (255, 0, 0)).next_frame())

    def test_service_media_then_text(self):
        clk = [0]
        core = C.DmdCore(clock=lambda: clk[0])
        out = []
        service = S.DmdService(core, self.r, out.append, clock=lambda: clk[0])
        service.start()
        service.message(C.TOPIC_DMD, "score|X - X - T20||255;0;0|O")
        colours = []
        while clk[0] < 3_000_000:
            service.step()
            if out and (not colours or colours[-1] != out[-1]):
                colours.append(out[-1])
            clk[0] += 5_000
        text = self.r.fixed_text("X - X - T20", (255, 0, 0)).next_frame()
        self.assertEqual([px(f, 0, 0) for f in colours[:2]], [RED, GREEN])
        self.assertEqual(colours[2:], [text])
        self.assertEqual(out[-1], text)


@unittest.skipIf(PIL is None, "Pillow absent : tests du rendu sautés")
class SpecialMovesMediaTest(unittest.TestCase):
    """SpecialsMoves décodés au démarrage et gardés en cache, comme Scores (49e complément)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gif("MAXIMUM_TON_80", "rouge.gif", [(255, 0, 0)])
        self.gif("MAXIMUM_TON_80", "vert.gif", [(0, 255, 0)])
        self.gif("DEVIL", "devil 2.gif", [(255, 0, 0), (0, 255, 0)])
        self.gif("DEVIL", "devil (triple) 2.gif", [(255, 255, 255)])
        self.gif("WHITE_HORSE", "a.gif", [(255, 0, 0)])                  # dossier inutilisé
        os.makedirs(os.path.join(self.tmp.name, C.SPECIALS_FOLDER, "HAT_TRICK"))   # vide
        os.makedirs(os.path.join(self.tmp.name, C.SCORES_FOLDER, "T20"))
        save_gif(os.path.join(self.tmp.name, C.SCORES_FOLDER, "T20", "s.gif"), (128, 32),
                 [(0, 0, 255)], [100])
        with self.assertLogs("dmd", "INFO"):
            self.r = R.Renderer(self.tmp.name, rng=random.Random(0))

    def gif(self, folder, name, colors):
        path = os.path.join(self.tmp.name, C.SPECIALS_FOLDER, folder)
        os.makedirs(path, exist_ok=True)
        save_gif(os.path.join(path, name), (128, 32), colors, [100] * len(colors))

    def core(self, **kw):
        return C.DmdCore(clock=lambda: 0, media_available=self.r.has_media, **kw)

    def scene(self, command):
        scene = self.r.scene(command)
        self.addCleanup(scene.close)
        return scene

    def test_only_used_folders_loaded(self):
        self.assertEqual(set(self.r.specials), {"MAXIMUM_TON_80", "DEVIL"})
        self.assertTrue(self.r.has_media("SpecialsMoves/DEVIL", "devil 2.gif"))
        self.assertFalse(self.r.has_media("SpecialsMoves/DEVIL", "autre.gif"))
        self.assertFalse(self.r.has_media("SpecialsMoves/HAT_TRICK"))
        self.assertFalse(self.r.has_media("SpecialsMoves/WHITE_HORSE"))
        self.assertTrue(self.r.has_media("Scores/T20"))
        self.assertFalse(self.r.has_media("Gifs/600 Gratos", "a.gif"))

    def test_devil_precise_file(self):
        cmd = self.core().message(C.TOPIC_DMD, "score|T6 - T6 - T6|||O")[0]
        for _ in range(10):
            self.assertEqual(set(pixels(self.scene(cmd).next_frame(0))), {WHITE})

    def test_random_draw(self):
        cmd = self.core().message(C.TOPIC_DMD, "score|T20 - T20 - T20|||O")[0]
        self.assertEqual(cmd.folder, "SpecialsMoves/MAXIMUM_TON_80")
        seen = {px(self.scene(cmd).next_frame(0), 0, 0) for _ in range(40)}
        self.assertEqual(seen, {RED, GREEN})

    def test_played_once_from_cache(self):
        cmd = self.core().message(C.TOPIC_DMD, "score|S6 - S6 - S6|||O")[0]
        os.remove(os.path.join(self.tmp.name, C.SPECIALS_FOLDER, "DEVIL", "devil 2.gif"))
        scene = self.scene(cmd)
        self.assertIsInstance(scene, R.MediaScene)
        self.assertEqual(px(scene.next_frame(1000), 0, 0), RED)
        self.assertEqual(px(scene.next_frame(1000 + 150 * MS), 0, 0), GREEN)
        self.assertTrue(scene.is_done(1000 + 200 * MS))

    def test_empty_folder_ordinary_score(self):
        cmd = self.core().message(C.TOPIC_DMD, "score|SB - DB - T20|||O")[0]   # 135 : LOW_TON absent
        self.assertEqual(cmd.folder, "Scores/T20")
        # HAT_TRICK vide, puis Scores/SB absent : texte seul
        self.assertEqual(self.core().message(C.TOPIC_DMD, "score|SB - DB - SB|||O"),
                         [C.ShowText("SB - DB - SB")])

    def test_switch_off_nothing_loaded(self):
        with self.assertLogs("dmd", "INFO"):
            r = R.Renderer(self.tmp.name, special_moves=False)
        self.assertEqual(r.specials, {})
        self.assertFalse(r.has_media("SpecialsMoves/MAXIMUM_TON_80"))

    def test_missing_folder_ordinary_scores(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertLogs("dmd", "WARNING") as logs:
                r = R.Renderer(empty)
        self.assertEqual(r.specials, {})
        self.assertEqual(len([line for line in logs.output if "special moves folder" in line]), 1)


def Image_frames(path):
    from PIL import Image
    with Image.open(path) as image:
        return image.n_frames


@unittest.skipIf(PIL is None, "Pillow absent : tests du rendu sautés")
class ClockTest(unittest.TestCase):
    """Heure de l'Attract Mode (SR-c commit 4, 48e complément)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = [time.mktime((2026, 11, 24, 12, 34, 30, 0, 0, -1))]   # heure locale
        with self.assertLogs("dmd", "WARNING") as logs:
            self.r = R.Renderer(self.tmp.name, wall=lambda: self.t[0])
        self.logs = logs.output

    def test_blackwood_missing_logged(self):
        self.assertTrue(self.r.builtin_clock_font)
        self.assertEqual(len([line for line in self.logs if "Blackwood.ttf missing" in line]), 1)

    def test_blackwood_path(self):
        self.assertEqual(R.CLOCK_FONT_FILE, os.path.join("Fonts", "Blackwood.ttf"))

    def test_unreadable_blackwood_falls_back(self):
        os.makedirs(os.path.join(self.tmp.name, "Fonts"))
        with open(os.path.join(self.tmp.name, "Fonts", "Blackwood.ttf"), "wb") as f:
            f.write(b"pas une police")
        with self.assertLogs("dmd", "WARNING") as logs:
            r = R.Renderer(self.tmp.name)
        self.assertTrue(r.builtin_clock_font)
        self.assertEqual(len([line for line in logs.output if "Blackwood.ttf unreadable" in line]), 1)

    def test_show_clock_scene(self):
        scene = self.r.scene(C.ShowClock())
        self.assertIsInstance(scene, R.ClockScene)
        self.assertFalse(scene.animated)
        self.assertFalse(hasattr(scene, "token"))       # sa durée est tenue par le cœur

    def test_hh_mm_blue_on_black_like_fixed_text(self):
        frame = self.r.scene(C.ShowClock()).next_frame(0)
        # Même taille et même centrage que le texte fixe (police intégrée des deux côtés ici)
        self.assertEqual(frame, self.r.fixed_text("12:34").next_frame())
        self.assertIn(BLUE, set(pixels(frame)))
        self.assertEqual(px(frame, 0, 0), 0)
        for v in set(pixels(frame)):
            r5, g6 = v >> 11, (v >> 5) & 0x3F
            self.assertEqual((r5, g6), (0, 0))           # bleu seul (antialiasing compris)

    def test_redrawn_when_minute_changes(self):
        scene = self.r.scene(C.ShowClock())
        first = scene.next_frame(0)
        self.t[0] += 20                                  # 12:34:50
        self.assertIs(scene.next_frame(1), first)        # même minute : pas redessinée
        self.t[0] += 10                                  # 12:35:00
        self.assertEqual(scene.next_frame(2), self.r.fixed_text("12:35").next_frame())

    def test_clock_uses_clock_font(self):
        seen = []
        original = self.r.fit

        def spy(text, max_w, max_h, path=None):
            seen.append(path)
            return original(text, max_w, max_h, path)
        self.r.fit = spy
        self.r.scene(C.ShowClock()).next_frame(0)
        self.r.fixed_text("x")
        self.assertEqual(seen, ["", None])               # "" : police intégrée de l'horloge


@unittest.skipIf(PIL is None, "Pillow absent : tests du rendu sautés")
class AttractCorruptTest(unittest.TestCase):
    """GIF corrompu de l'Attract Mode : journalisé une fois, retiré du sac pour la vie du
    service, même quand la liste est relue (SR-c commit 4, 48e complément)."""

    def test_corrupt_gif_excluded_and_logged_once(self):
        with tempfile.TemporaryDirectory() as media:
            folder = os.path.join(media, C.ATTRACT_FOLDER)
            os.makedirs(folder)
            save_gif(os.path.join(folder, "bon.gif"), (128, 32), [(255, 0, 0)], [600])
            with open(os.path.join(folder, "mauvais.gif"), "wb") as f:
                f.write(b"GIF89a" + bytes(3))
            with self.assertLogs("dmd", "WARNING"):
                renderer = R.Renderer(media)
            clk = [0]
            core = C.DmdCore(clock=lambda: clk[0], gif_source=S.AttractGifs(media),
                             rng=random.Random(0))
            service = S.DmdService(core, renderer, lambda f: None, clock=lambda: clk[0])
            service.start()
            with self.assertLogs("dmd", "WARNING") as logs:
                service.message(C.TOPIC_RASPY2DMD, "demo|gif")
                while clk[0] < 40_000_000:                 # 6 passages de 6 s environ
                    service.step()
                    clk[0] += 10_000
            corrupt = [line for line in logs.output if "mauvais.gif" in line]
            self.assertEqual(len(corrupt), 1)
            self.assertEqual(core._excluded, {"mauvais.gif"})
            service.scene.close()


@unittest.skipIf(PIL is None, "Pillow absent : tests du rendu sautés")
class GeneratedMediaTest(unittest.TestCase):
    """Fichiers de tools/make_test_gifs.py (48e complément), fabriqués dans un dossier
    temporaire : délais, NETSCAPE, taille, centrage et durée d'un passage."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.folder = os.path.join(cls.tmp.name, C.ATTRACT_FOLDER)
        cls.written = MG.generate(cls.folder)
        os.makedirs(os.path.join(cls.tmp.name, "Tests"))
        for path in cls.written:                    # même fichier, un passage hors Attract Mode
            with open(path, "rb") as src, open(os.path.join(cls.tmp.name, "Tests",
                                                            os.path.basename(path)), "wb") as dst:
                dst.write(src.read())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        with self.assertLogs("dmd", "WARNING"):
            self.r = R.Renderer(self.tmp.name)

    def scene(self, name, folder="Tests"):
        scene = self.r.scene(C.ShowMedia(folder, name, None, 5))
        self.addCleanup(scene.close)
        self.assertIsInstance(scene, R.MediaScene)
        return scene

    def test_files_written(self):
        names = sorted(os.path.basename(p) for p in self.written)
        self.assertEqual(names, sorted([n for n, _, _ in MG.cases()] + [MG.PPM_NAME]))
        self.assertEqual(len(names), 7)
        with open(os.path.join(self.folder, MG.PPM_NAME), "rb") as f:
            self.assertEqual(f.read(2), b"P6")

    def test_delays_and_netscape(self):
        for name, size, netscape in MG.cases():
            with self.subTest(name=name):
                info = G.read_gif(os.path.join(self.folder, name))
                self.assertEqual((info.width, info.height), size)
                self.assertEqual(info.raw_delays, (0, 2, 10))
                self.assertEqual(info.delays, (10, 2, 10))          # 0 compté 10/100 s
                self.assertEqual(info.netscape_loops, 0 if netscape else None)
                self.assertEqual(Image_frames(os.path.join(self.folder, name)), 3)

    def test_size_and_centring(self):
        expected = {(128, 32): (0, 0, 127, 31), (117, 32): (5, 0, 121, 31), (128, 30): (0, 1, 127, 30)}
        for name, size, _ in MG.cases():
            with self.subTest(name=name):
                frame = self.scene(name).next_frame(0)
                self.assertEqual(ink(frame), expected[size])        # centré, sans redimensionnement
                self.assertEqual(set(pixels(frame)) - {0}, {RED})

    def test_frames_follow_delays(self):
        for name, _, _ in MG.cases():
            with self.subTest(name=name):
                scene = self.scene(name)
                t0 = 1_000_000
                colours = [px(scene.next_frame(t0 + ms * MS), 64, 16) for ms in (0, 99, 100, 119, 120, 219)]
                self.assertEqual(colours, [RED, RED, GREEN, GREEN, BLUE, BLUE])

    def test_one_pass_220_ms_netscape_ignored(self):
        for name, _, _ in MG.cases():
            with self.subTest(name=name):
                scene = self.scene(name)
                scene.next_frame(0)
                self.assertFalse(scene.is_done(219_999))
                self.assertTrue(scene.is_done(220_000))

    def test_attract_replayed_to_5_s(self):
        # 220 ms par passage : 23 passages (5,06 s), avec ou sans NETSCAPE
        for name, _, _ in MG.cases():
            with self.subTest(name=name):
                scene = self.scene(name, C.ATTRACT_FOLDER)
                self.assertEqual(px(scene.next_frame(0), 64, 16), RED)
                self.assertEqual(px(scene.next_frame(230 * MS), 64, 16), RED)       # 2e passage
                self.assertFalse(scene.is_done(5_059_999))
                self.assertTrue(scene.is_done(5_060_000))

    def test_ppm_centred_shown_2_s(self):
        scene = self.scene(MG.PPM_NAME)
        self.assertEqual(ink(scene.next_frame(1000)), (5, 0, 121, 31))
        self.assertEqual(set(pixels(scene.next_frame(1000))) - {0}, {WHITE})
        self.assertFalse(scene.is_done(1000 + 1999 * MS))
        self.assertTrue(scene.is_done(1000 + 2000 * MS))

    def test_command_line(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as dest:
            out = subprocess.run([sys.executable, MG.__file__, os.path.join(dest, "gifs")],
                                 capture_output=True, text=True, check=True)
            self.assertEqual(out.stdout.count("écrit : "), 7)
            self.assertEqual(len(os.listdir(os.path.join(dest, "gifs"))), 7)


if __name__ == "__main__":
    unittest.main()
