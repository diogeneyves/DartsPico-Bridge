# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Patch 0003 (include/ci2c.py) : en mode carte Pico, follow_wiring ne fait rien.

Ni lecture ni écriture de config.txt, ni sudo, ni redémarrage demandé. En mode normal,
le ci2c.py patché donne exactement les mêmes résultats et les mêmes écritures que
l'original (lu dans la copie de référence, sans y écrire).
"""

import importlib.util
import os
import shutil
import sys
import tempfile
import types
import unittest

import _orig
import _paths  # noqa: F401
from test_patches import fresh_clone, run_script


class Logs:
    def __init__(self):
        self.lines = []

    def warning(self, m):
        self.lines.append(m)

    info = debug = error = warning


def fake_config(hardware, pins_in=None, raspberry=None):
    config = {"Raspberry_BoardPinsIns": pins_in or {}, "Raspberry_BoardPinsOuts": {},
              "SectionKeys": {}, "Raspberry": raspberry or {}}
    if hardware is not None:
        config["SectionAdvanced"] = {"hardware": hardware}
    return types.SimpleNamespace(config=config, GPIO_BUTTONS_CONFIG=())


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Câblages : GPIO 2 utilisé par la cible (bus à couper), carte MCP déclarée (bus à ouvrir), rien.
WIRINGS = {
    "gpio2": dict(pins_in={"IN1": "2"}),
    "mcp": dict(raspberry={"PIN_UP": "A0"}),
    "rien": dict(),
}
# État du Raspberry : (bus demandé dans config.txt, bus actif)
STATES = [(True, True), (False, False), (None, False), (True, False), (False, True)]


@unittest.skipUnless(_orig.available(), "copie de RaspyDarts absente (%s)" % _orig.ORIG)
class Patch0003(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert _orig.status() == ""
        sys.dont_write_bytecode = True
        cls.tmp = tempfile.mkdtemp()
        cls.copy = fresh_clone(cls.tmp)
        out = run_script(cls.copy)
        assert out.returncode == 0, out.stderr
        cls.patched_path = os.path.join(cls.copy, "include", "ci2c.py")
        cls.original_path = os.path.join(_orig.ORIG, "include", "ci2c.py")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)
        assert _orig.status() == "", "le test a écrit dans le clone"

    def instrument(self, path, state):
        """Le module, ses accès au système remplacés par des enregistreurs."""
        module = load(path, "ci2c_under_test")
        calls = []
        asked, active = state
        module.enabled_in_config = lambda: calls.append("read") or asked
        module.active = lambda: calls.append("active") or active
        module.write = lambda value: calls.append(("write", value)) or True
        module.subprocess = types.SimpleNamespace(
            run=lambda *a, **k: calls.append(("run", a)) or None)
        module.config_file = lambda: calls.append("config_file") or "/boot/firmware/config.txt"
        return module, calls

    def test_pico_mode_does_nothing(self):
        for wiring, kwargs in WIRINGS.items():
            for state in STATES:
                with self.subTest(wiring=wiring, state=state):
                    module, calls = self.instrument(self.patched_path, state)
                    logs = Logs()
                    self.assertIsNone(module.follow_wiring(fake_config("pico", **kwargs), logs))
                    self.assertEqual(calls, [])

    def test_pico_mode_from_craspberry(self):
        # Mode lu par craspberry (PICO), même si la configuration passée ne le dit pas.
        saved = sys.modules.get("include.craspberry")
        sys.modules["include.craspberry"] = types.SimpleNamespace(PICO=True)
        try:
            module, calls = self.instrument(self.patched_path, (True, True))
            self.assertIsNone(module.follow_wiring(fake_config(None, **WIRINGS["gpio2"]), Logs()))
            self.assertEqual(calls, [])
        finally:
            if saved is None:
                sys.modules.pop("include.craspberry", None)
            else:
                sys.modules["include.craspberry"] = saved

    def test_normal_mode_identical_to_original(self):
        for hardware in (None, "normal", "autre"):
            for wiring, kwargs in WIRINGS.items():
                for state in STATES:
                    with self.subTest(hardware=hardware, wiring=wiring, state=state):
                        results = []
                        for path in (self.original_path, self.patched_path):
                            module, calls = self.instrument(path, state)
                            logs = Logs()
                            result = module.follow_wiring(fake_config(hardware, **kwargs), logs)
                            results.append((result, calls, logs.lines))
                        self.assertEqual(results[1], results[0])

    def test_normal_mode_really_acts(self):
        # Garde-fou du test : le mode normal écrit bien config.txt dans ce cas.
        module, calls = self.instrument(self.patched_path, (True, True))
        self.assertEqual(module.follow_wiring(fake_config("normal", **WIRINGS["gpio2"]), Logs()),
                         ("off", True))
        self.assertIn(("write", False), calls)


if __name__ == "__main__":
    unittest.main()
