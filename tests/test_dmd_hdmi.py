# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests de l'écran HDMI dédié du DMD (§ 9.9, « Écran HDMI dédié », complément 72).

Seul le cœur en Python pur (dmd_hdmi) est testé : conversion RGB565 -> RGB, échelle et
placement, choix de l'écran par son nom, détection du branchement avec une horloge
simulée, envoi et lecture « seule la dernière image » sur un vrai tube, relance du
processus, branchement dans dmd_service.run(). La couche pygame (dmd_hdmi_screen) n'est
pas testée hors du Pi : ni le Mac ni la CI n'ont pygame ; seul son import sans pygame
est vérifié.
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock

import _paths  # noqa: F401
import dmd_config as K
import dmd_hdmi as H
import dmd_proto as D
import dmd_service as S

SEC = 1_000_000


def frame_of(value):
    """Image RGB565 uniforme (u16 petit-boutiste)."""
    return bytes([value & 0xFF, (value >> 8) & 0xFF]) * (D.PIXELS_BYTES // 2)


def numbered(n):
    """Image dont tous les octets valent n (sert à reconnaître les images reçues)."""
    return bytes([n & 0xFF]) * D.PIXELS_BYTES


class Clock:
    def __init__(self, t=1_000_000):
        self.t = t

    def __call__(self):
        return self.t


class Rgb565LayoutTest(unittest.TestCase):
    """Masques et ordre des octets de la surface pygame 16 bits (complément 73) : l'image
    RGB565 est copiée telle quelle, sans conversion pixel par pixel en Python. Ordre du
    protocole : u16 petit-boutiste (§ 6.2, shared/dmd_proto.py:149, firmware
    lib/card.cpp:272 et lib/pixel.hpp:32-35)."""

    def channels(self, frame, index=0):
        return H.pixel_channels(frame, index)

    def test_masks(self):
        self.assertEqual(H.MASKS, (0xF800, 0x07E0, 0x001F, 0))
        self.assertEqual(H.BYTE_ORDER, "little")
        self.assertEqual(H.DEPTH, 16)

    def test_known_images(self):
        cases = {
            "rouge": ((255, 0, 0), (31, 0, 0)),
            "vert": ((0, 255, 0), (0, 63, 0)),
            "bleu": ((0, 0, 255), (0, 0, 31)),
            "blanc": ((255, 255, 255), (31, 63, 31)),
            "noir": ((0, 0, 0), (0, 0, 0)),
        }
        for name, (rgb, expected) in cases.items():
            with self.subTest(couleur=name):
                frame = frame_of(D.rgb565(*rgb))
                self.assertEqual(self.channels(frame), expected)
                self.assertEqual(self.channels(frame, D.WIDTH * D.HEIGHT - 1), expected)

    def test_little_endian_bytes(self):
        # rouge pur : octets 00 F8 dans le flux (petit-boutiste), et non F8 00
        self.assertEqual(frame_of(D.rgb565(255, 0, 0))[:2], bytes([0x00, 0xF8]))
        self.assertEqual(self.channels(bytes([0x00, 0xF8]) * (D.PIXELS_BYTES // 2)), (31, 0, 0))
        self.assertEqual(self.channels(bytes([0xF8, 0x00]) * (D.PIXELS_BYTES // 2)), (0, 7, 24))

    def test_pixel_order(self):
        data = bytearray(D.PIXELS_BYTES)
        data[2:4] = bytes([0x1F, 0x00])         # 2e pixel (x = 1, y = 0) bleu
        data[-2:] = bytes([0x00, 0xF8])         # dernier pixel rouge
        frame = bytes(data)
        self.assertEqual(self.channels(frame, 0), (0, 0, 0))
        self.assertEqual(self.channels(frame, 1), (0, 0, 31))
        self.assertEqual(self.channels(frame, D.WIDTH * D.HEIGHT - 1), (31, 0, 0))

    def test_native_order(self):
        self.assertTrue(H.native_order_ok("little"))
        self.assertFalse(H.native_order_ok("big"))

    def test_wrong_length(self):
        for n in (0, D.PIXELS_BYTES - 1, D.PIXELS_BYTES + 2):
            with self.assertRaises(ValueError):
                H.pixel_channels(bytes(n), 0)

    def test_no_python_conversion_left(self):
        self.assertFalse(hasattr(H, "rgb565_to_rgb"))


class ChangesTest(unittest.TestCase):
    """Image identique à la précédente : rien n'est redessiné."""

    def test_same_frame_skipped(self):
        changes = H.Changes()
        self.assertTrue(changes.is_new(numbered(1)))
        self.assertFalse(changes.is_new(numbered(1)))
        self.assertFalse(changes.is_new(bytes(numbered(1))))
        self.assertTrue(changes.is_new(numbered(2)))
        self.assertTrue(changes.is_new(numbered(1)))

    def test_reset_redraws(self):
        changes = H.Changes()
        changes.is_new(numbered(3))
        changes.reset()                         # écran rouvert : tout est à redessiner
        self.assertTrue(changes.is_new(numbered(3)))

    def test_static_image_repeated_every_second(self):
        # le service répète une image fixe toutes les secondes : un seul dessin
        changes = H.Changes()
        drawn = sum(changes.is_new(numbered(4)) for _ in range(30))
        self.assertEqual(drawn, 1)


class FakeBackend:
    def __init__(self, calls=None):
        self.calls = [] if calls is None else calls

    def upload(self, frame):
        self.calls.append(("upload", frame[0]))

    def present(self):
        self.calls.append(("present",))


class ChoosePathTest(unittest.TestCase):
    """Rendu accéléré (texture agrandie par le GPU), sinon repli sur la surface 16 bits
    (complément 74)."""

    def setUp(self):
        self.once = H.OnceLog()
        self.built = []

    def surface(self):
        self.built.append("surface")
        return "surface-backend"

    def test_accelerated_when_available(self):
        def accelerated():
            self.built.append("accelerated")
            return "gpu-backend"
        self.assertEqual(H.choose_path(accelerated, self.surface, self.once),
                         (H.PATH_ACCELERATED, "gpu-backend"))
        self.assertEqual(self.built, ["accelerated"])    # repli jamais construit

    def test_fallback_logged_once(self):
        def accelerated():
            raise RuntimeError("Couldn't find matching render driver")
        with self.assertLogs("dmd", "WARNING") as logs:
            self.assertEqual(H.choose_path(accelerated, self.surface, self.once),
                             (H.PATH_SURFACE, "surface-backend"))
            self.assertEqual(H.choose_path(accelerated, self.surface, self.once),
                             (H.PATH_SURFACE, "surface-backend"))
        self.assertEqual(len(logs.output), 1)
        self.assertIn("accelerated renderer unavailable", logs.output[0])
        self.assertIn("Couldn't find matching render driver", logs.output[0])

    def test_any_error_falls_back(self):
        for error in (ImportError("pygame._sdl2"), AttributeError("from_display_module"),
                      ValueError("format")):
            with self.subTest(error=type(error).__name__):
                def accelerated(error=error):
                    raise error
                with self.assertLogs("dmd", "WARNING"):
                    path, _ = H.choose_path(accelerated, self.surface, H.OnceLog())
                self.assertEqual(path, H.PATH_SURFACE)

    def test_surface_failure_propagates(self):
        def accelerated():
            raise RuntimeError("pas de GPU")

        def surface():
            raise RuntimeError("pas de surface")
        with self.assertLogs("dmd", "WARNING"), self.assertRaises(RuntimeError):
            H.choose_path(accelerated, surface, self.once)

    def test_names(self):
        self.assertEqual((H.PATH_ACCELERATED, H.PATH_SURFACE), ("accelerated", "surface"))

    def test_open_message_names_the_path(self):
        self.assertEqual(H.open_message(1, (1920, 1080), 15, H.PATH_ACCELERATED),
                         "full screen on HDMI-A-2 (screen 1, 1920x1080, x15, accelerated)")
        self.assertEqual(H.open_message(1, (1920, 1080), 15, H.PATH_SURFACE),
                         "full screen on HDMI-A-2 (screen 1, 1920x1080, x15, surface)")


class FakeWindow:
    def __init__(self, position, display_index, events):
        self.position = position
        self.display_index = display_index
        self.events = events
        self.destroyed = False

    def destroy(self):
        self.destroyed = True
        self.events.append("destroy")


class OpenAcceleratedTest(unittest.TestCase):
    """Chemin accéléré : fenêtre SDL à part (sans set_mode) sur l'écran choisi, fermée si
    l'ouverture ou le rendu échouent (complément 75)."""

    def setUp(self):
        self.events = []
        self.windows = []

    def window(self, display_index=1):
        def make(position):
            self.events.append(("window", position))
            w = FakeWindow(position, display_index, self.events)
            self.windows.append(w)
            return w
        return make

    def renderer(self, window):
        self.events.append("renderer")
        return "renderer"

    def texture(self, renderer):
        self.events.append("texture")
        return "texture"

    def test_display_position(self):
        self.assertEqual(H.WINDOWPOS_UNDEFINED_DISPLAY, 0x1FFF0000)
        self.assertEqual(H.display_position(0), (0x1FFF0000, 0x1FFF0000))
        self.assertEqual(H.display_position(1), (0x1FFF0001, 0x1FFF0001))

    def test_success(self):
        window, renderer, texture = H.open_accelerated(1, self.window(1), self.renderer,
                                                       self.texture)
        self.assertEqual(window.position, (0x1FFF0001, 0x1FFF0001))
        self.assertEqual((renderer, texture), ("renderer", "texture"))
        self.assertFalse(window.destroyed)
        self.assertEqual(self.events, [("window", (0x1FFF0001, 0x1FFF0001)), "renderer", "texture"])

    def test_window_on_another_screen_is_closed(self):
        with self.assertRaisesRegex(RuntimeError, "screen 0 instead of 1"):
            H.open_accelerated(1, self.window(0), self.renderer, self.texture)
        self.assertTrue(self.windows[0].destroyed)
        self.assertNotIn("renderer", self.events)       # jamais de rendu sur HDMI-A-1

    def test_renderer_failure_closes_the_window(self):
        def renderer(window):
            raise RuntimeError("Couldn't find matching render driver")
        with self.assertRaises(RuntimeError):
            H.open_accelerated(1, self.window(1), renderer, self.texture)
        self.assertTrue(self.windows[0].destroyed)

    def test_texture_failure_closes_the_window(self):
        def texture(renderer):
            raise ValueError("format")
        with self.assertRaises(ValueError):
            H.open_accelerated(1, self.window(1), self.renderer, texture)
        self.assertTrue(self.windows[0].destroyed)

    def test_window_failure_propagates(self):
        def window(position):
            raise RuntimeError("no window")
        with self.assertRaises(RuntimeError):
            H.open_accelerated(1, window, self.renderer, self.texture)
        self.assertEqual(self.events, [])

    def test_fallback_after_closing(self):
        def accelerated():
            return H.open_accelerated(1, self.window(1), self.failing_renderer, self.texture)

        def surface():
            self.events.append("set_mode")
            return "surface-backend"
        with self.assertLogs("dmd", "WARNING") as logs:
            path, backend = H.choose_path(accelerated, surface, H.OnceLog())
        self.assertEqual((path, backend), (H.PATH_SURFACE, "surface-backend"))
        self.assertEqual(self.events[-2:], ["destroy", "set_mode"])   # fermée avant le repli
        self.assertEqual(len(logs.output), 1)

    def failing_renderer(self, window):
        raise RuntimeError("Surface already associated with window")


class FrameDrawerTest(unittest.TestCase):
    """Mise à jour et présentation seulement après un changement d'image."""

    def setUp(self):
        self.backend = FakeBackend()
        self.drawer = H.FrameDrawer(self.backend)

    def test_present_only_after_a_change(self):
        self.assertTrue(self.drawer.show(numbered(1)))
        self.assertFalse(self.drawer.show(numbered(1)))
        self.assertFalse(self.drawer.show(numbered(1)))
        self.assertTrue(self.drawer.show(numbered(2)))
        self.assertEqual(self.backend.calls, [("upload", 1), ("present",),
                                              ("upload", 2), ("present",)])

    def test_static_image_repeated_every_second(self):
        for _ in range(30):
            self.drawer.show(numbered(4))
        self.assertEqual(self.backend.calls.count(("present",)), 1)

    def test_reset_redraws(self):
        self.drawer.show(numbered(3))
        self.drawer.reset()
        self.assertTrue(self.drawer.show(numbered(3)))
        self.assertEqual(self.backend.calls.count(("present",)), 2)

    def test_failed_upload_does_not_mark_the_frame_shown(self):
        class Failing(FakeBackend):
            def upload(self, frame):
                raise RuntimeError("texture perdue")
        drawer = H.FrameDrawer(Failing())
        with self.assertRaises(RuntimeError):
            drawer.show(numbered(5))
        drawer.backend = FakeBackend()
        self.assertTrue(drawer.show(numbered(5)))         # réessayée au prochain envoi


class FitTest(unittest.TestCase):
    def test_largest_integer_scale_centred(self):
        cases = {
            (1920, 1080): (15, 0, 300),         # 1920 x 480
            (1280, 720): (10, 0, 200),
            (1024, 600): (8, 0, 172),
            (1366, 768): (10, 43, 224),
            (3840, 2160): (30, 0, 600),
            (128, 32): (1, 0, 0),
            (300, 40): (1, 86, 4),
        }
        for size, expected in cases.items():
            with self.subTest(size=size):
                self.assertEqual(H.fit(*size), expected)

    def test_band_rectangle(self):
        self.assertEqual(H.band(1920, 1080), (0, 300, 1920, 480))
        self.assertEqual(H.band(1366, 768), (43, 224, 1280, 320))
        self.assertEqual(H.band(128, 32), (0, 0, 128, 32))
        self.assertIsNone(H.band(127, 32))

    def test_too_small_screen(self):
        for size in ((127, 32), (128, 31), (0, 0)):
            with self.subTest(size=size):
                self.assertIsNone(H.fit(*size))


class ChooseDisplayTest(unittest.TestCase):
    def test_found(self):
        self.assertEqual(H.choose_display(["HDMI-A-1", "HDMI-A-2"]), 1)
        self.assertEqual(H.choose_display(["HDMI-A-2", "HDMI-A-1"]), 0)
        self.assertEqual(H.choose_display(["Philips FTV (HDMI-A-1)", "Dell U2412M (HDMI-A-2)"]), 1)
        self.assertEqual(H.choose_display([None, "HDMI-A-2"]), 1)

    def test_not_found_never_hdmi_a_1(self):
        for names in ([], ["HDMI-A-1"], ["Philips FTV (HDMI-A-1)"], [None], [""],
                      ["HDMI-A-20"], ["XHDMI-A-2"], ["HDMI-A-2 bis"], ["(HDMI-A-2) avant"]):
            with self.subTest(names=names):
                self.assertIsNone(H.choose_display(names))

    def test_ambiguous(self):
        self.assertIsNone(H.choose_display(["HDMI-A-2", "Écran (HDMI-A-2)"]))

    def test_output_name(self):
        self.assertEqual(H.OUTPUT_NAME, "HDMI-A-2")


class ReadStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def status(self, text):
        path = os.path.join(self.tmp.name, "status")
        with open(path, "w", encoding="ascii") as f:
            f.write(text)
        return path

    def test_values(self):
        self.assertTrue(H.read_status(self.status("connected\n")))
        self.assertTrue(H.read_status(self.status("connected")))
        for text in ("disconnected\n", "unknown\n", "", "Connected\n"):
            with self.subTest(text=text):
                self.assertFalse(H.read_status(self.status(text)))

    def test_unreadable_is_unplugged(self):
        self.assertFalse(H.read_status(os.path.join(self.tmp.name, "absent")))
        self.assertFalse(H.read_status(self.tmp.name))        # un dossier

    def test_constants(self):
        self.assertEqual(H.STATUS_PATH, "/sys/class/drm/card1-HDMI-A-2/status")
        self.assertEqual(H.POLL_US, 5 * SEC)


class ConnectorTest(unittest.TestCase):
    def setUp(self):
        self.clk = Clock()
        self.plugged = False
        self.reads = []

        def read():
            self.reads.append(self.clk.t)
            return self.plugged
        self.conn = H.Connector(read=read, clock=self.clk)

    def test_first_check_reads_at_once(self):
        self.assertIs(self.conn.check(), False)
        self.assertEqual(self.reads, [self.clk.t])

    def test_read_every_five_seconds(self):
        start = self.clk.t
        results = []
        while self.clk.t < start + 16 * SEC:
            results.append(self.conn.check())
            self.clk.t += 100_000
        self.assertEqual(self.reads, [start, start + 5 * SEC, start + 10 * SEC, start + 15 * SEC])
        self.assertEqual([r for r in results if r is not None], [False] * 4)

    def test_plug_and_unplug_seen_at_next_read(self):
        start = self.clk.t
        self.conn.check()
        self.clk.t = start + 7 * SEC
        self.plugged = True
        with self.assertLogs("dmd", "INFO") as logs:
            self.assertIs(self.conn.check(), True)            # 7 s après la lecture : relu
        self.assertEqual(self.reads, [start, start + 7 * SEC])
        self.assertTrue(self.conn.plugged)
        self.assertIn("HDMI-A-2 connected", "\n".join(logs.output))
        self.clk.t += 1 * SEC
        self.plugged = False
        self.assertIsNone(self.conn.check())                  # 1 s : pas relu
        self.assertTrue(self.conn.plugged)
        self.clk.t += 4 * SEC
        with self.assertLogs("dmd", "INFO") as logs:
            self.assertIs(self.conn.check(), False)
        self.assertIn("HDMI-A-2 disconnected", "\n".join(logs.output))

    def test_change_logged_once(self):
        self.plugged = True
        with self.assertLogs("dmd", "INFO") as logs:
            for _ in range(4):
                self.conn.check()
                self.clk.t += 5 * SEC
        self.assertEqual(sum("connected" in line for line in logs.output), 1)


class PipeTest(unittest.TestCase):
    """FrameSender et FrameReader sur un vrai tube."""

    def setUp(self):
        self.r, self.w = os.pipe()
        self.addCleanup(self.close_all)
        self.sender = H.FrameSender(self.w)
        self.reader = H.FrameReader(self.r)

    def close_all(self):
        for fd in (self.r, self.w):
            try:
                os.close(fd)
            except OSError:
                pass

    def test_one_frame(self):
        self.assertIsNone(self.reader.read())
        self.sender.send(numbered(7))
        self.assertEqual(self.reader.read(), numbered(7))
        self.assertIsNone(self.reader.read())

    def test_sender_never_waits(self):
        start = time.monotonic()
        for n in range(500):                              # personne ne lit
            self.sender.send(numbered(n))
        self.assertLess(time.monotonic() - start, 1.0)
        self.assertGreater(self.sender.dropped, 0)

    def test_only_the_last_frame_counts(self):
        for n in range(1, 41):
            self.sender.send(numbered(n))
        first = self.reader.read()                        # ce que le tube contenait
        self.assertIsNotNone(first)
        self.assertEqual(len(set(first)), 1)              # flux aligné : une image entière
        last = first
        for _ in range(20):
            self.sender.flush()
            got = self.reader.read()
            if got is not None:
                self.assertEqual(len(set(got)), 1)
                last = got
        self.assertEqual(last, numbered(40))
        self.assertGreater(self.sender.dropped, 0)

    def test_reader_keeps_the_last_complete_frame(self):
        os.write(self.w, numbered(1) + numbered(2) + numbered(3)[:100])
        self.assertEqual(self.reader.read(), numbered(2))
        os.write(self.w, numbered(3)[100:])
        self.assertEqual(self.reader.read(), numbered(3))

    def test_partial_frame_waits(self):
        os.write(self.w, numbered(5)[:5000])
        self.assertIsNone(self.reader.read())
        os.write(self.w, numbered(5)[5000:])
        self.assertEqual(self.reader.read(), numbered(5))

    def test_end_of_pipe(self):
        os.write(self.w, numbered(9))
        os.close(self.w)
        self.assertEqual(self.reader.read(), numbered(9))
        with self.assertRaises(EOFError):
            self.reader.read()

    def test_broken_pipe_raised(self):
        os.close(self.r)
        with self.assertRaises(BrokenPipeError):
            self.sender.send(numbered(1))

    def test_wrong_length(self):
        with self.assertRaises(ValueError):
            self.sender.send(bytes(10))


class FakeProc:
    """Processus simulé : son entrée standard est un vrai tube."""

    def __init__(self):
        self.r, w = os.pipe()
        self.stdin = os.fdopen(w, "wb", buffering=0)
        self.returncode = None
        self.waited = []
        self.killed = False
        self.hang = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.waited.append(timeout)
        if self.hang:
            self.hang = False
            raise subprocess.TimeoutExpired("screen", timeout)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def frames(self):
        return H.FrameReader(self.r)

    def close(self):
        try:
            os.close(self.r)
        except OSError:
            pass


class HdmiOutputTest(unittest.TestCase):
    def setUp(self):
        self.clk = Clock()
        self.procs = []
        self.spawn_fails = False

        def spawn():
            if self.spawn_fails:
                raise OSError("python3 introuvable")
            proc = FakeProc()
            self.addCleanup(proc.close)
            self.procs.append(proc)
            return proc
        self.out = H.HdmiOutput(spawn=spawn, clock=self.clk)

    def test_start_and_frames(self):
        self.out.start()
        self.assertEqual(len(self.procs), 1)
        self.out.frame(numbered(3))
        self.assertEqual(self.procs[0].frames().read(), numbered(3))

    def test_wrong_length(self):
        self.out.start()
        with self.assertRaises(ValueError):
            self.out.frame(bytes(100))

    def test_dead_process_logged_once_and_restarted_after_30_s(self):
        self.out.start()
        self.procs[0].returncode = 1
        with self.assertLogs("dmd", "WARNING") as logs:
            for _ in range(10):
                self.out.frame(numbered(1))
                self.clk.t += 1 * SEC
        self.assertEqual(len(logs.output), 1)
        self.assertIn("HDMI screen process stopped (code 1)", logs.output[0])
        self.assertEqual(len(self.procs), 1)
        self.clk.t = 1_000_000 + 30 * SEC
        with self.assertLogs("dmd", "INFO"):
            self.out.frame(numbered(2))
        self.assertEqual(len(self.procs), 2)
        self.assertEqual(self.procs[1].frames().read(), numbered(2))

    def test_broken_pipe_does_not_stop_the_service(self):
        self.out.start()
        self.procs[0].close()                             # le processus a fermé son entrée
        with self.assertLogs("dmd", "WARNING"):
            self.out.frame(numbered(1))
            self.out.frame(numbered(2))
        self.assertEqual(len(self.procs), 1)

    def test_spawn_failure_logged_and_retried(self):
        self.spawn_fails = True
        with self.assertLogs("dmd", "WARNING") as logs:
            self.out.start()
            for _ in range(5):
                self.out.frame(numbered(1))
                self.clk.t += 1 * SEC
        self.assertEqual(len(logs.output), 1)
        self.assertIn("python3 introuvable", logs.output[0])
        self.spawn_fails = False
        self.clk.t = 1_000_000 + 30 * SEC
        self.out.frame(numbered(4))
        self.assertEqual(len(self.procs), 1)
        self.assertEqual(self.procs[0].frames().read(), numbered(4))

    def test_close(self):
        self.out.start()
        proc = self.procs[0]
        self.out.close()
        self.assertTrue(proc.stdin.closed)
        self.assertEqual(proc.waited, [H.CLOSE_TIMEOUT_S])
        self.assertFalse(proc.killed)
        self.out.close()                                  # deux fois : rien de plus
        self.out.frame(numbered(1))                       # après close : ignoré
        self.assertEqual(len(self.procs), 1)

    def test_close_kills_a_stuck_process(self):
        self.out.start()
        self.procs[0].hang = True
        self.out.close()
        self.assertTrue(self.procs[0].killed)

    def test_default_command_and_environment(self):
        command = H.screen_command()
        self.assertEqual(command[0], sys.executable)
        self.assertTrue(command[1].endswith(os.path.join("pi", "dmd", "dmd_hdmi_screen.py")))
        self.assertTrue(os.path.isfile(command[1]))
        env = H.screen_env({"PATH": "/usr/bin", "SDL_VIDEODRIVER": "x11"}, uid=1000)
        self.assertEqual(env, {"PATH": "/usr/bin", "SDL_VIDEODRIVER": "wayland",
                               "PYGAME_HIDE_SUPPORT_PROMPT": "1",
                               "XDG_RUNTIME_DIR": "/run/user/1000", "WAYLAND_DISPLAY": "wayland-0"})
        kept = {"XDG_RUNTIME_DIR": "/run/user/1001", "WAYLAND_DISPLAY": "wayland-1"}
        env = H.screen_env(dict(kept), uid=1000)
        self.assertEqual(env, dict(kept, SDL_VIDEODRIVER="wayland", PYGAME_HIDE_SUPPORT_PROMPT="1"))
        original = {"PATH": "/bin"}
        H.screen_env(original, uid=1000)
        self.assertEqual(original, {"PATH": "/bin"})       # non modifié


class ScreenModuleTest(unittest.TestCase):
    def test_imports_without_pygame(self):
        # pygame n'est importé qu'à l'ouverture de l'écran (ni le Mac ni la CI ne l'ont)
        code = ("import sys; sys.modules['pygame'] = None; sys.path[:0] = %r; "
                "import dmd_hdmi_screen; print('ok')" % ([os.path.dirname(H.SCREEN_SCRIPT)],))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             check=True)
        self.assertEqual(out.stdout.strip(), "ok")

    def test_screen_uses_the_core(self):
        path = H.screen_command()[1]
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for name in ("choose_display", "band", "MASKS", "DEPTH", "native_order_ok", "FrameDrawer",
                     "choose_path", "open_message", "FrameReader", "Connector"):
            self.assertIn("H.%s" % name, text)
        self.assertNotIn("rgb565_to_rgb", text)
        self.assertIn("display.update(", text)            # bande seule
        self.assertEqual(text.count("display.flip("), 1)  # une fois, à l'ouverture
        for call in ("H.open_accelerated(", "fullscreen_desktop=True",
                     "Renderer(", "Texture(", ".present(", "texture.update(", "texture.draw(",
                     "window.destroy("):
            self.assertIn(call, text)
        self.assertNotIn("from_display_module", text)     # le chemin accéléré n'a pas de set_mode
        self.assertEqual(text.count("set_mode("), 1)       # seulement dans le repli
        self.assertNotIn("HDMI-A-1", text)


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def toml(self, text):
        path = os.path.join(self.tmp.name, "dmd.toml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_default_off(self):
        self.assertEqual(K.DmdConfig().hdmi_output, 0)
        self.assertEqual(S.parse_args([]).hdmi_output, 0)

    def test_on(self):
        self.assertEqual(K.load(self.toml("hdmi_output = 1\n")).hdmi_output, 1)
        self.assertEqual(S.parse_args(["--config", self.toml("hdmi_output = 1\n")]).hdmi_output, 1)

    def test_refused_values(self):
        for line in ("hdmi_output = 2", "hdmi_output = -1", "hdmi_output = true",
                     'hdmi_output = "1"', "hdmi_output = 1.0"):
            with self.subTest(line=line):
                with self.assertRaises(ValueError):
                    K.load(self.toml(line + "\n"))

    def test_no_command_line_option(self):
        with self.assertRaises(SystemExit), unittest.mock.patch("sys.stderr"):
            S.parse_args(["--hdmi-output", "1"])


class FakeLink:
    def __init__(self, host, port):
        self.frames = []
        self.closed = False

    def publish_frame(self, frame):
        self.frames.append(frame)

    def drain(self, inputs):
        pass

    def close(self):
        self.closed = True


class FakeHdmi:
    made = []

    def __init__(self):
        self.frames = []
        self.started = False
        self.closed = False
        FakeHdmi.made.append(self)

    def start(self):
        self.started = True

    def frame(self, data):
        self.frames.append(data)

    def close(self):
        self.closed = True


class RunTest(unittest.TestCase):
    """dmd_service.run() : sortie HDMI à côté de WebView, jamais en rejeu."""

    def setUp(self):
        FakeHdmi.made = []
        self.links = []

    def link(self, host, port):
        link = FakeLink(host, port)
        self.links.append(link)
        return link

    def run_service(self, argv, hdmi_output):
        from test_dmd_service import FakeRenderer
        args = S.parse_args(argv + ["--web-port", "0"])
        args.hdmi_output = hdmi_output
        clk = Clock()

        def sleep(s):
            clk.t += max(1000, int(s * 1e6))
        with self.assertLogs("dmd", "INFO") as logs:
            S.run(args, FakeRenderer(), link_factory=self.link, hdmi_factory=FakeHdmi,
                  clock=clk, sleep=sleep, time_synced=lambda: False,
                  stop=lambda: clk.t >= 3 * SEC)
        return "\n".join(logs.output)

    def test_frames_copied_to_the_screen(self):
        text = self.run_service([], 1)
        self.assertEqual(len(FakeHdmi.made), 1)
        hdmi = FakeHdmi.made[0]
        self.assertTrue(hdmi.started)
        self.assertTrue(hdmi.closed)
        self.assertGreater(len(hdmi.frames), 0)
        self.assertEqual(hdmi.frames, self.links[0].frames)  # exactement les images publiées
        self.assertIn("DMD : HDMI screen output on (HDMI-A-2)", text)

    def test_off_by_default(self):
        self.run_service([], 0)
        self.assertEqual(FakeHdmi.made, [])

    def test_never_in_replay(self):
        text = self.run_service(["--replay", os.devnull], 1)
        self.assertEqual(FakeHdmi.made, [])
        self.assertEqual(self.links, [])
        self.assertIn("DMD : hdmi_output ignored in replay", text)


if __name__ == "__main__":
    unittest.main()
