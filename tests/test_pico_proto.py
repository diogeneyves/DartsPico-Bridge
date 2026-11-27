# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests du protocole texte Pi <-> Pico (spécification § 5)."""

import unittest

import _paths  # noqa: F401
import pico_proto as P
from pico_proto import ProtocolError


class Helpers(unittest.TestCase):
    def assertRefused(self, fn, line, code, tag="absent"):
        with self.assertRaises(ProtocolError) as cm:
            fn(line)
        self.assertEqual(cm.exception.code, code, line)
        if tag != "absent":
            self.assertEqual(cm.exception.tag, tag, line)


class Ticks(unittest.TestCase):
    def test_diff_wraps_modulo_2_30(self):
        self.assertEqual(P.ticks_diff(5, 3), 2)
        self.assertEqual(P.ticks_diff(3, 5), -2)
        # Le compteur a bouclé entre b et a.
        self.assertEqual(P.ticks_diff(10, P.TICKS_MASK - 9), 20)
        self.assertEqual(P.ticks_diff(P.TICKS_MASK - 9, 10), -20)

    def test_messages_mask_ticks_to_30_bits(self):
        self.assertEqual(P.msg_pong((1 << 30) + 7), "PONG 7")
        with self.assertRaises(ProtocolError):
            P.parse_event("PONG %d" % (1 << 30))


class Numbers(Helpers):
    def test_dec_strict(self):
        self.assertEqual(P.parse_dec("0042", 0, 100), 42)
        for bad in ("", "+5", "-1", "1_0", "1.0", " 1", "٣", "12345678901", "0x10"):
            with self.subTest(bad=bad):
                self.assertRefused(lambda s: P.parse_dec(s, 0, 10**12), bad, P.ARGS)
        self.assertRefused(lambda s: P.parse_dec(s, 1, 8), "9", P.RANGE)

    def test_hex_mask(self):
        self.assertEqual(P.parse_hex("ff", 8), 255)
        self.assertEqual(P.parse_hex("00FF", 8), 255)
        self.assertEqual(P.parse_hex("7FFF", 15), 0x7FFF)
        self.assertRefused(lambda s: P.parse_hex(s, 8), "100", P.RANGE)
        self.assertRefused(lambda s: P.parse_hex(s, 15), "8000", P.RANGE)
        for bad in ("", "0x1", "G", "-1"):
            self.assertRefused(lambda s: P.parse_hex(s, 8), bad, P.ARGS)


class Syntax(Helpers):
    def test_strict_spacing_and_charset(self):
        for bad in (" PING", "PING ", "TOY  1 1", "TOY\t1 1", "PING\x00", "PINGé", ""):
            with self.subTest(bad=bad):
                self.assertRefused(P.parse_command, bad, P.SYNTAX)

    def test_lowercase_keyword_is_unknown(self):
        self.assertRefused(P.parse_command, "ping", P.UNKNOWN)
        self.assertRefused(P.parse_command, "FOO", P.UNKNOWN)

    def test_line_limit_exact(self):
        lim = P.DEFAULT_LIMITS.max_line
        self.assertEqual(lim, 2048)
        ok = "GET " + "a" * (lim - 4)
        self.assertEqual(len(ok), lim)
        self.assertRefused(P.parse_command, ok, P.KEY)       # longueur acceptée
        self.assertRefused(P.parse_command, ok + "a", P.TOOLONG)

    def test_limits_are_configurable(self):
        lim = P.Limits(max_line=100, max_leds=10)
        self.assertRefused(lambda s: P.parse_command(s, lim), "GET " + "a" * 97, P.TOOLONG)
        self.assertEqual(P.parse_command("LEDCFG 0 10", lim), (None, "LEDCFG", (0, 10)))
        self.assertRefused(lambda s: P.parse_command(s, lim), "LEDCFG 0 11", P.RANGE)
        self.assertRefused(lambda s: P.parse_command(s, lim), "SET led0_count 11", P.RANGE)


class Prefix(Helpers):
    def test_prefix(self):
        self.assertEqual(P.parse_command("@7 PING"), (7, "PING", ()))
        self.assertEqual(P.parse_command("@65535 PING"), (65535, "PING", ()))
        self.assertEqual(P.parse_command("PING"), (None, "PING", ()))

    def test_bad_prefix_is_syntax_without_tag(self):
        for bad in ("@abc PING", "@ PING", "@65536 PING", "@-1 PING"):
            self.assertRefused(P.parse_command, bad, P.SYNTAX, tag=None)

    def test_prefix_without_command(self):
        self.assertRefused(P.parse_command, "@3", P.SYNTAX, tag=3)

    def test_error_keeps_tag(self):
        self.assertRefused(P.parse_command, "@12 TOY 9 1", P.RANGE, tag=12)
        self.assertRefused(P.parse_command, "@12 NOPE", P.UNKNOWN, tag=12)
        self.assertRefused(P.parse_command, "TOY 9 1", P.RANGE, tag=None)


class Commands(Helpers):
    def test_no_arg_commands(self):
        for name in ("PING", "WHO", "SAVE", "DEFAULTS", "BTNPOLL", "MCPRESET", "REBOOT"):
            self.assertEqual(P.parse_command(name), (None, name, ()))
            self.assertRefused(P.parse_command, name + " 1", P.ARGS)

    def test_toys_and_lights(self):
        self.assertEqual(P.parse_command("TOY 8 1")[2], (8, 1))
        self.assertEqual(P.parse_command("LGT 12 0")[2], (12, 0))
        self.assertRefused(P.parse_command, "TOY 0 1", P.RANGE)
        self.assertRefused(P.parse_command, "TOY 9 1", P.RANGE)    # P4 : TOY9..16 absents
        self.assertRefused(P.parse_command, "TOY 16 1", P.RANGE)
        self.assertRefused(P.parse_command, "LGT 13 1", P.RANGE)
        self.assertRefused(P.parse_command, "TOY 1 2", P.ARGS)
        self.assertRefused(P.parse_command, "TOY 1", P.ARGS)
        self.assertEqual(P.parse_command("TOYS a5")[2], (0xA5,))
        self.assertEqual(P.parse_command("LGTS FFF")[2], (0xFFF,))
        self.assertRefused(P.parse_command, "TOYS 100", P.RANGE)
        self.assertRefused(P.parse_command, "LGTS 1000", P.RANGE)

    def test_pulses(self):
        self.assertEqual(P.parse_command("TOYPULSE 2 100 50 0")[2], (2, 100, 50, 0))
        self.assertEqual(P.parse_command("LGTPULSE 12 60000 1 65535")[2], (12, 60000, 1, 65535))
        self.assertRefused(P.parse_command, "TOYPULSE 2 0 50 0", P.RANGE)
        self.assertRefused(P.parse_command, "TOYPULSE 2 100 60001 0", P.RANGE)
        self.assertRefused(P.parse_command, "TOYPULSE 2 100 50 65536", P.RANGE)
        self.assertRefused(P.parse_command, "TOYPULSE 2 100 50", P.ARGS)
        self.assertEqual(P.parse_command("TOYSTOP 8")[2], (8,))
        self.assertRefused(P.parse_command, "LGTSTOP 13", P.RANGE)

    def test_leds(self):
        self.assertEqual(P.parse_command("LEDCFG 2 300")[2], (2, 300))
        self.assertRefused(P.parse_command, "LEDCFG 3 1", P.RANGE)
        self.assertRefused(P.parse_command, "LEDCFG 0 301", P.RANGE)
        self.assertEqual(P.parse_command("LEDB 1 255")[2], (1, 255))
        self.assertRefused(P.parse_command, "LEDB 1 256", P.RANGE)
        self.assertEqual(P.parse_command("LEDOFF 0")[2], (0,))
        self.assertEqual(P.parse_command("LEDF 1 FF0000a0b0c0")[2],
                         (1, b"\xff\x00\x00\xa0\xb0\xc0"))
        self.assertRefused(P.parse_command, "LEDF 1 FF00", P.ARGS)     # pas 3 octets
        self.assertRefused(P.parse_command, "LEDF 1 FF00GG", P.ARGS)
        self.assertRefused(P.parse_command, "LEDF 3 FF0000", P.RANGE)
        # Chiffres vérifiés par unhexlify (ValueError) : même code qu'avant.
        for bad in ("GG0000", "0000+1", "-10000", "00000x"):
            with self.subTest(bad=bad):
                self.assertRefused(P.parse_command, "LEDF 1 " + bad, P.ARGS)
        # Caractère de contrôle, DEL ou non ASCII dans les données : SYNTAX (ligne).
        for bad in ("FF00\x0b0", "FF00\x7f0", "FF00\x0c0", "FF00é0"):
            with self.subTest(bad=bad):
                self.assertRefused(P.parse_command, "LEDF 1 " + bad, P.SYNTAX)

    def test_ledf_at_300_leds_limit(self):
        line = P.command("LEDF", 0, bytes(range(256)) * 3 + bytes(132))
        self.assertEqual(len(P.parse_command(line)[2][1]), 900)
        self.assertLessEqual(len(line), P.DEFAULT_LIMITS.max_line)
        self.assertRefused(P.parse_command, "LEDF 0 " + "00" * 903, P.RANGE)

    def test_params(self):
        self.assertEqual(P.parse_command("GET watchdog_ms")[2], ("watchdog_ms",))
        self.assertRefused(P.parse_command, "GET nope", P.KEY)
        # Plage de watchdog_ms : 2 000 à 60 000 (v0.2.1).
        self.assertEqual(P.parse_command("SET watchdog_ms 2000")[2], ("watchdog_ms", 2000))
        self.assertEqual(P.parse_command("SET watchdog_ms 60000")[2], ("watchdog_ms", 60000))
        self.assertRefused(P.parse_command, "SET watchdog_ms 1999", P.RANGE)
        self.assertRefused(P.parse_command, "SET watchdog_ms 60001", P.RANGE)
        self.assertRefused(P.parse_command, "SET watchdog_ms abc", P.ARGS)
        self.assertEqual(P.parse_command("SET shock_mode adc")[2], ("shock_mode", "adc"))
        self.assertRefused(P.parse_command, "SET shock_mode ADC", P.RANGE)
        self.assertEqual(P.parse_command("SET led2_order BGR")[2], ("led2_order", "BGR"))
        self.assertRefused(P.parse_command, "SET led3_order RGB", P.KEY)
        self.assertRefused(P.parse_command, "SET nope 1", P.KEY)
        self.assertEqual(P.parse_command("SET reject_report 1")[2], ("reject_report", 1))
        self.assertRefused(P.parse_command, "SET reject_report 2", P.RANGE)

    def test_defaults_match_specification(self):
        d = P.default_params()
        self.assertEqual(d["min_contact_us"], 300)
        self.assertEqual(d["release_ms"], 20)
        self.assertEqual(d["hold_max_ms"], 400)
        self.assertEqual(d["btn_debounce_ms"], 10)
        self.assertEqual(d["shock_mode"], "digital")
        self.assertEqual(d["shock_polarity"], "high")
        self.assertEqual(d["shock_threshold"], 2048)
        self.assertEqual(d["shock_holdoff_ms"], 50)
        self.assertEqual(d["watchdog_ms"], 3000)
        self.assertEqual(d["reject_report"], 0)
        self.assertEqual(d["hw_watchdog"], 1)
        self.assertEqual(d["bench_row_pullup"], 0)                  # réservé au banc
        for ch in range(3):
            self.assertEqual(d["led%d_order" % ch], "GRB")
            self.assertEqual(d["led%d_count" % ch], 0)

    def test_cal(self):
        self.assertEqual(P.parse_command("CAL 1")[2], (1,))
        self.assertRefused(P.parse_command, "CAL 2", P.ARGS)


class Builders(unittest.TestCase):
    def test_round_trip_commands(self):
        cases = [
            (("PING",), {}, "PING"),
            (("TOY", 3, 1), {"tag": 9}, "@9 TOY 3 1"),
            (("TOYS", 0xA5), {}, "TOYS A5"),
            (("LGTS", 0x801), {}, "LGTS 801"),
            (("SET", "shock_mode", "adc"), {}, "SET shock_mode adc"),
            (("LEDF", 2, b"\x01\x02\x03"), {}, "LEDF 2 010203"),
            (("TOYPULSE", 1, 100, 100, 0), {}, "TOYPULSE 1 100 100 0"),
        ]
        for args, kw, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(P.command(*args, **kw), expected)
                P.parse_command(expected)

    def test_builder_rejects_invalid(self):
        for args in (("TOY", 9, 1), ("NOPE",), ("LEDF", 0, b"\x01\x02"),
                     ("SET", "watchdog_ms", 10), ("TOYS", 0x100)):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    P.command(*args)

    def test_event_builders(self):
        self.assertEqual(P.msg_hello((1, 2, 3), "E66138935F3B2C25"),
                         "HELLO 1 darts-pico 1.2.3 E66138935F3B2C25")
        self.assertEqual(P.msg_caps(),
                         "CAPS rows=12 cols=7 buttons=15 toys=8 lights=12 leds=3 shock=1")
        self.assertEqual(P.msg_dart(9, 6, 123, 2500), "D 9 6 123 2500")
        self.assertEqual(P.msg_reject(0, 0, 120), "X 0 0 120")
        self.assertEqual(P.msg_button(15, True, 5), "B 15 1 5")
        self.assertEqual(P.msg_buttons_state(0x4001), "BS 4001")
        self.assertEqual(P.msg_shock(7, 4095), "S 7 4095")
        self.assertEqual(P.msg_error("MCP", "0x21 absent"), "E MCP 0x21 absent")
        self.assertEqual(P.msg_error("WDG", "resumed"), "E WDG resumed")
        self.assertEqual(P.msg_ack(4), "K 4")
        self.assertEqual(P.msg_nack(None, P.TOOLONG), "N - TOOLONG")
        self.assertEqual(P.msg_nack(4, P.RANGE), "N 4 RANGE")
        self.assertEqual(P.msg_value("shock_mode", "adc"), "V shock_mode adc")
        self.assertEqual(P.msg_dart(11, 6, 1, 2), "D 11 6 1 2")         # 12 lignes (v0.3.6)
        for bad in (lambda: P.msg_dart(12, 0, 0, 0), lambda: P.msg_button(16, 1, 0),
                    lambda: P.msg_shock(0, 4096), lambda: P.msg_buttons_state(0x8000),
                    lambda: P.msg_hello((1, 2, 3), "xyz")):
            with self.assertRaises(ValueError):
                bad()


class Events(Helpers):
    def test_parse_all(self):
        self.assertEqual(P.parse_event("HELLO 1 darts-pico 0.1.0 ABCDEF"),
                         ("HELLO", 1, "darts-pico", (0, 1, 0), "ABCDEF"))
        caps = P.parse_event(P.msg_caps())[1]
        self.assertEqual(caps["buttons"], 15)
        self.assertEqual(P.parse_event("D 3 4 100 2000"), ("D", 3, 4, 100, 2000))
        self.assertEqual(P.parse_event("X 3 4 200"), ("X", 3, 4, 200))
        self.assertEqual(P.parse_event("B 1 0 9"), ("B", 1, 0, 9))
        self.assertEqual(P.parse_event("BS 7FFF"), ("BS", 0x7FFF))
        self.assertEqual(P.parse_event("S 1 1"), ("S", 1, 1))
        self.assertEqual(P.parse_event("E MCP 0x21 absent"), ("E", "MCP", "0x21 absent"))
        self.assertEqual(P.parse_event("E FUTURE_CODE"), ("E", "FUTURE_CODE", ""))
        self.assertEqual(P.parse_event("PONG 5"), ("PONG", 5))
        self.assertEqual(P.parse_event("K 0"), ("K", 0))
        self.assertEqual(P.parse_event("N - SYNTAX"), ("N", None, "SYNTAX"))
        self.assertEqual(P.parse_event("N 3 RANGE"), ("N", 3, "RANGE"))
        self.assertEqual(P.parse_event("V watchdog_ms 3000"), ("V", "watchdog_ms", "3000"))
        self.assertEqual(P.parse_event("# journal libre"), ("#", "journal libre"))

    def test_invalid_events(self):
        self.assertEqual(P.parse_event("D 11 6 1 2"), ("D", 11, 6, 1, 2))
        self.assertEqual(P.parse_event("X 10 0 5"), ("X", 10, 0, 5))
        for bad, code in (("D 12 0 0 0", P.RANGE), ("D 0 7 0 0", P.RANGE), ("X 12 0 5", P.RANGE),
                          ("D 0 0 0", P.ARGS), ("B 0 1 0", P.RANGE), ("B 1 2 0", P.ARGS),
                          ("BS FFF", P.ARGS), ("BS 8000", P.RANGE), ("S 0 4096", P.RANGE),
                          ("PONG 1073741824", P.RANGE), ("E", P.ARGS), ("E mcp x", P.ARGS),
                          ("HELLO 1 other 0.1.0 AB", P.ARGS), ("HELLO 1 darts-pico 0.1 AB", P.ARGS),
                          ("HELLO 1 darts-pico 0.1.0 ab", P.ARGS),
                          ("CAPS rows=12", P.ARGS), ("CAPS rows=12 rows=12", P.ARGS),
                          ("ZZ", P.UNKNOWN), ("D  1 1 1 1", P.SYNTAX)):
            with self.subTest(bad=bad):
                self.assertRefused(P.parse_event, bad, code)


class Splitter(unittest.TestCase):
    def test_split_across_chunks_and_crlf(self):
        s = P.LineSplitter()
        self.assertEqual(s.feed(b"PI"), [])
        self.assertEqual(s.pending(), 2)
        self.assertEqual(s.feed(b"NG\r\nWHO\n@1 "), ["PING", "WHO"])
        self.assertEqual(s.feed(b"PING\n"), ["@1 PING"])
        self.assertEqual(s.feed(b"\n"), [""])

    def test_too_long_line_is_dropped_until_newline(self):
        lim = P.Limits(max_line=64)
        s = P.LineSplitter(lim)
        out = s.feed(b"A" * 64 + b"\r\n")
        self.assertEqual(out, ["A" * 64])                      # pile à la limite, \r non compté
        out = s.feed(b"A" * 40)
        out += s.feed(b"A" * 40)                               # dépasse en deux morceaux
        self.assertEqual(out, [])
        self.assertLessEqual(s.pending(), 66)                  # rien n'est accumulé au-delà
        out = s.feed(b"AAAA\nPING\n")
        self.assertIsInstance(out[0], ProtocolError)
        self.assertEqual(out[0].code, P.TOOLONG)
        self.assertEqual(out[1], "PING")

    def test_invalid_utf8(self):
        out = P.LineSplitter().feed(b"\xff\xfe\nPING\n")
        self.assertEqual(out[0].code, P.SYNTAX)
        self.assertEqual(out[1], "PING")

    def test_encode_line(self):
        self.assertEqual(P.encode_line("PING"), b"PING\n")


class CheckBuilt(unittest.TestCase):
    def tearDown(self):
        P.CHECK_BUILT = True

    def test_on_by_default_and_detects_bad_message(self):
        self.assertTrue(P.CHECK_BUILT)
        with self.assertRaises(ValueError):
            P.msg_error("bad code", "x")

    def test_off_returns_same_text_without_check(self):
        ref = [P.msg_pong(123), P.msg_ack(7), P.msg_dart(3, 4, 99, 1200), P.msg_value("release_ms", "20")]
        P.CHECK_BUILT = False
        self.assertEqual([P.msg_pong(123), P.msg_ack(7), P.msg_dart(3, 4, 99, 1200),
                          P.msg_value("release_ms", "20")], ref)


if __name__ == "__main__":
    unittest.main()
