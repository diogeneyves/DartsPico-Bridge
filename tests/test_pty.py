# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests de bout en bout : simulateurs exposés sur un vrai pseudo-terminal.

Le client ouvre le côté esclave comme le fera le démon matériel. Chaque
lecture a un délai maximal : un test ne peut pas bloquer la CI. Les temps
mesurés ici ne représentent pas l'USB et ne sont pas vérifiés.
"""

import os
import select
import time
import unittest

import _paths  # noqa: F401
import dmd_proto as D
import pico_proto as P
from fake_i75w import FakeI75W
from fake_pico import FakePico
from ptypair import PtyServer, open_client

TIMEOUT = 3.0


def read_until(fd, pred, buf=b"", timeout=TIMEOUT):
    """Lit jusqu'à ce que pred(buf) soit vrai ; renvoie buf. Échoue au délai."""
    end = time.monotonic() + timeout
    while not pred(buf):
        left = end - time.monotonic()
        if left <= 0:
            raise AssertionError("délai dépassé ; reçu : %r" % buf[-200:])
        r, _, _ = select.select([fd], [], [], left)
        if r:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                raise EOFError(buf)
            buf += chunk
    return buf


@unittest.skipUnless(hasattr(os, "openpty"), "pas de pseudo-terminal sur ce système")
class PicoOverPty(unittest.TestCase):
    def setUp(self):
        self.pico = FakePico()
        self.srv = PtyServer(self.pico).start()
        self.fd = open_client(self.srv.path)

    def tearDown(self):
        os.close(self.fd)
        self.srv.stop()

    def lines(self, until):
        buf = read_until(self.fd, lambda b: until.encode() + b"\n" in b)
        return [s for s in buf.decode().split("\n") if s]

    def test_session(self):
        os.write(self.fd, b"@1 WHO\n")
        out = self.lines("K 1")
        self.assertIn(P.msg_caps(), out)
        os.write(self.fd, b"@2 TOY 3 1\n@3 TOY 9 1\n")
        out = self.lines("N 3 RANGE")
        self.assertIn("K 2", out)
        self.assertEqual(self.pico.toys[2], 1)

    def test_injected_event_and_raw_mode(self):
        # 0x03 (Ctrl-C) et \r ne doivent pas être interprétés par le pty.
        os.write(self.fd, b"\x03\n@4 PING\r\n")
        out = self.lines("K 4")
        self.assertIn("N - SYNTAX", out)
        self.srv.call(self.pico.dart, 2, 5, 1500)
        out = self.lines("1500")
        self.assertTrue(any(s.startswith("D 2 5 ") for s in out))

    def test_long_ledf_line(self):
        os.write(self.fd, b"LEDCFG 1 300\n")
        os.write(self.fd, ("@9 " + P.command("LEDF", 1, bytes(range(30)) * 30) + "\n").encode())
        self.lines("K 9")
        self.assertEqual(self.pico.led_rgb[1], bytes(range(30)) * 30)

    def test_unplug_is_seen_by_reader(self):
        read_until(self.fd, lambda b: b"CAPS" in b)            # vider HELLO / CAPS
        self.srv.stop()
        # macOS : fin de fichier ; Linux : EIO, converti en fin de fichier par read_until.
        with self.assertRaises(EOFError):
            read_until(self.fd, lambda b: False, timeout=1.0)


@unittest.skipUnless(hasattr(os, "openpty"), "pas de pseudo-terminal sur ce système")
class I75WOverPty(unittest.TestCase):
    def setUp(self):
        self.card = FakeI75W()
        self.srv = PtyServer(self.card).start()
        self.fd = open_client(self.srv.path)
        self.dec = D.Decoder()

    def tearDown(self):
        os.close(self.fd)
        self.srv.stop()

    def frames_until(self, ftype):
        got = []

        def pred(buf):
            got.extend(self.dec.feed(buf[len(pred.seen):]))
            pred.seen = buf
            return any(t == ftype for t, _ in got)
        pred.seen = b""
        read_until(self.fd, pred)
        return got

    def test_hello_and_full_frames(self):
        os.write(self.fd, D.encode(D.HELLO_REQ))
        got = self.frames_until(D.HELLO)
        self.assertEqual(D.parse_hello([p for t, p in got if t == D.HELLO][0])[1:3], (128, 32))
        # Trois images complètes contenant des octets 0x03, 0x11, 0x13, 0xAA, 0x55.
        px = bytes([0x03, 0x11, 0x13, 0xAA, 0x55, 0x0A, 0x0D, 0x00]) * (D.PIXELS_BYTES // 8)
        for n in range(3):
            os.write(self.fd, D.encode(D.FRAME, D.frame_payload(n, px)))
        os.write(self.fd, D.encode(D.PING, b"fin"))
        self.frames_until(D.PONG)
        self.assertEqual(self.card.frames_shown, 3)
        self.assertEqual(bytes(self.card.framebuffer), px)
        self.assertEqual(self.card.lost, 0)

    def test_telemetry_arrives(self):
        got = self.frames_until(D.TELEMETRY)
        p = [p for t, p in got if t == D.TELEMETRY][0]
        self.assertEqual(D.parse_telemetry(p)[0], 5000)


if __name__ == "__main__":
    unittest.main()
