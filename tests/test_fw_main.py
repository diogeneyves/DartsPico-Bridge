# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""main.py de la carte Pico (§ 5.1) : GP16 éteint, issue GP18, fenêtre Ctrl-C de 2 s, puis app.run()."""

import ast
import json
import sys
import unittest

import _fw_paths  # noqa: F401
import _paths  # noqa: F401
import machine
import micropython
import board
import main as main_mod


class Time:
    def __init__(self, log, interrupt_at=None):
        self.log = log
        self.interrupt_at = interrupt_at

    def sleep_ms(self, n):
        self.log.append(("sleep_ms", n))
        if n == self.interrupt_at:
            raise KeyboardInterrupt


class Base(unittest.TestCase):
    def setUp(self):
        self.sim = machine.reset_sim()
        micropython.kbd_intr_value = 3
        self.log = []
        self.out = []

    def start(self, gp18=None, interrupt_at=None):
        if gp18 is not None:
            self.sim.ext_in[18] = gp18
        oe, out = self.sim.oe, self.sim.out
        hb = 1 << main_mod.HEARTBEAT_PIN
        try:
            return main_mod.start(machine=machine, micropython=micropython,
                                  time=Time(self.log, interrupt_at), write=self.out.append,
                                  run=self.run_firmware)
        finally:
            # seule sortie touchée : GP16, au niveau haut (voyant éteint)
            self.assertEqual((self.sim.oe, self.sim.out), (oe | hb, out | hb))

    def run_firmware(self):
        self.log.append("run")


class Heartbeat(Base):
    def test_gp16_off_before_anything_else(self):
        seen = []
        real_sleep = Time.sleep_ms

        def sleep_ms(t, n):
            seen.append((self.sim.oe >> 16 & 1, self.sim.out >> 16 & 1))
            real_sleep(t, n)
        Time.sleep_ms = sleep_ms
        try:
            self.start()
        finally:
            Time.sleep_ms = real_sleep
        self.assertEqual(seen[0], (1, 1))           # GP16 éteint avant même la lecture de GP18
        self.assertEqual(self.sim.pins[16].mode, machine.Pin.OUT)

    def test_gp16_off_when_gp18_grounded(self):
        self.assertFalse(self.start(gp18=0))
        self.assertEqual(self.sim.oe >> 16 & 1, 1)
        self.assertEqual(self.sim.out >> 16 & 1, 1)

    def test_first_call_of_start_is_gp16(self):
        tree = ast.parse((_fw_paths.FW / "main.py").read_text(encoding="utf-8"))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "start")
        body = [n for n in fn.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
        self.assertEqual(ast.unparse(body[0]), "machine.Pin(HEARTBEAT_PIN, machine.Pin.OUT, value=1)")


class Gp18Grounded(Base):
    def test_log_line_then_stop_without_firmware(self):
        self.assertFalse(self.start(gp18=0))
        self.assertEqual(self.out, ["# main: GP18 grounded, firmware not started\n"])
        self.assertNotIn("run", self.log)
        self.assertNotIn(("sleep_ms", main_mod.WINDOW_MS), self.log)

    def test_read_with_internal_pull_up(self):
        self.start(gp18=0)
        self.assertTrue(self.sim.pad(18) & machine.PAD_PUE)
        self.assertEqual(self.sim.pins[18].mode, machine.Pin.IN)


class NormalStart(Base):
    def test_log_line_window_then_run(self):
        self.assertTrue(self.start())                  # GP18 ouvert : tirage vers le haut
        self.assertEqual(self.out, ["# main: Ctrl-C enabled for 2 s\n"])
        self.assertEqual(self.log, [("sleep_ms", main_mod.SETTLE_MS),
                                    ("sleep_ms", 2000), "run"])
        self.assertEqual(micropython.kbd_intr_value, 3)

    def test_gp18_high_from_outside(self):
        self.assertTrue(self.start(gp18=1))
        self.assertIn("run", self.log)

    def test_ctrl_c_during_window_stops_before_run(self):
        with self.assertRaises(KeyboardInterrupt):
            self.start(interrupt_at=main_mod.WINDOW_MS)
        self.assertNotIn("run", self.log)

    def test_default_runner_is_app_run(self):
        calls = []
        fake = type(sys)("app")
        fake.run = lambda: calls.append(1)
        saved = sys.modules.get("app")
        sys.modules["app"] = fake
        try:
            main_mod.start(machine=machine, micropython=micropython, time=Time([]),
                           write=lambda s: None)
        finally:
            if saved is None:
                del sys.modules["app"]
            else:
                sys.modules["app"] = saved
        self.assertEqual(calls, [1])


class Source(unittest.TestCase):
    def test_pin_matches_board_and_pinout(self):
        self.assertEqual(main_mod.BTN15_PIN, board.BTN15_PIN)
        self.assertEqual(main_mod.HEARTBEAT_PIN, board.HEARTBEAT_PIN)
        pinout = json.loads((_fw_paths.ROOT / "hardware" / "pinout.json").read_text(encoding="utf-8"))
        self.assertEqual(pinout["pico"]["pins"]["GP%d" % main_mod.BTN15_PIN]["net"], "BTN15")
        self.assertEqual(pinout["pico"]["pins"]["GP%d" % main_mod.HEARTBEAT_PIN]["net"], "LED_VIE")

    def test_no_firmware_import_before_run(self):
        tree = ast.parse((_fw_paths.FW / "main.py").read_text(encoding="utf-8"))
        top = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top.update(a.name for a in node.names)
            self.assertNotIsInstance(node, ast.ImportFrom)
        self.assertEqual(top, {"machine", "micropython", "sys", "time"})
        inner = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) and n not in tree.body
                 for a in n.names]
        self.assertEqual(inner, ["app"])


if __name__ == "__main__":
    unittest.main()
