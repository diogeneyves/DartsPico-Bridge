# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Firmware de la carte Pico (C3) : MCP23017, puces absentes, boutons, Toys et voyants (§ 3.2, § 7.3)."""

import unittest

import _fw_paths  # noqa: F401
import machine
import proto as P
import board
import scanner
import xcore
from app import App
from buttons import Buttons
from heartbeat import Heartbeat
from leds import Leds
from mcp23017 import GPIO, GPINTEN, GPPU, IOCON, IODIR, OLAT, McpBus, output_writers
from outputs import Outputs
from test_fw_core0 import Clock, Hw, lines


class Rig(unittest.TestCase):
    def make(self, present=(0x20, 0x21, 0x22)):
        self.sim = machine.reset_sim()
        self.chips = {}
        for a in board.MCP_ADDRS:
            d = machine.FakeMCP23017(a)
            d.present = a in present
            self.sim.i2c_devices[a] = d
            self.chips[a] = d
        self.clk = Clock()
        self.out = []
        self.shared = xcore.Shared()
        self.queue = xcore.EventQueue(8)
        self.reset_pin = machine.Pin(board.MCP_RESET_PIN, machine.Pin.OUT, value=1)
        self.rst_levels = []
        orig = self.reset_pin.value

        def spy(v=None):
            if v is not None:
                self.rst_levels.append(v)
            return orig(v)
        self.reset_pin.value = spy
        i2c = machine.I2C(board.I2C_ID)
        app = App(self.clk, self.shared, self.queue, self.out.append, P.default_params(),
                  "0123456789ABCDEF", hw=Hw(), leds=Leds())
        self.bus = McpBus(i2c, self.clk, self.reset_pin, app.emit, app.mcp_changed,
                          sleep_ms=lambda n: self.clk.ms(n))
        toys, lights = output_writers(self.bus)
        app.outputs = Outputs(self.clk, toys, lights)
        app.mcp = self.bus
        self.btn15 = machine.Pin(board.BTN15_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
        self.int_pin = machine.Pin(board.MCP_INT_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
        app.buttons = Buttons(self.clk, self.bus, self.btn15, self.int_pin, app.emit)
        app.heartbeat = Heartbeat(self.clk, machine.Pin(board.HEARTBEAT_PIN, machine.Pin.OUT))
        self.app = app
        app.boot()
        return self.take()

    def take(self):
        r = lines(b"".join(self.out))
        del self.out[:]
        return r

    def send(self, text):
        self.app.feed(text.encode())
        return self.take()

    def run_ms(self, ms, step=1):
        for _ in range(ms // step):
            self.clk.ms(step)
            self.shared.life += 1
            self.app.feed(b"\n" if False else b"")
            self.app.last_rx = self.clk.ticks_ms()     # le Pi reste là
            self.app.step()
        return self.take()


class Boot(Rig):
    def test_all_present_configured_safely(self):
        out = self.make()
        self.assertEqual(out[2:], [])
        self.assertEqual(self.rst_levels[-2:], [0, 1])                # impulsion sur GP28
        for a, d in self.chips.items():
            regs = [w[0] for w in d.writes]
            self.assertLess(regs.index(OLAT), regs.index(IODIR), hex(a))  # sorties à 0 d'abord
            self.assertEqual(d.word(IODIR), board.IODIR[a])
            self.assertEqual(d.outputs(), 0)
            self.assertFalse(d.word(IODIR) & 0x8080)                  # GPA7, GPB7 jamais en entrée
        u3 = self.chips[0x20]
        self.assertEqual(u3.regs[IOCON] & 0x40, 0x40)                 # MIRROR
        self.assertEqual(u3.word(GPPU), 0x7F7F)
        self.assertEqual(u3.word(GPINTEN), 0x7F7F)
        self.assertFalse(self.app.heartbeat.in_error())

    def test_none_present_clean_errors(self):
        out = self.make(present=())
        self.assertEqual(out[:2], ["HELLO 1 darts-pico 0.2.0 0123456789ABCDEF", P.msg_caps()])
        self.assertEqual(out[2:], ["E MCP 0x20 absent", "E MCP 0x21 absent", "E MCP 0x22 absent"])
        self.assertTrue(self.app.heartbeat.in_error())
        self.assertEqual(self.send("@1 TOY 1 1\n@2 LGT 3 1\n@3 BTNPOLL\n"), ["K 1", "K 2", "BS 0000", "K 3"])
        self.assertEqual(self.run_ms(3000, step=10), [])              # pas de plantage, rien de répété
        for s in out:
            P.parse_event(s)

    def test_heartbeat_5hz_while_absent(self):
        self.make(present=(0x20, 0x22))
        hb = self.app.heartbeat
        flips, level = [], hb.level
        for _ in range(100):
            self.clk.ms(10)
            self.shared.life += 1
            self.app.last_rx = self.clk.ticks_ms()
            self.app.step()
            if hb.level != level:
                level = hb.level
                flips.append(self.clk.t // 1000)
        self.assertEqual({b - a for a, b in zip(flips, flips[1:])}, {100})


class Presence(Rig):
    def test_research_one_chip_per_second(self):
        self.make(present=())
        self.sim.i2c_accesses = []
        self.run_ms(999)
        self.assertEqual(self.sim.i2c_accesses, [])
        self.run_ms(1)
        self.assertEqual(set(self.sim.i2c_accesses), {0x20})          # une seule puce
        self.assertEqual(len(self.sim.i2c_accesses), 2)               # deux essais
        self.sim.i2c_accesses = []
        self.run_ms(1000)
        self.assertEqual(set(self.sim.i2c_accesses), {0x21})

    def test_chip_back(self):
        self.make(present=(0x20, 0x22))
        self.chips[0x21].present = True
        out = self.run_ms(3000, step=10)
        self.assertEqual(out, ["E MCP 0x21 back"])
        self.assertFalse(self.app.heartbeat.in_error())
        self.assertEqual(self.chips[0x21].word(IODIR), 0)

    def test_one_failure_is_retried(self):
        self.make()
        self.chips[0x21].fail_next = 1
        self.assertEqual(self.send("TOY 2 1\n"), [])
        self.assertEqual(self.run_ms(10), [])
        self.assertEqual(self.chips[0x21].outputs(), 0b10)

    def test_two_failures_mean_absent(self):
        self.make()
        self.chips[0x21].fail_next = 2
        self.send("TOY 2 1\n")
        self.assertEqual(self.run_ms(10), ["E MCP 0x21 absent"])
        self.assertTrue(self.app.heartbeat.in_error())

    def test_vanish_then_back_toys_zero_lights_restored(self):
        self.make()
        self.send("TOY 2 1\nLGT 12 1\n")
        self.run_ms(5)
        self.chips[0x21].present = False
        self.chips[0x22].present = False
        self.send("TOY 3 1\nLGT 1 1\n")
        self.assertEqual(self.run_ms(5), ["E MCP 0x21 absent", "E MCP 0x22 absent"])
        for a in (0x21, 0x22):                       # puces remises sous tension, à 0
            self.chips[a].power_on_reset()
            self.chips[a].present = True
        out = self.run_ms(2500, step=10)
        self.assertEqual(sorted(out), ["E MCP 0x21 back", "E MCP 0x22 back"])
        self.assertEqual(self.chips[0x21].outputs(), 0)               # Toys à 0 (D7)
        self.assertEqual(self.chips[0x22].outputs(), (1 << 11) | 1)   # voyants rétablis
        self.assertEqual(self.send("@1 TOY 4 1\n"), ["K 1"])
        self.run_ms(1)
        self.assertEqual(self.chips[0x21].outputs(), 0b1000)

    def test_mcpreset_zeroes_outputs(self):
        self.make()
        self.send("TOYS FF\nLGTS FFF\n")
        self.run_ms(2)
        self.assertEqual(self.chips[0x21].outputs(), 0xFF)
        self.rst_levels = []
        self.assertEqual(self.send("@5 MCPRESET\n"), ["K 5"])
        self.run_ms(2)
        self.assertEqual(self.rst_levels, [0, 1])
        self.assertEqual(self.chips[0x21].outputs(), 0)
        self.assertEqual(self.chips[0x22].outputs(), 0)

    def test_light_bits_mapping(self):
        self.make()
        self.send("LGT 9 1\nTOY 8 1\n")
        self.run_ms(1)
        self.assertEqual(self.chips[0x22].outputs(), 1 << 8)          # V9 = GPB0
        self.assertEqual(self.chips[0x21].outputs(), 1 << 7)          # T8 = GPA7


class ButtonsTest(Rig):
    def press(self, n, on):
        if n == 15:
            if on:
                self.sim.ext_in[board.BTN15_PIN] = 0
            else:
                self.sim.ext_in.pop(board.BTN15_PIN, None)
            return
        bit = 1 << board.BUTTON_BITS[n - 1]
        u3 = self.chips[0x20]
        u3.inputs = (u3.inputs & ~bit) if on else (u3.inputs | bit)

    def test_press_release_with_debounce(self):
        self.make()
        t = None
        self.press(8, True)                                         # BTN8 = GPB6
        self.sim.ext_in[board.MCP_INT_PIN] = 0                      # INTA active
        out = self.run_ms(5)
        self.assertEqual(out, [])
        t_first = (self.clk.t - 4000) % (1 << 30)
        out = self.run_ms(20)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].split()[:3], ["B", "8", "1"])
        self.assertLessEqual(abs(int(out[0].split()[3]) - t_first), 1000)
        self.assertEqual(self.send("BTNPOLL\n"), ["BS 0080"])
        self.sim.ext_in.pop(board.MCP_INT_PIN)
        self.press(8, False)
        self.assertEqual([s.split()[:3] for s in self.run_ms(30)], [["B", "8", "0"]])
        del t

    def test_bounce_shorter_than_debounce_ignored(self):
        self.make()
        self.press(1, True)
        self.run_ms(3)
        self.press(1, False)
        self.assertEqual(self.run_ms(30), [])

    def test_all_buttons(self):
        self.make()
        for n in range(1, 16):
            self.press(n, True)
            out = self.run_ms(20)
            self.assertEqual([s.split()[:3] for s in out], [["B", str(n), "1"]], n)
            self.press(n, False)
            self.run_ms(20)

    def test_btn15_works_without_u3(self):
        self.make(present=(0x21, 0x22))
        self.press(15, True)
        self.assertEqual([s.split()[:3] for s in self.run_ms(20)], [["B", "15", "1"]])
        self.assertEqual(self.send("BTNPOLL\n"), ["BS 4000"])

    def test_u3_lost_releases_pressed_buttons_once(self):
        self.make()
        self.press(3, True)
        self.press(15, True)
        self.run_ms(20)
        self.chips[0x20].present = False
        out = self.run_ms(30)
        self.assertEqual([s.split()[:3] for s in out[:2]], [["E", "MCP", "0x20"], ["B", "3", "0"]][:len(out[:2])])
        self.assertIn("E MCP 0x20 absent", out)
        self.assertEqual([s.split()[:3] for s in out if s.startswith("B")], [["B", "3", "0"]])
        self.assertEqual(self.send("BTNPOLL\n"), ["BS 4000"])

    def test_debounce_param(self):
        self.make()
        self.send("SET btn_debounce_ms 50\n")
        self.press(2, True)
        self.assertEqual(self.run_ms(40), [])
        self.assertEqual(len(self.run_ms(20)), 1)


class StuckHeartbeat(Rig):
    def test_stuck_contact_5hz_until_released(self):
        self.make()
        self.queue.push(xcore.EV_STUCK, 3, 4, 0, 500000)
        self.assertEqual(self.run_ms(1), ["E STUCK 3 4"])
        self.assertTrue(self.app.heartbeat.in_error())
        self.queue.push(xcore.EV_STUCK_CLEAR, 3, 4, 0, 0)
        self.assertEqual(self.run_ms(1), [])
        self.assertFalse(self.app.heartbeat.in_error())

    def test_contact_closed_at_start_stuck_5hz_no_dart(self):
        # § 7.2 point 3 et § 3.1.2 : E STUCK au premier balayage, 5 Hz, aucun D.
        self.make()
        self.clk.sleep_us = lambda n: setattr(self.clk, "t", self.clk.t + n)
        scanner.setup_pins(False)
        matrix = scanner.Matrix(self.queue, self.clk)
        contacts = {4: 1 << 3}
        self.sim.matrix = lambda col: contacts.get(col, 0)

        def run(ms):
            out, flips, level = [], [], self.app.heartbeat.level
            for _ in range(ms):
                matrix.scan()
                out += self.run_ms(1)
                if self.app.heartbeat.level != level:
                    level = self.app.heartbeat.level
                    flips.append(self.clk.t)
            return out, flips

        out, flips = run(1000)
        self.assertEqual(out, ["E STUCK 3 4"])
        self.assertTrue(self.app.heartbeat.in_error())
        gaps = {round((b - a) / 100_000) for a, b in zip(flips, flips[1:])}
        self.assertEqual(gaps, {1})                                  # 5 Hz : 100 ms / 100 ms
        contacts.clear()
        out, _ = run(30)
        self.assertEqual(out, [])                                    # ni D ni X à l'ouverture
        self.assertFalse(self.app.heartbeat.in_error())
        out, flips = run(1500)
        self.assertEqual({round((b - a) / 100_000) for a, b in zip(flips, flips[1:])}, {5})  # 1 Hz
        contacts[4] = 1 << 3                                         # contact suivant : normal
        out, _ = run(5)
        self.assertEqual([s.split()[:3] for s in out], [["D", "3", "4"]])

    def test_cfg_overflow_link_loss_stay_1hz(self):
        self.make()
        for i in range(12):
            self.queue.push(xcore.EV_SHOCK, 1, 0, i, 0)
        self.app.step()
        self.clk.ms(4000)
        self.app.step()                                              # E WDG timeout
        out = self.take()
        self.assertIn("E WDG timeout", out)
        self.assertTrue(any(s.startswith("E OVERFLOW") for s in out))
        self.assertFalse(self.app.heartbeat.in_error())


if __name__ == "__main__":
    unittest.main()
