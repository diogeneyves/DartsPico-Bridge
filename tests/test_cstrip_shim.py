# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Le remplaçant de rpi_ws281x contre le VRAI addons/CStrip.py de RaspyDarts.

CStrip.py et Colors.py sont lus dans ~/RaspyDarts-ref465 sans rien y écrire
(pas de bytecode) ; l'état git du clone est vérifié avant et après.
"""

import contextlib
import io
import os
import sys
import unittest

import _orig
import _paths  # noqa: F401

SHIMS = os.path.join(str(_paths.ROOT), "pi", "raspydarts_layer", "shims")


@unittest.skipUnless(_orig.available(), "copie de RaspyDarts absente (%s)" % _orig.ORIG)
class RealCStrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.status_before = _orig.status()
        assert cls.status_before == "", "clone déjà modifié : %r" % cls.status_before
        assert _orig.available()                 # code de la copie = BASE
        cls.saved_path = list(sys.path)
        cls.saved_env = os.environ.get("DARTSPICO_BRIDGE_LED_CHANNEL")
        for name in ("rpi_ws281x", "Colors", "CStrip_orig"):
            sys.modules.pop(name, None)
        sys.path.insert(0, os.path.join(_orig.ORIG, "addons"))   # pour « import Colors »
        sys.path.insert(0, SHIMS)                                 # remplaçant en tête
        import rpi_ws281x
        cls.shim = rpi_ws281x
        cls.module = _orig.load(os.path.join("addons", "CStrip.py"), "CStrip_orig")

    @classmethod
    def tearDownClass(cls):
        sys.path[:] = cls.saved_path
        for name in ("Colors", "CStrip_orig"):
            sys.modules.pop(name, None)
        if cls.saved_env is None:
            os.environ.pop("DARTSPICO_BRIDGE_LED_CHANNEL", None)
        else:
            os.environ["DARTSPICO_BRIDGE_LED_CHANNEL"] = cls.saved_env
        after = _orig.status()
        assert after == "", "le test a écrit dans le clone : %r" % after

    def setUp(self):
        # CStrip.Logs écrit sur la sortie standard : capturé.
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.sent = []
        self.shim.set_transport(lambda t, p: self.sent.append((t, p)))
        os.environ["DARTSPICO_BRIDGE_LED_CHANNEL"] = "1"

    def tearDown(self):
        self.shim.set_transport(None)

    def frames(self):
        return [p for t, p in self.sent if t == "raspydarts/hw/led/1"]

    def strip(self, n=5, brightness=0.5, strip_type="grb"):
        # Mêmes arguments que addons/StripLeds_Server.py:262 (pin, nombre, luminosité, DMA).
        return self.module.CStrip(18, n, brightness, 9, "STRIP", strip_type=strip_type)

    def test_the_real_module_uses_the_shim(self):
        self.assertIs(self.module.PixelStrip, self.shim.PixelStrip)
        self.assertTrue(self.shim.__file__.startswith(SHIMS))

    def test_begin_sends_colour_order(self):
        self.strip(strip_type="grb")
        self.strip(strip_type="rgb")
        cmds = [p for t, p in self.sent if t == "raspydarts/hw/cmd"]
        self.assertEqual(cmds, ["SET led1_order GRB", "LEDB 1 127", "SET led1_order RGB", "LEDB 1 127"])

    def cmds(self):
        return [p for t, p in self.sent if t == "raspydarts/hw/cmd"]

    def test_all_leds_with_brightness(self):
        # La luminosité part par LEDB (v0.3.14) ; l'image reste sans atténuation.
        s = self.strip(n=3, brightness=0.5)                 # int(0.5 * 255) = 127
        s.AllLeds([255, 0, 128])
        self.assertEqual(self.frames()[-1], bytes([255, 0, 128]) * 3)
        self.assertEqual(self.cmds()[-1], "LEDB 1 127")
        s.SetBrightness(1.0)
        s.AllLeds([255, 0, 128])
        self.assertEqual(self.cmds()[-1], "LEDB 1 255")
        self.assertEqual(self.frames()[-1], bytes([255, 0, 128]) * 3)

    def test_blit_colors_from_handle(self):
        s = self.strip(n=4, brightness=1.0)
        s.handle[2] = [10, 20, 30]
        s.BlitColors(0)
        self.assertEqual(self.frames()[-1], bytes(6) + bytes([10, 20, 30]) + bytes(3))
        self.assertEqual(s.strip.getPixelColor(2), self.shim.Color(10, 20, 30))

    def test_animation_colour_wipe(self):
        s = self.strip(n=4, brightness=1.0)
        s.SA_ColorWipe(None, 0, [0, 255, 0], 1)
        frames = self.frames()
        self.assertEqual(len(frames), 1 + 4 + 1)            # extinction, 4 pas, extinction
        self.assertEqual(frames[4], bytes([0, 255, 0]) * 4)
        self.assertEqual(frames[-1], bytes(12))

    def test_target_device_with_configuration(self):
        # Toutes les clés de [LEDTarget] (cconfig.py:463-465) : une LED par segment.
        keys = ["%s%d" % (m, n) for m in "SDT" for n in range(1, 21)] + ["SB", "DB"]
        conf = {k: str(i) for i, k in enumerate(keys)}
        conf.update({"E%d" % n: "" for n in range(1, 21)})
        s = self.module.CStrip(10, len(keys), 1.0, 10, "TARGET", configuration=str(conf))
        self.assertEqual(s.target_leds_count, 62)
        s.AllLeds([1, 2, 3])
        self.assertEqual(self.frames()[-1], bytes([1, 2, 3]) * 62)


if __name__ == "__main__":
    unittest.main()
