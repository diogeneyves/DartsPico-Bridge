# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Firmware de la carte Pico : point d'entrée app.build/loop, mode banc (§ 3.1.2, § 5.1, § 5.4)."""

import os
import tempfile
import unittest

import _fw_paths  # noqa: F401
import _paths  # noqa: F401
import machine
import micropython
import pico_proto
import app as app_mod
import board
import pads
from test_fw_core0 import Clock as _Clock


class Clock(_Clock):
    def sleep_ms(self, n):
        self.ms(n)

    def sleep_us(self, n):
        self.t += n


def lines(out):
    return [s for s in b"".join(out).decode().split("\n") if s]


class Base(unittest.TestCase):
    bench = True
    chips = board.MCP_ADDRS

    def setUp(self):
        self.sim = machine.reset_sim()
        for a in self.chips:
            self.sim.i2c_devices[a] = machine.FakeMCP23017(a)
        micropython.kbd_intr_value = 3
        self.cwd = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)                          # params.json absent
        self.clk = Clock()
        self.out = []
        self.app, self.c1 = app_mod.build(self.bench, self.clk, machine, micropython,
                                          self.out.append)
        self.app.boot()

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def tours(self, ms, step_ms=10, tour_us=0):
        for _ in range(ms // step_ms):
            self.clk.ms(step_ms)
            self.c1.step()                               # le compteur de vie avance
            self.app.step()
            self.app.note_tour(tour_us)

    def hb_lines(self):
        return [s for s in lines(self.out) if s.startswith("# HB")]


class BenchMode(Base):
    def test_ctrl_c_left_active_and_bench_params(self):
        self.assertEqual(micropython.kbd_intr_value, 3)   # kbd_intr(-1) jamais appelé
        self.assertEqual(self.app.params["hw_watchdog"], 0)
        self.assertEqual(self.app.params["bench_row_pullup"], 1)
        for n in board.ROW_PINS:
            self.assertTrue(self.sim.pad(n) & machine.PAD_PUE)
        self.assertTrue(self.app.bench)

    def test_boot_messages_and_heartbeat_off(self):
        got = lines(self.out)
        self.assertTrue(got[0].startswith("HELLO 1 "))
        self.assertTrue(got[0].endswith(" 0123456789ABCDEF"))
        self.assertTrue(got[1].startswith("CAPS "))
        self.assertEqual(self.sim.out >> board.HEARTBEAT_PIN & 1, 1)   # éteint
        self.assertTrue(self.sim.oe >> board.HEARTBEAT_PIN & 1)
        self.assertFalse(pads.input_enabled(board.SHOCK_PIN))

    def test_hb_log_real_level_and_max_tour(self):
        self.tours(500, tour_us=123)
        self.app.note_tour(4567)
        self.tours(10, tour_us=0)                        # pas de bascule
        self.tours(490, tour_us=7)
        hb = self.hb_lines()
        self.assertEqual(len(hb), 2)
        self.assertEqual(hb[0], "# HB 0 123")             # allumé (niveau bas), 1er tour max
        self.assertEqual(hb[1], "# HB 1 4567")            # max depuis la ligne précédente
        self.assertEqual(pico_proto.parse_event(hb[0]), ("#", "HB 0 123"))

    def test_hb_log_reads_sio_register(self):
        reads = []
        orig = self.app.hw.read_heartbeat
        self.app.hw.read_heartbeat = lambda: reads.append(1) or orig()
        self.tours(500)
        self.assertEqual(len(reads), 1)
        self.assertEqual(orig(), self.sim.gpio_in() >> 16 & 1)

    def test_hw_watchdog_only_from_set(self):
        self.tours(100)
        self.assertIsNone(self.sim.wdt)
        self.app.feed(b"@1 SET hw_watchdog 1\n")
        self.assertIsNotNone(self.sim.wdt)
        self.assertEqual(self.sim.wdt.timeout, app_mod.HW_WDT_MS)
        self.assertIn("K 1", lines(self.out))

    def test_reboot_acks_then_waits_before_reset(self):
        events = []
        self.app.write = lambda d: events.append(("out", d))
        self.clk.sleep_ms = lambda n: events.append(("sleep", n))
        self.app.hw.sleep_ms = self.clk.sleep_ms
        with self.assertRaises(SystemExit):                 # machine.reset() simulé
            self.app.feed(b"@9 REBOOT\n")
        self.assertEqual(events[-2], ("out", b"K 9\n"))
        self.assertEqual(events[-1], ("sleep", app_mod.REBOOT_DELAY_MS))
        self.assertEqual(self.sim.resets, 1)

    def test_loop_stops_on_ctrl_c_and_shutdown_zeroes(self):
        self.app.outputs.set(0, 1, 1)
        self.app.outputs.set(1, 2, 1)
        feeds = [b"@2 PING\n", None]

        def read_input():
            if not feeds:
                raise KeyboardInterrupt
            return feeds.pop(0)
        with self.assertRaises(KeyboardInterrupt):
            try:
                app_mod.loop(self.app, read_input, self.clk)
            finally:
                self.app.shutdown()
        self.assertIn("K 2", lines(self.out))
        self.assertFalse(self.app.shared.running)
        self.assertTrue(self.app.outputs.is_zero())
        self.assertTrue(self.app.leds.is_black())


class BenchWithoutMcp(Base):
    chips = ()

    def test_5hz_and_hb_log_when_mcp_absent(self):
        self.assertIn("E MCP 0x20 absent", lines(self.out))
        self.tours(1000)
        self.assertEqual(len(self.hb_lines()), 10)       # 100 ms / 100 ms (§ 3.1.2)


class NormalMode(Base):
    bench = False

    def test_ctrl_c_disabled_and_no_hb_log(self):
        self.assertEqual(micropython.kbd_intr_value, -1)
        self.assertEqual(self.app.params["hw_watchdog"], 1)
        self.assertEqual(self.app.params["bench_row_pullup"], 0)
        self.tours(2000)
        self.assertEqual(self.hb_lines(), [])
        self.assertIsNone(self.sim.wdt)                  # jamais au démarrage seul (§ 5.4)

    def test_bench_py_calls_run_bench(self):
        src = (_fw_paths.FW / "bench.py").read_text(encoding="utf-8")
        code = [s for s in src.splitlines() if s and not s.startswith("#") and '"' not in s]
        self.assertEqual(code[-1], "app.run(bench=True)")


class MicroPythonSource(unittest.TestCase):
    def test_no_exception_init_call(self):
        # MicroPython v1.29 : Exception.__init__ n'existe pas (constaté au banc, M3b-1).
        for f in sorted(_fw_paths.FW.glob("*.py")):
            with self.subTest(f=f.name):
                self.assertNotIn("Exception.__init__", f.read_text(encoding="utf-8"))


class FakeStdin:
    """stdin de MicroPython : readinto(b) attend len(b) octets (ici : erreur s'il en manque)."""

    def __init__(self):
        self.pending = bytearray()
        self.buffer = self
        self.reads = 0

    def readinto(self, b):
        n = len(b)
        if n > len(self.pending):
            raise AssertionError("readinto bloquerait : %d demandés, %d en attente" % (n, len(self.pending)))
        b[:] = self.pending[:n]
        del self.pending[:n]
        self.reads += 1
        return n


class FakeSelect:
    POLLIN = 1

    def __init__(self, stdin):
        self.stdin = stdin

    def poll(self):
        stdin = self.stdin

        class Poll:
            def register(self, obj, mask):
                assert obj is stdin

            def ipoll(self, timeout):
                assert timeout == 0
                return iter([(stdin, 1)] if stdin.pending else [])
        return Poll()


class StdinReader(unittest.TestCase):
    def setUp(self):
        self.stdin = FakeStdin()
        sys_ = type("sys", (), {"stdin": self.stdin})
        self.read = app_mod.stdin_reader(sys_, FakeSelect(self.stdin))

    def test_nothing_pending(self):
        self.assertIsNone(self.read())

    def test_reads_only_what_is_pending_one_byte_at_a_time(self):
        self.stdin.pending += b"PING\n"
        self.assertEqual(bytes(self.read()), b"PING\n")
        self.assertEqual(self.stdin.reads, 5)
        self.assertIsNone(self.read())

    def test_at_most_read_max_per_call_and_result_is_a_copy(self):
        data = bytes(range(32, 127)) * 12
        self.stdin.pending += data
        a = self.read()
        self.assertEqual(len(a), app_mod.READ_MAX)
        b = self.read()
        self.assertEqual(bytes(a) + bytes(b), data[:2 * app_mod.READ_MAX])
        self.assertEqual(bytes(a), data[:app_mod.READ_MAX])     # pas écrasé par le 2e appel

    def test_splitter_accepts_result_one_line_per_call(self):
        self.stdin.pending += b"@1 PING\nLEDOFF 0\n"
        sp = pico_proto.LineSplitter()
        self.assertEqual(sp.feed(self.read()), ["@1 PING"])
        self.assertEqual(sp.feed(self.read()), ["LEDOFF 0"])


class LedfBlockRead(unittest.TestCase):
    """En-tête de LEDF octet par octet, puis le reste d'un seul readinto (M3b-3)."""

    def setUp(self):
        self.stdin = FakeStdin()
        self.counts = [2, 1, 0]
        sys_ = type("sys", (), {"stdin": self.stdin})
        ledf_len = lambda ch: 6 * self.counts[ch] + 1 if self.counts[ch] else 0  # noqa: E731
        self.read = app_mod.stdin_reader(sys_, FakeSelect(self.stdin), ledf_len)
        self.sp = pico_proto.LineSplitter()

    def lines(self):
        out = []
        while True:
            d = self.read()
            if d is None:
                return out
            out += self.sp.feed(d)

    def test_header_then_one_exact_read(self):
        line = b"LEDF 0 FF0000a0b0c0\n"
        self.stdin.pending += line
        self.assertEqual(bytes(self.read()), line)
        self.assertEqual(self.stdin.reads, 7 + 1)

    def test_tagged_header(self):
        line = b"@65535 LEDF 1 010203\n"
        self.stdin.pending += line + b"PING\n"
        self.assertEqual(bytes(self.read()), line)
        self.assertEqual(self.stdin.reads, 14 + 1)
        self.assertEqual(bytes(self.read()), b"PING\n")

    def test_crlf_and_following_lines(self):
        self.stdin.pending += b"LEDF 1 010203\r\nLEDF 1 040506\n"
        self.assertEqual(self.lines(), ["LEDF 1 010203", "LEDF 1 040506"])

    def test_no_block_read_without_leds_or_off_header(self):
        self.stdin.pending += b"LEDF 2 \nXLEDF 1 010203\nLEDF 3 01\n@ LEDF 1 0\n"
        self.assertEqual(self.lines(), ["LEDF 2 ", "XLEDF 1 010203", "LEDF 3 01", "@ LEDF 1 0"])
        self.assertEqual(self.stdin.reads, len(b"LEDF 2 \nXLEDF 1 010203\nLEDF 3 01\n@ LEDF 1 0\n"))

    def test_short_line_resyncs_on_newline(self):
        # Ligne trop courte : le bloc mange le début des lignes suivantes, le découpage reste juste.
        self.stdin.pending += b"LEDF 0 FF0000\nPING\nPING\n"
        lines = self.lines()
        self.assertEqual(lines, ["LEDF 0 FF0000", "PING", "PING"])
        self.assertEqual(pico_proto.parse_command("LEDF 0 FF0000")[2][1], b"\xff\x00\x00")  # refusé ensuite (ARGS)

    def test_long_line_continues_bytewise(self):
        self.stdin.pending += b"LEDF 1 01020304\nPING\n"
        self.assertEqual(self.lines(), ["LEDF 1 01020304", "PING"])

    def test_count_read_after_previous_line(self):
        self.stdin.pending += b"LEDCFG 2 1\n"
        self.assertEqual(bytes(self.read()), b"LEDCFG 2 1\n")
        self.counts[2] = 1                       # exécuté par App avant la lecture suivante
        self.stdin.pending += b"LEDF 2 0A0B0C\n"
        self.assertEqual(bytes(self.read()), b"LEDF 2 0A0B0C\n")
        self.assertEqual(self.stdin.reads, 11 + 7 + 1)

    def test_header_channel(self):
        f = app_mod.ledf_header_channel
        for h, c in ((b"LEDF 0 ", 0), (b"LEDF 2 ", 2), (b"@7 LEDF 1 ", 1), (b"@65535 LEDF 2 ", 2),
                     (b"LEDF 3 ", -1), (b"LEDB 0 ", -1), (b"@ LEDF 0 ", -1), (b"@1x LEDF 0 ", -1),
                     (b"LEDF 00 ", -1), (b" LEDF 0 ", -1)):
            with self.subTest(h=h):
                self.assertEqual(f(bytearray(h), len(h)), c)

    def test_app_ledf_len(self):
        app = app_mod.App.__new__(app_mod.App)
        app.params = {"led0_count": 204, "led1_count": 0}
        app.wdt = object()
        self.assertEqual(app.ledf_len(0), 6 * 204 + 1)
        self.assertEqual(app.ledf_len(1), 0)

    def test_app_ledf_len_zero_without_hw_watchdog(self):
        app = app_mod.App.__new__(app_mod.App)
        app.params = {"led0_count": 204}
        app.wdt = None
        self.assertEqual(app.ledf_len(0), 0)


class LedfReadDependsOnHwWatchdog(Base):
    """§ 5.4 (M3b-4) : LEDF d'un seul readinto seulement après SET hw_watchdog 1."""

    def setUp(self):
        super().setUp()
        self.stdin = FakeStdin()
        sys_ = type("sys", (), {"stdin": self.stdin})
        self.read = app_mod.stdin_reader(sys_, FakeSelect(self.stdin), self.app.ledf_len)
        self.app.feed(b"LEDCFG 0 2\n")
        self.line = b"LEDF 0 FF0000A0B0C0\n"

    def test_bytewise_while_hw_watchdog_not_started(self):
        self.assertIsNone(self.app.wdt)
        self.stdin.pending += self.line[:10]                  # ligne coupée : rien ne bloque
        self.assertEqual(bytes(self.read()), self.line[:10])
        self.assertIsNone(self.read())
        self.stdin.pending += self.line[10:]
        self.assertEqual(bytes(self.read()), self.line[10:])
        self.assertEqual(self.stdin.reads, len(self.line))

    def test_bytewise_with_hw_watchdog_0(self):
        self.app.feed(b"SET hw_watchdog 0\n")
        self.stdin.pending += self.line
        self.assertEqual(bytes(self.read()), self.line)
        self.assertEqual(self.stdin.reads, len(self.line))

    def test_one_block_after_set_hw_watchdog_1(self):
        self.app.feed(b"SET hw_watchdog 1\n")
        self.assertIsNotNone(self.app.wdt)
        self.stdin.pending += self.line
        self.assertEqual(bytes(self.read()), self.line)
        self.assertEqual(self.stdin.reads, 7 + 1)


if __name__ == "__main__":
    unittest.main()
