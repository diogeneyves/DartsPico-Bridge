# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests sur PC du banc jetable de l'Interstate 75 W (tools/bench_i75w).

Testé ici : format des trames de l'émetteur, cadence, bilan des phases, compteurs STATS
(écarts, chien de garde, uptime, critères du § 11.3), réponses invalides comptées sans arrêter
la lecture, lecteur de trames du récepteur (resynchronisation, CRC, trous), permutation des
octets, imports compatibles avec MicroPython, et l'émetteur complet (essai et --check) contre
la fausse carte et contre une carte sans STATS, sur pseudo-terminal.
Non testé (carte seulement) : affichage HUB75, viper, console USB, ADC, bouton A.
"""

import ast
import io
import os
import sys
import unittest

import _paths  # noqa: F401
import dmd_proto as D
from fake_i75w import FakeI75W
from ptypair import PtyServer

BENCH = os.path.join(str(_paths.ROOT), "tools", "bench_i75w")
if BENCH not in sys.path:
    sys.path.insert(0, BENCH)

import rx_bench as R  # noqa: E402
import tx_bench as T  # noqa: E402


class Trickle(io.RawIOBase):
    """Flux qui rend au plus `chunk` octets par lecture, comme un port USB."""

    def __init__(self, data, chunk=7):
        self._data = memoryview(bytes(data))
        self._chunk = chunk

    def readinto(self, mv):
        n = min(len(mv), self._chunk, len(self._data))
        mv[0:n] = self._data[:n]
        self._data = self._data[n:]
        return n


def frames(n, start=0):
    src = T.FrameSource(start)
    return [src.next() for _ in range(n)]


class TestSender(unittest.TestCase):
    def test_frame_format(self):
        fs = frames(3, 65534)
        dec = D.Decoder()
        got = dec.feed(b"".join(fs))
        self.assertEqual(len(got), 3)
        self.assertEqual(dec.crc_errors, 0)
        for f in fs:
            self.assertEqual(len(f), T.FRAME_BYTES)
            self.assertEqual(len(f), 8203)
        numbers = [D.parse_frame(p)[0] for (_, p) in got]
        self.assertEqual(numbers, [65534, 65535, 0])
        self.assertTrue(all(t == D.FRAME for (t, _) in got))

    def test_stress_bytes_in_pixels(self):
        p = T.test_pixels(0)
        self.assertEqual(len(p), D.PIXELS_BYTES)
        self.assertEqual(p[0:4], b"\xAA\x55\x03\x03")
        self.assertNotEqual(T.test_pixels(1), p)

    def test_pacer_fixed_rate(self):
        p = T.Pacer(15, 100.0)
        self.assertEqual(p.delay(100.0), 0.0)
        self.assertAlmostEqual(p.delay(100.0), 1 / 15)
        self.assertAlmostEqual(p.delay(100.1), 2 / 15 - 0.1)
        self.assertEqual(p.late, 0)
        self.assertEqual(p.delay(105.0), 0.0)     # très en retard : grille relancée
        self.assertEqual(p.late, 1)

    def test_pacer_max(self):
        p = T.Pacer(None, 0.0)
        self.assertEqual([p.delay(0.0) for _ in range(3)], [0.0, 0.0, 0.0])

    def test_run_phase_with_fake_clock(self):
        clock = [0.0]
        sent = []

        def sleep(s):
            clock[0] += s

        def write(fd, data):
            sent.append(len(data))
            clock[0] += 0.001

        ph = T.run_phase(None, T.FrameSource(), 15, 2.0, clock=lambda: clock[0],
                         sleep=sleep, write=write)
        self.assertIn(ph["frames"], (30, 31))
        self.assertAlmostEqual(ph["fps"], 15, delta=0.6)
        self.assertTrue(all(n == 8203 for n in sent))
        self.assertAlmostEqual(ph["kbps"], ph["fps"] * 8.203, places=6)

    def test_card_side_and_verdict(self):
        tel = [(0.5, 5000, 350, 0, 0), (1.5, 5000, 350, 14, 0), (2.5, 5000, 350, 16, 2),
               (3.5, 5000, 350, 15, 3)]
        mean, best, lost = T.card_side(tel, 0.0, 3.4)
        self.assertAlmostEqual(mean, 15.0)
        self.assertEqual(best, 16)
        self.assertEqual(lost, 3)
        self.assertIsNone(T.card_side(tel, 10.0, 12.0))
        self.assertIn(">= 15", T.verdict((15.0, 16, 0)))
        self.assertIn("< 15", T.verdict((9.0, 10, 0)))
        self.assertIn("indéterminé", T.verdict(None))


class TestReceiver(unittest.TestCase):
    def test_reads_frames_in_small_chunks(self):
        r = R.FrameReader(Trickle(b"".join(frames(4))).readinto)
        for k in range(4):
            ftype, p = r.read_frame()
            self.assertEqual(ftype, D.FRAME)
            self.assertEqual(len(p), D.MAX_PAYLOAD)
            self.assertEqual(p[0] | (p[1] << 8), k)
        self.assertEqual(r.frames, 4)
        with self.assertRaises(EOFError):
            r.read_frame()

    def test_bad_crc_then_resync(self):
        fs = frames(3)
        bad = bytearray(fs[1])
        bad[100] ^= 0xFF
        r = R.FrameReader(io.BytesIO(b"xyz\xAA" + fs[0] + bytes(bad) + fs[2]).readinto)
        nums = []
        for _ in range(2):
            _, p = r.read_frame()
            nums.append(p[0] | (p[1] << 8))
        self.assertEqual(nums, [0, 2])
        self.assertEqual(r.crc_errors, 1)
        self.assertGreater(r.skipped, 4)

    def test_false_header_with_huge_length(self):
        junk = b"\xAA\x55\x04\xFF\xFF"           # CLEAR qui annonce 65 535 octets
        r = R.FrameReader(io.BytesIO(junk + frames(1)[0]).readinto)
        ftype, _ = r.read_frame()
        self.assertEqual(ftype, D.FRAME)
        self.assertEqual(r.length_errors, 1)

    def test_aa_aa_55(self):
        r = R.FrameReader(io.BytesIO(b"\xAA" + D.encode(D.PING, b"ab")).readinto)
        ftype, p = r.read_frame()
        self.assertEqual((ftype, bytes(p)), (D.PING, b"ab"))

    def test_same_frames_as_shared_decoder(self):
        stream = b"".join([D.encode(D.HELLO_REQ), D.encode(D.PING, b"\xAA\x55"),
                           frames(1)[0], D.encode(0x42, b"inconnu")])
        ref = D.Decoder().feed(stream)
        r = R.FrameReader(Trickle(stream, 3).readinto)
        mine = [(t, bytes(p)) for t, p in (r.read_frame() for _ in range(len(ref)))]
        self.assertEqual(mine, ref)

    def test_stats_gaps_fps_idle(self):
        s = R.Stats(0)
        for n, t in ((10, 0), (11, 100), (14, 200), (15, 300)):
            s.on_frame(n, t)
        self.assertEqual(s.gaps, 2)
        self.assertFalse(s.telemetry_due(999))
        self.assertTrue(s.telemetry_due(1000))
        self.assertEqual(s.last_fps, 4)
        self.assertFalse(s.idle(1200))
        self.assertTrue(s.idle(1300))
        s.on_frame(0, 1400)                       # 15 -> 0 : retour en arrière, pas un trou
        self.assertEqual(s.gaps, 2)

    def test_telemetry_frame(self):
        s = R.Stats(0)
        s.last_fps = 300
        s.gaps = 2
        r = R.FrameReader(None)
        r.crc_errors = 3
        ftype, p = D.Decoder().feed(R.telemetry_frame(s, r, 5012, 351))[0]
        self.assertEqual(ftype, D.TELEMETRY)
        self.assertEqual(D.parse_telemetry(p), (5012, 351, 255, 5))

    def test_swap16_py(self):
        src = bytes(range(16))
        dst = bytearray(16)
        R.swap16_py(dst, memoryview(src), 16)
        self.assertEqual(bytes(dst), bytes([1, 0, 3, 2, 5, 4, 7, 6, 9, 8, 11, 10, 13, 12, 15, 14]))

    def test_readinto_from_read(self):
        f = R.readinto_from_read(io.BytesIO(b"abc").read)
        buf = bytearray(5)
        self.assertEqual(f(memoryview(buf)), 3)
        self.assertEqual(bytes(buf[:3]), b"abc")
        self.assertEqual(f(memoryview(buf)), 0)


class TestMicroPythonImports(unittest.TestCase):
    """Les scripts de la carte n'importent que des modules présents sous MicroPython (Pimoroni)."""

    ALLOWED = {"struct", "binascii", "sys", "time", "select", "machine", "micropython", "gc",
               "hub75", "interstate75", "picographics", "dmd_proto", "bench_config",
               "bench_fast"}

    def test_board_scripts(self):
        for name in ("rx_bench.py", "testcard.py", "bench_fast.py", "bench_config.py"):
            with open(os.path.join(BENCH, name), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    mods = [node.module]
                else:
                    self.assertNotIsInstance(node, ast.JoinedStr, name + " : f-string")
                    continue
                for m in mods:
                    self.assertIn(m.split(".")[0], self.ALLOWED, "%s importe %s" % (name, m))


def raw_frame(ftype, payload):
    """Trame au CRC correct, quelle que soit la longueur (encode refuse les longueurs fausses)."""
    import binascii
    body = bytes([ftype]) + len(payload).to_bytes(2, "little") + payload
    return b"\xAA\x55" + body + (binascii.crc32(body) & 0xFFFFFFFF).to_bytes(4, "little")


def stats(**kw):
    st = dict((k, 0) for k in D.STATS_FIELDS)
    st.update(kw)
    return st


class TestStats(unittest.TestCase):
    def test_delta_wraps(self):
        d = T.stats_delta(stats(frames_rx=0xFFFFFFFE, skipped=3), stats(frames_rx=2, skipped=3))
        self.assertEqual(d["frames_rx"], 4)
        self.assertEqual(d["skipped"], 0)

    def test_first_stats_notes(self):
        self.assertEqual(T.first_stats_notes(stats()), [])
        notes = T.first_stats_notes(stats(flags=1))
        self.assertEqual(len(notes), 1)
        self.assertIn("démarrage précédent par le chien de garde", notes[0])
        self.assertIn("bits inconnus", T.first_stats_notes(stats(flags=0x04))[0])

    def test_validity(self):
        a = stats(uptime_ms=1000)
        self.assertEqual(T.stats_validity(a, stats(uptime_ms=2000)), ([], []))
        bad, _ = T.stats_validity(a, stats(uptime_ms=500))
        self.assertIn("uptime_ms recule", bad[0])
        bad, _ = T.stats_validity(a, stats(uptime_ms=2000, flags=1))
        self.assertIn("passé à 1", bad[0])
        # Bit 0 déjà à 1 au départ : pas un nouvel incident.
        self.assertEqual(T.stats_validity(stats(uptime_ms=1, flags=1),
                                          stats(uptime_ms=2, flags=1)), ([], []))
        bad, notes = T.stats_validity(a, stats(uptime_ms=2000, flags=0x80))
        self.assertEqual(bad, [])
        self.assertIn("bits inconnus", notes[0])

    def test_phase_stats(self):
        phase = {"frames": 10}
        sent = 10 * T.FRAME_BYTES + T.STATS_REQ_BYTES
        before = stats(bytes_rx=100, skipped=2)          # sondages au branchement : ignorés
        after = stats(frames_rx=10, bytes_rx=100 + sent, skipped=2, swaps=10,
                      loop_max_us=850, refresh_hz=401)
        lines, good = T.phase_stats(phase, before, after)
        self.assertTrue(good)
        text = "\n".join(lines)
        self.assertIn("10 FRAME reçus sur 10 envoyés", text)
        self.assertIn("850 µs", text)
        self.assertIn("401 cycles/s", text)
        self.assertNotIn("Attention", text)
        for field in ("skipped", "crc_errors", "length_errors"):
            after2 = dict(after)
            after2[field] += 1
            self.assertFalse(T.phase_stats(phase, before, after2)[1], field)
        after2 = dict(after, frames_rx=9)
        self.assertFalse(T.phase_stats(phase, before, after2)[1])
        after2 = dict(after, bytes_rx=after["bytes_rx"] + 1)
        lines, good = T.phase_stats(phase, before, after2)
        self.assertTrue(good)                             # écart d'octets : signalé seulement
        self.assertIn("Attention", "\n".join(lines))

    def test_verdict_with_stats(self):
        self.assertIn("bon", T.verdict((16.0, 17, 0), True))
        self.assertIn("hors critère", T.verdict((16.0, 17, 0), False))
        self.assertIn("non atteint", T.verdict((9.0, 10, 0), True))
        self.assertIn("non valable", T.verdict((16.0, 17, 0), True, valid=False))
        self.assertIn("STATS absent", T.verdict((16.0, 17, 0), None))


class TestListenerInvalid(unittest.TestCase):
    def test_short_replies_counted_reading_goes_on(self):
        lst = T.Listener(None, clock=lambda: 1.0)
        good_stats = D.encode(D.STATS, D.stats_payload(frames_rx=7))
        good_tel = D.encode(D.TELEMETRY, struct_tel())
        lst.handle(raw_frame(D.STATS, bytes(46)) + raw_frame(D.TELEMETRY, bytes(8))
                   + raw_frame(D.HELLO, bytes(3)) + good_stats + good_tel)
        self.assertEqual(lst.invalid, {"STATS": 1, "TELEMETRY": 1, "HELLO": 1})
        self.assertEqual(len(lst.stats), 1)
        self.assertEqual(lst.stats[0][1]["frames_rx"], 7)
        self.assertEqual(len(lst.telemetry), 1)
        self.assertIsNone(lst.hello)


def struct_tel():
    import struct
    return struct.pack("<HhBI", 5000, 350, 15, 0)


class HelloOnlyCard:
    """Carte qui ne répond qu'à HELLO_REQ (récepteur MicroPython, ou taille inattendue)."""

    def __init__(self, width=D.WIDTH, height=D.HEIGHT):
        self.size = (width, height)
        self.decoder = D.Decoder()

    def boot(self):
        return b""

    def tick(self):
        return b""

    def feed(self, data):
        out = b""
        for t, _ in self.decoder.feed(data):
            if t == D.HELLO_REQ:
                out += D.encode(D.HELLO, D.hello_payload(self.size[0], self.size[1],
                                                         (0, 1, 0), 5000))
        return out


class TestCheck(unittest.TestCase):
    def run_check(self, sim):
        with PtyServer(sim) as srv:
            out = io.StringIO()
            rc = T.main([srv.path, "--check"], out=out)
        return rc, out.getvalue()

    def test_check_fake_card(self):
        rc, text = self.run_check(FakeI75W())
        self.assertEqual(rc, 0, text)
        self.assertIn("HELLO : protocole 1, 128 × 32", text)
        self.assertIn("STATS : uptime", text)
        self.assertIn("flags 0x00", text)

    def test_check_without_stats(self):
        rc, text = self.run_check(HelloOnlyCard())
        self.assertEqual(rc, 0, text)
        self.assertIn("STATS absent", text)

    def test_check_refuses_size(self):
        rc, text = self.run_check(HelloOnlyCard(64, 32))
        self.assertEqual(rc, 3, text)
        self.assertIn("Refus", text)

    def test_check_no_hello(self):
        class Mute(HelloOnlyCard):
            def feed(self, data):
                return b""
        rc, text = self.run_check(Mute())
        self.assertEqual(rc, 2, text)


class TestAgainstFakeCard(unittest.TestCase):
    """Émetteur complet contre la fausse carte du § 6, sur pseudo-terminal."""

    def test_main(self):
        with PtyServer(FakeI75W()) as srv:
            out = io.StringIO()
            rc = T.main([srv.path, "--duration", "1.6", "--settle", "1.3"], out=out)
        text = out.getvalue()
        self.assertEqual(rc, 0, text)
        self.assertIn("HELLO : protocole 1, 128 × 32", text)
        self.assertIn("Phase 15 images/s visées", text)
        self.assertIn("Phase au maximum", text)
        self.assertIn("carte :", text)
        self.assertIn("Résultat", text)
        self.assertIn("0 CRC faux", text)
        self.assertIn("STATS : uptime", text)
        self.assertEqual(text.count("FRAME reçus sur"), 2)
        self.assertIn("0 octets sautés, 0 CRC faux, 0 longueurs refusées", text)
        self.assertNotIn("non valable", text)
        self.assertNotIn("invalide", text)

    def test_main_without_stats(self):
        with PtyServer(HelloOnlyCard()) as srv:
            out = io.StringIO()
            rc = T.main([srv.path, "--duration", "0.5", "--settle", "0.2", "--no-max"],
                        out=out)
        text = out.getvalue()
        self.assertEqual(rc, 0, text)
        self.assertIn("STATS absent : résultat sur TELEMETRY seule", text)
        self.assertNotIn("FRAME reçus sur", text)


if __name__ == "__main__":
    unittest.main()
