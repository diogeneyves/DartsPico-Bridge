# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests du DMD dans hwd (§ 6.4, § 9.5, 30e et 32e compléments) contre la fausse 75 W.

En mémoire, horloge simulée commune ; un test de démon passe par un vrai
pseudo-terminal. Aucun matériel.
"""

import json
import os
import tempfile
import time
import unittest

import _paths  # noqa: F401
import dmd_proto as D
import hwbus as H
from fake_i75w import FakeI75W
from hwd_config import HwdConfig, load
from hwd_core import HwdCore
from hwd_dmd import ABSENT, CONNECTED, HANDSHAKE

T = H.topics()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Clock:
    def __init__(self, t=10_000_000):
        self.t = t

    def __call__(self):
        return self.t


def image(color=0, tiles=()):
    """Image unie, avec des tuiles d'une autre couleur (color + 1 + indice)."""
    img = bytearray(color.to_bytes(2, "little") * (D.WIDTH * D.HEIGHT))
    for i in tiles:
        D.put_tile(img, i, ((color + 1 + i) & 0xFFFF).to_bytes(2, "little") * D.TILE_PIXELS)
    return bytes(img)


def noise(seed):
    return bytes((seed * 7 + k * 13 + (k * k >> 5)) & 0xFF for k in range(D.PIXELS_BYTES))


class Rig:
    """hwd (cœur complet) branché sur une fausse 75 W ; pump() fait circuler les trames."""

    def __init__(self, testcase, **cfg):
        self.tc = testcase
        self.clk = Clock()
        self.card = FakeI75W(clock=self.clk)
        self.core = HwdCore(HwdConfig(**cfg).validate(), self.clk)
        self.dmd = self.core.dmd
        self.linked = True
        self.sent = []                     # (type, données) reçus par la carte
        self.pub = []
        self._dec = D.Decoder()

    def pump(self, rounds=200):
        for _ in range(rounds):
            moved = False
            out = self.card.tick()
            if out and self.linked:
                self.dmd.serial_data(out)
                moved = True
            self.core.tick()
            frame = self.dmd.take_serial()
            while frame is not None:
                moved = True
                self.sent += self._dec.feed(frame)
                if self.linked:
                    reply = self.card.feed(frame)
                    if reply:
                        self.dmd.serial_data(reply)
                frame = self.dmd.take_serial()
            self.pub += self.core.take_publishes()
            if not moved:
                return
        self.tc.fail("le système ne se calme pas")

    def advance(self, ms, step_ms=10):
        for _ in range(ms // step_ms):
            self.clk.t += step_ms * 1000
            self.pump()

    def connect(self):
        self.dmd.serial_opened()
        self.pump()
        self.tc.assertEqual(self.dmd.state, CONNECTED)

    def publish(self, img):
        self.core.bus_message(T["dmd_frame"], img)

    def types(self):
        return [t for t, _ in self.sent]

    def images_sent(self):
        return [t for t in self.types() if t in (D.FRAME, D.DELTA)]

    def events(self):
        return [p for t, p, _ in self.pub if t == T["events"]]

    def status(self):
        return [json.loads(p) for t, p, _ in self.pub if t == T["status"]][-1]

    def run_render(self, ms, make, period_ms=66):
        """Le rendu publie make(k) toutes les period_ms pendant ms."""
        k = 0
        for _ in range(ms // period_ms):
            self.publish(make(k))
            k += 1
            self.advance(period_ms, step_ms=period_ms // 3 or 1)


class Handshake(unittest.TestCase):
    def test_hello_mode0_status(self):
        rig = Rig(self)
        self.assertEqual(rig.dmd.state, ABSENT)
        rig.connect()
        self.assertEqual(rig.types()[:2], [D.HELLO_REQ, D.MODE])
        self.assertEqual(rig.sent[1][1], bytes([D.MODE_PI]))          # MODE 0 : images du Pi
        st = rig.status()
        self.assertEqual((st["dmd"], st["dmd_fw"]), ("connected", "0.1.0"))
        self.assertEqual(st["pico"], "absent")
        self.assertTrue(all(r for t, p, r in rig.pub if t == T["status"]))

    def test_hello_req_repeated_without_answer(self):
        rig = Rig(self)
        rig.linked = False
        rig.dmd.serial_opened()
        rig.pump()
        rig.advance(3500, step_ms=50)                  # 0, 1 000, 2 000, 3 000 ms
        self.assertEqual(rig.types().count(D.HELLO_REQ), 4)
        self.assertEqual(rig.dmd.state, HANDSHAKE)
        self.assertEqual(rig.status()["dmd"], "handshake")
        rig.linked = True
        rig.advance(1100, step_ms=50)
        self.assertEqual(rig.dmd.state, CONNECTED)

    def test_unexpected_board(self):
        rig = Rig(self)
        rig.linked = False
        rig.dmd.serial_opened()
        rig.pump()
        bad = D.encode(D.HELLO, D.hello_payload(64, 32, (1, 2, 3), 5000))
        rig.dmd.serial_data(bad)
        rig.pump()
        self.assertEqual(rig.dmd.state, HANDSHAKE)
        self.assertIn("E DMD Unexpected board : protocol 1, 64x32", rig.events())
        self.assertNotIn(D.MODE, rig.types())

    def test_silence_returns_to_hello(self):
        rig = Rig(self)
        rig.connect()
        rig.linked = False                             # plus de TELEMETRY
        rig.advance(2900, step_ms=50)
        self.assertEqual(rig.dmd.state, CONNECTED)
        rig.advance(200, step_ms=50)
        self.assertEqual(rig.dmd.state, HANDSHAKE)
        self.assertEqual(rig.types().count(D.HELLO_REQ), 2)
        rig.linked = True
        rig.advance(1100, step_ms=50)
        self.assertEqual(rig.dmd.state, CONNECTED)

    def test_telemetry_keeps_link_and_is_not_an_event(self):
        rig = Rig(self)
        rig.connect()
        rig.advance(10000, step_ms=100)
        self.assertEqual(rig.dmd.state, CONNECTED)
        self.assertGreaterEqual(rig.dmd.counters["telemetry"], 9)
        self.assertEqual(rig.dmd.telemetry[0], 5000)
        self.assertEqual(rig.events(), [])

    def test_closed_is_absent(self):
        rig = Rig(self)
        rig.connect()
        rig.dmd.serial_closed()
        rig.pump()
        st = rig.status()
        self.assertEqual((st["dmd"], st["dmd_fw"]), ("absent", None))
        self.assertIsNone(rig.dmd.take_serial())


class Images(unittest.TestCase):
    def test_nothing_without_image_then_local_animation(self):
        rig = Rig(self)
        rig.connect()
        rig.advance(3100, step_ms=50)
        self.assertEqual(rig.images_sent(), [])
        self.assertTrue(rig.card.local_animation)      # § 6.3 : la carte prend le relais

    def test_frame_then_deltas(self):
        rig = Rig(self)
        rig.connect()
        rig.publish(image(0x1234))
        rig.pump()
        self.assertEqual(rig.images_sent(), [D.FRAME])
        self.assertEqual(bytes(rig.card.framebuffer), image(0x1234))
        rig.advance(70)
        rig.publish(image(0x1234, tiles=(3, 40)))
        rig.advance(70)
        self.assertEqual(rig.images_sent(), [D.FRAME, D.DELTA])
        _n, base, mask = D.delta_header(rig.sent[-1][1])
        self.assertEqual(mask, (1 << 3) | (1 << 40))
        self.assertEqual(bytes(rig.card.framebuffer), image(0x1234, tiles=(3, 40)))
        self.assertEqual(rig.card.lost, 0)
        self.assertEqual(rig.card.errors_sent, [])

    def test_identical_image_gives_empty_delta(self):
        rig = Rig(self)
        rig.connect()
        rig.run_render(660, lambda k: image(7))
        kinds = rig.images_sent()
        self.assertEqual(kinds[0], D.FRAME)
        self.assertGreaterEqual(kinds.count(D.DELTA), 8)
        for t, p in rig.sent:
            if t == D.DELTA:
                self.assertEqual(len(p), D.DELTA_HEADER)          # masque nul
                self.assertEqual(D.delta_header(p)[2], 0)
        self.assertEqual(rig.card.frames_shown, len(kinds))   # chaque envoi = un échange

    def test_at_most_15_per_second(self):
        rig = Rig(self)
        rig.connect()
        rig.run_render(1980, lambda k: image(k & 0xFF, tiles=(k % 64,)), period_ms=33)
        n = len(rig.images_sent())
        self.assertLessEqual(n, 2 * D.TARGET_FPS + 1)
        self.assertGreaterEqual(n, 2 * D.TARGET_FPS - 2)
        last = rig.dmd.latest
        rig.advance(100)
        self.assertEqual(bytes(rig.card.framebuffer), last)   # la dernière image finit affichée

    def test_periodic_full_frame(self):
        rig = Rig(self, dmd_full_period_ms=500)
        rig.connect()
        rig.run_render(3000, lambda k: image(1, tiles=(k % 64,)))
        frames = rig.images_sent().count(D.FRAME)
        self.assertIn(frames, (6, 7))                          # 3 s / 500 ms, plus le premier
        self.assertGreater(rig.images_sent().count(D.DELTA), 30)
        self.assertEqual(rig.card.lost, 0)

    def test_full_change_falls_back_to_frame(self):
        rig = Rig(self)
        rig.connect()
        rig.publish(image(0))
        rig.advance(70)
        rig.publish(noise(1))                                  # DELTA de 8 194 octets ou plus
        rig.advance(70)
        self.assertEqual(D.delta_payload(2, 1, image(0), noise(1)), None)
        self.assertEqual(rig.images_sent(), [D.FRAME, D.FRAME])
        self.assertEqual(bytes(rig.card.framebuffer), noise(1))

    def test_stale_stops_then_resumes_with_frame(self):
        rig = Rig(self)
        rig.connect()
        rig.run_render(990, lambda k: image(2, tiles=(k,)))
        before = len(rig.images_sent())
        rig.advance(2000, step_ms=50)                          # dmd_stale_ms = 2 000
        stopped = len(rig.images_sent())
        rig.advance(3000, step_ms=50)
        self.assertEqual(len(rig.images_sent()), stopped)      # plus rien n'est émis
        self.assertLessEqual(stopped - before, 2)              # FRAME périodique au plus
        self.assertTrue(rig.card.local_animation)
        rig.publish(image(9, tiles=(5,)))
        rig.pump()
        self.assertEqual(rig.images_sent()[-1], D.FRAME)       # reprise par un FRAME
        self.assertFalse(rig.card.local_animation)
        self.assertEqual(bytes(rig.card.framebuffer), image(9, tiles=(5,)))

    def test_board_error_event_then_frame(self):
        rig = Rig(self)
        rig.connect()
        rig.publish(image(3))
        rig.advance(70)
        rig.dmd.serial_data(D.encode(D.ERROR, D.error_payload(D.ERR_DELTA, "tile 3")))
        rig.publish(image(3, tiles=(1,)))
        rig.advance(70)
        self.assertIn("E DMD Error 0x04 : tile 3", rig.events())
        self.assertEqual(rig.images_sent(), [D.FRAME, D.FRAME])

    def test_wrong_size_rejected_once_a_minute(self):
        rig = Rig(self)
        rig.connect()
        for _ in range(5):
            rig.publish(b"\0" * 10)
        rig.advance(30000, step_ms=1000)
        rig.publish(b"\0" * 8193)
        self.assertEqual(rig.events(), ["E DMD Image of 10 bytes rejected"])
        rig.advance(30000, step_ms=1000)
        rig.publish(b"\0" * 12)
        rig.pump()
        self.assertEqual(rig.events(), ["E DMD Image of 10 bytes rejected",
                                        "E DMD Image of 12 bytes rejected"])
        self.assertEqual(rig.dmd.counters["images_rejected"], 7)
        self.assertEqual(rig.images_sent(), [])

    def test_never_clear_mode1_brightness(self):
        rig = Rig(self)
        rig.connect()
        rig.run_render(1000, lambda k: noise(k) if k % 4 == 0 else image(k, tiles=(k,)))
        rig.advance(4000, step_ms=100)
        rig.dmd.serial_closed()
        rig.dmd.serial_opened()
        rig.pump()
        allowed = {D.HELLO_REQ, D.MODE, D.FRAME, D.DELTA}
        self.assertTrue(set(rig.types()) <= allowed, rig.types())
        self.assertTrue(all(p == bytes([D.MODE_PI]) for t, p in rig.sent if t == D.MODE))

    def test_reconnect_starts_with_frame(self):
        rig = Rig(self)
        rig.connect()
        rig.run_render(330, lambda k: image(4, tiles=(k,)))
        rig.dmd.serial_closed()
        rig.card.boot()
        rig.sent = []
        rig.dmd.serial_opened()
        rig.run_render(330, lambda k: image(4, tiles=(k + 10,)))
        imgs = rig.images_sent()
        self.assertEqual(rig.types()[:2], [D.HELLO_REQ, D.MODE])
        self.assertEqual(imgs[0], D.FRAME)
        self.assertEqual(rig.card.deltas_ignored, 0)


def short(ftype, n):
    """Trame de la carte au CRC bon, avec n octets nuls de données."""
    return D.encode(ftype, b"\0" * n)


def bad_crc_frame():
    good = short(D.TELEMETRY, 9)
    return good[:-1] + bytes([good[-1] ^ 0xFF])


BAD_LENGTH_HEADER = D.SYNC + bytes([D.TELEMETRY, 100, 0])      # 100 > 9, maximum de TELEMETRY


def warnings_of(cm):
    return [r.getMessage() for r in cm.records]


class InvalidFrames(unittest.TestCase):
    """Trames invalides de la carte : journal au plus une fois par minute et par clé,
    aucun événement ni publication (§ 9.5, 51e complément). Horloge simulée, aucun sleep."""

    def rig(self):
        rig = Rig(self)
        rig.connect()
        return rig

    def test_short_telemetry_once_a_minute_with_count(self):
        rig = self.rig()
        with self.assertLogs("hwd", "WARNING") as cm:
            for _ in range(3):
                rig.dmd.serial_data(short(D.TELEMETRY, 5))
            rig.advance(59000, step_ms=1000)
        self.assertEqual(warnings_of(cm), ["# DMD : invalid TELEMETRY (5 bytes), 1 since last log"])
        with self.assertLogs("hwd", "WARNING") as cm:
            rig.advance(1000, step_ms=1000)          # fin de la minute : les 2 autres, sans nouveau cas
        self.assertEqual(warnings_of(cm), ["# DMD : invalid TELEMETRY (5 bytes), 2 since last log"])
        with self.assertNoLogs("hwd", "WARNING"):
            rig.advance(120000, step_ms=1000)        # jamais recomptés
        self.assertEqual(rig.dmd.counters["invalid_replies"],
                         {"HELLO": 0, "ERROR": 0, "TELEMETRY": 3})
        self.assertEqual(rig.events(), [])
        self.assertEqual(rig.dmd.state, CONNECTED)
        self.assertEqual(rig.dmd.telemetry[0], 5000)  # les TELEMETRY valides passent toujours

    def test_each_type_has_its_own_key(self):
        rig = self.rig()
        with self.assertLogs("hwd", "WARNING") as cm:
            rig.dmd.serial_data(short(D.HELLO, 4) + short(D.ERROR, 0) + short(D.TELEMETRY, 8))
            rig.dmd.serial_data(short(D.PONG, 2) + short(D.STATS, D.STATS_LEN) + short(0x42, 3))
            rig.pump()
        self.assertEqual(warnings_of(cm), [
            "# DMD : invalid HELLO (4 bytes), 1 since last log",
            "# DMD : invalid ERROR (0 bytes), 1 since last log",
            "# DMD : invalid TELEMETRY (8 bytes), 1 since last log",
            "# DMD : unexpected PONG (2 bytes), 1 since last log",
            "# DMD : unexpected STATS (%d bytes), 1 since last log" % D.STATS_LEN,
            "# DMD : unexpected type 0x42 (3 bytes), 1 since last log"])
        self.assertEqual(rig.dmd.counters["invalid_replies"],
                         {"HELLO": 1, "ERROR": 1, "TELEMETRY": 1})
        self.assertEqual(rig.dmd.counters["unexpected_replies"],
                         {"PONG": 1, "STATS": 1, "unknown": 1})
        self.assertEqual(rig.dmd.counters["board_errors"], 0)
        self.assertEqual(rig.events(), [])
        self.assertEqual(rig.dmd.state, CONNECTED)

    def test_unknown_types_share_one_key_last_type_shown(self):
        rig = self.rig()
        with self.assertLogs("hwd", "WARNING") as cm:
            rig.dmd.serial_data(short(0x42, 3) + short(D.MODE, 1))    # MODE : sens Pi -> carte
            rig.advance(60000, step_ms=1000)
        self.assertEqual(warnings_of(cm), [
            "# DMD : unexpected type 0x42 (3 bytes), 1 since last log",
            "# DMD : unexpected type 0x05 (1 bytes), 1 since last log"])
        self.assertEqual(rig.dmd.counters["unexpected_replies"]["unknown"], 2)

    def test_decoder_errors_logged_counters_unchanged(self):
        rig = self.rig()
        with self.assertLogs("hwd", "WARNING") as cm:
            rig.dmd.serial_data(bad_crc_frame() + BAD_LENGTH_HEADER)
            rig.dmd.serial_data(bad_crc_frame() + bad_crc_frame() + short(D.TELEMETRY, 9))
            rig.advance(60000, step_ms=1000)
        self.assertEqual(warnings_of(cm), [
            "# DMD : bad frame CRC, 1 since last log",
            "# DMD : bad frame length, 1 since last log",
            "# DMD : bad frame CRC, 2 since last log"])
        self.assertEqual((rig.dmd._decoder.crc_errors, rig.dmd._decoder.length_errors), (3, 1))
        self.assertEqual(rig.events(), [])

    def test_reopen_new_decoder_minute_kept(self):
        rig = self.rig()
        with self.assertLogs("hwd", "WARNING") as cm:
            rig.dmd.serial_data(bad_crc_frame())
        self.assertEqual(warnings_of(cm), ["# DMD : bad frame CRC, 1 since last log"])
        rig.dmd.serial_closed()
        rig.dmd.serial_opened()                     # Decoder neuf, compteurs à 0
        rig.pump()
        self.assertEqual(rig.dmd.state, CONNECTED)
        with self.assertNoLogs("hwd", "WARNING"):
            rig.dmd.serial_data(bad_crc_frame())
            rig.advance(59000, step_ms=1000)        # le délai de la clé survit à la fermeture
        with self.assertLogs("hwd", "WARNING") as cm:
            rig.advance(1000, step_ms=1000)
        self.assertEqual(warnings_of(cm), ["# DMD : bad frame CRC, 1 since last log"])

    def test_pending_written_after_port_closed(self):
        rig = self.rig()
        with self.assertLogs("hwd", "WARNING"):
            rig.dmd.serial_data(short(D.ERROR, 0) + short(D.ERROR, 0))
        rig.dmd.serial_closed()
        with self.assertLogs("hwd", "WARNING") as cm:
            rig.advance(60000, step_ms=1000)
        self.assertEqual(warnings_of(cm), ["# DMD : invalid ERROR (0 bytes), 1 since last log"])
        self.assertEqual(rig.dmd.state, ABSENT)

    def test_normal_operation_logs_nothing(self):
        rig = Rig(self)
        with self.assertNoLogs("hwd", "WARNING"):
            rig.connect()
            rig.run_render(2000, lambda k: noise(k) if k % 5 == 0 else image(k, tiles=(k % 64,)))
            rig.advance(3000, step_ms=100)
        self.assertEqual(rig.dmd.counters["invalid_replies"],
                         {"HELLO": 0, "ERROR": 0, "TELEMETRY": 0})
        self.assertEqual((rig.dmd._decoder.crc_errors, rig.dmd._decoder.length_errors), (0, 0))


class Config(unittest.TestCase):
    def test_defaults(self):
        cfg = HwdConfig().validate()
        self.assertEqual((cfg.dmd_device, cfg.dmd_full_period_ms, cfg.dmd_stale_ms,
                          cfg.dmd_silence_ms), ("/dev/dmd", 1000, 2000, 3000))

    def test_ranges(self):
        for key, ok, bad in (("dmd_full_period_ms", (200, 2500), (199, 2501)),
                             ("dmd_stale_ms", (1500, 10000), (1499, 10001)),
                             ("dmd_silence_ms", (1500, 10000), (1499, 10001))):
            for v in ok:
                HwdConfig(**{key: v}).validate()
            for v in bad:
                with self.assertRaises(ValueError):
                    HwdConfig(**{key: v}).validate()

    def test_example_file_has_dmd_defaults(self):
        cfg = load(os.path.join(ROOT, "pi", "hwd", "hwd.example.toml"))
        self.assertEqual(cfg, HwdConfig().validate())

    def test_disabled_status_absent(self):
        rig = Rig(self, dmd_device="")
        rig.pump()
        st = [json.loads(p) for t, p, _ in rig.core.take_publishes() + rig.pub
              if t == T["status"]][-1]
        self.assertEqual((st["dmd"], st["dmd_fw"]), ("absent", None))


@unittest.skipUnless(hasattr(os, "openpty"), "pas de pseudo-terminal sur ce système")
class DaemonOverPty(unittest.TestCase):
    def test_daemon_drives_fake_card(self):
        from hwd_daemon import Daemon, MemoryBus
        from ptypair import PtyServer

        tmp = tempfile.mkdtemp()
        card = FakeI75W()
        srv = PtyServer(card).start()
        link = os.path.join(tmp, "dmd")
        os.symlink(srv.path, link)
        bus = MemoryBus()
        cfg = HwdConfig(pico_device=os.path.join(tmp, "absent"), dmd_device=link,
                        reconnect_ms=100).validate()
        daemon = Daemon(cfg, bus)
        try:
            def run_until(pred, what):
                end = time.monotonic() + 5.0
                while time.monotonic() < end:
                    daemon.run_once(0.005)
                    if pred():
                        return
                self.fail("délai dépassé : %s" % what)

            def shown():
                with srv._lock:                # fil du simulateur
                    return bytes(card.framebuffer)

            run_until(lambda: daemon.core.dmd.state == CONNECTED, "présentation")
            img = image(0x0F0F, tiles=(0, 63))
            bus.inject(T["dmd_frame"], img)
            run_until(lambda: shown() == img, "image affichée")
            img2 = image(0x0F0F, tiles=(0, 17, 63))
            bus.inject(T["dmd_frame"], img2)
            run_until(lambda: shown() == img2, "DELTA affiché")
            self.assertGreaterEqual(daemon.core.dmd.counters["deltas"], 1)
            st = [json.loads(p) for t, p, r in bus.published if t == T["status"]][-1]
            self.assertEqual((st["pico"], st["dmd"]), ("absent", "connected"))
        finally:
            daemon.close()
            srv.stop()
            os.unlink(link)
            os.rmdir(tmp)


if __name__ == "__main__":
    unittest.main()
