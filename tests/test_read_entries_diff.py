# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Test différentiel : Gpio_extender.read_entries (le VRAI code de RaspyDarts) contre
PicoExtender.read_entries, sur les mêmes séquences d'états des boutons.

include/cgpio_extender.py est lu dans ~/RaspyDarts-ref465 sans rien y écrire. Ses
dépendances matérielles (board, busio, digitalio, adafruit_mcp230xx) et son
import relatif (from .ccoins import I2C_LOCK) sont remplacés par des modules
factices dans sys.modules ; la puce MCP23017 est simulée.

Le verrou de PicoExtender (appui bref, R3) est neutralisé ici : on compare la
logique sur l'état seul, qui doit être identique. Depuis RaspyDarts 4.6.3, la logique
dépend du temps (rebonds, répétition des flèches, bouton bloqué) : les deux côtés lisent
la même horloge simulée, avancée de dt à chaque lecture.
"""

import os
import sys
import threading
import types
import unittest

import _cplatform
import _orig
import _paths  # noqa: F401

sys.path.insert(0, os.path.join(str(_paths.ROOT), "pi", "raspydarts_layer", "include"))
import cpico  # noqa: E402

FAKE_NAMES = ("board", "busio", "digitalio", "adafruit_mcp230xx", "adafruit_mcp230xx.mcp23017",
              "include", "include.ccoins", "include.cgpio_extender", "include.cplatform")


class FakePin:
    """Broche simulée : les écritures de value sont notées dans FakePin.log (blink_toy)."""
    log = []

    def __init__(self):
        self.direction = None
        self.pull = None
        self._value = 1

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, v):
        self._value = int(bool(v))
        FakePin.log.append(("set", int(bool(v))))


class FakeMCP23017:
    """Puce simulée : gpio (16 bits, 1 = niveau haut = bouton relâché), registres 8 bits."""
    level = 0xFFFF

    def __init__(self, i2c, address=0x20, reset=True):
        self.address = address
        self._regs = {}
        self._pins = [FakePin() for _ in range(16)]

    def _write_u8(self, reg, value):
        self._regs[reg] = value

    def _read_u8(self, reg):
        return self._regs.get(reg, 0)

    def get_pin(self, n):
        return self._pins[n]

    @property
    def gpio(self):
        return FakeMCP23017.level


class FakeI2C:
    def __init__(self, scl, sda):
        pass

    def try_lock(self):
        return True

    def unlock(self):
        pass

    def scan(self):
        return [0x20]


def fake_modules():
    board = types.ModuleType("board")
    board.SCL, board.SDA = "SCL", "SDA"
    busio = types.ModuleType("busio")
    busio.I2C = FakeI2C
    digitalio = types.ModuleType("digitalio")
    digitalio.Direction = types.SimpleNamespace(INPUT="in", OUTPUT="out")
    digitalio.Pull = types.SimpleNamespace(UP="up", DOWN="down")
    ada = types.ModuleType("adafruit_mcp230xx")
    ada.__path__ = []
    mcp = types.ModuleType("adafruit_mcp230xx.mcp23017")
    mcp.MCP23017 = FakeMCP23017
    include = types.ModuleType("include")
    include.__path__ = []                       # paquet vide : rien du vrai include/
    ccoins = types.ModuleType("include.ccoins")
    ccoins.I2C_LOCK = threading.Lock()
    modules = {"board": board, "busio": busio, "digitalio": digitalio, "adafruit_mcp230xx": ada,
               "adafruit_mcp230xx.mcp23017": mcp, "include": include, "include.ccoins": ccoins}
    _cplatform.install(include, modules)        # RaspyDarts 4.6.5 : un Pi 4, Blinka chargé
    return modules


class Clock:
    """Horloge simulée (secondes), commune aux deux côtés."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeTime:
    """Remplace le module time de cgpio_extender : monotonic() lit l'horloge simulée,
    sleep() est noté dans FakePin.log, sans attendre."""

    def __init__(self, clock):
        self.monotonic = clock

    @staticmethod
    def sleep(seconds):
        FakePin.log.append(("sleep", round(seconds, 6)))

    def __getattr__(self, name):
        import time
        return getattr(time, name)


class Logs:
    def __init__(self):
        self.lines = []

    def warning(self, m):
        self.lines.append(m)

    info = debug = error = warning


class Link:
    """Lien minimal : état des boutons seul (verrou neutralisé), commandes notées."""
    connected = True

    def __init__(self):
        self.pressed = 0
        self.sent = []

    def take_buttons(self):
        return self.pressed, False

    def send(self, line):
        self.sent.append(line)


# Les 15 fonctions des boutons, dans l'ordre de cpico.DEFAULTS : même ordre des deux côtés.
FUNCTIONS = [k for k, v in cpico.DEFAULTS.items() if v.startswith("BTN")]
ORIG_PINS = ["A%d" % i for i in range(7)] + ["B%d" % i for i in range(8)]   # 15 broches
LED = "LIGHT_NAVIGATE"                                  # LED de bouton pour blink_toy


@unittest.skipUnless(_orig.available(), "copie de RaspyDarts absente (%s)" % _orig.ORIG)
class ReadEntriesDifferential(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        before = _orig.status()
        assert before == "", "clone déjà modifié : %r" % before
        cls.saved = {n: sys.modules.get(n) for n in FAKE_NAMES}
        cls.saved_env = os.environ.get("BLINKA_SKIP_DEPENDENCY_CHECK")
        sys.modules.update(fake_modules())
        cls.module = _orig.load(os.path.join("include", "cgpio_extender.py"),
                                "include.cgpio_extender")

    @classmethod
    def tearDownClass(cls):
        for name, mod in cls.saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        if cls.saved_env is None:
            os.environ.pop("BLINKA_SKIP_DEPENDENCY_CHECK", None)
        after = _orig.status()
        assert after == "", "le test a écrit dans le clone : %r" % after

    def setUp(self):
        m = self.module
        self.clock = Clock()
        m.time = FakeTime(self.clock)                    # module time du code d'origine
        FakeMCP23017.level = 0xFFFF
        wiring = dict(zip(FUNCTIONS, ORIG_PINS))
        wiring[LED] = "A7"                               # seule broche libre de la puce
        self.orig = m.Gpio_extender(Logs(), wiring)
        self.link = Link()
        self.pico = cpico.PicoExtender(Logs(), self.link, cpico.DEFAULTS)
        self.pico.clock = self.clock
        self.pico_log = []
        self.pico.sleep = lambda s: self.pico_log.append(("sleep", round(s, 6)))

    def set_state(self, pressed):
        """pressed : ensemble de fonctions (PIN_UP…) appuyées, appliqué aux deux côtés."""
        level, mask = 0xFFFF, 0
        for f in pressed:
            i = FUNCTIONS.index(f)
            bank, num = ORIG_PINS[i][0], int(ORIG_PINS[i][1])
            level &= ~(1 << ((bank == "B") * 8 + num))
            mask |= 1 << (int(cpico.DEFAULTS[f][3:]) - 1)
        FakeMCP23017.level = level
        self.link.pressed = mask

    def run_sequence(self, steps):
        """steps : liste de (ensemble appuyé, nombre de lectures[, secondes entre lectures]).

        Par défaut 0,1 s entre deux lectures (ordre de grandeur de la boucle du jeu).
        """
        orig, pico = [], []
        for step in steps:
            pressed, reads = step[0], step[1]
            dt = step[2] if len(step) > 2 else 0.1
            self.set_state(pressed)
            for _ in range(reads):
                self.clock.now += dt
                orig.append((self.orig.read_entries(), self.orig.banned_button))
                pico.append((self.pico.read_entries(), self.pico.banned_button))
        return orig, pico

    def assertSame(self, steps):
        orig, pico = self.run_sequence(steps)
        self.assertEqual(pico, orig)
        return orig

    def test_original_chip_is_really_used(self):
        self.assertEqual(len(self.orig.chips), 1)
        self.assertEqual(len(self.orig.pin_switcher), 15)
        self.set_state({"PIN_UP"})
        self.assertEqual(self.orig.read_entries(), "PIN_UP")

    def test_idle(self):
        self.assertSame([(set(), 5)])

    def test_one_button(self):
        out = self.assertSame([({"PIN_VALIDATE"}, 3), (set(), 2)])
        self.assertEqual([r for r, _ in out], ["PIN_VALIDATE", None, None, None, None])

    def test_two_buttons_together(self):
        self.assertSame([({"PIN_UP", "PIN_CPTPLAYER"}, 5), ({"PIN_UP"}, 2), (set(), 2)])

    def test_held_validate_counts_once(self):
        out = self.assertSame([({"PIN_VALIDATE"}, 30, 0.05), (set(), 2)])
        self.assertEqual([r for r, _ in out].count("PIN_VALIDATE"), 1)

    def test_held_arrow_repeats(self):
        out = self.assertSame([({"PIN_DOWN"}, 40, 0.05), (set(), 2)])
        self.assertGreater([r for r, _ in out].count("PIN_DOWN"), 5)

    def test_bounce_ignored(self):
        out = self.assertSame([({"PIN_PLUS"}, 1), (set(), 1, 0.02), ({"PIN_PLUS"}, 1, 0.02),
                               (set(), 1, 0.2), ({"PIN_PLUS"}, 1, 0.2)])
        self.assertEqual([r for r, _ in out], ["PIN_PLUS", None, None, None, "PIN_PLUS"])

    def test_stuck_button_banned_then_released(self):
        out = self.assertSame([({"PIN_NEXTPLAYER"}, 110), (set(), 3), ({"PIN_NEXTPLAYER"}, 1)])
        self.assertIn((None, "PIN_NEXTPLAYER"), out)              # banni à un moment
        self.assertEqual(out[-1], ("PIN_NEXTPLAYER", None))       # relâché : débanni

    def test_banned_then_other_button(self):
        self.assertSame([({"PIN_BACK"}, 110), ({"PIN_BACK", "PIN_PLUS"}, 5),
                         ({"PIN_PLUS"}, 5), (set(), 3)])

    def test_stuck_arrow_banned(self):
        self.assertSame([({"PIN_LEFT"}, 120), (set(), 2)])

    def test_alternating(self):
        steps = []
        for i in range(10):
            steps.append(({"PIN_LEFT"} if i % 2 else {"PIN_RIGHT"}, 1))
        steps += [({"PIN_MINUS"}, 3), (set(), 1), ({"PIN_MINUS"}, 3), (set(), 1)]
        self.assertSame(steps)

    def test_comparison_detects_a_difference(self):
        # Délai du bouton bloqué décalé : les deux séquences doivent différer.
        saved = cpico.STUCK
        cpico.STUCK = saved - 1
        try:
            orig, pico = self.run_sequence([({"PIN_UP"}, 110)])
        finally:
            cpico.STUCK = saved
        self.assertNotEqual(pico, orig)

    # ---- mode capture (RaspyDarts 4.6.4 : ccapture.py met hold_validate à True)

    def capture(self, steps):
        self.orig.hold_validate = self.pico.hold_validate = True
        return self.assertSame(steps)

    def test_capture_short_validate_given_on_release(self):
        out = self.capture([({"PIN_VALIDATE"}, 5), (set(), 2)])
        self.assertEqual([r for r, _ in out], [None] * 5 + ["PIN_VALIDATE", None])

    def test_capture_long_validate_gives_end_once(self):
        out = self.capture([({"PIN_VALIDATE"}, 40), (set(), 3)])
        results = [r for r, _ in out]
        self.assertEqual(results.count("PIN_CAPTURE_END"), 1)
        self.assertNotIn("PIN_VALIDATE", results)

    def test_capture_validate_then_other_button(self):
        self.capture([({"PIN_VALIDATE"}, 3), ({"PIN_VALIDATE", "PIN_UP"}, 3), ({"PIN_UP"}, 3),
                      (set(), 2), ({"PIN_VALIDATE"}, 25), ({"PIN_VALIDATE", "PIN_BACK"}, 2),
                      (set(), 2)])

    def test_capture_bounce_and_stuck(self):
        self.capture([({"PIN_VALIDATE"}, 1), (set(), 1, 0.02), ({"PIN_VALIDATE"}, 1, 0.02),
                      (set(), 2), ({"PIN_VALIDATE"}, 120), (set(), 2)])

    def test_capture_other_buttons_unchanged(self):
        self.capture([({"PIN_DOWN"}, 20, 0.05), (set(), 2), ({"PIN_PLUS"}, 3), (set(), 2)])

    def test_capture_off_again(self):
        self.capture([({"PIN_VALIDATE"}, 30), (set(), 1)])
        self.orig.hold_validate = self.pico.hold_validate = False
        self.assertSame([({"PIN_VALIDATE"}, 30), (set(), 2)])

    # ---- blink_toy (LED des boutons, cbuttonleds.py)

    def blink(self, lit, cycles, on_ms, off_ms):
        self.orig.light_toys([LED], lit)
        self.pico.light_toys([LED], lit)
        FakePin.log.clear()
        self.link.sent.clear()
        self.pico_log.clear()
        for side in (self.orig, self.pico):
            thread = side.blink_toy(LED, cycles, on_ms, off_ms)
            if thread is not None:
                thread.join(5)
        output = self.pico.toys_list[LED][1]
        kind, n = output.kind, output.number
        prefix = "%s %d " % (kind, n)
        # Côté Pico : envois et attentes entremêlés, dans l'ordre (le fil est terminé)
        pico = []
        sent = iter(self.link.sent)
        sleeps = iter(self.pico_log)
        for event in FakePin.log:
            if event[0] == "set":
                line = next(sent)
                self.assertTrue(line.startswith(prefix), line)
                pico.append(("set", int(line[len(prefix):])))
            else:
                pico.append(next(sleeps))
        self.assertEqual(list(sent), [])
        self.assertEqual(list(sleeps), [])
        return FakePin.log[:], pico

    def test_blink_toy_same_sequence(self):
        for lit in (True, False):
            for cycles in (1, 2, 3):
                with self.subTest(lit=lit, cycles=cycles):
                    orig, pico = self.blink(lit, cycles, 120, 40)
                    self.assertEqual(pico, orig)
                    self.assertEqual(orig[-1], ("set", 1 if lit else 0))   # état de départ

    def test_output_value_same(self):
        # toys_list[nom][1].value (cscreen.button_led_try) : même lecture des deux côtés.
        for lit in (True, False, True):
            self.orig.light_toys([LED], lit)
            self.pico.light_toys([LED], lit)
            self.assertEqual(self.pico.toys_list[LED][1].value,
                             self.orig.toys_list[LED][1].value)

    def test_blink_toy_nothing(self):
        for cycles in (0, -1):
            self.assertIsNone(self.orig.blink_toy(LED, cycles, 10, 10))
            self.assertIsNone(self.pico.blink_toy(LED, cycles, 10, 10))
        self.assertIsNone(self.orig.blink_toy("LIGHT_INCONNUE", 2, 10, 10))
        self.assertIsNone(self.pico.blink_toy("LIGHT_INCONNUE", 2, 10, 10))

    def test_every_single_button(self):
        for f in FUNCTIONS:
            with self.subTest(button=f):
                self.assertSame([({f}, 2), (set(), 1), ({f}, 1)])


if __name__ == "__main__":
    unittest.main()
