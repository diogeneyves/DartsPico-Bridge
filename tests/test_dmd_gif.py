# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests des délais des GIF, de la mise à l'échelle et du minutage des médias
(§ 9.9, 48e complément, sous-phase SR-c).

Python standard seulement : tournent partout, sans Pillow. Les GIF sont
fabriqués octet par octet (le contenu LZW n'est pas décodé par dmd_gif).
"""

import io
import os
import struct
import tempfile
import unittest

import _paths  # noqa: F401
import dmd_gif as G


def gce(delay, transparent=False):
    return b"\x21\xF9\x04" + bytes([1 if transparent else 0]) + struct.pack("<H", delay) + b"\x00\x00"


def netscape(loops=0):
    return b"\x21\xFF\x0BNETSCAPE2.0\x03\x01" + struct.pack("<H", loops) + b"\x00"


def comment(text=b"essai"):
    return b"\x21\xFE" + bytes([len(text)]) + text + b"\x00"


def image(w=4, h=2, local_table=False):
    desc = b"\x2C" + struct.pack("<HHHH", 0, 0, w, h) + bytes([0x80 if local_table else 0])
    table = bytes(6) if local_table else b""
    return desc + table + b"\x02" + b"\x03\x01\x02\x03" + b"\x02\x44\x01" + b"\x00"


def gif(*blocks, w=4, h=2, version=b"GIF89a", global_table=True, trailer=True):
    lsd = struct.pack("<HH", w, h) + bytes([0x80 if global_table else 0, 0, 0])
    table = bytes(6) if global_table else b""
    return version + lsd + table + b"".join(blocks) + (b"\x3B" if trailer else b"")


def info(data):
    return G.read_gif_file(io.BytesIO(data))


class DelayTest(unittest.TestCase):
    def test_delays_0_2_10(self):
        i = info(gif(gce(0), image(), gce(2), image(), gce(10), image()))
        self.assertEqual(i.raw_delays, (0, 2, 10))
        self.assertEqual(i.delays, (10, 2, 10))
        self.assertEqual(i.delays_us, (100000, 20000, 100000))

    def test_delay_1_is_10(self):
        self.assertEqual(info(gif(gce(1), image())).delays, (10,))

    def test_no_gce_is_10(self):
        i = info(gif(image(), gce(7), image(), image()))
        self.assertEqual(i.raw_delays, (0, 7, 0))
        self.assertEqual(i.delays, (10, 7, 10))

    def test_large_delay_kept(self):
        self.assertEqual(info(gif(gce(500), image())).delays, (500,))

    def test_gce_applies_to_next_image_only(self):
        self.assertEqual(info(gif(gce(5), comment(), image(), image())).raw_delays, (5, 0))

    def test_netscape_read_and_ignored(self):
        with_loop = info(gif(netscape(3), gce(4), image(), gce(4), image()))
        without = info(gif(gce(4), image(), gce(4), image()))
        self.assertEqual(with_loop.netscape_loops, 3)
        self.assertIsNone(without.netscape_loops)
        self.assertEqual(with_loop.delays, without.delays)

    def test_size_and_tables(self):
        i = info(gif(gce(3), image(117, 32, local_table=True), w=117, h=32, global_table=False))
        self.assertEqual((i.width, i.height), (117, 32))
        self.assertEqual(i.delays, (3,))

    def test_gif87a(self):
        self.assertEqual(info(gif(image(), version=b"GIF87a")).delays, (10,))

    def test_not_a_gif(self):
        for data in (b"", b"GIF8", b"P6\n128 32\n255\n" + bytes(30), b"PNG..........."):
            with self.assertRaises(ValueError):
                info(data)

    def test_no_image(self):
        with self.assertRaises(ValueError):
            info(gif(netscape(), gce(5)))

    def test_unknown_block(self):
        with self.assertRaises(ValueError):
            info(gif(image(), b"\x99"))

    def test_truncated_keeps_complete_images(self):
        data = gif(gce(4), image(), gce(6), image(), trailer=False)
        self.assertEqual(info(data).delays, (4, 6))
        self.assertEqual(info(data[:-3]).delays, (4,))     # 2e image coupée

    def test_read_gif_from_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.gif")
            with open(path, "wb") as f:
                f.write(gif(gce(8), image()))
            self.assertEqual(G.read_gif(path).delays, (8,))
            with self.assertRaises(OSError):
                G.read_gif(os.path.join(tmp, "absent.gif"))


class FitTest(unittest.TestCase):
    def test_smaller_kept(self):
        self.assertEqual(G.fit_size(128, 32), (128, 32))
        self.assertEqual(G.fit_size(117, 32), (117, 32))
        self.assertEqual(G.fit_size(128, 30), (128, 30))
        self.assertEqual(G.fit_size(10, 5), (10, 5))

    def test_larger_reduced_proportionally(self):
        self.assertEqual(G.fit_size(256, 64), (128, 32))
        self.assertEqual(G.fit_size(200, 20), (128, 13))     # largeur limitante
        self.assertEqual(G.fit_size(64, 64), (32, 32))        # hauteur limitante
        self.assertEqual(G.fit_size(130, 32), (128, 32))
        self.assertEqual(G.fit_size(1000, 1), (128, 1))

    def test_offset_centres(self):
        self.assertEqual(G.offset((117, 32)), (5, 0))
        self.assertEqual(G.offset((128, 30)), (0, 1))
        self.assertEqual(G.offset((128, 32)), (0, 0))

    def test_bad_size(self):
        with self.assertRaises(ValueError):
            G.fit_size(0, 32)


MS = 1000


class TimelineTest(unittest.TestCase):
    def test_index_follows_real_time(self):
        t = G.Timeline((20 * MS,) * 10)                   # 10 images de 2/100 s
        self.assertEqual(t.total_us, 200 * MS)
        step = 1000000 // 15
        # à 15 images/s, les images plus courtes qu'un pas sont sautées
        self.assertEqual([t.index_at(n * step) for n in range(3)], [0, 3, 6])

    def test_uneven_delays(self):
        t = G.Timeline((100 * MS, 20 * MS, 100 * MS))
        self.assertEqual([t.index_at(x * MS) for x in (0, 99, 100, 119, 120, 219)], [0, 0, 1, 1, 2, 2])

    def test_done_and_last_frame_kept(self):
        t = G.Timeline((100 * MS, 100 * MS))
        self.assertFalse(t.done(199 * MS))
        self.assertTrue(t.done(200 * MS))
        self.assertEqual(t.index_at(10 ** 9), 1)

    def test_attract_short_gif_replayed_to_5_s(self):
        t = G.Timeline((1000 * MS,) * 2, G.ATTRACT_MIN_US, G.ATTRACT_MAX_US)   # 2 s
        self.assertEqual((t.passes, t.total_us), (3, 6000 * MS))
        self.assertEqual(t.position(4500 * MS), (2, 0))
        t = G.Timeline((2500 * MS,), G.ATTRACT_MIN_US, G.ATTRACT_MAX_US)       # 2,5 s : 2 fois, 5 s
        self.assertEqual((t.passes, t.total_us), (2, 5000 * MS))
        t = G.Timeline((5000 * MS,), G.ATTRACT_MIN_US, G.ATTRACT_MAX_US)
        self.assertEqual((t.passes, t.total_us), (1, 5000 * MS))

    def test_attract_long_gif_cut_at_60_s(self):
        # 50e complément : coupé à 60 s (attract_gif_max_s), 30 s au 48e
        self.assertEqual(G.ATTRACT_MAX_US, 60000 * MS)
        t = G.Timeline((1000 * MS,) * 90, G.ATTRACT_MIN_US, G.ATTRACT_MAX_US)
        self.assertEqual(t.total_us, 60000 * MS)
        self.assertEqual(t.index_at(59999 * MS), 59)
        self.assertTrue(t.done(60000 * MS))
        t = G.Timeline((1000 * MS,) * 45, G.ATTRACT_MIN_US, G.ATTRACT_MAX_US)
        self.assertEqual(t.total_us, 45000 * MS)               # 45 s : plus coupé

    def test_single_frame_gif_in_attract(self):
        t = G.Timeline((100 * MS,), G.ATTRACT_MIN_US, G.ATTRACT_MAX_US)
        self.assertEqual((t.passes, t.total_us), (50, 5000 * MS))

    def test_one_pass_without_limits(self):
        t = G.Timeline((40 * MS,) * 2000)                  # 80 s, pas de coupure hors Attract Mode
        self.assertEqual((t.passes, t.total_us), (1, 80000 * MS))

    def test_bad_delays(self):
        for delays in ((), (0,), (10, -1)):
            with self.assertRaises(ValueError):
                G.Timeline(delays)


if __name__ == "__main__":
    unittest.main()
