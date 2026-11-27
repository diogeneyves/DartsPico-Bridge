# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests de la fausse carte Interstate 75 W, en mémoire, avec une horloge simulée."""

import struct
import unittest

import _paths  # noqa: F401
import dmd_proto as D
from fake_i75w import FakeI75W


class Clock:
    def __init__(self, t=1000):
        self.t = t

    def __call__(self):
        return self.t

    def ms(self, n):
        self.t += n * 1000


def frame(n, fill=0x12):
    return D.encode(D.FRAME, D.frame_payload(n, bytes([fill]) * D.PIXELS_BYTES))


class Base(unittest.TestCase):
    def setUp(self):
        self.clk = Clock()
        self.card = FakeI75W(clock=self.clk)
        self.rx = D.Decoder()

    def send(self, data):
        return self.rx.feed(self.card.feed(data))


class Requests(Base):
    def test_hello(self):
        (t, p), = self.send(D.encode(D.HELLO_REQ))
        self.assertEqual(t, D.HELLO)
        self.assertEqual(D.parse_hello(p), (1, 128, 32, (0, 1, 0), 5000))

    def test_ping_echo(self):
        self.assertEqual(self.send(D.encode(D.PING, b"abc")), [(D.PONG, b"abc")])

    def test_unknown_and_wrong_direction_types(self):
        for t in (0x42, D.TELEMETRY):
            (rt, p), = self.send(D.encode(t, b"x" if t == 0x42 else bytes(9)))
            self.assertEqual(rt, D.ERROR)
            self.assertEqual(D.parse_error(p)[0], D.ERR_UNKNOWN_TYPE)

    def test_bad_length_and_range(self):
        # Longueur 0 au lieu de 1 : dans le maximum du type, donc reçue, puis refusée.
        (rt, p), = self.send(D.encode(D.BRIGHTNESS, b""))
        self.assertEqual(D.parse_error(p)[0], D.ERR_LENGTH)
        (rt, p), = self.send(D.encode(D.MODE, b"\x02"))
        self.assertEqual(D.parse_error(p)[0], D.ERR_RANGE)

    def test_brightness_clear(self):
        self.send(D.encode(D.BRIGHTNESS, b"\x40") + frame(0, 0xFF))
        self.assertEqual(self.card.brightness, 0x40)
        self.assertEqual(self.card.framebuffer[0], 0xFF)
        self.send(D.encode(D.CLEAR))
        self.assertEqual(bytes(self.card.framebuffer), bytes(D.PIXELS_BYTES))


class Frames(Base):
    def test_frames_and_lost_counting(self):
        self.send(frame(65534) + frame(65535) + frame(0))              # boucle u16
        self.assertEqual(self.card.lost, 0)
        self.send(frame(3))                                            # 1 et 2 manquent
        self.assertEqual(self.card.lost, 2)
        self.send(frame(3))                                            # doublon : pas de perte
        self.assertEqual(self.card.lost, 2)
        bad = bytearray(frame(4))
        bad[500] ^= 1
        self.send(bytes(bad) + frame(5))
        self.assertEqual(self.card.lost, 2 + 1 + 1)                    # CRC + trou du n° 4
        self.assertEqual(self.card.frames_shown, 6)

    def test_telemetry_every_second(self):
        self.assertEqual(self.card.tick(), b"")
        for i in range(D.TARGET_FPS):                                  # 15 images/s (§ 6.3)
            self.send(frame(i))
        self.clk.ms(1000)
        (t, p), = self.rx.feed(self.card.tick())
        self.assertEqual(t, D.TELEMETRY)
        self.assertEqual(D.parse_telemetry(p), (5000, 350, 15, 0))
        self.clk.ms(500)
        self.assertEqual(self.card.tick(), b"")
        self.clk.ms(500)
        self.assertEqual(D.parse_telemetry(self.rx.feed(self.card.tick())[0][1])[2], 0)


class Modes(Base):
    def test_local_animation_after_3_s_then_auto_return(self):
        self.send(frame(0))
        self.clk.ms(2999)
        self.card.tick()
        self.assertEqual(self.card.mode(), D.MODE_PI)
        self.clk.ms(1)
        self.card.tick()
        self.assertEqual(self.card.mode(), D.MODE_LOCAL)
        self.send(frame(1))
        self.assertEqual(self.card.mode(), D.MODE_PI)

    def test_forced_local_ignores_frames(self):
        self.send(D.encode(D.MODE, b"\x01"))
        self.send(frame(0, 0x77))
        self.assertEqual(self.card.mode(), D.MODE_LOCAL)
        self.assertEqual(self.card.frames_shown, 0)
        self.send(D.encode(D.MODE, b"\x00") + frame(1, 0x77))
        self.assertEqual(self.card.mode(), D.MODE_PI)
        self.assertEqual(self.card.framebuffer[0], 0x77)



# ------------------------------------------------------------ DELTA (§ 6.4)

def image(fill=0x12):
    return bytes([fill]) * D.PIXELS_BYTES


def with_pixel(img, x, y, color):
    b = bytearray(img)
    b[2 * (y * D.WIDTH + x):2 * (y * D.WIDTH + x) + 2] = struct.pack("<H", color)
    return bytes(b)


def delta(n, base, old, new):
    return D.encode(D.DELTA, D.delta_payload(n, base, old, new))


class Deltas(Base):
    def test_swap_and_copy_back(self):
        a = image()
        b = with_pixel(a, 0, 0, 0xFFFF)
        c = with_pixel(b, 127, 31, 0xF800)
        self.assertEqual(self.send(frame(0) + delta(1, 0, a, b)), [])
        self.assertEqual(bytes(self.card.framebuffer), b)
        self.assertEqual(bytes(self.card.backbuffer), b)      # recopie après échange
        self.send(delta(2, 1, b, c))
        self.assertEqual(bytes(self.card.framebuffer), c)
        self.assertEqual(bytes(self.card.backbuffer), c)
        self.assertEqual((self.card.displayed_no, self.card.frames_shown), (2, 3))
        self.assertEqual(self.card.lost, 0)

    def test_empty_delta_swaps(self):
        a = image()
        self.send(frame(0) + delta(1, 0, a, a))
        self.assertEqual((self.card.displayed_no, self.card.frames_shown), (1, 2))

    def test_wrong_base_ignored_until_frame(self):
        a = image()
        b = with_pixel(a, 5, 5, 0xFFFF)
        self.assertEqual(self.send(frame(0) + delta(2, 1, a, b)), [])   # 1 perdu
        self.assertEqual(bytes(self.card.framebuffer), a)
        self.assertEqual(self.card.lost, 1 + 1)                          # trou + ignoré
        self.send(delta(3, 0, a, b))              # base juste mais en attente d'un FRAME
        self.assertEqual(bytes(self.card.framebuffer), a)
        self.assertEqual(self.card.lost, 3)
        self.send(frame(4, 0x12) + delta(5, 4, a, b))
        self.assertEqual(bytes(self.card.framebuffer), b)
        self.assertEqual(self.card.lost, 3)

    def test_invalid_content_error_0x04(self):
        a = image()
        b = with_pixel(a, 9, 9, 0xFFFF)
        p = bytearray(D.delta_payload(1, 0, a, b))
        for name, bad in (("tronqué", bytes(p[:-1])),
                          ("masque incohérent", bytes(p[:4]) + struct.pack("<Q", 3) + bytes(p[12:]))):
            with self.subTest(name):
                self.setUp()
                self.send(frame(0))
                (t, r), = self.send(D.encode(D.DELTA, bad))
                self.assertEqual(t, D.ERROR)
                self.assertEqual(D.parse_error(r)[0], D.ERR_DELTA)
                self.assertEqual(bytes(self.card.framebuffer), a)
                self.assertEqual(bytes(self.card.backbuffer), a)
                self.assertEqual(self.card.lost, 1)
                self.send(delta(2, 0, a, b))                 # ignoré jusqu'au FRAME
                self.assertEqual(bytes(self.card.framebuffer), a)

    def test_delta_ignored_at_boot_and_after_clear(self):
        a = image()
        b = with_pixel(a, 1, 1, 0xFFFF)
        self.send(delta(1, 0, a, b))
        self.assertEqual(self.card.frames_shown, 0)
        self.send(frame(2) + D.encode(D.CLEAR))
        self.assertEqual(bytes(self.card.framebuffer), bytes(D.PIXELS_BYTES))
        self.assertEqual(bytes(self.card.backbuffer), bytes(D.PIXELS_BYTES))
        self.send(delta(3, 2, a, b))
        self.assertEqual(bytes(self.card.framebuffer), bytes(D.PIXELS_BYTES))

    def test_mode_1_not_lost_then_frame_needed(self):
        a = image()
        b = with_pixel(a, 1, 1, 0xFFFF)
        self.send(frame(0) + D.encode(D.MODE, b"\x01"))
        self.send(frame(1) + delta(2, 1, a, b))
        self.assertEqual(self.card.lost, 0)
        self.assertEqual(self.card.frames_shown, 1)
        self.send(D.encode(D.MODE, b"\x00") + delta(3, 0, a, b))
        self.assertEqual(self.card.frames_shown, 1)            # ignoré : FRAME attendu
        self.assertEqual(self.card.lost, 1)

    def test_delay_reset_by_delta_not_by_ping(self):
        a = image()
        self.send(frame(0))
        self.clk.ms(2000)
        self.send(D.encode(D.PING, b"x") + D.encode(D.BRIGHTNESS, b"\x10"))
        self.clk.ms(1000)
        self.card.tick()
        self.assertEqual(self.card.mode(), D.MODE_LOCAL)        # PING ne remet pas à zéro
        self.send(delta(1, 0, a, a))                            # ignoré, pas de retour
        self.assertEqual(self.card.mode(), D.MODE_LOCAL)
        self.send(frame(2))
        self.assertEqual(self.card.mode(), D.MODE_PI)
        # Base fausse, puis contenu invalide (masque 1 sans tuile) : délai remis à zéro.
        for base_ok, mask, code in ((False, 0, None), (True, 1, D.ERR_DELTA)):
            n = self.card.displayed_no
            self.clk.ms(2500)
            out = self.send(D.encode(D.DELTA, struct.pack(
                "<HHQ", n + 1, n if base_ok else n + 50, mask)))
            errors = [D.parse_error(r)[0] for t, r in out if t == D.ERROR]
            self.assertEqual(errors, [code] if code else [])
            self.clk.ms(2500)
            self.card.tick()
            self.assertEqual(self.card.mode(), D.MODE_PI)
            self.send(frame(n + 2))

    def test_telemetry_counts_swaps(self):
        a = image()
        self.send(frame(0))
        for i in range(1, 10):
            self.send(delta(i, i - 1, a, a))
        self.send(delta(20, 3, a, a))                           # ignoré : pas d'échange
        self.clk.ms(1000)
        (t, p), = self.rx.feed(self.card.tick())
        self.assertEqual(D.parse_telemetry(p)[2], 10)


class Stats(Base):
    def stats(self):
        out = [(t, p) for t, p in self.send(D.encode(D.STATS_REQ)) if t != D.TELEMETRY]
        (t, p), = out
        self.assertEqual(t, D.STATS)
        self.assertEqual(len(p), 47)
        return D.parse_stats(p)

    def test_counters(self):
        a = bytes(D.PIXELS_BYTES)
        sent = frame(1) + D.encode(D.DELTA, D.delta_payload(2, 1, a, a))
        self.send(sent)
        self.send(b"\x00\x01" + D.encode(D.DELTA, D.delta_payload(9, 5, a, a)))  # base fausse
        self.send(b"\xAA\x55\x02\xFF\xFF")                                     # longueur refusée
        bad = bytearray(frame(10))
        bad[-1] ^= 1
        self.send(bytes(bad))                                                 # CRC faux
        self.send(D.encode(D.MODE, b"\x01") + frame(11))                      # MODE 1 compris
        self.clk.ms(1234)
        self.total = (len(sent) + 2 + len(D.encode(D.DELTA, D.delta_payload(9, 5, a, a))) + 5
                      + len(bad) + len(D.encode(D.MODE, b"\x01") + frame(11)))
        st = self.stats()
        dec = self.card.decoder
        self.assertEqual(st["uptime_ms"], 1234)
        self.assertEqual(st["frames_rx"], 2)
        self.assertEqual(st["deltas_rx"], 2)
        # Octets reçus jusqu'à STATS_REQ compris (comptés à la lecture).
        self.assertEqual(st["bytes_rx"], self.card.bytes_rx)
        self.assertEqual(st["bytes_rx"], self.total + len(D.encode(D.STATS_REQ)))
        self.assertEqual((st["skipped"], st["crc_errors"], st["length_errors"]),
                         (dec.skipped, 1, 1))
        self.assertGreater(st["skipped"], 0)
        self.assertEqual(st["deltas_ignored"], 1)
        self.assertEqual(st["swaps"], 2)
        for name in ("replaced", "loop_max_us", "refresh_hz", "flags"):
            self.assertEqual(st[name], 0)

    def test_bad_length_wrong_direction_and_order(self):
        # Longueur au-delà du maximum du type (0) : rejetée par le récepteur, sans réponse.
        body = bytes([D.STATS_REQ, 1, 0]) + b"x"
        self.assertEqual(self.send(D.SYNC + body + struct.pack("<I", D.crc32(body))), [])
        self.assertEqual(self.card.decoder.length_errors, 1)
        (t, p), = self.send(D.encode(D.STATS, bytes(47)))
        self.assertEqual(D.parse_error(p)[0], D.ERR_UNKNOWN_TYPE)
        # TELEMETRY due avant la réponse (35e complément).
        self.clk.ms(1000)
        self.assertEqual([t for t, _ in self.send(D.encode(D.STATS_REQ))],
                         [D.TELEMETRY, D.STATS])

    def test_accepted_in_mode_1_without_resetting_delay(self):
        self.send(frame(1))
        self.clk.ms(2000)
        self.stats()
        self.clk.ms(1000)
        self.card.tick()
        self.assertEqual(self.card.mode(), D.MODE_LOCAL)
        self.send(D.encode(D.MODE, b"\x01"))
        self.assertEqual(self.stats()["frames_rx"], 1)


if __name__ == "__main__":
    unittest.main()
