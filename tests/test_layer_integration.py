# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Intégration de bout en bout, sans carte ni réseau :

    couche du jeu (cpico, rpi_ws281x)  <-> broker en mémoire <->  hwd (cœur + PahoBus)
                                                                     <-> faux Pico

Les clients MQTT des deux côtés sont les vraies classes (PicoLink, PahoBus),
avec un faux module paho relié au broker. Horloge simulée commune.
"""

import os
import queue
import sys
import unittest

import _paths  # noqa: F401

sys.path.insert(0, os.path.join(str(_paths.ROOT), "pi", "raspydarts_layer", "include"))
sys.path.insert(0, os.path.join(str(_paths.ROOT), "pi", "raspydarts_layer", "shims"))
import cpico  # noqa: E402
import rpi_ws281x  # noqa: E402
from fake_mqtt import FakeBroker, make_module  # noqa: E402
from fake_pico import FakePico  # noqa: E402
from hwd_config import HwdConfig  # noqa: E402
from hwd_core import CONNECTED, HwdCore  # noqa: E402
from hwd_daemon import PahoBus  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 50_000_000

    def __call__(self):
        return self.t


class Logs:
    def __init__(self):
        self.lines = []

    def warning(self, m):
        self.lines.append(m)

    info = debug = error = warning


class Rig:
    def __init__(self, tc):
        self.tc = tc
        self.clk = Clock()
        self.broker = FakeBroker()
        mqtt = make_module(self.broker)
        cfg = HwdConfig().validate()
        # Côté hwd.
        self.pico = FakePico(clock=self.clk)
        self.pico.boot()
        self.core = HwdCore(cfg, self.clk)
        self.bus = PahoBus(cfg, mqtt_module=mqtt)
        self.hwd_client = mqtt.Client.instances[-1]
        # Côté jeu.
        self.logs = Logs()
        self.link = cpico.PicoLink(self.logs, mqtt_module=mqtt)
        self.game_client = mqtt.Client.instances[-1]
        self.ext = cpico.PicoExtender(self.logs, self.link, cpico.DEFAULTS)
        self.darts = queue.Queue()
        self.source = cpico.PicoTargetSource(self.link, self.darts)
        # Serveur LED (remplaçant de rpi_ws281x).
        self.led_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "leds")
        self.led_client.connect_async("localhost", 1883, keepalive=5)
        rpi_ws281x.set_transport(lambda t, p: self.led_client.publish(t, p))
        self.core.serial_opened()
        self.pump()
        self.advance(cfg.handshake_flush_ms)       # vidage de la ligne avant WHO (§ 9.5)

    def pump(self, rounds=1000):
        for _ in range(rounds):
            moved = False
            out = self.pico.tick()
            if out:
                self.core.serial_data(out)
                moved = True
            self.core.tick()
            line = self.core.take_serial()
            while line is not None:
                moved = True
                reply = self.pico.feed(line)
                if reply:
                    self.core.serial_data(reply)
                line = self.core.take_serial()
            for topic, payload, retain in self.core.take_publishes():
                self.bus.publish(topic, payload, retain)
                moved = True
            if self.broker.pump():
                moved = True
            for topic, payload in self.bus.poll():
                self.core.bus_message(topic, payload)
                moved = True
            if not moved:
                return
        self.tc.fail("le système ne se calme pas")

    def advance(self, ms, step=10):
        for _ in range(ms // step):
            self.clk.t += step * 1000
            self.pump()


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(self)

    def tearDown(self):
        rpi_ws281x.set_transport(None)

    def test_connected_on_both_sides(self):
        self.assertEqual(self.rig.core.state, CONNECTED)
        self.assertTrue(self.rig.link.connected)           # état retenu reçu à l'abonnement
        self.assertIs(self.rig.ext.mcp1, self.rig.ext)

    def test_light_toy_reaches_pico_and_absent_toys_do_not(self):
        rig = self.rig
        rig.ext.light_toys(["TOY3", "LIGHT_LASER", "TOY9", "TOY16"], True)
        rig.pump()
        self.assertEqual(rig.pico.toys[2], 1)
        self.assertEqual(rig.pico.lights[4], 1)
        cmds = [p for t, p in rig.broker.log if t == "raspydarts/hw/cmd"]
        self.assertEqual(cmds, [b"TOY 3 1", b"LGT 5 1"])    # rien pour TOY9 et TOY16
        self.assertFalse(any(p.startswith(b"E CMD") for t, p in rig.broker.log))

    def test_dart_arrives_as_segment_code(self):
        rig = self.rig
        rig.core.serial_data(rig.pico.dart(3, 4, 2000))
        rig.pump()
        self.assertEqual(rig.darts.get_nowait()[0], "R3C4")

    def test_button_and_short_press(self):
        rig = self.rig
        rig.core.serial_data(rig.pico.button(1, 1))
        rig.pump()
        self.assertEqual(rig.ext.read_entries(), "PIN_UP")
        rig.core.serial_data(rig.pico.button(1, 0))
        rig.core.serial_data(rig.pico.button(15, 1))
        rig.core.serial_data(rig.pico.button(15, 0))       # appui bref, relâché aussitôt
        rig.pump()
        self.assertEqual(rig.ext.read_entries(), "PIN_CPTPLAYER")
        self.assertIsNone(rig.ext.read_entries())

    def test_shock_alone_gives_missdart(self):
        rig = self.rig
        rig.core.serial_data(rig.pico.shock())
        rig.advance(100)
        self.assertEqual(rig.ext.read_entries(), "PIN_MISSDART")
        self.assertTrue(rig.darts.empty())

    def test_shock_with_dart_is_not_a_miss(self):
        rig = self.rig
        rig.core.serial_data(rig.pico.shock())
        rig.advance(30)
        rig.core.serial_data(rig.pico.dart(1, 2, 2000))
        rig.advance(100)
        self.assertEqual(rig.darts.get_nowait()[0], "R1C2")
        self.assertIsNone(rig.ext.read_entries())

    def test_strobe_then_restore(self):
        rig = self.rig
        rig.ext.light_toys(["LIGHT_NAVIGATE"], True)
        threads = rig.ext.strobe_toys(["LIGHT_NAVIGATE"], iterations=2, delay_on=30, delay_off=40)
        rig.pump()
        self.assertIn(("L", 3), rig.pico.pulses)
        rig.advance(150)                                    # fin prévue : 2 × (30 + 40) ms
        self.assertNotIn(("L", 3), rig.pico.pulses)         # le Pico a fini (à 0)…
        self.assertEqual(rig.pico.lights[2], 1)             # … et hwd a restauré l'état (D9)
        for t in threads:
            t.join()

    def test_led_frame_from_shim(self):
        rig = self.rig
        os.environ["DARTSPICO_BRIDGE_LED_CHANNEL"] = "1"
        try:
            strip = rpi_ws281x.PixelStrip(3, 18, 800000, 9, False, 255, 0,
                                          strip_type=rpi_ws281x.WS2811_STRIP_RGB)
        finally:
            del os.environ["DARTSPICO_BRIDGE_LED_CHANNEL"]
        strip.begin()
        strip.setPixelColor(1, rpi_ws281x.Color(1, 2, 3))
        strip.show()
        rig.advance(30)
        self.assertEqual(rig.pico.params["led1_order"], "RGB")
        self.assertEqual(rig.pico.led_rgb[1], bytes([0, 0, 0, 1, 2, 3, 0, 0, 0]))

    def test_game_last_will_turns_everything_off(self):
        rig = self.rig
        rig.ext.light_toys(["TOY1", "LIGHT_FLASH"], True)
        rig.pump()
        self.assertEqual(rig.pico.toys[0], 1)
        rig.broker.kill(rig.game_client)                    # le jeu disparaît (R6)
        rig.pump()
        self.assertTrue(rig.pico.all_outputs_zero())

    def test_hwd_last_will_tells_game_pico_is_absent(self):
        rig = self.rig
        rig.broker.kill(rig.hwd_client)                     # hwd disparaît
        rig.broker.pump()
        self.assertFalse(rig.link.connected)
        self.assertIsNone(rig.ext.mcp1)

    def test_clean_game_exit_sends_off(self):
        rig = self.rig
        rig.ext.light_toys(["TOY2"], True)
        rig.pump()
        rig.link.close()
        rig.pump()
        self.assertTrue(rig.pico.all_outputs_zero())


if __name__ == "__main__":
    unittest.main()
