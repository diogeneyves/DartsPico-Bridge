# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Vecteurs de test du DMD (spécification § 8, 33e complément).

Vérifie que firmware/i75w_display/tests/vectors/ est à jour (régénéré en
mémoire et comparé octet par octet), au bon format (ASCII, LF, < 1 Mo), et
relit chaque fichier avec un analyseur indépendant du générateur, comme le
fera la bibliothèque C++ : CRC-32 contre zlib, récepteur contre Decoder,
DELTA contre parse_delta, scénarios contre FakeI75W sans horloge.
"""

import importlib.util
import unittest
import zlib

import _paths  # noqa: F401
import dmd_proto as D
from fake_i75w import FakeI75W

VEC_DIR = _paths.ROOT / "firmware" / "i75w_display" / "tests"
GEN = VEC_DIR / "gen_vectors.py"
LIB = VEC_DIR / "vectors" / "dmd_lib.txt"
CARD = VEC_DIR / "vectors" / "dmd_card.txt"


def _load_gen():
    spec = importlib.util.spec_from_file_location("gen_vectors", GEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _bytes(field):
    return b"" if field == "-" else bytes.fromhex(field)


def _records(path):
    """(numéro de ligne, mot-clé, champs) pour chaque ligne utile."""
    out = []
    for no, line in enumerate(path.read_text("ascii").split("\n"), 1):
        if not line or line.startswith("#"):
            continue
        f = line.split(" ")
        out.append((no, f[0], f[1:]))
    return out


def _crc(b):
    return "%08x" % (zlib.crc32(bytes(b)) & 0xFFFFFFFF)


class Freshness(unittest.TestCase):
    def test_files_match_generator(self):
        gen = _load_gen()
        built = gen.build_all()
        self.assertEqual(sorted(built), ["dmd_card.txt", "dmd_lib.txt"])
        for name, text in built.items():
            with open(VEC_DIR / "vectors" / name, "rb") as fh:
                on_disk = fh.read()
            self.assertTrue(on_disk == text.encode("ascii"),
                            "%s pas à jour : python3 %s" % (name, GEN.relative_to(_paths.ROOT)))

    def test_generator_rng_is_stable(self):
        rng = _load_gen().Rng(1)
        self.assertEqual([rng.u32() for _ in range(3)], [270369, 67634689, 2647435461])


class Format(unittest.TestCase):
    def test_ascii_lf_size(self):
        total = 0
        for p in (LIB, CARD):
            data = p.read_bytes()
            total += len(data)
            data.decode("ascii")
            self.assertNotIn(b"\r", data, p.name)
            self.assertTrue(data.endswith(b"\n") and not data.endswith(b"\n\n"), p.name)
            for b in data:
                self.assertTrue(b == 0x0A or 0x20 <= b < 0x7F, "%s : octet 0x%02X" % (p.name, b))
        self.assertLess(total, 1000000)

    def test_keywords(self):
        allowed = {LIB: {"crc", "image", "decoder", "feed", "frame", "counters", "delta", "result"},
                   CARD: {"scenario", "feed", "reply", "state"}}
        for p, kw in allowed.items():
            for no, k, f in _records(p):
                self.assertIn(k, kw, "%s:%d" % (p.name, no))
                for x in f:
                    self.assertTrue(x and x == x.lower(), "%s:%d" % (p.name, no))


class Crc(unittest.TestCase):
    def test_check_value(self):
        self.assertEqual(zlib.crc32(b"123456789"), 0xCBF43926)
        lines = [f for _n, k, f in _records(LIB) if k == "crc"]
        self.assertIn(["313233343536373839", "cbf43926"], lines)
        self.assertIn(["-", "00000000"], lines)

    def test_all_crc_lines(self):
        n = 0
        for no, k, f in _records(LIB):
            if k == "crc":
                data = _bytes(f[0])
                self.assertEqual(_crc(data), f[1], "ligne %d" % no)
                self.assertEqual("%08x" % D.crc32(data), f[1], "ligne %d" % no)
                n += 1
        self.assertGreaterEqual(n, 10)


class LibReplay(unittest.TestCase):
    def setUp(self):
        self.recs = _records(LIB)
        self.images = {f[0]: _bytes(f[1]) for _n, k, f in self.recs if k == "image"}

    def test_images(self):
        for name, img in self.images.items():
            self.assertEqual(len(img), D.PIXELS_BYTES, name)

    def test_decoder_cases(self):
        names = []
        dec = None
        expected = []
        for no, k, f in self.recs:
            if k == "decoder":
                names.append(f[0])
                dec = D.Decoder()
            elif k == "feed" and dec is not None:
                got = [("%02x" % t, str(len(p)), _crc(p)) for t, p in dec.feed(_bytes(f[0]))]
                expected.append((no, got))
            elif k == "frame":
                pass
            elif k == "counters":
                self.assertEqual(f, [str(dec.crc_errors), str(dec.length_errors),
                                     str(dec.skipped), str(dec.frames), str(len(dec._buf))],
                                 "ligne %d (%s)" % (no, names[-1]))
                dec = None
        # Trames attendues : les lignes « frame » qui suivent chaque « feed ».
        frames_after = {}
        last_feed = None
        in_decoder = False
        for no, k, f in self.recs:
            if k == "decoder":
                in_decoder = True
            elif k in ("delta", "image", "crc"):
                in_decoder = False
            if not in_decoder:
                continue
            if k == "feed":
                last_feed = no
                frames_after[no] = []
            elif k == "frame":
                frames_after[last_feed].append(tuple(f))
        for no, got in expected:
            self.assertEqual(got, frames_after[no], "feed ligne %d" % no)
        for required in ("byte_by_byte", "split_frame", "garbage_before", "sync_in_pixels",
                         "trailing_aa_pending", "trailing_aa_completed", "crc_error_then_frame",
                         "crc_error_in_pixels", "length_over_type_max",
                         "length_over_8194_unknown_type", "delta_8194", "delta_8195",
                         "false_header_plausible", "partial_at_end"):
            self.assertIn(required, names)

    def test_pending_aa_not_counted(self):
        it = iter(self.recs)
        for _no, k, f in it:
            if k == "decoder" and f[0] == "trailing_aa_pending":
                break
        _no, k, f = next(it)
        self.assertEqual((k, f), ("feed", ["0102aa"]))
        _no, k, f = next(it)
        self.assertEqual((k, f), ("counters", ["0", "0", "2", "0", "1"]))

    def test_delta_cases(self):
        names = []
        pending = None
        for no, k, f in self.recs:
            if k == "delta":
                name, base_name, payload = f[0], f[1], _bytes(f[2])
                names.append(name)
                try:
                    number, base, tiles = D.parse_delta(payload)
                    mask = D.delta_header(payload)[2]
                    img = bytearray(self.images[base_name])
                    for i, t in tiles:
                        D.put_tile(img, i, t)
                    pending = ["ok", str(number), str(base), "%016x" % mask, _crc(img)]
                except D.PayloadError as e:
                    pending = ["error", "%02x" % e.code]
            elif k == "result":
                self.assertEqual(f, pending, "ligne %d (%s)" % (no, names[-1]))
                pending = None
        results = [f[0] for _n, k, f in self.recs if k == "result"]
        self.assertEqual(len(results), len(names))
        errors = [f[1] for _n, k, f in self.recs if k == "result" and f[0] == "error"]
        self.assertIn("02", errors)
        self.assertGreaterEqual(errors.count("04"), 10)
        for required in ("raw_tile_0", "rle_one_run", "empty_mask", "raw_63_tiles",
                         "mode_2", "rle_count_zero", "rle_sum_over_64", "rle_sum_under_64_end",
                         "raw_truncated", "extra_byte", "mask_bit_no_data", "too_short_11"):
            self.assertIn(required, names)

    def test_empty_mask_keeps_image(self):
        for _no, k, f in self.recs:
            if k == "delta" and f[0] == "empty_mask":
                self.assertEqual(len(_bytes(f[2])), D.DELTA_HEADER)
                base_crc = _crc(self.images[f[1]])
        res = [f for i, (_n, k, f) in enumerate(self.recs)
               if k == "result" and self.recs[i - 1][2][0] == "empty_mask"]
        self.assertEqual(res[0][-1], base_crc)


class CardReplay(unittest.TestCase):
    def test_scenarios(self):
        recs = _records(CARD)
        names = []
        card = rx = None
        replies = []
        errors = set()
        for no, k, f in recs:
            where = "ligne %d (%s)" % (no, names[-1] if names else "?")
            if k == "scenario":
                names.append(f[0])
                card = FakeI75W(clock=lambda: 0)
                rx = D.Decoder()
            elif k == "feed":
                replies = []
                for t, p in rx.feed(card.feed(_bytes(f[0]))):
                    if t == D.ERROR:
                        replies.append(["e0", "%02x" % p[0]])
                    elif t == D.HELLO:
                        h = D.parse_hello(p)
                        replies.append(["81", str(h[0]), str(h[1]), str(h[2])])
                    elif t == D.STATS:
                        # Longueur et octets 4 à 35 (frames_rx à swaps), 38e complément.
                        replies.append(["88", str(len(p)), p[4:36].hex()])
                    else:
                        replies.append(["%02x" % t, p.hex() if p else "-"])
            elif k == "reply":
                self.assertTrue(replies, where)
                self.assertEqual(f, replies.pop(0), where)
                if f[0] == "e0":
                    errors.add(f[1])
            elif k == "state":
                self.assertEqual(replies, [], where)
                no_ = "-" if card.displayed_no is None else str(card.displayed_no)
                self.assertEqual(f, [_crc(card.framebuffer), _crc(card.backbuffer), no_,
                                     str(card.lost), str(card.frames_shown),
                                     str(card.deltas_ignored), str(card.mode()),
                                     "1" if card.need_frame else "0", str(card.brightness)],
                                 where)
        # Chaque « feed » est suivi de son « state ».
        kinds = [k for _n, k, _f in recs]
        for i, k in enumerate(kinds):
            if k == "feed":
                j = i + 1
                while kinds[j] == "reply":
                    j += 1
                self.assertEqual(kinds[j], "state")
        self.assertEqual(errors, {"01", "02", "03", "04"})
        for required in ("frame_delta_raw", "frame_delta_encoder", "delta_before_first_frame",
                         "delta_wrong_base", "delta_invalid_content", "clear", "mode",
                         "crc_error", "resync", "bad_lengths", "numbering", "split_feeds"):
            self.assertIn(required, names)


if __name__ == "__main__":
    unittest.main()
