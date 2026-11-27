# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests de la publication des images du DMD (§ 9.9, 46e et 48e compléments,
sous-phases SR-b et SR-c).

Cadence, sujets et fin de média avec un faux rendu (sans Pillow) ; MQTT par fake_mqtt.
L'essai de bout en bout (rendu Pillow -> MQTT -> hwd -> fausse 75 W) est
sauté si Pillow manque.
"""

import os
import time
import unittest

import _paths  # noqa: F401
import dmd_core as C
import dmd_proto as D
import dmd_service as S
import hwbus
import hwd_dmd
from fake_mqtt import FakeBroker, make_module

try:
    import dmd_render
except ImportError:                         # Pillow absent
    dmd_render = None

FRAME = 1000000 // D.TARGET_FPS


class Clock:
    def __init__(self, t=5_000_000):
        self.t = t

    def __call__(self):
        return self.t


class FakeScene:
    def __init__(self, name, animated_frames=0):
        self.name = name
        self.left = animated_frames
        self.count = 0
        self.closed = False

    @property
    def animated(self):
        return self.left > 0

    def next_frame(self, now_us=None):
        self.count += 1
        if self.left:
            self.left -= 1
        return bytes([len(self.name) & 0xFF]) * D.PIXELS_BYTES

    def close(self):
        self.closed = True


class FakeMediaScene(FakeScene):
    """Média qui dure duration_us depuis sa première image publiée (comme MediaScene)."""

    def __init__(self, name, token, duration_us):
        super().__init__(name)
        self.token = token
        self.duration_us = duration_us
        self.start = None
        self.done_calls = 0

    @property
    def animated(self):
        return True

    def is_done(self, now_us):
        self.done_calls += 1
        return self.start is not None and now_us - self.start >= self.duration_us

    def next_frame(self, now_us=None):
        if self.start is None:
            self.start = now_us
        return super().next_frame(now_us)


class FakeRenderer:
    def __init__(self, scroll_frames=30, media_us=2_000_000):
        self.scenes = []
        self.scroll_frames = scroll_frames
        self.media_us = media_us

    def scene(self, command):
        name = type(command).__name__
        if isinstance(command, C.ShowText):
            name += ":" + command.text
        if isinstance(command, C.ShowMedia):
            s = FakeMediaScene(name + ":" + (command.name or command.folder), command.token, self.media_us)
            self.scenes.append(s)
            return s
        s = FakeScene(name, self.scroll_frames if getattr(command, "scroll", None) else 0)
        self.scenes.append(s)
        return s


class Rig:
    def __init__(self, scroll_frames=30, gifs=(), media_us=2_000_000):
        self.clk = Clock()
        self.core = C.DmdCore(clock=self.clk, gifs=gifs)
        self.renderer = FakeRenderer(scroll_frames, media_us)
        self.out = []                       # (instant, image)
        self.service = S.DmdService(self.core, self.renderer,
                                    lambda f: self.out.append((self.clk.t, f)), self.clk)

    def run(self, us, step_us=1000):
        end = self.clk.t + us
        while self.clk.t < end:
            self.service.step()
            self.clk.t += step_us

    def times(self):
        return [t for t, _ in self.out]


class TickingClock:
    """Horloge qui avance d'1 µs à chaque lecture, comme une vraie : deux lectures dans le
    même calcul ne rendent jamais la même valeur (ce que l'horloge figée Clock masque)."""

    def __init__(self, t=5_000_000):
        self.t = t
        self.reads = 0

    def __call__(self):
        self.t += 1
        self.reads += 1
        return self.t


class TickingClockTest(unittest.TestCase):
    def make(self, scroll_frames=0):
        self.clk = TickingClock()
        self.core = C.DmdCore(clock=self.clk)
        self.renderer = FakeRenderer(scroll_frames)
        self.out = []
        self.service = S.DmdService(self.core, self.renderer,
                                    lambda f: self.out.append(self.clk.t), self.clk)
        self.service.start()

    def run_for(self, us, step_us=1000):
        end = self.clk.t + us
        while self.clk.t < end:
            self.service.step()
            self.clk.t += step_us

    def test_first_frame_at_first_step(self):
        self.make()
        self.service.step()
        self.assertEqual(len(self.out), 1)

    def test_animation_at_most_15_fps(self):
        self.make(scroll_frames=30)
        self.service.step()
        self.service.message(C.TOPIC_DMD, "msgmove|Bravo|left")   # 30 images animées
        self.out.clear()
        self.run_for(2_000_000, step_us=500)
        gaps = [b - a for a, b in zip(self.out, self.out[1:])]
        self.assertGreaterEqual(min(gaps), S.FRAME_PERIOD_US)
        self.assertGreaterEqual(len(self.out), 28)                 # environ 15 images/s
        self.assertLessEqual(len(self.out), 31)

    def test_static_repeated_every_second(self):
        self.make()
        self.run_for(3_500_000)
        gaps = [b - a for a, b in zip(self.out, self.out[1:])]
        self.assertEqual(len(self.out), 4)
        self.assertTrue(all(S.STATIC_REPEAT_US <= g < S.STATIC_REPEAT_US + 2000 for g in gaps))

    def test_wait_never_negative(self):
        self.make()
        for _ in range(100):
            wait = self.service.step()
            self.assertGreaterEqual(wait, 0)
            self.assertLessEqual(wait, S.STATIC_REPEAT_US)
            self.clk.t += max(wait, 1)
        self.assertGreater(len(self.out), 1)


class ConstantsTest(unittest.TestCase):
    def test_same_topic_as_hwd(self):
        self.assertEqual(S.TOPIC_FRAME, hwbus.topics()["dmd_frame"])

    def test_same_rate_as_hwd(self):
        self.assertEqual(S.FRAME_PERIOD_US, hwd_dmd.IMAGE_PERIOD_US)
        self.assertEqual(D.TARGET_FPS, 15)

    def test_static_repeat_below_stale(self):
        from hwd_config import HwdConfig
        self.assertLess(S.STATIC_REPEAT_US, 1500 * 1000)         # plus petit dmd_stale_ms permis
        self.assertLess(S.STATIC_REPEAT_US, HwdConfig().dmd_stale_ms * 1000)

    def test_service_without_pillow(self):
        # Le cœur et la publication n'importent pas Pillow (§ 9.9)
        import subprocess
        import sys
        code = ("import sys; sys.path[:0] = %r; import dmd_core, dmd_service; "
                "assert 'PIL' not in sys.modules, 'PIL importé'" % (sys.path,))
        subprocess.run([sys.executable, "-c", code], check=True)


class PublishTest(unittest.TestCase):
    def test_welcome_published_at_once(self):
        rig = Rig()
        rig.service.start()
        rig.service.step()
        self.assertEqual(len(rig.out), 1)
        self.assertEqual(rig.renderer.scenes[0].name, "Welcome")
        self.assertEqual(len(rig.out[0][1]), D.PIXELS_BYTES)

    def test_nothing_before_start(self):
        rig = Rig()
        self.assertEqual(rig.service.step(), S.FRAME_PERIOD_US)
        self.assertEqual(rig.out, [])

    def test_static_repeated_every_second(self):
        rig = Rig()
        rig.service.start()
        rig.run(3_500_000)
        t = rig.times()
        self.assertEqual(len(t), 4)
        self.assertEqual([b - a for a, b in zip(t, t[1:])], [S.STATIC_REPEAT_US] * 3)

    def test_change_published_at_next_slot(self):
        rig = Rig()
        rig.service.start()
        rig.service.step()
        t0 = rig.clk.t
        rig.clk.t += 10_000
        rig.service.message(C.TOPIC_DMD, "msg|Bravo")
        wait = rig.service.step()
        self.assertEqual(len(rig.out), 1)                    # trop tôt
        self.assertEqual(wait, FRAME - 10_000)
        rig.clk.t = t0 + FRAME
        rig.service.step()
        self.assertEqual(len(rig.out), 2)
        self.assertEqual(rig.renderer.scenes[-1].name, "ShowText:Bravo")

    def test_change_after_quiet_published_at_once(self):
        rig = Rig()
        rig.service.start()
        rig.service.step()
        rig.clk.t += 500_000
        rig.service.message(C.TOPIC_DMD, "msg|Bravo")
        rig.service.step()
        self.assertEqual(rig.times()[-1] - rig.times()[0], 500_000)

    def test_animation_at_most_15_per_second(self):
        rig = Rig(scroll_frames=1000)
        rig.service.start()
        rig.service.message(C.TOPIC_DMD, "msgmove|Un long texte|left")
        rig.run(2_000_000, step_us=500)
        t = rig.times()
        gaps = [b - a for a, b in zip(t, t[1:])]
        self.assertTrue(all(g >= FRAME for g in gaps))
        self.assertTrue(all(g <= FRAME + 500 for g in gaps))
        self.assertIn(len(t), (30, 31))

    def test_animation_then_static(self):
        rig = Rig(scroll_frames=5)
        rig.service.start()
        rig.service.message(C.TOPIC_DMD, "msgmove|Un long texte|left")
        rig.run(3_000_000)
        t = rig.times()
        gaps = [b - a for a, b in zip(t, t[1:])]
        self.assertTrue(all(abs(g - FRAME) <= 1000 for g in gaps[:4]))
        self.assertTrue(all(g == S.STATIC_REPEAT_US for g in gaps[4:]))
        self.assertEqual(rig.renderer.scenes[-1].count, len(t))

    def test_ignored_message_changes_nothing(self):
        rig = Rig()
        rig.service.start()
        rig.service.step()
        rig.clk.t += 100_000
        with self.assertLogs("dmd", "INFO"):
            rig.service.message(C.TOPIC_DMD, "zap501|x")
        rig.service.step()
        self.assertEqual(len(rig.renderer.scenes), 1)
        self.assertEqual(len(rig.out), 1)

    def test_attract_after_delay(self):
        rig = Rig()
        rig.service.start()
        rig.run(C.ATTRACT_DELAY_S * 1_000_000 + 10_000)
        self.assertEqual(rig.renderer.scenes[-1].name, "Welcome")  # sans GIF : accueil
        self.assertEqual(len(rig.renderer.scenes), 2)

    def test_bad_frame_length(self):
        rig = Rig()
        rig.service.start()
        rig.renderer.scenes[0].next_frame = lambda now_us=None: b"\x00" * 10
        with self.assertRaises(ValueError):
            rig.service.step()


class MediaEndTest(unittest.TestCase):
    """Fin de média relayée au cœur par core.media_done(jeton) (48e complément)."""

    def attract(self, **kw):
        rig = Rig(gifs=("a.gif", "b.gif", "c.gif"), **kw)
        rig.service.start()
        rig.run(C.ATTRACT_DELAY_S * 1_000_000 + 1_000)
        self.assertEqual(rig.core.state, C.ATTRACT_GIF)
        self.assertIsInstance(rig.renderer.scenes[-1], FakeMediaScene)
        return rig

    def medias(self, rig):
        return [s for s in rig.renderer.scenes if isinstance(s, FakeMediaScene)]

    def test_next_gif_after_media_end(self):
        rig = self.attract()
        first = rig.renderer.scenes[-1]
        rig.run(2_000_000 - 1_000)
        self.assertIs(rig.renderer.scenes[-1], first)          # pas encore fini
        rig.run(2_000)
        self.assertEqual(len(self.medias(rig)), 2)
        self.assertNotEqual(self.medias(rig)[1].name, first.name)
        self.assertTrue(first.closed)                          # fichier libéré

    def test_each_media_reported_once(self):
        rig = self.attract()
        rig.run(20_000_000)
        # 2 s par GIF, sans heure (non synchronisée) : un GIF toutes les 2 s environ
        n = len(self.medias(rig))
        self.assertIn(n, (10, 11))
        self.assertEqual(rig.core._played, n - 1)

    def test_stale_token_ignored(self):
        rig = self.attract()
        scene = rig.renderer.scenes[-1]
        rig.service.message(C.TOPIC_DMD, "msg|Bravo")           # le jeu interrompt le média
        self.assertEqual(rig.core.state, C.DISPLAY)
        self.assertTrue(scene.closed)
        rig.run(3_000_000)
        self.assertEqual(rig.renderer.scenes[-1].name, "ShowText:Bravo")
        self.assertEqual(len(self.medias(rig)), 1)

    def test_media_published_at_15_fps(self):
        rig = self.attract(media_us=10_000_000)
        n = len(rig.out)
        rig.run(2_000_000, step_us=500)
        self.assertIn(len(rig.out) - n, (29, 30, 31))   # pas de 500 µs : écart de 66 667 à 67 000 µs

    def test_failed_media_skipped_at_once(self):
        rig = self.attract(media_us=0)                          # fini dès sa première image
        rig.run(FRAME * 3)
        self.assertGreaterEqual(len(self.medias(rig)), 3)

    def test_clock_after_two_minutes(self):
        # 50e complément : heure 120 s après l'entrée en Attract Mode, à la fin du GIF en cours
        # (au lieu de toutes les 10 GIF, 48e complément) ; horloge qui avance par pas de 1 ms
        rig = Rig(gifs=("a.gif", "b.gif"), media_us=7_000_000)
        rig.core.time_synced = lambda: True
        rig.service.start()
        rig.run(C.ATTRACT_DELAY_S * 1_000_000 + 119_000_000)
        self.assertEqual(rig.core.state, C.ATTRACT_GIF)
        self.assertNotIn("ShowClock", [s.name for s in rig.renderer.scenes])
        rig.run(8_000_000)                                     # 18e GIF fini vers 126 s
        self.assertEqual(rig.core.state, C.ATTRACT_CLOCK)
        self.assertEqual(rig.renderer.scenes[-1].name, "ShowClock")
        self.assertEqual(len(self.medias(rig)), 18)
        rig.run(10_200_000)                                    # 10 s d'heure, puis un GIF
        self.assertEqual(rig.core.state, C.ATTRACT_GIF)


class ScoreTest(unittest.TestCase):
    """Score : le média une fois, puis le texte fixe (48e complément), avec un faux rendu."""

    def score(self, payload="score|T20 - X - X||255;0;0|O"):
        rig = Rig(media_us=1_000_000)
        rig.service.start()
        rig.run(100_000)
        rig.service.message(C.TOPIC_DMD, payload)
        return rig

    def names(self, rig):
        return [s.name for s in rig.renderer.scenes]

    def test_media_then_fixed_text(self):
        rig = self.score()
        rig.run(1_500_000)
        self.assertEqual(self.names(rig), ["Welcome", "ShowMedia:Scores/T20", "ShowText:T20 - X - X"])
        self.assertTrue(rig.renderer.scenes[1].closed)
        rig.run(5_000_000)
        self.assertEqual(len(rig.renderer.scenes), 3)                # texte gardé, fixe

    def test_game_text_waits_for_media(self):
        # 49e complément : le texte attend la fin du média (1 s), puis remplace le texte du score
        rig = self.score()
        rig.run(300_000)
        rig.service.message(C.TOPIC_DMD, "msg|Hop")
        rig.run(600_000)
        self.assertEqual(self.names(rig)[1:], ["ShowMedia:Scores/T20"])
        rig.run(2_000_000)
        self.assertEqual(self.names(rig)[1:], ["ShowMedia:Scores/T20", "ShowText:Hop"])

    def test_ignored_message_does_not_interrupt(self):
        rig = self.score()
        rig.run(300_000)
        with self.assertLogs("dmd", "INFO"):
            rig.service.message(C.TOPIC_DMD, "zap501|x")
        rig.run(1_500_000)
        self.assertEqual(self.names(rig)[1:], ["ShowMedia:Scores/T20", "ShowText:T20 - X - X"])

    def test_score_without_gif_text_only(self):
        rig = self.score("score|T20 - X - X||255;0;0|N")
        rig.run(1_500_000)
        self.assertEqual(self.names(rig)[1:], ["ShowText:T20 - X - X"])


class SpecialMovesServiceTest(unittest.TestCase):
    """Coups spéciaux et textes en attente à travers le service, faux rendu et horloge qui
    avance (49e complément)."""

    def rig(self, media_us=3_000_000, **core_kw):
        rig = Rig(media_us=media_us)
        rig.core = C.DmdCore(clock=rig.clk, gifs=("a.gif",), **core_kw)
        rig.service = S.DmdService(rig.core, rig.renderer,
                                   lambda f: rig.out.append((rig.clk.t, f)), rig.clk)
        rig.service.start()
        rig.run(100_000)
        return rig

    def names(self, rig):
        return [s.name for s in rig.renderer.scenes]

    def test_special_then_last_text(self):
        rig = self.rig()
        rig.service.message(C.TOPIC_DMD, "score|T20 - T20 - T20||255;0;0|O")
        rig.run(200_000)
        rig.service.message(C.TOPIC_DMD, "msg|Lâchez les fléchettes")      # comme le jeu, aussitôt
        rig.run(1_600_000)
        rig.service.message(C.TOPIC_DMD, "msg|Joueur 2")                    # 1,8 s après le score
        rig.run(1_000_000)
        self.assertEqual(self.names(rig)[1:], ["ShowMedia:SpecialsMoves/MAXIMUM_TON_80"])
        rig.run(500_000)                                                    # média de 3 s fini
        self.assertEqual(self.names(rig)[1:], ["ShowMedia:SpecialsMoves/MAXIMUM_TON_80",
                                               "ShowText:Joueur 2"])
        self.assertTrue(rig.renderer.scenes[1].closed)

    def test_attract_delay_from_pending_text(self):
        rig = self.rig()
        rig.service.message(C.TOPIC_DMD, "score|S6 - S6 - S6|||O")
        self.assertEqual(rig.renderer.scenes[-1].name, "ShowMedia:devil 2.gif")
        rig.run(2_000_000)
        rig.service.message(C.TOPIC_DMD, "msg|Hop")
        rig.run(C.ATTRACT_DELAY_S * 1_000_000 - 1_000)
        self.assertEqual(rig.core.state, C.DISPLAY)
        self.assertEqual(self.names(rig)[-1], "ShowText:Hop")
        rig.run(2_000)
        self.assertEqual(rig.core.state, C.ATTRACT_GIF)

    def test_new_score_interrupts(self):
        rig = self.rig()
        rig.service.message(C.TOPIC_DMD, "score|T20 - T20 - T20|||O")
        rig.run(500_000)
        rig.service.message(C.TOPIC_DMD, "msg|Hop")
        rig.service.message(C.TOPIC_DMD, "score|X - X - X|||O")
        rig.run(4_000_000)
        self.assertEqual(self.names(rig)[1:], ["ShowMedia:SpecialsMoves/MAXIMUM_TON_80",
                                               "ShowText:X - X - X"])

    def test_switch_off(self):
        rig = self.rig(special_moves=False)
        rig.service.message(C.TOPIC_DMD, "score|T20 - T20 - T20|||O")
        self.assertEqual(self.names(rig)[-1], "ShowMedia:Scores/T20")

    def test_option(self):
        self.assertFalse(S.parse_args([]).no_special_moves)
        self.assertTrue(S.parse_args(["--no-special-moves"]).no_special_moves)

    def test_run_wires_option_and_cache(self):
        clk = Clock(1_000_000)
        renderer = FakeRenderer()
        asked = []

        def has_media(folder, name=None):
            asked.append((folder, name))
            return folder == "Scores/T20"
        renderer.has_media = has_media

        def sleep(s):
            clk.t += max(1000, int(s * 1e6))
        for argv, special in (([], True), (["--no-special-moves"], False)):
            args = S.parse_args(["--replay", os.devnull, "--web-port", "0"] + argv)
            service = S.run(args, renderer, clock=clk, sleep=sleep, time_synced=lambda: False,
                            stop=lambda: True)
            self.assertIs(service.core.special_moves, special)
            del asked[:]
            out = service.core.message(C.TOPIC_DMD, "score|T20 - T20 - T20|||O")
            self.assertEqual(out[0].folder, "Scores/T20")                  # dossier vide : ordinaire
            self.assertEqual(bool([a for a in asked if a[0].startswith("SpecialsMoves")]), special)


class MqttLinkTest(unittest.TestCase):
    def setUp(self):
        self.broker = FakeBroker()
        self.mqtt = make_module(self.broker)
        self.link = S.MqttLink(mqtt_module=self.mqtt)
        self.game = self.mqtt.Client(self.mqtt.CallbackAPIVersion.VERSION2, "game")
        self.game.connect_async("127.0.0.1")
        self.hwd = self.mqtt.Client(self.mqtt.CallbackAPIVersion.VERSION2, "hwd")
        self.hwd.connect_async("127.0.0.1")
        self.hwd.subscribe(S.TOPIC_FRAME)

    def test_nothing_published_before_connect(self):
        self.link.publish_frame(bytes(D.PIXELS_BYTES))
        self.assertEqual(self.broker.log, [])

    def test_subscribes_both_topics_and_publishes(self):
        self.broker.pump()
        self.assertTrue(self.link.connected)
        self.assertEqual(sorted(self.link.client.subscriptions), sorted(C.TOPICS))
        rig = Rig()
        rig.service.publish = self.link.publish_frame
        rig.service.start()
        self.game.publish(C.TOPIC_DMD, "msg|Bravo")
        self.game.publish(C.TOPIC_RASPY2DMD, "pico|test_rgb")
        self.game.publish("raspydarts/other", "msg|non")
        self.broker.pump()
        self.link.drain(rig.service)
        rig.service.step()
        names = [s.name for s in rig.renderer.scenes]
        self.assertEqual(names, ["Welcome", "ShowText:Bravo", "ShowPattern"])
        frames = [(t, p) for t, p in self.broker.log if t == S.TOPIC_FRAME]
        self.assertEqual(len(frames), 1)
        self.assertEqual(len(frames[0][1]), D.PIXELS_BYTES)
        publish = [c for c in self.link.client.calls if c[0] == "publish"][0]
        self.assertEqual(publish[3:], (0, False))           # QoS 0, non retenue


class AttractGifsTest(unittest.TestCase):
    """Liste des GIF de Gifs/600 Gratos (SR-c commit 4, 48e complément)."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = os.path.join(self.tmp.name, C.ATTRACT_FOLDER)

    def touch(self, name):
        with open(os.path.join(self.folder, name), "wb") as f:
            f.write(b"GIF89a")

    def test_filters(self):
        os.makedirs(self.folder)
        for name in ("b.gif", "A.GIF", ".cache.gif", "._b.gif", "c.png", "d.gif.txt"):
            self.touch(name)
        os.mkdir(os.path.join(self.folder, "dossier.gif"))
        self.assertEqual(S.AttractGifs(self.tmp.name)(), ["A.GIF", "b.gif"])

    def test_read_again_each_call(self):
        os.makedirs(self.folder)
        gifs = S.AttractGifs(self.tmp.name)
        self.assertEqual(gifs(), [])
        self.touch("x.gif")
        self.assertEqual(gifs(), ["x.gif"])

    def test_missing_folder_logged_once(self):
        gifs = S.AttractGifs(self.tmp.name)
        with self.assertLogs("dmd", "WARNING") as logs:
            self.assertEqual(gifs(), [])
            self.assertEqual(gifs(), [])
        self.assertEqual(len(logs.records), 1)
        os.makedirs(self.folder)                            # créé après le démarrage
        self.touch("y.gif")
        self.assertEqual(gifs(), ["y.gif"])


class BadMediaRenderer(FakeRenderer):
    """Renderer qui déclare corrompu (bad_media) tout GIF dont le nom commence par « bad »."""

    def __init__(self, media_dir):
        super().__init__(media_us=1_000_000)
        self.media_dir = media_dir
        self.bad_media = set()

    def scene(self, command):
        s = super().scene(command)
        if isinstance(command, C.ShowMedia) and command.name and command.name.startswith("bad"):
            self.bad_media.add(os.path.join(self.media_dir, command.folder, command.name))
            s.duration_us = 0
        return s


class ExcludeBadMediaTest(unittest.TestCase):
    def test_corrupt_gif_leaves_the_bag_for_good(self):
        clk = Clock()
        names = ["bad.gif", "good1.gif", "good2.gif"]
        core = C.DmdCore(clock=clk, gif_source=lambda: list(names))
        renderer = BadMediaRenderer("/medias")
        renderer.bad_media.add("/medias/Scores/S1/bad.gif")  # autre dossier : sans effet
        service = S.DmdService(core, renderer, lambda f: None, clk)
        service.start()
        service.message(C.TOPIC_RASPY2DMD, "demo|gif")
        while clk.t < 5_000_000 + 30_000_000:
            service.step()
            clk.t += 10_000
        played = [s.name for s in renderer.scenes if s.name.startswith("ShowMedia")]
        self.assertGreater(len(played), 20)
        self.assertLessEqual(played.count("ShowMedia:bad.gif"), 1)
        self.assertIn("bad.gif", core._excluded)
        self.assertEqual(core._excluded, {"bad.gif"})


class NtpTest(unittest.TestCase):
    """Lecture de NTPSynchronized, toujours simulée : le vrai timedatectl n'est jamais lancé."""

    def setUp(self):
        import unittest.mock
        patcher = unittest.mock.patch.object(S.subprocess, "run", side_effect=AssertionError("vrai timedatectl"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_run(self, **result):
        import types
        import unittest.mock
        calls = []

        def run(cmd, **kw):
            calls.append((cmd, kw))
            if "error" in result:
                raise result["error"]
            return types.SimpleNamespace(returncode=result.get("code", 0), stdout=result.get("out", ""))
        patcher = unittest.mock.patch.object(S.subprocess, "run", run)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_yes(self):
        calls = self.fake_run(out="yes\n")
        self.assertTrue(S.read_ntp_synchronized())
        cmd, kw = calls[0]
        self.assertEqual(list(cmd), ["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
        self.assertEqual(kw["timeout"], 2)

    def test_failures_mean_not_synced(self):
        import subprocess
        for result in ({"out": "no\n"}, {"out": "yes\n", "code": 1}, {"out": ""},
                       {"error": FileNotFoundError("timedatectl")},
                       {"error": subprocess.TimeoutExpired("timedatectl", 2)},
                       {"error": PermissionError("refusé")}):
            with self.subTest(result=result):
                self.fake_run(**result)
                self.assertFalse(S.read_ntp_synchronized())

    def make(self, values):
        self.clk = Clock(0)
        self.pending = []
        self.values = list(values)

        def reader():
            value = self.values.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        return S.NtpStatus(reader=reader, clock=self.clk, spawn=self.pending.append)

    def finish(self):
        while self.pending:
            self.pending.pop(0)()

    def test_never_blocks_and_false_before_first_read(self):
        ntp = self.make([True])
        self.assertFalse(ntp())                              # lecture lancée, pas finie
        self.assertEqual(len(self.pending), 1)
        self.assertFalse(ntp())                              # pas de seconde lecture en cours
        self.assertEqual(len(self.pending), 1)
        self.finish()
        self.assertTrue(ntp())

    def test_read_at_most_every_60_s(self):
        ntp = self.make([True, False])
        ntp.refresh()
        self.finish()
        self.clk.t += 59_999_999
        self.assertTrue(ntp())
        self.assertEqual(self.pending, [])
        self.clk.t += 1
        self.assertTrue(ntp())                               # ancienne valeur, nouvelle lecture lancée
        self.finish()
        self.assertFalse(ntp())
        self.assertEqual(ntp.reads, 2)

    def test_reader_exception_means_not_synced(self):
        ntp = self.make([True, RuntimeError("panne")])
        ntp.refresh()
        self.finish()
        self.clk.t += S.NTP_PERIOD_US
        ntp()
        self.finish()
        self.assertFalse(ntp())

    def test_real_thread(self):
        import threading
        done = threading.Event()

        def reader():
            done.set()
            return True
        ntp = S.NtpStatus(reader=reader)
        ntp.refresh()
        self.assertTrue(done.wait(5))
        for _ in range(500):
            if ntp.reads:
                break
            time.sleep(0.01)
        self.assertTrue(ntp())

    def test_run_default_uses_ntp_thread(self):
        # run() sans time_synced : NtpStatus, première lecture lancée au démarrage (simulée)
        calls = self.fake_run(out="yes")
        args = S.parse_args(["--replay", os.devnull, "--web-port", "0"])
        clk = Clock(1_000_000)

        def sleep(s):
            clk.t += max(1000, int(s * 1e6))
        service = S.run(args, FakeRenderer(), clock=clk, sleep=sleep, stop=lambda: clk.t >= 1_100_000)
        for _ in range(500):
            if calls and service.core.time_synced.reads:
                break
            time.sleep(0.01)
        self.assertEqual(len(calls), 1)
        self.assertIsInstance(service.core.time_synced, S.NtpStatus)
        self.assertTrue(service.core.time_synced.value)


class RunAttractTest(unittest.TestCase):
    """run() branche l'Attract Mode sur --media-dir et l'heure sur time_synced."""

    def test_gifs_from_media_dir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as media:
            folder = os.path.join(media, C.ATTRACT_FOLDER)
            os.makedirs(folder)
            for name in ("un.gif", "deux.GIF", ".cache.gif"):
                open(os.path.join(folder, name), "wb").close()
            args = S.parse_args(["--replay", os.devnull, "--web-port", "0", "--media-dir", media])
            args.attract_clock_every_s = 10         # comme attract_clock_every_s = 10 dans dmd.toml
            clk = Clock(1_000_000)
            renderer = FakeRenderer(media_us=1_000_000)
            synced = []

            def time_synced():
                synced.append(clk.t)
                return True

            def sleep(s):
                clk.t += max(1000, int(s * 1e6))
            service = S.run(args, renderer, clock=clk, sleep=sleep, time_synced=time_synced,
                            stop=lambda: clk.t >= 1_000_000 + (C.ATTRACT_DELAY_S + 11) * 1_000_000)
        names = [s.name for s in renderer.scenes]
        self.assertEqual(names[0], "Welcome")
        gifs = [n for n in names if n.startswith("ShowMedia")]
        self.assertEqual(len(gifs), 10)
        self.assertEqual(set(gifs), {"ShowMedia:un.gif", "ShowMedia:deux.GIF"})
        self.assertEqual(names[-1], "ShowClock")
        self.assertEqual(len(synced), 1)
        self.assertIs(service.core.time_synced, time_synced)

    def test_missing_media_dir_keeps_welcome(self):
        import tempfile
        with tempfile.TemporaryDirectory() as media:
            args = S.parse_args(["--replay", os.devnull, "--web-port", "0",
                                 "--media-dir", os.path.join(media, "absent")])
            clk = Clock(1_000_000)
            renderer = FakeRenderer()

            def sleep(s):
                clk.t += max(1000, int(s * 1e6))
            with self.assertLogs("dmd", "WARNING") as logs:
                S.run(args, renderer, clock=clk, sleep=sleep, time_synced=lambda: False,
                      stop=lambda: clk.t >= 1_000_000 + 3 * C.ATTRACT_DELAY_S * 1_000_000 + 1)
        self.assertEqual({s.name for s in renderer.scenes}, {"Welcome"})
        self.assertGreaterEqual(len(renderer.scenes), 3)
        folder_logs = [r for r in logs.records if "Attract Mode folder" in r.getMessage()]
        self.assertEqual(len(folder_logs), 1)


@unittest.skipIf(dmd_render is None, "Pillow absent : essai de bout en bout sauté")
class EndToEndTest(unittest.TestCase):
    """Rendu Pillow -> fake_mqtt -> hwd (cœur complet) -> fausse 75 W."""

    def test_text_reaches_the_board(self):
        from test_hwd_dmd import Rig as HwdRig
        hw = HwdRig(self)
        hw.connect()
        broker = FakeBroker()
        mqtt = make_module(broker)
        link = S.MqttLink(mqtt_module=mqtt)
        bridge = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "hwd")
        bridge.on_message = lambda c, u, m: hw.publish(m.payload)
        bridge.connect_async("127.0.0.1")
        bridge.subscribe(S.TOPIC_FRAME)
        broker.pump()
        import tempfile
        with tempfile.TemporaryDirectory() as media, self.assertLogs("dmd", "WARNING"):
            renderer = dmd_render.Renderer(media)
        sent = []
        core = C.DmdCore(clock=hw.clk)
        service = S.DmdService(core, renderer, lambda f: (sent.append(f), link.publish_frame(f)), hw.clk)
        service.start()
        game = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "game")
        game.connect_async("127.0.0.1")
        broker.pump()
        game.publish(C.TOPIC_DMD, "msgcolor|Bravo|255;0;0|0;0;0||")
        for _ in range(100):                                 # 1 s, pas de 10 ms
            broker.pump()
            link.drain(service)
            service.step()
            broker.pump()
            hw.advance(10)
        self.assertGreaterEqual(len(sent), 1)
        self.assertEqual(bytes(hw.card.framebuffer), sent[-1])
        self.assertEqual(sent[-1], renderer.fixed_text("Bravo", (255, 0, 0), (0, 0, 0)).next_frame())


if __name__ == "__main__":
    unittest.main()
