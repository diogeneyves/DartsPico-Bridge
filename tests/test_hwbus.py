# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests du protocole hwd <-> jeu (hwbus) et de la configuration de hwd."""

import os
import tempfile
import unittest

import _paths  # noqa: F401
import hwbus as H
import hwd_config
import pico_proto as P


class SegmentCodes(unittest.TestCase):
    def test_all_contacts_have_distinct_codes(self):
        codes = {H.segment_code(r, c) for r in range(P.ROWS) for c in range(P.COLS)}
        self.assertEqual(len(codes), P.ROWS * P.COLS)
        for r in range(P.ROWS):
            for c in range(P.COLS):
                self.assertEqual(H.parse_segment_code(H.segment_code(r, c)), (r, c))

    def test_examples_and_errors(self):
        self.assertEqual(H.segment_code(3, 4), "R3C4")
        self.assertEqual(H.segment_code(9, 6), "R9C6")
        # 12 lignes (v0.3.6) : codes à deux chiffres pour les lignes 10 et 11.
        self.assertEqual(H.segment_code(10, 0), "R10C0")
        self.assertEqual(H.segment_code(11, 6), "R11C6")
        self.assertEqual(H.parse_segment_code("R11C6"), (11, 6))
        self.assertEqual(P.ROWS, 12)
        for bad in ((12, 0), (0, 7), (-1, 0)):
            with self.assertRaises(ValueError):
                H.segment_code(*bad)
        for bad in ("R3C7", "R12C0", "R03C4", "R3C04", "R100C0", "R3C4C1", "RC3",
                    "r3c4", "R3C", "2410", "", "R3C4 "):
            with self.assertRaises(H.BusError):
                H.parse_segment_code(bad)

    def test_codes_never_look_like_gpio_codes(self):
        # Les anciens codes de [SectionKeys] sont des chiffres seuls ("2410").
        for r in range(P.ROWS):
            for c in range(P.COLS):
                self.assertFalse(H.segment_code(r, c).isdigit())


class Events(unittest.TestCase):
    def test_round_trip(self):
        cases = [
            (H.ev_dart(3, 4, 123, 2000), ("D", "R3C4", 123, 2000)),
            (H.ev_reject(0, 6, 120), ("X", "R0C6", 120)),
            (H.ev_button(15, 1, 9), ("B", 15, 1, 9)),
            (H.ev_buttons_state(0x4001), ("BS", 0x4001)),
            (H.ev_miss(77), ("MISS", 77)),
            (H.ev_shock(5, 4095), ("S", 5, 4095)),
            (H.ev_error("WDG", "timeout"), ("E", "WDG", "timeout")),
        ]
        for line, parsed in cases:
            with self.subTest(line=line):
                self.assertEqual(H.parse_event(line), parsed)

    def test_invalid(self):
        for bad in ("D 3 4 1 1", "D R3C9 1 1", "B 16 1 0", "MISS", "Z 1", "S 1 4096"):
            with self.assertRaises(H.BusError):
                H.parse_event(bad)


class Commands(unittest.TestCase):
    def test_allowed(self):
        self.assertEqual(H.parse_command("TOY 3 1"), ("TOY", (3, 1)))
        self.assertEqual(H.parse_command("LGTPULSE 1 10 10 3"), ("LGTPULSE", (1, 10, 10, 3)))
        self.assertEqual(H.parse_command("SET led1_order RGB"), ("SET", ("led1_order", "RGB")))
        self.assertEqual(H.parse_command("OFF"), ("OFF", ()))

    def test_refused(self):
        for bad in ("SAVE", "DEFAULTS", "REBOOT", "WHO", "PING", "LEDF 0 FFFFFF", "LEDCFG 0 3",
                    "MCPRESET", "SET watchdog_ms 3000", "SET led0_count 3", "TOY 9 1",
                    "TOY 16 1", "@1 TOY 1 1", "toy 1 1", "TOY 1"):
            with self.subTest(bad=bad):
                with self.assertRaises(H.BusError):
                    H.parse_command(bad)

    def test_topics(self):
        t = H.topics()
        self.assertEqual(t["events"], "raspydarts/hw/events")
        self.assertEqual(t["cmd"], "raspydarts/hw/cmd")
        self.assertEqual(t["status"], "raspydarts/hw/status")
        self.assertEqual(t["led"] + "1", "raspydarts/hw/led/1")
        self.assertEqual(t["dmd_frame"], "raspydarts/hw/dmd/frame")
        self.assertEqual(H.KEEPALIVE_S, 5)


class Config(unittest.TestCase):
    def write(self, text):
        fd, path = tempfile.mkstemp(suffix=".toml")
        os.write(fd, text.encode())
        os.close(fd)
        self.addCleanup(os.unlink, path)
        return path

    def test_defaults(self):
        cfg = hwd_config.HwdConfig().validate()
        self.assertEqual(cfg.miss_window_ms, 50)
        self.assertEqual(cfg.keepalive_s, 5)
        self.assertEqual(cfg.params()["watchdog_ms"], 3000)
        p = cfg.params()
        self.assertEqual((p["led0_count"], p["led1_count"], p["led2_count"]), (204, 0, 0))
        self.assertEqual(cfg.led_brightness, [128, 255, 255])           # canal 0 limité (§ 3.8)

    def test_example_file_loads(self):
        example = os.path.join(_paths.ROOT, "pi", "hwd", "hwd.example.toml")
        cfg = hwd_config.load(example)
        self.assertEqual(cfg.pico_device, "/dev/cible")
        self.assertEqual(cfg.led_brightness, [128, 255, 255])

    def test_file_values_and_errors(self):
        cfg = hwd_config.load(self.write('miss_window_ms = 80\n[pico_params]\nmin_contact_us = 500\n'))
        self.assertEqual(cfg.miss_window_ms, 80)
        self.assertEqual(cfg.params()["min_contact_us"], 500)
        cfg = hwd_config.load(self.write('[pico_params]\nled1_count = 60\n'))
        self.assertEqual((cfg.params()["led0_count"], cfg.params()["led1_count"]), (204, 60))
        for text in ('[pico_params]\nwatchdog_ms = 1999\n', 'nope = 1\n',
                     '[pico_params]\nled0_count = 301\n', 'miss_window_ms = 0\n',
                     '[pico_params]\nunknown_key = 1\n'):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    hwd_config.load(self.write(text))


if __name__ == "__main__":
    unittest.main()
