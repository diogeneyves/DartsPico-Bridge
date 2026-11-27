# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests du cœur de hwd contre le faux Pico, en mémoire, horloge simulée commune."""

import json
import unittest

import _paths  # noqa: F401
import hwbus as H
import pico_proto as P
from fake_pico import FakePico
from hwd_config import HwdConfig
from hwd_core import ABSENT, CONNECTED, HANDSHAKE, HwdCore

T = H.topics()
FLUSH_MS = 50                              # handshake_flush_ms par défaut (§ 9.5)


class Clock:
    def __init__(self, t=10_000_000):
        self.t = t

    def __call__(self):
        return self.t


class Rig:
    """hwd branché sur un faux Pico ; pump() fait circuler les octets jusqu'au calme."""

    def __init__(self, testcase, **cfg):
        self.tc = testcase
        self.clk = Clock()
        self.pico = FakePico(clock=self.clk)
        self.pico.boot()
        self.core = HwdCore(HwdConfig(**cfg).validate(), self.clk)
        self.linked = True
        self.sent = []                     # lignes reçues par le Pico (sans @n ; "" = ligne vide)
        self.pub = []

    def pump(self, rounds=500):
        for _ in range(rounds):
            moved = False
            out = self.pico.tick()
            if out and self.linked:
                self.core.serial_data(out)
                moved = True
            self.core.tick()
            line = self.core.take_serial()
            while line is not None:
                moved = True
                text = line.decode().rstrip("\n")
                self.sent.append(text.split(" ", 1)[1] if text.startswith("@") else text)
                if self.linked:
                    reply = self.pico.feed(line)
                    if reply:
                        self.core.serial_data(reply)
                line = self.core.take_serial()
            self.pub += self.core.take_publishes()
            if not moved:
                return
        self.tc.fail("le système ne se calme pas")

    def advance(self, ms, step_ms=10):
        for _ in range(ms // step_ms):
            self.clk.t += step_ms * 1000
            self.pump()
        rest = ms % step_ms
        if rest:
            self.clk.t += rest * 1000
            self.pump()

    def open(self):
        """Ouverture du port : vidage de la ligne, puis WHO (§ 9.5, 28e complément)."""
        self.core.serial_opened()
        self.pump()
        self.advance(FLUSH_MS)

    def connect(self):
        self.open()
        self.tc.assertEqual(self.core.state, CONNECTED)
        self.take_events()
        self.sent.clear()

    def events(self):
        return [p for t, p, _ in self.pub if t == T["events"]]

    def take_events(self):
        ev = self.events()
        self.pub = [x for x in self.pub if x[0] != T["events"]]
        return ev

    def statuses(self):
        return [json.loads(p)["pico"] for t, p, r in self.pub if t == T["status"]]

    def cmd(self, line):
        self.core.bus_message(T["cmd"], line.encode())
        self.pump()

    def led(self, ch, data):
        self.core.bus_message(T["led"] + str(ch), data)


class BenchParams(unittest.TestCase):
    def test_bench_params_refused_in_config(self):
        for key in P.BENCH_PARAMS:
            with self.assertRaises(ValueError):
                HwdConfig(pico_params={key: 1}).validate()

    def test_default_led_max_fps_is_30(self):
        self.assertEqual(HwdConfig().led_max_fps, 30)


class Handshake(unittest.TestCase):
    def test_who_hello_caps_then_init(self):
        rig = Rig(self, pico_params={"min_contact_us": 500})
        self.assertEqual(rig.core.state, ABSENT)
        rig.core.serial_opened()
        self.assertEqual(rig.core.state, HANDSHAKE)
        rig.pump()
        rig.advance(FLUSH_MS)
        self.assertEqual(rig.core.state, CONNECTED)
        self.assertEqual(rig.sent[:2], ["", "WHO"])
        self.assertIn("SET min_contact_us 500", rig.sent)
        self.assertEqual(rig.pico.params["min_contact_us"], 500)
        self.assertIn("BTNPOLL", rig.sent)
        self.assertNotIn("SAVE", " ".join(rig.sent))                 # jamais SAVE (D8)
        self.assertIn("SET hw_watchdog 1", rig.sent)                  # chien de garde matériel (§ 5.4)
        for key in P.BENCH_PARAMS:                            # jamais les paramètres de banc
            self.assertFalse([l for l in rig.sent if l.startswith("SET %s " % key)])
        self.assertEqual(rig.pico.save_count, 0)
        self.assertEqual(rig.statuses()[-1], "connected")
        last = [json.loads(p) for t, p, r in rig.pub if t == T["status"]][-1]
        self.assertEqual(last["uid"], rig.pico.uid)
        self.assertTrue(all(r for t, p, r in rig.pub if t == T["status"]))   # retenu
        self.assertIn("BS 0000", rig.events())

    def test_no_answer_retries_who(self):
        # WHO à 50 ms, puis toutes les handshake_timeout_ms (1 000) : 1 050, 2 050, 3 050.
        rig = Rig(self)
        rig.linked = False
        rig.core.serial_opened()
        rig.pump()
        rig.advance(3500, step_ms=10)
        self.assertEqual(rig.sent.count("WHO"), 4)
        self.assertEqual(rig.sent.count(""), 1)                      # un seul vidage
        self.assertEqual(rig.core.state, HANDSHAKE)
        rig.linked = True
        rig.advance(1100, step_ms=100)
        self.assertEqual(rig.core.state, CONNECTED)

    def test_flush_line_sent_first_without_tag(self):
        rig = Rig(self)
        rig.core.serial_opened()
        self.assertEqual(rig.core.take_serial(), b"\n")            # sans préfixe @n
        self.assertIsNone(rig.core.take_serial())
        rig.clk.t += (FLUSH_MS - 1) * 1000
        rig.core.tick()
        self.assertIsNone(rig.core.take_serial())                    # pas encore de WHO
        rig.clk.t += 1000
        rig.core.tick()
        self.assertRegex(rig.core.take_serial(), rb"^@\d+ WHO\n$")

    def test_flush_duration_configurable(self):
        rig = Rig(self, handshake_flush_ms=200)
        rig.core.serial_opened()
        rig.pump()
        rig.advance(190)
        self.assertEqual(rig.sent, [""])
        rig.advance(10)
        self.assertEqual(rig.sent[:2], ["", "WHO"])
        self.assertEqual(rig.core.state, CONNECTED)

    def test_lines_during_flush_ignored_without_event(self):
        rig = Rig(self)
        rig.pico.feed(b"\x01\x04xyz")                     # octets laissés par mpremote
        rig.core.serial_opened()
        rig.pump()                                         # le Pico répond N - SYNTAX
        rig.core.serial_data(b"N - UNKNOWN\nN 3 SYNTAX\nE STUCK 3 4\nB 1 1 100\n"
                             b"D 3 4 100 2000\nS 100 900\nHELLO 1 0 1.0.0 0123\n")
        rig.pump()
        self.assertEqual(rig.core.state, HANDSHAKE)
        self.assertEqual(rig.events(), [])
        self.assertEqual(rig.core.counters["nacks"], 0)
        self.assertEqual(rig.core.counters["darts"], 0)
        rig.advance(FLUSH_MS)
        self.assertEqual(rig.core.state, CONNECTED)
        self.assertFalse([e for e in rig.events() if e.startswith("E NACK")], rig.events())
        self.assertEqual(rig.core.counters["nacks"], 0)

    def test_no_flush_when_silence_returns_to_who(self):
        rig = Rig(self)
        rig.connect()
        rig.linked = False
        rig.advance(3100, step_ms=100)
        self.assertEqual(rig.core.state, HANDSHAKE)
        self.assertNotIn("", rig.sent)
        self.assertIn("WHO", rig.sent)
        rig.linked = True
        rig.advance(1100, step_ms=100)
        self.assertEqual(rig.core.state, CONNECTED)
        self.assertNotIn("", rig.sent)

    def test_flush_config_default_and_bounds(self):
        self.assertEqual(HwdConfig().handshake_flush_ms, 50)
        for bad in (0, 1001):
            with self.assertRaises(ValueError):
                HwdConfig(handshake_flush_ms=bad).validate()
        HwdConfig(handshake_flush_ms=1).validate()
        HwdConfig(handshake_flush_ms=1000).validate()

    def test_every_command_acknowledged(self):
        rig = Rig(self)
        rig.connect()
        rig.cmd("TOY 1 1")
        rig.advance(500)
        self.assertEqual(rig.core._pending, {})
        self.assertEqual(rig.core.counters["timeouts"], 0)
        self.assertEqual(rig.core.counters["nacks"], 0)


class Darts(unittest.TestCase):
    def test_dart_becomes_segment_code(self):
        rig = Rig(self)
        rig.connect()
        rig.core.serial_data(rig.pico.dart(3, 4, 2000))
        rig.pump()
        self.assertEqual(rig.take_events(), ["D R3C4 %d 2000" % (rig.clk.t & P.TICKS_MASK)])

    def test_contact_closed_at_start_publishes_no_dart(self):
        # § 7.2 point 3 : le Pico démarre avec une case fermée, E STUCK seulement.
        rig = Rig(self)
        rig.pico = FakePico(clock=rig.clk, closed_at_start=((3, 4),))
        boot = rig.pico.boot()
        rig.open()
        rig.core.serial_data(boot)                           # après le vidage (§ 9.5)
        rig.pump()
        self.assertEqual(rig.core.state, CONNECTED)
        ev = rig.take_events()
        self.assertFalse([e for e in ev if e.startswith("D ")], ev)
        self.assertEqual([e for e in ev if e.startswith("E ")], ["E STUCK 3 4"])
        rig.core.serial_data(rig.pico.dart(3, 4, 2000))       # case toujours fermée
        rig.advance(100)
        self.assertFalse([e for e in rig.take_events() if e.startswith("D ")])
        rig.core.serial_data(rig.pico.release(3, 4))
        rig.core.serial_data(rig.pico.dart(3, 4, 2000))       # rouverte : fléchette normale
        rig.pump()
        self.assertEqual([e.split()[:2] for e in rig.take_events()], [["D", "R3C4"]])

    def test_buttons_relayed(self):
        rig = Rig(self)
        rig.connect()
        rig.core.serial_data(rig.pico.button(15, 1))
        rig.core.serial_data(rig.pico.button(15, 0))
        rig.pump()
        ev = rig.take_events()
        self.assertEqual([e.split()[:3] for e in ev], [["B", "15", "1"], ["B", "15", "0"]])


class CalibrationPath(unittest.TestCase):
    """D1 : chemin de l'étalonnage par l'écran du jeu, avec des codes R<l>C<c>.

    Reproduit ce que fait Screen.calibrate_target (cscreen.py:1098-1131) : pour
    chaque segment, le premier code brut reçu, pas déjà pris, est retenu ; puis
    Craspberry.read_target (craspberry.py:553-560) traduit code -> segment.
    """

    SEGMENTS = (["S%d" % n for n in range(1, 21)] + ["D%d" % n for n in range(1, 21)]
                + ["T%d" % n for n in range(1, 21)] + ["SB", "DB"])

    def test_calibrate_then_play(self):
        rig = Rig(self)
        rig.connect()
        contacts = [(r, c) for r in range(P.ROWS) for c in range(P.COLS)][:len(self.SEGMENTS)]
        section_keys = {}
        for segment, (r, c) in zip(self.SEGMENTS, contacts):
            # Le joueur touche le segment ; un rebond trop court est filtré par le Pico.
            rig.core.serial_data(rig.pico.dart(r, c, 100))
            rig.core.serial_data(rig.pico.dart(r, c, 1800))
            rig.advance(200)
            raw = [H.parse_event(e) for e in rig.take_events() if e.startswith("D ")]
            self.assertEqual(len(raw), 1, segment)
            code = raw[0][1]
            self.assertNotIn(code, section_keys.values())
            section_keys[segment] = code
        self.assertEqual(section_keys["S1"], "R0C0")
        self.assertEqual(section_keys["DB"], "R8C5")
        # Partie : chaque contact retrouve son segment.
        keys = {code: seg for seg, code in section_keys.items()}
        for segment, (r, c) in zip(self.SEGMENTS, contacts):
            rig.core.serial_data(rig.pico.dart(r, c, 2500))
            rig.advance(100)
            code = H.parse_event(rig.take_events()[0])[1]
            self.assertEqual(keys.get(code), segment)

    def test_calibration_uses_filtered_mode(self):
        rig = Rig(self)
        rig.connect()
        self.assertEqual(rig.pico.cal, 0)
        rig.core.serial_data(rig.pico.dart(1, 1, 100))      # rebond : ignoré en CAL 0
        rig.pump()
        self.assertEqual([e for e in rig.take_events() if e.startswith("D")], [])


class MissFusion(unittest.TestCase):
    def shock_then(self, rig, gap_ms, dart_first=False):
        if dart_first:
            rig.core.serial_data(rig.pico.dart(2, 2, 2000))
            rig.advance(gap_ms)
            rig.core.serial_data(rig.pico.shock())
        else:
            rig.core.serial_data(rig.pico.shock())
            rig.advance(gap_ms)
            rig.core.serial_data(rig.pico.dart(2, 2, 2000))
        rig.advance(200)
        return [e for e in rig.take_events() if e.startswith(("MISS", "D "))]

    def setUp(self):
        self.rig = Rig(self)
        self.rig.connect()

    def test_shock_alone_is_miss_after_window(self):
        rig = self.rig
        t = rig.clk.t & P.TICKS_MASK
        rig.core.serial_data(rig.pico.shock())
        rig.advance(60)                                      # 50 + 20 de marge pas atteints
        self.assertFalse(any(e.startswith("MISS") for e in rig.events()))
        rig.advance(20)
        self.assertIn("MISS %d" % t, rig.take_events())

    def test_shock_before_dart_inside_window(self):
        out = self.shock_then(self.rig, 40)
        self.assertEqual([e.split()[0] for e in out], ["D"])

    def test_dart_before_shock_inside_window(self):
        self.rig.advance(100)                                 # chocs hors temps de blocage
        out = self.shock_then(self.rig, 40, dart_first=True)
        self.assertEqual([e.split()[0] for e in out], ["D"])

    def test_window_is_symmetric_and_bounded(self):
        rig = self.rig
        self.assertEqual([e.split()[0] for e in self.shock_then(rig, 50)], ["D"])
        rig.advance(100)
        self.assertEqual([e.split()[0] for e in self.shock_then(rig, 50, True)], ["D"])
        rig.advance(100)
        # 60 ms d'écart : le choc est un vrai manqué, la fléchette reste une fléchette.
        out = self.shock_then(rig, 60, dart_first=True)
        self.assertEqual(sorted(e.split()[0] for e in out), ["D", "MISS"])

    def test_window_configurable(self):
        rig = Rig(self, miss_window_ms=100)
        rig.connect()
        out = self.shock_then(rig, 90)
        self.assertEqual([e.split()[0] for e in out], ["D"])

    def test_ticks_wrap(self):
        rig = self.rig
        rig.clk.t = (1 << 30) * 5 - 20_000                    # le compteur 30 bits va boucler
        rig.pump()
        out = self.shock_then(rig, 40)
        self.assertEqual([e.split()[0] for e in out], ["D"])

    def test_raw_shock_published_for_diagnostics(self):
        rig = self.rig
        rig.core.serial_data(rig.pico.shock())
        rig.pump()
        self.assertTrue(any(e.startswith("S ") for e in rig.events()))


class CommandsAndOutputs(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(self)
        self.rig.connect()

    def test_toys_and_lights(self):
        rig = self.rig
        rig.cmd("TOY 3 1")
        rig.cmd("LGTS 801")
        self.assertEqual(rig.pico.toys[2], 1)
        self.assertEqual(rig.pico.lights, [1] + [0] * 10 + [1])

    def test_refused_commands_never_reach_pico(self):
        rig = self.rig
        for bad in ("TOY 9 1", "TOY 16 1", "SAVE", "REBOOT", "SET watchdog_ms 2000", "LEDF 0 FF0000"):
            rig.cmd(bad)
        self.assertEqual(rig.sent, [])
        self.assertEqual(len([e for e in rig.take_events() if e.startswith("E CMD")]), 6)

    def test_off_and_last_will(self):
        rig = self.rig
        rig.cmd("TOYS FF")
        rig.cmd("LGTS FFF")
        rig.led(1, b"\xff" * 30)
        rig.pump()
        rig.cmd(H.OFF)                                       # charge du testament du jeu
        self.assertTrue(rig.pico.all_outputs_zero())
        self.assertEqual(rig.core.toys, [0] * 8)
        self.assertEqual(rig.core.lights, [0] * 12)

    def test_pulse_restores_previous_state(self):
        # D9 : voyant allumé, stroboscope, puis retour à l'état d'avant.
        rig = self.rig
        rig.cmd("LGT 4 1")
        rig.cmd("LGTPULSE 4 50 50 3")                        # fin prévue dans 300 ms
        rig.advance(290)
        self.assertNotIn("LGT 4 1", rig.sent[2:])
        rig.advance(20)
        self.assertEqual(rig.pico.lights[3], 1)
        self.assertEqual(rig.sent[-1], "LGT 4 1")

    def test_pulse_on_off_output_leaves_it_off(self):
        rig = self.rig
        rig.cmd("TOYPULSE 2 50 50 2")
        rig.advance(300)
        self.assertEqual(rig.pico.toys[1], 0)
        self.assertNotIn("TOY 2 1", rig.sent)

    def test_command_during_pulse_cancels_restore(self):
        rig = self.rig
        rig.cmd("LGT 4 1")
        rig.cmd("LGTPULSE 4 50 50 3")
        rig.advance(100)
        rig.cmd("LGT 4 0")                                   # commande intermédiaire
        rig.advance(400)
        self.assertEqual(rig.pico.lights[3], 0)
        self.assertEqual(rig.sent.count("LGT 4 1"), 1)        # seulement la première

    def test_mask_command_cancels_restore(self):
        rig = self.rig
        rig.cmd("TOY 1 1")
        rig.cmd("TOYPULSE 1 20 20 2")
        rig.cmd("TOYS 0")
        rig.advance(200)
        self.assertEqual(rig.pico.toys[0], 0)

    def test_continuous_pulse_not_restored(self):
        rig = self.rig
        rig.cmd("LGT 5 1")
        rig.cmd("LGTPULSE 5 50 50 0")
        rig.advance(1000)
        self.assertIn(("L", 5), rig.pico.pulses)
        self.assertEqual(rig.core.lights[4], 0)

    def test_ping_period_is_watchdog_third(self):
        rig = self.rig
        rig.advance(10_000, step_ms=50)
        self.assertGreaterEqual(rig.sent.count("PING"), 9)
        self.assertFalse(rig.pico.wdg_tripped)

    def test_led_order_and_brightness(self):
        rig = self.rig
        rig.cmd("SET led1_order RGB")
        rig.cmd("LEDB 1 128")
        self.assertEqual(rig.pico.params["led1_order"], "RGB")
        self.assertEqual(rig.pico.led_brightness[1], 128)

    def test_brightness_capped_never_multiplied(self):
        # § 3.8, v0.3.14 : LEDB envoyé = min(valeur du jeu, plafond du canal).
        rig = self.rig
        rig.cmd("LEDB 0 200")
        self.assertEqual(rig.pico.led_brightness[0], 128)          # plafond du canal 0
        rig.cmd("LEDB 0 127")
        self.assertEqual(rig.pico.led_brightness[0], 127)          # sous le plafond : tel quel
        rig.cmd("LEDB 1 255")
        self.assertEqual(rig.pico.led_brightness[1], 255)
        self.assertIn("LEDB 0 127", rig.sent)
        self.assertNotIn("LEDB 0 200", rig.sent)

    def test_brightness_restored_capped_after_reboot(self):
        rig = self.rig
        rig.cmd("LEDB 0 250")
        rig.cmd("LEDB 2 40")
        rig.core.serial_data(rig.pico.boot())                      # redémarrage du Pico
        rig.pump()
        self.assertEqual(rig.pico.led_brightness, [128, 255, 40])  # canal 1 : plafond (pas de valeur du jeu)

    def test_nack_published(self):
        rig = self.rig
        rig.core._pending[42] = ("TOY 1 1", rig.clk.t + 10**9)
        rig.core.serial_data(b"N 42 RANGE\n")
        rig.pump()
        self.assertIn("E NACK RANGE TOY 1 1", rig.take_events())

    def test_ack_timeout_published(self):
        rig = self.rig
        rig.linked = False
        rig.cmd("TOY 1 1")
        rig.advance(250)
        self.assertTrue(any(e.startswith("E TIMEOUT TOY 1 1") for e in rig.take_events()))


class Leds(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(self, led_max_fps=50)    # période de 20 ms, supposée par les tests
        self.rig.connect()

    def test_default_counts_sent_at_connection(self):
        # § 3.5 : 204 LED de la cible sur le canal 0, rubans accessoires à 0.
        rig = Rig(self)
        rig.open()
        for line in ("LEDCFG 0 204", "LEDCFG 1 0", "LEDCFG 2 0",
                     "LEDB 0 128", "LEDB 1 255", "LEDB 2 255"):
            self.assertIn(line, rig.sent)
        self.assertEqual(rig.pico.led_brightness, [128, 255, 255])
        self.assertEqual(rig.pico.params["led0_count"], 204)
        self.assertEqual(len(rig.pico.led_rgb[0]), 3 * 204)
        rig.led(0, bytes(3 * 204))
        rig.pump()
        self.assertEqual(rig.sent[-1][:7], "LEDF 0 ")               # sans nouveau LEDCFG
        self.assertNotIn("LEDCFG 0 204", rig.sent[-2:])

    def test_count_from_configuration(self):
        rig = Rig(self, pico_params={"led1_count": 60})
        rig.open()
        self.assertIn("LEDCFG 1 60", rig.sent)
        self.assertEqual(rig.pico.params["led1_count"], 60)

    def test_first_frame_configures_count(self):
        rig = self.rig
        rig.led(0, bytes([10, 20, 30]) * 4)
        rig.pump()
        self.assertEqual(rig.sent[:2], ["LEDCFG 0 4", "LEDF 0 0A141E0A141E0A141E0A141E"])
        self.assertEqual(rig.pico.led_rgb[0], bytes([10, 20, 30]) * 4)

    def test_only_latest_frame_is_sent(self):
        rig = self.rig
        rig.led(1, b"\x01\x01\x01")
        rig.pump()
        for i in range(10):                                 # 10 images dans la même période
            rig.led(1, bytes([i, i, i]))
            rig.pump()
        rig.advance(30)
        frames = [s for s in rig.sent if s.startswith("LEDF")]
        self.assertEqual(len(frames), 2)
        self.assertEqual(rig.pico.led_rgb[1], b"\x09\x09\x09")
        self.assertGreaterEqual(rig.core.counters["led_frames_dropped"], 8)

    def test_rate_limit(self):
        rig = Rig(self, led_max_fps=10)
        rig.connect()
        for _ in range(100):                                # 1 s d'images toutes les 10 ms
            rig.led(2, b"\x05\x05\x05")
            rig.advance(10)
        frames = [s for s in rig.sent if s.startswith("LEDF")]
        self.assertLessEqual(len(frames), 11)
        self.assertGreaterEqual(len(frames), 9)

    def test_commands_go_before_led_frames(self):
        rig = self.rig
        rig.led(0, b"\x01" * 900)
        rig.cmd("TOY 1 1")
        self.assertLess(rig.sent.index("TOY 1 1"), next(i for i, s in enumerate(rig.sent)
                                                         if s.startswith("LEDF")))

    def test_300_leds_and_refusals(self):
        rig = self.rig
        rig.led(1, bytes(900))
        rig.pump()
        self.assertEqual(len(rig.pico.led_rgb[1]), 900)
        rig.led(1, bytes(903))                              # 301 LED
        rig.led(1, bytes(4))                                # pas un multiple de 3
        rig.core.bus_message(T["led"] + "3", bytes(3))     # canal inconnu
        rig.pump()
        self.assertEqual(len([e for e in rig.take_events() if e.startswith("E LED")]), 3)

    def test_count_change_reconfigures(self):
        rig = self.rig
        rig.led(0, bytes(6))
        rig.pump()
        rig.advance(30)
        rig.led(0, bytes(9))
        rig.advance(30)
        self.assertIn("LEDCFG 0 3", rig.sent)
        self.assertEqual(len(rig.pico.led_rgb[0]), 9)


class Reconnection(unittest.TestCase):
    def test_unplug_then_replug_restores_lights_not_toys(self):
        rig = Rig(self, led_max_fps=50)
        rig.connect()
        rig.cmd("TOYS FF")
        rig.cmd("LGTS 00F")
        rig.led(1, b"\x11\x22\x33" * 2)
        rig.pump()
        rig.core.serial_closed()                             # débranchement
        rig.pump()
        self.assertEqual(rig.statuses()[-1], "absent")
        # Nouveau Pico (redémarré : tout à zéro), nouveau port.
        rig.pico = FakePico(clock=rig.clk)
        rig.pico.boot()
        rig.sent.clear()
        rig.open()
        self.assertEqual(rig.core.state, CONNECTED)
        self.assertEqual(rig.pico.lights[:4], [1, 1, 1, 1])  # voyants restaurés (D7)
        self.assertEqual(rig.pico.toys, [0] * 8)             # Toys non (D7)
        rig.advance(30)                                      # délai entre deux images (R5)
        self.assertEqual(rig.pico.led_rgb[1], b"\x11\x22\x33" * 2)

    def test_lights_set_while_absent_are_applied(self):
        rig = Rig(self)
        rig.cmd("LGT 2 1")
        rig.cmd("TOY 2 1")
        rig.connect()
        self.assertEqual(rig.pico.lights[1], 1)
        self.assertEqual(rig.pico.toys[1], 0)

    def test_pico_reboot_detected(self):
        rig = Rig(self)
        rig.connect()
        rig.cmd("LGT 1 1")
        rig.cmd("TOY 1 1")
        rig.cmd("REBOOT")                                    # refusé depuis le jeu…
        self.assertEqual(rig.pico.reboots, 0)
        rig.core.serial_data(rig.pico.feed(b"REBOOT\n"))      # … redémarrage spontané
        rig.pump()
        self.assertEqual(rig.pico.reboots, 1)
        self.assertEqual(rig.core.state, CONNECTED)
        self.assertEqual(rig.pico.lights[0], 1)
        self.assertEqual(rig.pico.toys[0], 0)

    def test_watchdog_resumed_restores(self):
        rig = Rig(self)
        rig.connect()
        rig.cmd("LGT 6 1")
        rig.cmd("TOY 6 1")
        # hwd bloqué 3,5 s : le Pico coupe tout.
        rig.linked = False
        for _ in range(35):
            rig.clk.t += 100_000
            rig.pico.tick()
        rig.linked = True
        rig.core.serial_data(b"E WDG timeout\n")
        rig.pump()
        rig.advance(1500, step_ms=100)                       # PING suivant : E WDG resumed
        ev = rig.take_events()
        self.assertIn("E WDG resumed", ev)
        self.assertEqual(rig.pico.lights[5], 1)
        self.assertEqual(rig.pico.toys[5], 0)

    def test_silence_triggers_new_handshake(self):
        rig = Rig(self)
        rig.connect()
        rig.linked = False
        rig.advance(3100, step_ms=100)
        self.assertEqual(rig.core.state, HANDSHAKE)
        self.assertTrue(any(e.startswith("E SILENCE") for e in rig.take_events()))
        rig.linked = True
        rig.advance(1100, step_ms=100)
        self.assertEqual(rig.core.state, CONNECTED)


class EventTexts(unittest.TestCase):
    """Texte exact des événements E de hwd : anglais, ASCII (§ 9.5, complément 61)."""

    def assertEventsAscii(self, events):
        for e in events:
            self.assertTrue(e.isascii(), e)

    def test_led_image_rejected(self):
        rig = Rig(self)
        rig.connect()
        rig.led(1, bytes(4))                                # pas un multiple de 3
        rig.led(1, bytes(903))                              # 301 LED
        rig.pump()
        ev = [e for e in rig.take_events() if e.startswith("E LED")]
        self.assertEqual(ev, ["E LED Image of 4 bytes rejected",
                              "E LED Image of 903 bytes rejected"])
        self.assertEventsAscii(ev)

    def test_led_unknown_channel(self):
        rig = Rig(self)
        rig.connect()
        rig.core.bus_message(T["led"] + "3", bytes(3))
        rig.pump()
        ev = [e for e in rig.take_events() if e.startswith("E LED")]
        self.assertEqual(ev, ["E LED Unknown channel '3'"])
        self.assertEventsAscii(ev)

    def test_caps_unexpected_board(self):
        rig = Rig(self)
        rig.linked = False
        rig.core.serial_opened()
        rig.pump()
        rig.advance(FLUSH_MS)                               # WHO envoyé, non reçu
        caps = P.msg_caps().replace("rows=%d" % P.ROWS, "rows=%d" % (P.ROWS - 1))
        rig.core.serial_data((P.msg_hello((0, 1, 0), rig.pico.uid) + "\n" + caps + "\n").encode())
        rig.pump()
        self.assertEqual(rig.core.state, HANDSHAKE)
        ev = [e for e in rig.take_events() if e.startswith("E ")]
        self.assertEqual(ev, ["E CAPS Unexpected board"])
        self.assertEventsAscii(ev)

    def test_silence_no_news(self):
        rig = Rig(self)
        rig.connect()
        rig.linked = False
        rig.advance(3100, step_ms=100)
        ev = [e for e in rig.take_events() if e.startswith("E ")]
        self.assertIn("E TIMEOUT PING", ev)                 # PING sans réponse, attendu
        ev = [e for e in ev if e.startswith("E SILENCE")]
        self.assertEqual(ev, ["E SILENCE No news from the Pico"])
        self.assertEventsAscii(ev)


if __name__ == "__main__":
    unittest.main()
