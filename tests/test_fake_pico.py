# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests du faux Pico, en mémoire, avec une horloge simulée."""

import unittest

import _paths  # noqa: F401
import pico_proto as P
from fake_pico import FakePico


class Clock:
    def __init__(self, t=1000):
        self.t = t

    def __call__(self):
        return self.t

    def ms(self, n):
        self.t += n * 1000


def lines(data):
    return [s for s in data.decode().split("\n") if s]


class Base(unittest.TestCase):
    def setUp(self):
        self.clk = Clock()
        self.pico = FakePico(clock=self.clk)
        self.boot = lines(self.pico.boot())

    def send(self, text):
        return lines(self.pico.feed(text.encode()))


class Boot(Base):
    def test_hello_caps_and_outputs_zero(self):
        self.assertEqual(P.parse_event(self.boot[0])[0], "HELLO")
        self.assertEqual(self.boot[1], P.msg_caps())
        self.assertTrue(self.pico.all_outputs_zero())

    def test_missing_mcp_reported(self):
        pico = FakePico(clock=self.clk, missing_mcp=("0x21",))
        self.assertIn("E MCP 0x21 absent", lines(pico.boot()))

    def test_every_output_line_is_a_valid_event(self):
        out = self.boot + self.send("WHO\n@1 PING\nBTNPOLL\nGET watchdog_ms\nNOPE\n")
        for s in out:
            P.parse_event(s)


class Replies(Base):
    def test_prefixed_and_unprefixed(self):
        self.assertEqual(self.send("PING\n"), ["PONG %d" % self.clk.t])
        self.assertEqual(self.send("@4 PING\n"), ["PONG %d" % self.clk.t, "K 4"])
        self.assertEqual(self.send("TOY 1 1\n"), [])
        self.assertEqual(self.send("@5 TOY 1 0\n"), ["K 5"])
        self.assertEqual(self.send("@6 WHO\n")[1:], [P.msg_caps(), "K 6"])

    def test_refusals(self):
        self.assertEqual(self.send("TOY 9 1\n"), ["N - RANGE"])
        self.assertEqual(self.send("@7 TOY 9 1\n"), ["N 7 RANGE"])
        self.assertEqual(self.send("@x PING\n"), ["N - SYNTAX"])
        self.assertEqual(self.send("FOO\n"), ["N - UNKNOWN"])
        self.assertEqual(self.send("@8 GET nope\n"), ["N 8 KEY"])
        self.assertEqual(self.send("X" * 3000 + "\n"), ["N - TOOLONG"])

    def test_crlf_and_chunking(self):
        self.assertEqual(lines(self.pico.feed(b"@1 PI")), [])
        self.assertEqual(lines(self.pico.feed(b"NG\r\n"))[-1], "K 1")


class Params(Base):
    def test_get_set_save_defaults(self):
        self.assertEqual(self.send("GET watchdog_ms\n"), ["V watchdog_ms 3000"])
        self.send("SET watchdog_ms 2000\n")
        self.assertEqual(self.send("GET watchdog_ms\n"), ["V watchdog_ms 2000"])
        self.assertEqual(self.pico.saved["watchdog_ms"], 3000)          # pas encore sauvé
        self.send("SAVE\n")
        self.assertEqual(self.pico.saved["watchdog_ms"], 2000)
        self.send("DEFAULTS\n")
        self.assertEqual(self.send("GET watchdog_ms\n"), ["V watchdog_ms 3000"])
        self.assertEqual(self.pico.saved["watchdog_ms"], 2000)          # fichier intact

    def test_reboot_reloads_saved(self):
        self.send("SET shock_mode adc\n")
        out = self.send("@2 REBOOT\n")
        self.assertEqual(out[0], "K 2")                                 # réponse avant
        self.assertEqual(P.parse_event(out[1])[0], "HELLO")
        self.assertEqual(self.pico.params["shock_mode"], "digital")


class Outputs(Base):
    def test_toys_lights_masks(self):
        self.send("TOYS A5\nLGTS 801\n")
        self.assertEqual(self.pico.toys, [1, 0, 1, 0, 0, 1, 0, 1])
        self.assertEqual(self.pico.lights, [1] + [0] * 10 + [1])
        self.send("TOY 1 0\nLGT 12 0\n")
        self.assertEqual(self.pico.toys[0], 0)
        self.assertEqual(self.pico.lights[11], 0)

    def test_pulse(self):
        self.send("TOYPULSE 2 100 50 2\n")
        self.assertEqual(self.pico.toys[1], 1)
        self.clk.ms(120)
        self.pico.tick()
        self.assertEqual(self.pico.toys[1], 0)
        self.clk.ms(40)
        self.pico.tick()
        self.assertEqual(self.pico.toys[1], 1)
        self.clk.ms(200)                                                # 2 répétitions finies
        self.pico.tick()
        self.assertEqual(self.pico.toys[1], 0)
        self.assertNotIn(("T", 2), self.pico.pulses)

    def test_pulse_continuous_and_stop(self):
        self.send("LGTPULSE 3 10 10 0\n")
        for _ in range(10):                                             # PING chaque seconde
            self.clk.ms(1000)
            self.pico.feed(b"PING\n")
        self.assertIn(("L", 3), self.pico.pulses)                       # continu
        self.send("LGTSTOP 3\n")
        self.assertEqual(self.pico.lights[2], 0)
        self.assertNotIn(("L", 3), self.pico.pulses)

    def test_toy_command_stops_pulse(self):
        self.send("TOYPULSE 1 10 10 0\nTOY 1 1\n")
        self.assertNotIn(("T", 1), self.pico.pulses)
        self.assertEqual(self.pico.toys[0], 1)

    def test_leds(self):
        self.send("LEDCFG 1 2\n")
        self.assertEqual(self.send("LEDF 1 FF0000\n"), ["N - ARGS"])     # 1 LED au lieu de 2
        self.assertEqual(self.pico.led_rgb[1], bytes(6))                 # aucun effet partiel
        self.send("LEDF 1 FF000000FF80\n")
        self.assertEqual(self.pico.led_rgb[1], b"\xff\x00\x00\x00\xff\x80")
        self.assertEqual(self.pico.led_wire(1), b"\x00\xff\x00\xff\x00\x80")   # GRB
        self.send("SET led1_order RGB\nLEDB 1 128\n")
        self.assertEqual(self.pico.led_wire(1), b"\x80\x00\x00\x00\x80\x40")
        self.send("LEDOFF 1\n")
        self.assertEqual(self.pico.led_rgb[1], bytes(6))

    def test_300_leds(self):
        self.send("LEDCFG 0 300\n")
        self.assertEqual(self.send("@1 " + P.command("LEDF", 0, bytes(900)) + "\n"), ["K 1"])
        self.assertEqual(self.send("@2 LEDCFG 0 301\n"), ["N 2 RANGE"])

    def test_mcpreset_zeroes_mcp_outputs(self):
        self.send("TOYS FF\nLGTS FFF\nTOYPULSE 1 10 10 0\n")
        self.send("MCPRESET\n")
        self.assertFalse(any(self.pico.toys) or any(self.pico.lights) or self.pico.pulses)


class Watchdog(Base):
    def test_timeout_then_resumed(self):
        self.send("TOYS FF\nLEDCFG 0 1\nLEDF 0 FFFFFF\nTOYPULSE 1 10 10 0\n")
        self.clk.ms(2999)
        self.assertEqual(lines(self.pico.tick()), [])
        self.clk.ms(1)
        self.assertEqual(lines(self.pico.tick()), ["E WDG timeout"])
        self.assertTrue(self.pico.all_outputs_zero())
        self.clk.ms(5000)
        self.assertEqual(lines(self.pico.tick()), [])                    # une seule fois
        self.assertEqual(self.send("@3 TOY 1 1\n"), ["E WDG resumed", "K 3"])
        self.assertEqual(self.pico.toys[0], 1)

    def test_any_byte_feeds_watchdog(self):
        for _ in range(10):
            self.clk.ms(2000)
            self.pico.feed(b"x")                                         # ligne incomplète
            self.assertFalse(self.pico.wdg_tripped)

    def test_late_bytes_trip_first(self):
        self.clk.ms(4000)
        self.assertEqual(self.send("PING\n"),
                         ["E WDG timeout", "E WDG resumed", "PONG %d" % self.clk.t])

    def test_watchdog_parameter_limits(self):
        self.assertEqual(self.send("@1 SET watchdog_ms 1999\n"), ["N 1 RANGE"])
        self.assertEqual(self.send("@2 SET watchdog_ms 60001\n"), ["N 2 RANGE"])
        self.assertEqual(self.pico.params["watchdog_ms"], 3000)          # aucun effet
        self.assertEqual(self.send("@3 SET watchdog_ms 60000\n"), ["K 3"])
        self.clk.ms(59999)
        self.assertEqual(lines(self.pico.tick()), [])
        self.clk.ms(1)
        self.assertEqual(lines(self.pico.tick()), ["E WDG timeout"])

    def test_watchdog_minimum(self):
        self.send("SET watchdog_ms 2000\n")
        self.clk.ms(1999)
        self.assertEqual(lines(self.pico.tick()), [])
        self.clk.ms(1)
        self.assertEqual(lines(self.pico.tick()), ["E WDG timeout"])

    def test_ping_every_third_keeps_watchdog_quiet(self):
        # Le Pi envoie PING au plus toutes les watchdog_ms / 3 (§ 5.4).
        self.send("SET watchdog_ms 2000\n")
        for _ in range(30):
            self.clk.ms(2000 // 3)
            self.assertEqual(lines(self.pico.feed(b"PING\n"))[0][:5], "PONG ")
        self.assertFalse(self.pico.wdg_tripped)


class Events(Base):
    def test_dart_filters(self):
        self.assertEqual(lines(self.pico.dart(3, 4, 2000)), ["D 3 4 %d 2000" % self.clk.t])
        self.assertEqual(self.pico.dart(3, 4, 299), b"")                 # trop court
        self.send("SET reject_report 1\n")
        self.assertEqual(lines(self.pico.dart(3, 4, 299)), ["X 3 4 299"])
        self.assertEqual(lines(self.pico.dart(3, 4, 400_001)),            # bloqué
                         ["D 3 4 %d 400001" % self.clk.t, "E STUCK 3 4"])
        self.send("CAL 1\n")
        self.assertEqual(lines(self.pico.dart(3, 4, 10)), ["D 3 4 %d 10" % self.clk.t])

    def test_contact_closed_at_start(self):
        # § 7.2 point 3 : E STUCK juste après HELLO et CAPS, jamais de D, 5 Hz.
        pico = FakePico(clock=self.clk, closed_at_start=((3, 4),))
        boot = lines(pico.boot())
        self.assertEqual(boot[2:], ["E STUCK 3 4"])
        self.assertFalse(any(s.startswith("D ") for s in boot))
        self.assertEqual(pico.heartbeat_mode(), "error")
        self.send("SET reject_report 1\n")
        self.assertEqual(pico.dart(3, 4, 2000), b"")                    # case encore fermée
        self.assertEqual(pico.dart(3, 4, 900_000), b"")                 # pas de second E STUCK
        self.assertEqual(lines(pico.dart(0, 0, 2000)), ["D 0 0 %d 2000" % self.clk.t])
        self.assertEqual(pico.release(3, 4), b"")                       # ouverture : ni D ni X
        self.assertEqual(pico.heartbeat_mode(), "normal")
        self.assertEqual(lines(pico.dart(3, 4, 2000)), ["D 3 4 %d 2000" % self.clk.t])

    def test_contact_closed_at_start_again_after_reboot(self):
        pico = FakePico(clock=self.clk, closed_at_start=((11, 6),))
        pico.boot()
        self.assertEqual(lines(pico.feed(b"@1 REBOOT\n"))[-1], "E STUCK 11 6")
        pico.release(11, 6)
        out = lines(pico.feed(b"@2 REBOOT\n"))
        self.assertEqual(out[0], "K 2")
        self.assertFalse(any(s.startswith("E ") for s in out))         # case rouverte : rien
        self.assertEqual(pico.heartbeat_mode(), "normal")

    def test_ticks_wrap_at_2_30(self):
        self.clk.t = (1 << 30) + 5
        self.assertEqual(lines(self.pico.dart(0, 0, 1000)), ["D 0 0 5 1000"])

    def test_buttons(self):
        self.assertEqual(lines(self.pico.button(15, 1)), ["B 15 1 %d" % self.clk.t])
        self.assertEqual(self.pico.button(15, 1), b"")                   # pas de changement
        self.pico.button(1, 1)
        self.assertEqual(self.send("BTNPOLL\n"), ["BS 4001"])
        self.pico.button(15, 0)
        self.assertEqual(self.send("BTNPOLL\n"), ["BS 0001"])

    def test_shock_holdoff_and_modes(self):
        self.assertEqual(lines(self.pico.shock(3000)), ["S %d 1" % self.clk.t])  # numérique
        self.clk.ms(49)
        self.assertEqual(self.pico.shock(), b"")
        self.clk.ms(1)
        self.assertNotEqual(self.pico.shock(), b"")
        self.send("SET shock_mode adc\nSET shock_threshold 1000\n")
        self.clk.ms(100)
        self.assertEqual(self.pico.shock(999), b"")
        self.assertEqual(lines(self.pico.shock(4095)), ["S %d 4095" % self.clk.t])

    def test_digital_mode_applies_threshold(self):
        # § 7.4 : lecture toujours par l'ADC, seuil appliqué aussi en mode digital
        self.send("SET shock_threshold 1000\n")
        self.assertEqual(self.pico.shock(999), b"")                      # sous le seuil
        self.assertEqual(lines(self.pico.shock(1000)), ["S %d 1" % self.clk.t])
        self.clk.ms(100)
        self.send("SET shock_threshold 4095\n")
        self.assertEqual(self.pico.shock(4094), b"")
        self.assertEqual(lines(self.pico.shock()), ["S %d 1" % self.clk.t])  # défaut : pleine échelle

    def test_below_threshold_does_not_start_holdoff(self):
        self.assertEqual(self.pico.shock(100), b"")
        self.assertNotEqual(self.pico.shock(3000), b"")

    def test_low_polarity(self):
        self.send("SET shock_polarity low\nSET shock_threshold 1000\n")
        self.assertEqual(self.pico.shock(1001), b"")                     # au-dessus : repos
        self.assertEqual(lines(self.pico.shock(1000)), ["S %d 1" % self.clk.t])
        self.clk.ms(100)
        self.assertEqual(lines(self.pico.shock()), ["S %d 1" % self.clk.t])  # défaut : 0
        self.send("SET shock_mode adc\n")
        self.clk.ms(100)
        self.assertEqual(lines(self.pico.shock(12)), ["S %d 12" % self.clk.t])


class Heartbeat(unittest.TestCase):
    """Voyant de vie sur GP16 (spécification § 3.1.2)."""

    def setUp(self):
        self.clk = Clock()

    def toggles(self, pico, total_ms, step_ms=10):
        times, prev = [], pico.gp16
        for _ in range(total_ms // step_ms):
            self.clk.ms(step_ms)
            pico.tick()
            if pico.gp16 != prev:
                times.append(self.clk.t)
                prev = pico.gp16
        return times

    def test_off_before_and_at_boot(self):
        pico = FakePico(clock=self.clk)
        self.clk.ms(2000)
        pico.tick()
        self.assertFalse(pico.heartbeat_on)          # pas de firmware : éteinte
        pico.boot()
        self.assertEqual(pico.gp16, 1)

    def test_normal_1_hz(self):
        pico = FakePico(clock=self.clk)
        pico.boot()
        self.assertEqual(pico.heartbeat_mode(), "normal")
        t = self.toggles(pico, 3000)
        self.assertEqual(len(t), 6)
        self.assertEqual({b - a for a, b in zip(t, t[1:])}, {500_000})

    def test_error_5_hz_when_mcp_missing(self):
        pico = FakePico(clock=self.clk, missing_mcp=("0x22",))
        pico.boot()
        self.assertEqual(pico.heartbeat_mode(), "error")
        t = self.toggles(pico, 1000)
        self.assertEqual(len(t), 10)
        self.assertEqual({b - a for a, b in zip(t, t[1:])}, {100_000})

    def test_frozen_when_main_loop_stalls(self):
        pico = FakePico(clock=self.clk)
        pico.boot()
        self.toggles(pico, 600)
        level = pico.gp16
        self.clk.ms(5000)                            # boucle bloquée : aucun tick
        self.assertEqual(pico.gp16, level)
        pico.tick()                                  # reprise : un seul basculement
        self.assertEqual(pico.gp16, level ^ 1)

    def test_keeps_blinking_after_watchdog(self):
        pico = FakePico(clock=self.clk)
        pico.boot()
        t = self.toggles(pico, 5000)                 # aucun octet du Pi
        self.assertTrue(pico.wdg_tripped)
        self.assertEqual(len(t), 10)


if __name__ == "__main__":
    unittest.main()
