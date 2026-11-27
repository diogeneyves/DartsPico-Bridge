# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Firmware de la carte Pico (C4) : LED WS2812 par PIO et DMA (§ 3.5, § 5.3, § 7.1)."""

import unittest

import _fw_paths  # noqa: F401
import machine
import rp2
import board
import leds as leds_mod
from leds import Leds, PioDriver
import proto as P
from test_fw_core0 import Base, Clock


class Driver(unittest.TestCase):
    def setUp(self):
        self.sim = machine.reset_sim()
        rp2.machines.clear()
        del rp2.dmas[:]
        self.clk = Clock()
        self.drv = PioDriver(self.clk, machine.Pin)

    def test_three_state_machines_pins_low(self):
        for ch, pin in enumerate(board.LED_PINS):
            sm = rp2.machines[ch]
            self.assertTrue(sm.running)
            self.assertEqual(sm.pin.n, pin)
            self.assertEqual(sm.freq, 8000000)
            self.assertEqual(sm.prog, leds_mod.ws2812)
            self.assertEqual(self.sim.pins[pin].value(), 0)
        opts = leds_mod.ws2812.asm_pio_options
        self.assertEqual((opts["autopull"], opts["pull_thresh"]), (True, 24))
        self.assertEqual(opts["out_shiftdir"], rp2.PIO.SHIFT_LEFT)

    def test_words_and_dma_ctrl(self):
        self.assertTrue(self.drv.show(1, bytes([1, 2, 3, 0xFF, 0x80, 0x00])))
        self.assertEqual(rp2.machines[1].words, [0x01020300, 0xFF800000])
        words, sm, ctrl = rp2.dmas[1].transfers[0]
        self.assertIs(sm, rp2.machines[1])
        self.assertEqual(ctrl, dict(size=2, inc_read=True, inc_write=False, treq_sel=1))

    def test_busy_while_dma_or_latch(self):
        self.drv.show(0, bytes(3 * 204))
        self.assertFalse(self.drv.show(0, bytes(3 * 204)))       # 204 × 30 µs + 300 µs
        self.clk.t += 204 * 30 + 299
        self.assertFalse(self.drv.show(0, bytes(3 * 204)))
        self.clk.t += 1
        rp2.dmas[0].busy = True
        self.assertFalse(self.drv.show(0, bytes(3 * 204)))
        rp2.dmas[0].busy = False
        self.assertTrue(self.drv.show(0, bytes(3 * 204)))

    def test_long_idle_not_busy(self):
        self.drv.show(2, bytes(3))
        self.clk.t += 700 * 1000 * 1000                            # au-delà de 2^29 µs
        self.assertTrue(self.drv.show(2, bytes(3)))

    def test_zero_leds_sends_nothing(self):
        self.assertTrue(self.drv.show(0, b""))
        self.assertEqual(rp2.dmas[0].transfers, [])


class SameRoundingAsRpiWs281x(unittest.TestCase):
    """Même arrondi que ws2811_render() de rpi_ws281x (4.6.4) : (c × (b + 1)) >> 8."""

    def test_every_value_and_brightness(self):
        lv = Leds()
        lv.configure(0, 256, "RGB")
        lv.frame(0, bytes(c for c in range(256) for _ in range(3)))
        for b in range(256):
            lv.set_brightness(0, b)
            wire = lv.wire(0)
            want = bytes((c * (b + 1)) >> 8 for c in range(256) for _ in range(3))
            self.assertEqual(bytes(wire), want, b)

    def test_full_brightness_unchanged(self):
        lv = Leds()
        lv.configure(1, 2, "RGB")
        lv.frame(1, bytes([0, 1, 127, 128, 254, 255]))
        self.assertEqual(bytes(lv.wire(1)), bytes([0, 1, 127, 128, 254, 255]))


class Pipeline(Base):
    """LEDCFG / LEDF / LEDB / LEDOFF jusqu'aux mots envoyés à la PIO."""

    def setUp(self):
        super().setUp()
        rp2.machines.clear()
        del rp2.dmas[:]
        self.drv = PioDriver(self.clk, machine.Pin)
        self.leds.driver = self.drv

    def frame(self, ch):
        return rp2.machines[ch].words

    def test_grb_order_and_brightness(self):
        self.send("LEDCFG 0 2\nLEDF 0 FF8000000010\n")
        self.app.step()
        self.assertEqual(self.frame(0), [0x80FF0000, 0x00001000])    # GRB : G, R, B
        self.clk.ms(10)
        self.send("LEDB 0 128\n")
        self.app.step()
        self.assertEqual(self.frame(0)[2:], [0x40800000, 0x00000800])   # (c × 129) >> 8

    def test_frame_waits_for_latch_then_sent(self):
        self.send("LEDCFG 1 204\n")
        self.app.step()
        rp2.machines[1].words.clear()
        self.send("LEDF 1 " + "FFFFFF" * 204 + "\n")
        self.app.step()                                            # trame noire encore en cours
        self.assertEqual(rp2.machines[1].words, [])
        self.clk.ms(7)
        self.app.step()
        self.assertEqual(len(rp2.machines[1].words), 204)
        # Blanc plein à 255 : bridé à 10 000 mA (54e complément) ; facteur
        # 256 × 124 899 // 156 060 = 204, octet (255 × 204) >> 8 = 0xCB.
        self.assertEqual(rp2.machines[1].words[0], 0xCBCBCB00)

    def test_link_watchdog_blackens_leds(self):
        self.send("LEDCFG 2 1\nLEDF 2 FFFFFF\n")
        self.app.step()
        self.clk.ms(3000)
        self.assertEqual(self.step(), ["E WDG timeout"])
        self.clk.ms(1)
        self.app.step()
        self.assertEqual(self.frame(2)[-1], 0)

    def test_wrong_length_refused_nothing_sent(self):
        self.send("LEDCFG 0 2\n")
        self.app.step()
        n = len(self.frame(0))
        self.clk.ms(10)
        self.assertEqual(self.send("@1 LEDF 0 FFFFFF\n"), ["N 1 ARGS"])
        self.app.step()
        self.assertEqual(len(self.frame(0)), n)


# ------------------------------------------------ bridage du courant (54e complément)

def est_ma(lv, chans=(0, 1, 2)):
    """Courant estimé des octets émis : 1 mA par LED, 20 mA par couleur à 255."""
    n = sum(lv.count[ch] for ch in chans)
    return n * leds_mod.LED_IDLE_MA + sum(sum(lv.wire(ch)) for ch in chans) * leds_mod.LED_MA_PER_COLOR / 255


def ring(n, rgb=(255, 255, 255), b=255, ch=0, lv=None):
    lv = lv or Leds()
    lv.configure(ch, n, "GRB")
    lv.frame(ch, bytes(rgb) * n)
    lv.set_brightness(ch, b)
    return lv


class CurrentLimit(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(P.LED_MAX_MA, 10000)
        self.assertEqual(P.param_specs()["led_max_ma"], (10000, (0, P.LED_MAX_MA)))
        self.assertEqual(leds_mod.LED_MA_PER_COLOR, 20)
        self.assertEqual(leds_mod.LED_IDLE_MA, 1)
        for ch in range(P.LED_CHANNELS):
            self.assertEqual(P.param_specs()["led%d_5v" % ch], (1, (0, 1)))

    def test_full_white_204_and_244_under_budget(self):
        for n in (204, 244):
            lv = ring(n)
            ma = est_ma(lv)
            self.assertLessEqual(ma, 10000, n)
            self.assertGreater(ma, 9800, n)                      # pas de réduction excessive
            w = bytes(lv.wire(0))
            self.assertEqual(len(set(w)), 1, n)                  # réduction uniforme

    def test_full_white_on_three_channels_under_one_budget(self):
        lv = ring(244)
        ring(300, ch=1, lv=lv)
        ring(300, ch=2, lv=lv)
        self.assertLessEqual(est_ma(lv), 10000)

    def test_channel_off_5v_not_counted_nor_reduced(self):
        lv = ring(204)
        ring(300, ch=1, lv=lv)
        lv.set_power(10000, [1, 0, 1])
        self.assertEqual(bytes(lv.wire(1)), b"\xff" * 900)        # ruban en 12 V intact
        self.assertLessEqual(est_ma(lv, (0, 2)), 10000)
        self.assertGreater(est_ma(lv, (0, 2)), 9800)

    def test_under_limit_byte_identical(self):
        import random
        rnd = random.Random(54)
        for _ in range(200):
            n = rnd.randint(1, 244)
            b = rnd.randint(0, 255)
            rgb = bytes(rnd.randint(0, 255) for _ in range(3 * n))
            want = bytes((c * (b + 1)) >> 8 for c in rgb)
            if n + sum(want) * 20 / 255 > 10000:
                continue                                         # au-delà : bridé, autre test
            lv = Leds()
            lv.configure(0, n, "RGB")
            lv.frame(0, rgb)
            lv.set_brightness(0, b)
            self.assertEqual(bytes(lv.wire(0)), want)

    def test_hwd_default_cap_unchanged(self):
        """Plafond 128 de hwd : blanc plein sur 204 et 244 LED, octets d'avant le bridage."""
        for n in (204, 244):
            lv = ring(n, b=128)
            self.assertEqual(bytes(lv.wire(0)), bytes([(255 * 129) >> 8]) * (3 * n))

    def test_exact_limit_kept(self):
        """Somme exacte égale au budget : rien ne change."""
        lv = Leds()
        lv.configure(0, 1, "RGB")
        lv.frame(0, bytes([255, 255, 255]))
        lv.set_power(1 + 3 * 20, [1, 1, 1])                      # 61 mA : 765 × 20 / 255 + 1
        self.assertEqual(bytes(lv.wire(0)), b"\xff\xff\xff")
        lv.set_power(60, [1, 1, 1])
        self.assertLessEqual(est_ma(lv, (0,)), 60)

    def test_hues_kept(self):
        lv = ring(244, rgb=(255, 200, 64))                       # 244 × 519 octets : au-delà
        w = bytes(lv.wire(0))
        k = lv.k_eff[0]
        self.assertLess(k, 256)
        self.assertEqual(w[:3], bytes([(200 * k) >> 8, (255 * k) >> 8, (64 * k) >> 8]))   # GRB
        self.assertLessEqual(est_ma(lv), 10000)

    def test_brightness_applied_before_limit(self):
        lv = ring(204, b=200)
        self.assertLessEqual(est_ma(lv), 10000)
        lv.set_brightness(0, 150)                                # 612 × 150 = 91 800 octets
        self.assertEqual(bytes(lv.wire(0)), bytes([(255 * 151) >> 8]) * 612)

    def test_max_ma_never_above_constant(self):
        lv = ring(244)
        lv.set_power(50000, [1, 1, 1])
        self.assertEqual(lv.max_ma, P.LED_MAX_MA)
        self.assertLessEqual(est_ma(lv), 10000)

    def test_zero_budget_black(self):
        lv = ring(10)
        lv.set_power(0, [1, 1, 1])
        self.assertEqual(bytes(lv.wire(0)), bytes(30))

    def test_random_images_always_under_budget(self):
        import random
        rnd = random.Random(5)
        for _ in range(100):
            lv = Leds()
            for ch in range(3):
                n = rnd.randint(0, 300)
                lv.configure(ch, n, "GRB")
                lv.frame(ch, bytes(rnd.choice((0, 255, rnd.randint(0, 255))) for _ in range(3 * n)))
                lv.set_brightness(ch, rnd.randint(0, 255))
            ma = rnd.randint(0, 10000)
            lv.set_power(ma, [1, 1, 1])
            self.assertLessEqual(est_ma(lv), max(ma, sum(lv.count)))


class CurrentLimitProtocol(Base):
    def setUp(self):
        super().setUp()
        rp2.machines.clear()
        del rp2.dmas[:]
        self.drv = PioDriver(self.clk, machine.Pin)
        self.leds.driver = self.drv

    def test_set_above_constant_refused(self):
        self.assertEqual(self.send("@1 SET led_max_ma 10001\n"), ["N 1 RANGE"])
        self.assertEqual(self.send("@2 SET led_max_ma 10000\n"), ["K 2"])
        self.assertEqual(self.send("GET led_max_ma\n"), ["V led_max_ma 10000"])
        self.assertEqual(self.send("@3 SET led1_5v 2\n"), ["N 3 RANGE"])

    def test_counter_get_only(self):
        self.assertEqual(self.send("GET led_limited\n"), ["V led_limited 0"])
        self.assertEqual(self.send("@1 SET led_limited 0\n"), ["N 1 KEY"])
        self.assertNotIn("led_limited", P.default_params())

    def test_limited_frames_counted_and_budget_from_set(self):
        self.send("LEDCFG 0 204\n")
        self.app.step()
        self.clk.ms(10)
        self.send("LEDF 0 " + "FFFFFF" * 204 + "\n")
        self.app.step()
        self.assertEqual(self.send("GET led_limited\n"), ["V led_limited 1"])
        self.assertEqual(rp2.machines[0].words[-1], 0xCBCBCB00)
        self.clk.ms(10)
        self.send("SET led_max_ma 5000\n")
        self.app.step()
        word = rp2.machines[0].words[-1]
        self.assertLess(word, 0xCBCBCB00)
        self.assertLessEqual(204 + 612 * (word >> 24) * 20 / 255, 5000)
        self.assertEqual(self.send("GET led_limited\n"), ["V led_limited 2"])
        self.clk.ms(10)
        self.send("SET led_max_ma 10000\nLEDB 0 128\n")        # plafond de hwd : plus de bridage
        self.app.step()
        self.assertEqual(rp2.machines[0].words[-1], 0x80808000)
        self.assertEqual(self.send("GET led_limited\n"), ["V led_limited 2"])

    def test_other_channel_resent_when_factor_changes(self):
        self.send("LEDCFG 0 204\nLEDCFG 1 100\n")
        self.app.step()
        self.clk.ms(10)
        self.send("LEDF 1 " + "FFFFFF" * 100 + "\n")
        self.app.step()
        self.assertEqual(rp2.machines[1].words[-1], 0xFFFFFF00)  # seul : sous le budget
        self.clk.ms(10)
        self.send("LEDF 0 " + "FFFFFF" * 204 + "\n")
        self.app.step()
        w0 = rp2.machines[0].words[-1] >> 24
        w1 = rp2.machines[1].words[-1] >> 24
        self.assertLess(w1, 0xFF)                                # canal 1 renvoyé, réduit
        self.assertLessEqual(304 + (612 * w0 + 300 * w1) * 20 / 255, 10000)
        self.clk.ms(10)
        self.send("SET led1_5v 0\n")                            # ruban en 12 V : plus compté
        self.app.step()
        self.assertEqual(rp2.machines[1].words[-1], 0xFFFFFF00)

    def test_defaults_restore_budget(self):
        self.send("SET led_max_ma 100\nDEFAULTS\n")
        self.assertEqual(self.leds.max_ma, 10000)

    def test_saved_file_above_constant_gives_defaults(self):
        import json
        import os
        import tempfile
        import config
        d = tempfile.mkdtemp()
        path = os.path.join(d, "params.json")
        with open(path, "w") as f:
            json.dump({"led_max_ma": 20000}, f)
        params, err = config.load(path)
        self.assertEqual(params, P.default_params())
        self.assertEqual(err, "params file invalid, defaults loaded")
        with open(path, "w") as f:
            json.dump({"led_max_ma": 4000, "led2_5v": 0}, f)
        params, err = config.load(path)
        self.assertIsNone(err)
        Base.setUp(self, params=params)
        self.assertEqual((self.leds.max_ma, self.leds.on_5v), (4000, [1, 1, 0]))


if __name__ == "__main__":
    unittest.main()
