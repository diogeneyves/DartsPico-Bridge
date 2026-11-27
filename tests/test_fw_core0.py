# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Firmware de la carte Pico, cœur 0 (C1) : protocole, paramètres, chiens de garde (§ 5, § 7.1)."""

import unittest

import _fw_paths  # noqa: F401
import _paths  # noqa: F401
import machine
import proto as P
import xcore
from app import App, HW_WDT_MS
from fake_pico import FakePico
from leds import Leds
from outputs import Outputs


class Clock:
    """Horloge simulée en µs ; ticks_ms et ticks_us sur 30 bits comme MicroPython."""
    PERIOD = 1 << 30

    def __init__(self, t=1000):
        self.t = t

    def __call__(self):                      # horloge du faux Pico
        return self.t

    def ticks_us(self):
        return self.t % self.PERIOD

    def ticks_ms(self):
        return (self.t // 1000) % self.PERIOD

    def ticks_diff(self, a, b):
        d = (a - b) % self.PERIOD
        return d - self.PERIOD if d >= self.PERIOD // 2 else d

    def ms(self, n):
        self.t += n * 1000


class Hw:
    def __init__(self):
        self.pullups = []
        self.wdts = []
        self.saved = []
        self.resets = 0

    def set_row_pullups(self, on):
        self.pullups.append(on)

    def start_wdt(self, timeout_ms):
        self.wdts.append(machine.WDT(timeout=timeout_ms))
        return self.wdts[-1]

    def save_params(self, params):
        self.saved.append(dict(params))

    def reset(self):
        self.resets += 1


def lines(data):
    return [s for s in data.decode().split("\n") if s]


class Base(unittest.TestCase):
    def setUp(self, params=None, cfg_error=None):
        machine.reset_sim()
        self.clk = Clock()
        self.out = []
        self.shared = xcore.Shared()
        self.queue = xcore.EventQueue(8)
        self.hw = Hw()
        self.outputs = Outputs(self.clk)
        self.leds = Leds()
        self.app = App(self.clk, self.shared, self.queue, self.out.append,
                       params or P.default_params(), "0123456789ABCDEF", hw=self.hw,
                       outputs=self.outputs, leds=self.leds, cfg_error=cfg_error)
        self.app.boot()
        self.boot = self.take()

    def take(self):
        r = lines(b"".join(self.out))
        del self.out[:]
        return r

    def send(self, text):
        self.app.feed(text.encode())
        return self.take()

    def step(self):
        self.app.step()
        return self.take()


class Boot(Base):
    def test_hello_caps_outputs_zero(self):
        self.assertEqual(self.boot[0], "HELLO 1 darts-pico 0.2.0 0123456789ABCDEF")
        self.assertEqual(self.boot[1], P.msg_caps())
        self.assertEqual(len(self.boot), 2)
        self.assertTrue(self.outputs.is_zero())
        self.assertTrue(self.leds.is_black())
        self.assertEqual(self.hw.pullups, [False])
        self.assertEqual(self.shared.get_params()[0], 1)

    def test_cfg_error_reported(self):
        self.setUp(cfg_error="params file invalid, defaults loaded")
        self.assertEqual(self.boot[2], "E CFG params file invalid, defaults loaded")

    def test_bench_pullup_from_saved_params(self):
        p = P.default_params()
        p["bench_row_pullup"] = 1
        self.setUp(params=p)
        self.assertEqual(self.hw.pullups, [True])


class SameAsFakePico(Base):
    """Le firmware répond comme le faux Pico, référence des tests de hwd."""

    SCRIPT = [
        "PING", "@4 PING", "TOY 1 1", "@5 TOY 1 0", "TOY 9 1", "@7 TOY 9 1", "@x PING",
        "FOO", "@8 GET nope", "X" * 3000, "GET watchdog_ms", "SET watchdog_ms 2000",
        "@9 GET watchdog_ms", "SET shock_mode ADC", "TOYS 1FF", "LGTS FFF", "LGTS 1000",
        "@10 LEDCFG 0 2", "LEDF 0 FF0000", "@19 LEDF 0 FF0000", "@11 LEDF 0 FF000000FF00", "@12 LEDF 3 00",
        "LEDB 0 128", "LEDOFF 0", "@13 CAL 1", "CAL 2", "@14 BTNPOLL", "@15 DEFAULTS",
        "GET watchdog_ms", "TOYPULSE 2 100 100 3", "@16 TOYSTOP 2", "LGTPULSE 12 1 1 0",
        "@17 LGTSTOP 12", "@18 MCPRESET", " PING", "PING ", "PING  1", "SET hw_watchdog 2",
        "@65535 PING", "@65536 PING", "GET led0_order", "SET led1_order BGR",
    ]

    def test_same_replies(self):
        fake = FakePico(clock=self.clk)
        fake.boot()
        for line in self.SCRIPT:
            data = (line + "\n").encode()
            want = lines(fake.feed(data))
            self.assertEqual(self.send(line + "\n"), want, line)

    def test_who_same_except_version(self):
        fake = FakePico(clock=self.clk, uid="0123456789ABCDEF")
        fake.boot()
        a = self.send("@1 WHO\n")
        b = lines(fake.feed(b"@1 WHO\n"))
        self.assertEqual(a[1:], b[1:])
        self.assertEqual(a[0].split()[:3], b[0].split()[:3])


class Params(Base):
    def test_set_republishes_core1_params(self):
        v = self.shared.get_params()[0]
        self.send("SET min_contact_us 500\nCAL 1\n")
        v2, p = self.shared.get_params()
        self.assertEqual(v2, v + 2)
        self.assertEqual((p[0], p[3]), (500, 1))

    def test_refused_set_changes_nothing(self):
        v = self.shared.get_params()[0]
        self.assertEqual(self.send("SET release_ms 5000\n"), ["N - RANGE"])
        self.assertEqual(self.shared.get_params()[0], v)
        self.assertEqual(self.app.params["release_ms"], 20)

    def test_bench_row_pullup(self):
        self.send("SET bench_row_pullup 1\nSET bench_row_pullup 1\n")
        self.assertEqual(self.hw.pullups, [False, True])
        self.send("DEFAULTS\n")
        self.assertEqual(self.hw.pullups, [False, True, False])

    def test_save(self):
        self.send("SET release_ms 30\nSAVE\n")
        self.assertEqual(self.hw.saved[-1]["release_ms"], 30)

    def test_led_params_configure_leds(self):
        self.send("SET led2_count 3\nSET led2_order RGB\n")
        self.assertEqual((self.leds.count[2], self.leds.order[2]), (3, "RGB"))
        self.assertEqual(self.send("LEDF 2 010203040506070809\n"), [])
        self.assertEqual(bytes(self.leds.wire(2)), bytes(range(1, 10)))


class HardwareWatchdog(Base):
    def test_not_started_at_boot_even_if_param_is_1(self):
        self.assertEqual(self.app.params["hw_watchdog"], 1)
        for _ in range(5):
            self.step()
        self.assertEqual(self.hw.wdts, [])

    def test_started_once_by_set(self):
        self.send("SET hw_watchdog 1\nSET hw_watchdog 1\n")
        self.assertEqual(len(self.hw.wdts), 1)
        self.assertEqual(self.hw.wdts[0].timeout, HW_WDT_MS)
        self.send("SET hw_watchdog 0\n")                # accepté, mais ne l'arrête pas
        self.assertEqual(self.app.params["hw_watchdog"], 0)
        self.assertIsNotNone(self.app.wdt)

    def test_never_started_with_0_or_defaults(self):
        self.send("SET hw_watchdog 0\nDEFAULTS\n")
        self.assertEqual(self.hw.wdts, [])

    def test_fed_only_when_core1_life_moves(self):
        self.send("SET hw_watchdog 1\n")
        wdt = self.hw.wdts[0]
        self.step()
        self.assertEqual(wdt.feeds, 0)                  # cœur 1 immobile
        self.shared.life += 1
        self.step()
        self.assertEqual(wdt.feeds, 1)
        self.step()
        self.step()
        self.assertEqual(wdt.feeds, 1)                  # bloqué : plus nourri
        self.shared.life += 3
        self.step()
        self.assertEqual(wdt.feeds, 2)

    def test_hw_watchdog_value_range(self):
        self.assertEqual(self.send("SET hw_watchdog 2\n"), ["N - RANGE"])
        self.assertEqual(self.hw.wdts, [])


class LinkWatchdog(Base):
    def test_timeout_cuts_outputs_then_resumes(self):
        self.send("TOY 3 1\nLGTPULSE 1 10 10 0\nLEDCFG 0 1\nLEDF 0 FFFFFF\n")
        self.step()
        self.clk.ms(2999)
        self.assertEqual(self.step(), [])
        self.clk.ms(1)
        self.assertEqual(self.step(), ["E WDG timeout"])
        self.assertTrue(self.outputs.is_zero())
        self.assertTrue(self.leds.is_black())
        self.clk.ms(10000)
        self.assertEqual(self.step(), [])               # une seule fois
        self.assertEqual(self.app.feed(b"PI") or self.take(), [])   # ligne incomplète
        self.assertEqual(self.send("NG\n"), ["E WDG resumed", "PONG %d" % self.clk.ticks_us()])

    def test_bytes_keep_link_alive(self):
        for _ in range(10):
            self.clk.ms(1000)
            self.send("PING\n")
            self.assertEqual(self.step(), [])


class Events(Base):
    def test_events_formatted(self):
        q = self.queue
        q.push(xcore.EV_DART, 3, 4, 123, 800)
        q.push(xcore.EV_REJECT, 0, 6, 0, 200)
        q.push(xcore.EV_SHOCK, 1, 0, 456, 0)
        q.push(xcore.EV_SHOCK_STUCK, 4095, 0, 7, 0)
        q.push(xcore.EV_SHOCK_CLEAR, 0, 0, 8, 0)
        q.push(xcore.EV_STUCK, 11, 6, 9, 500000)
        self.assertEqual(self.step(), ["D 3 4 123 800", "X 0 6 200", "S 456 1",
                                       "E SHOCK active at start", "E STUCK 11 6"])

    def test_overflow_reported(self):
        for i in range(11):
            self.queue.push(xcore.EV_SHOCK, 1, 0, i, 0)
        out = self.step()
        self.assertEqual(out[:8], ["S %d 1" % i for i in range(8)])
        self.assertEqual(out[8], "E OVERFLOW 3 events lost")

    def test_every_line_is_valid(self):
        out = self.boot + self.send("WHO\n@1 PING\nBTNPOLL\nGET hw_watchdog\nNOPE\n")
        for s in out:
            P.parse_event(s)


class Reboot(Base):
    def test_ack_before_reset(self):
        self.app.feed(b"@3 REBOOT\nPING\n")
        self.assertEqual(lines(b"".join(self.out)), ["K 3"])
        self.assertEqual(self.hw.resets, 1)


class OutputsLogic(Base):
    def test_pulse_ends_at_zero_and_writes_only_changes(self):
        writes = []
        o = Outputs(self.clk, write_toys=writes.append)
        o.pulse(0, 2, 100, 100, 2)
        o.update()
        self.assertEqual(writes, [0b10])
        self.clk.ms(50)
        o.update()
        self.assertEqual(writes, [0b10])
        self.clk.ms(60)
        o.update()
        self.clk.ms(100)
        o.update()
        self.clk.ms(100)
        o.update()
        self.assertEqual(writes, [0b10, 0, 0b10, 0])
        self.assertFalse(o.is_zero())                   # 2e période pas finie
        self.clk.ms(100)
        o.update()
        self.assertTrue(o.is_zero())
        self.assertEqual(writes, [0b10, 0, 0b10, 0])

    def test_long_continuous_pulse_no_drift(self):
        o = Outputs(self.clk)
        o.pulse(1, 12, 500, 500, 0)
        for _ in range(3):
            self.clk.ms(400 * 1000 * 1000)              # plusieurs jours en tout
            self.clk.ms(250)
            o.update()
            self.assertEqual(o.state[1] >> 11 & 1, 1)
            self.clk.ms(500)
            o.update()
            self.assertEqual(o.state[1] >> 11 & 1, 0)
            self.clk.ms(250)


class HeartbeatTest(Base):
    def setUp(self):
        super().setUp()
        from heartbeat import Heartbeat
        self.pin = machine.Pin(16, machine.Pin.OUT)
        machine.sim.out &= ~(1 << 16)                  # niveau bas avant : la classe l'éteint
        self.hb = Heartbeat(self.clk, self.pin)
        self.app.heartbeat = self.hb

    def run_ms(self, total, step_ms=10, life=True):
        """Tours de boucle ; renvoie les instants (ms) des basculements."""
        flips = []
        level = self.pin.value()
        for _ in range(total // step_ms):
            self.clk.ms(step_ms)
            if life:
                self.shared.life += 1
            self.app.step()
            if self.pin.value() != level:
                level = self.pin.value()
                flips.append(self.clk.t // 1000)
        self.take()
        return flips

    def test_off_at_start(self):
        self.assertEqual(self.pin.value(), 1)
        self.assertFalse(self.hb.lit)

    def test_1hz_normal(self):
        flips = self.run_ms(3000)
        gaps = {b - a for a, b in zip(flips, flips[1:])}
        self.assertEqual(gaps, {500})

    def test_5hz_during_shock_error_then_1hz(self):
        self.queue.push(xcore.EV_SHOCK_STUCK, 4095, 0, 0, 0)
        self.assertEqual(self.step(), ["E SHOCK active at start"])
        flips = self.run_ms(1000)
        self.assertEqual({b - a for a, b in zip(flips, flips[1:])}, {100})
        self.queue.push(xcore.EV_SHOCK_CLEAR, 0, 0, 0, 0)
        self.assertEqual(self.step(), [])
        flips = self.run_ms(3000)
        self.assertEqual({b - a for a, b in zip(flips[1:], flips[2:])}, {500})

    def test_frozen_when_core1_stops(self):
        self.run_ms(1000)
        self.assertEqual(self.run_ms(5000, life=False), [])

    def test_frozen_when_core0_stops(self):
        level = self.pin.value()
        self.clk.ms(5000)                              # aucun tour de boucle
        self.assertEqual(self.pin.value(), level)

    def test_no_catch_up_after_delay(self):
        self.clk.ms(2000)
        self.shared.life += 1
        self.app.step()                                 # un seul basculement
        flips = self.run_ms(600)
        self.assertEqual(len(flips), 1)

    def test_not_cut_by_link_watchdog(self):
        flips = self.run_ms(5000)                       # le chien de garde de liaison part à 3 s
        self.assertTrue(self.app.wdg_tripped)
        self.assertGreaterEqual(len(flips), 9)


if __name__ == "__main__":
    unittest.main()
