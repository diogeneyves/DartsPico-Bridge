# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests du protocole binaire Pi <-> Interstate 75 W (spécification § 6)."""

import random
import struct
import unittest
import zlib

import _paths  # noqa: F401
import dmd_proto as D


def pixels(seed=0):
    rnd = random.Random(seed)
    return bytes(rnd.getrandbits(8) for _ in range(D.PIXELS_BYTES))


class Crc(unittest.TestCase):
    def test_crc32_zip_reference(self):
        # Valeur de contrôle du CRC-32 « ZIP » sur "123456789".
        self.assertEqual(D.crc32(b"123456789"), 0xCBF43926)
        self.assertEqual(D.crc32(b"abc"), zlib.crc32(b"abc"))

    def test_frame_layout(self):
        f = D.encode(D.BRIGHTNESS, b"\x80")
        self.assertEqual(f[:2], b"\xAA\x55")
        self.assertEqual(f[2], D.BRIGHTNESS)
        self.assertEqual(f[3:5], b"\x01\x00")                  # longueur petit-boutiste
        self.assertEqual(f[5:6], b"\x80")
        crc = struct.unpack("<I", f[6:10])[0]                    # CRC petit-boutiste
        self.assertEqual(crc, zlib.crc32(f[2:6]))               # type + longueur + données
        self.assertEqual(len(f), 10)

    def test_encode_rejects(self):
        with self.assertRaises(ValueError):
            D.encode(D.BRIGHTNESS, b"\x01\x02")
        with self.assertRaises(ValueError):
            D.encode(D.PING, bytes(65))
        with self.assertRaises(ValueError):
            D.encode(0x7F, bytes(D.MAX_PAYLOAD + 1))
        with self.assertRaises(ValueError):
            D.encode(256)


class Payloads(unittest.TestCase):
    def test_hello(self):
        p = D.hello_payload(128, 32, (1, 2, 3), 5012)
        self.assertEqual(len(p), 10)
        self.assertEqual(D.parse_hello(p), (1, 128, 32, (1, 2, 3), 5012))
        with self.assertRaises(D.PayloadError) as cm:
            D.parse_hello(p[:9])
        self.assertEqual(cm.exception.code, D.ERR_LENGTH)

    def test_frame(self):
        px = pixels(1)
        p = D.frame_payload(0x1_0005, px)                      # numéro sur 16 bits
        self.assertEqual(len(p), D.MAX_PAYLOAD)
        n, view = D.parse_frame(p)
        self.assertEqual(n, 5)
        self.assertEqual(bytes(view), px)
        with self.assertRaises(ValueError):
            D.frame_payload(0, px[:-1])
        with self.assertRaises(D.PayloadError):
            D.parse_frame(p[:-1])

    def test_rgb565_little_endian(self):
        self.assertEqual(D.rgb565(255, 0, 0), 0xF800)
        self.assertEqual(D.rgb565(0, 255, 0), 0x07E0)
        self.assertEqual(D.rgb565(0, 0, 255), 0x001F)
        self.assertEqual(struct.pack("<H", D.rgb565(255, 0, 0)), b"\x00\xF8")

    def test_telemetry(self):
        p = D.telemetry_payload(4980, -105, D.TARGET_FPS, 7)
        self.assertEqual(len(p), 9)
        self.assertEqual(D.parse_telemetry(p), (4980, -105, 15, 7))

    def test_target_fps(self):
        self.assertEqual(D.TARGET_FPS, 15)                             # § 6.3

    def test_error(self):
        p = D.error_payload(D.ERR_UNKNOWN_TYPE, "type 0x42")
        self.assertEqual(D.parse_error(p), (1, "type 0x42"))
        self.assertEqual(len(D.error_payload(1, "x" * 500)), 129)
        with self.assertRaises(D.PayloadError):
            D.parse_error(b"")

    def test_u8(self):
        self.assertEqual(D.parse_u8(D.MODE, b"\x01", 1), 1)
        with self.assertRaises(D.PayloadError) as cm:
            D.parse_u8(D.MODE, b"\x02", 1)
        self.assertEqual(cm.exception.code, D.ERR_RANGE)
        with self.assertRaises(D.PayloadError) as cm:
            D.check_length(0x42, 0)
        self.assertEqual(cm.exception.code, D.ERR_UNKNOWN_TYPE)


class Decoding(unittest.TestCase):
    def test_single_and_multiple(self):
        dec = D.Decoder()
        data = D.encode(D.PING, b"abc") + D.encode(D.CLEAR) + D.encode(D.HELLO_REQ)
        self.assertEqual(dec.feed(data), [(D.PING, b"abc"), (D.CLEAR, b""), (D.HELLO_REQ, b"")])
        self.assertEqual((dec.crc_errors, dec.length_errors, dec.skipped), (0, 0, 0))

    def test_byte_by_byte_full_frame(self):
        dec = D.Decoder()
        px = pixels(2)
        data = D.encode(D.FRAME, D.frame_payload(42, px))
        got = []
        for b in data:
            got += dec.feed(bytes([b]))
        self.assertEqual(len(got), 1)
        self.assertEqual(D.parse_frame(got[0][1])[0], 42)
        self.assertEqual(bytes(D.parse_frame(got[0][1])[1]), px)

    def test_random_chunks(self):
        rnd = random.Random(3)
        frames = [D.encode(D.FRAME, D.frame_payload(i, pixels(i))) for i in range(5)]
        stream = b"".join(frames)
        dec = D.Decoder()
        got = []
        pos = 0
        while pos < len(stream):
            k = rnd.randint(1, 3000)
            got += dec.feed(stream[pos:pos + k])
            pos += k
        self.assertEqual([D.parse_frame(p)[0] for _, p in got], [0, 1, 2, 3, 4])

    def test_garbage_before_and_between(self):
        dec = D.Decoder()
        data = b"\x00\x11\xAA\x22" + D.encode(D.CLEAR) + b"\xAA\xAA" + D.encode(D.MODE, b"\x01")
        self.assertEqual(dec.feed(data), [(D.CLEAR, b""), (D.MODE, b"\x01")])
        self.assertEqual(dec.skipped, 6)

    def test_trailing_aa_kept(self):
        dec = D.Decoder()
        f = D.encode(D.CLEAR)
        self.assertEqual(dec.feed(b"\x00" + f[:1]), [])
        self.assertEqual(dec.feed(f[1:]), [(D.CLEAR, b"")])

    def test_crc_error_then_resync(self):
        dec = D.Decoder()
        bad = bytearray(D.encode(D.PING, b"xyz"))
        bad[-1] ^= 0xFF
        good = D.encode(D.CLEAR)
        self.assertEqual(dec.feed(bytes(bad) + good), [(D.CLEAR, b"")])
        self.assertEqual(dec.crc_errors, 1)

    def test_corrupted_byte_in_frame(self):
        dec = D.Decoder()
        f = bytearray(D.encode(D.FRAME, D.frame_payload(1, pixels(4))))
        f[1000] ^= 0x01
        out = dec.feed(bytes(f) + D.encode(D.FRAME, D.frame_payload(2, pixels(5))))
        self.assertEqual([D.parse_frame(p)[0] for _, p in out], [2])
        self.assertEqual(dec.crc_errors, 1)

    def test_false_sync_inside_pixels_does_not_skip_real_frame(self):
        # Une fausse en-tête annonce une longueur qui « avalerait » la vraie trame
        # suivante ; le récepteur doit reprendre à l'octet après le 0xAA.
        real = D.encode(D.BRIGHTNESS, b"\x10")
        fake_header = b"\xAA\x55" + bytes([D.PING]) + struct.pack("<H", 40)
        dec = D.Decoder()
        out = dec.feed(fake_header + real + bytes(60))
        self.assertIn((D.BRIGHTNESS, b"\x10"), out)
        self.assertEqual(dec.crc_errors, 1)

    def test_length_above_type_maximum_rejected_at_once(self):
        dec = D.Decoder()
        fake = b"\xAA\x55" + bytes([D.BRIGHTNESS]) + struct.pack("<H", 5000)
        out = dec.feed(fake + D.encode(D.CLEAR))
        self.assertEqual(out, [(D.CLEAR, b"")])                # sans attendre 5 000 octets
        self.assertEqual(dec.length_errors, 1)

    def test_unknown_type_is_delivered(self):
        dec = D.Decoder()
        self.assertEqual(dec.feed(D.encode(0x42, b"zz")), [(0x42, b"zz")])
        big = b"\xAA\x55\x42" + struct.pack("<H", D.MAX_PAYLOAD + 1)
        self.assertEqual(dec.feed(big), [])
        self.assertEqual(dec.length_errors, 1)

    def test_aa55_sequences_inside_payload(self):
        px = (b"\xAA\x55" * (D.PIXELS_BYTES // 2))
        dec = D.Decoder()
        out = dec.feed(D.encode(D.FRAME, D.frame_payload(9, px)))
        self.assertEqual(len(out), 1)
        self.assertEqual(bytes(D.parse_frame(out[0][1])[1]), px)

    def test_reset_drops_partial(self):
        dec = D.Decoder()
        f = D.encode(D.PING, b"abc")
        dec.feed(f[:6])
        dec.reset()
        self.assertEqual(dec.feed(D.encode(D.CLEAR)), [(D.CLEAR, b"")])

    def test_fuzz_recovers_every_frame(self):
        rnd = random.Random(7)
        dec = D.Decoder()
        good = D.encode(D.MODE, b"\x00")
        received = 0
        for _ in range(300):
            noise = bytes(rnd.choice((0xAA, 0x55, rnd.getrandbits(8))) for _ in range(rnd.randint(0, 40)))
            for t, p in dec.feed(noise + good):
                if (t, p) == (D.MODE, b"\x00"):
                    received += 1
        # Une fausse en-tête du bruit peut annoncer jusqu'à 8 194 octets : les
        # trames suivantes attendent alors que ces octets soient arrivés et que
        # le CRC échoue, puis sont toutes récupérées. On complète le flux.
        for t, p in dec.feed(bytes(D.MAX_PAYLOAD + D.HEADER_LEN + D.CRC_LEN)):
            if (t, p) == (D.MODE, b"\x00"):
                received += 1
        self.assertEqual(received, 300)

    def test_false_header_delays_but_does_not_lose(self):
        dec = D.Decoder()
        fake = b"\xAA\x55" + bytes([D.FRAME]) + struct.pack("<H", D.MAX_PAYLOAD)
        good = D.encode(D.CLEAR)
        self.assertEqual(dec.feed(fake + good), [])            # retenue
        out = dec.feed(bytes(D.MAX_PAYLOAD + D.CRC_LEN))
        self.assertEqual(out, [(D.CLEAR, b"")])                 # puis récupérée
        self.assertEqual(dec.crc_errors, 1)



# ------------------------------------------------------------ DELTA (§ 6.4.1)

def solid(color=0x0000):
    return struct.pack("<H", color) * (D.WIDTH * D.HEIGHT)


def set_pixel(img, x, y, color):
    img[2 * (y * D.WIDTH + x):2 * (y * D.WIDTH + x) + 2] = struct.pack("<H", color)


class Delta(unittest.TestCase):
    def test_constants(self):
        self.assertEqual((D.DELTA, D.ERR_DELTA, D.TILE_COUNT), (0x07, 0x04, 64))
        self.assertEqual(D.max_length(D.DELTA), 8194)
        D.check_length(D.DELTA, 12)
        with self.assertRaises(D.PayloadError) as cm:
            D.check_length(D.DELTA, 11)
        self.assertEqual(cm.exception.code, D.ERR_LENGTH)

    def test_tile_numbering_and_pixel_order(self):
        # Tuile i = ty × 16 + tx ; pixels ligne par ligne dans la tuile.
        img = bytearray(solid())
        set_pixel(img, 8 * 5 + 3, 8 * 2 + 1, 0xABCD)       # tx 5, ty 2 -> tuile 37
        t = D.tile_bytes(img, 37)
        k = 1 * 8 + 3
        self.assertEqual(t[2 * k:2 * k + 2], b"\xCD\xAB")
        p = D.delta_payload(9, 8, solid(), img)
        number, base, mask = struct.unpack("<HHQ", p[:12])
        self.assertEqual((number, base, mask), (9, 8, 1 << 37))
        self.assertEqual(p[12], D.TILE_RLE)
        # RLE : 11 pixels noirs, 1 pixel 0xABCD, 52 pixels noirs.
        self.assertEqual(p[13:], bytes([11, 0, 0, 1, 0xCD, 0xAB, 52, 0, 0]))

    def test_rle_round_trip(self):
        rnd = random.Random(3)
        old = solid(0x1234)
        new = bytearray(old)
        for _ in range(300):
            set_pixel(new, rnd.randrange(128), rnd.randrange(32),
                      rnd.choice((0xF800, 0x07E0, 0x001F)))
        for y in range(10, 26):                           # bande de texte
            for x in range(0, 128, 3):
                set_pixel(new, x, y, 0xFFFF)
        p = D.delta_payload(1, 0, old, bytes(new))
        self.assertIsNotNone(p)
        number, base, tiles = D.parse_delta(p)
        img = bytearray(old)
        for i, t in tiles:
            D.put_tile(img, i, t)
        self.assertEqual(bytes(img), bytes(new))
        self.assertEqual(D.encode(D.DELTA, p)[2], D.DELTA)

    def test_checkerboard_tile_is_raw(self):
        img = bytearray(solid())
        for y in range(8):
            for x in range(8):
                if (x + y) % 2:                           # damier
                    set_pixel(img, x, y, 0xFFFF)
        t = D.tile_bytes(img, 0)
        # 57 plages (fin de ligne et début de la suivante de même couleur).
        self.assertEqual(len(D.rle_tile(t)), 3 * 57)
        self.assertEqual(D.encode_tile(t), bytes([D.TILE_RAW]) + t)
        p = D.delta_payload(1, 0, solid(), bytes(img))
        self.assertEqual(len(p), 12 + 1 + 128)

    def test_rle_at_128_bytes_is_raw(self):
        # 42 plages = 126 octets : RLE ; 43 plages = 129 : brut.
        def tile(runs):
            colors = [0x0000, 0xFFFF]
            px = [colors[min(r, runs - 1) % 2] for r in range(64)]
            return b"".join(struct.pack("<H", c) for c in px)
        self.assertEqual(D.encode_tile(tile(42))[0], D.TILE_RLE)
        self.assertEqual(D.encode_tile(tile(43))[0], D.TILE_RAW)

    def test_identical_image_gives_empty_delta(self):
        img = pixels(5)
        p = D.delta_payload(7, 6, img, img)
        self.assertEqual(p, struct.pack("<HHQ", 7, 6, 0))
        self.assertEqual(D.parse_delta(p), (7, 6, []))

    def test_worst_case_falls_back_to_frame(self):
        self.assertIsNone(D.delta_payload(1, 0, solid(), pixels(1)))
        # 63 tuiles brutes : 12 + 63 × 129 = 8 139 octets, tient encore.
        old = solid()
        new = bytearray(pixels(2))
        for y in range(8):                                # tuile 0 inchangée
            new[y * 256:y * 256 + 16] = old[y * 256:y * 256 + 16]
        p = D.delta_payload(1, 0, old, bytes(new))
        self.assertEqual(len(p), 12 + 63 * 129)
        self.assertEqual(struct.unpack("<Q", p[4:12])[0], ((1 << 64) - 1) & ~1)

    def test_invalid_content(self):
        tile = bytes([D.TILE_RLE, 64, 0x11, 0x22])
        ok = struct.pack("<HHQ", 1, 0, 1 << 3) + tile
        self.assertEqual(len(D.parse_delta(ok)[2]), 1)
        cases = {
            "tronqué": ok[:-1],
            "tuile absente": struct.pack("<HHQ", 1, 0, (1 << 3) | (1 << 4)) + tile,
            "en trop": ok + b"\x00",
            "masque nul avec données": struct.pack("<HHQ", 1, 0, 0) + tile,
            "mode inconnu": struct.pack("<HHQ", 1, 0, 1) + bytes([2]) + bytes(128),
            "compte nul": struct.pack("<HHQ", 1, 0, 1) + bytes([1, 0, 0, 0, 64, 0, 0]),
            "somme 63": struct.pack("<HHQ", 1, 0, 1) + bytes([1, 63, 0, 0]),
            "somme 65": struct.pack("<HHQ", 1, 0, 1) + bytes([1, 63, 0, 0, 2, 0, 0]),
            "brut tronqué": struct.pack("<HHQ", 1, 0, 1) + bytes([0]) + bytes(127),
        }
        for name, p in cases.items():
            with self.subTest(name):
                with self.assertRaises(D.PayloadError) as cm:
                    D.parse_delta(p)
                self.assertEqual(cm.exception.code, D.ERR_DELTA)


class Stats(unittest.TestCase):
    def test_codes_and_lengths(self):
        self.assertEqual((D.STATS_REQ, D.STATS), (0x08, 0x88))
        self.assertEqual(D.STATS, D.STATS_REQ + D.REPLY)
        self.assertEqual(D.STATS_LEN, 47)
        self.assertEqual(D.max_length(D.STATS_REQ), 0)
        self.assertEqual(D.max_length(D.STATS), 47)
        D.check_length(D.STATS_REQ, 0)
        with self.assertRaises(D.PayloadError):
            D.check_length(D.STATS_REQ, 1)

    def test_byte_layout(self):
        # Octets du § 6.2 : 11 × u32 (0 à 43), u16 (44-45), u8 (46), petit-boutistes.
        fields = {name: i + 1 for i, name in enumerate(D.STATS_FIELDS)}
        p = D.stats_payload(**fields)
        self.assertEqual(len(p), 47)
        for i in range(11):
            self.assertEqual(p[4 * i:4 * i + 4], struct.pack("<I", i + 1))
        self.assertEqual(p[44:46], b"\x0c\x00")
        self.assertEqual(p[46], 13)
        self.assertEqual(D.parse_stats(p), fields)
        self.assertEqual(D.STATS_FIELDS[1], "frames_rx")
        self.assertEqual(D.STATS_FIELDS[8], "swaps")

    def test_wrap_and_defaults(self):
        st = D.parse_stats(D.stats_payload(bytes_rx=(1 << 32) + 5, refresh_hz=70000))
        self.assertEqual(st["bytes_rx"], 5)
        self.assertEqual(st["refresh_hz"], 0xFFFF)
        self.assertEqual(st["flags"], 0)
        with self.assertRaises(ValueError):
            D.stats_payload(inconnu=1)


if __name__ == "__main__":
    unittest.main()
