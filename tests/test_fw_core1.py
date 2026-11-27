# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Firmware de la carte Pico, cœur 1 : file partagée, balayage, choc (§ 7.1, § 7.2, § 7.4)."""

import threading
import unittest

import _fw_paths  # noqa: F401
import machine
import proto as P
import board
import pads
import scanner
import shock as shock_mod
import xcore
from core1 import Core1
from xcore import EV_DART, EV_REJECT, EV_SHOCK, EV_SHOCK_CLEAR, EV_SHOCK_STUCK, EV_STUCK, EV_STUCK_CLEAR


class FakeClock:
    """ticks_us sur 30 bits, comme MicroPython ; sleep_us avance le temps."""
    PERIOD = 1 << 30

    def __init__(self, t=1000):
        self.t = t

    def ticks_us(self):
        return self.t % self.PERIOD

    def ticks_diff(self, a, b):
        d = (a - b) % self.PERIOD
        return d - self.PERIOD if d >= self.PERIOD // 2 else d

    def sleep_us(self, n):
        self.t += n

    def us(self, n):
        self.t += n


def drain(q):
    out = []
    while True:
        ev = q.pop()
        if ev is None:
            return out
        out.append(ev)


class Queue(unittest.TestCase):
    def test_fifo_wrap_and_overflow(self):
        q = xcore.EventQueue(4)
        for i in range(3):
            self.assertTrue(q.push(EV_DART, i, 0, 10 * i, 5))
        self.assertEqual(q.pop(), (EV_DART, 0, 0, 0, 5))
        for i in range(3, 6):
            q.push(EV_DART, i, 0, 0, 0)
        self.assertFalse(q.push(EV_DART, 9, 0, 0, 0))       # pleine
        self.assertEqual([e[1] for e in drain(q)], [1, 2, 3, 4])
        self.assertEqual(q.take_dropped(), 2)
        self.assertEqual(q.take_dropped(), 0)

    def test_two_threads_lose_nothing_and_keep_order(self):
        q = xcore.EventQueue(16)
        n = 3000
        got = []

        def writer():
            i = 0
            while i < n:
                if q.push(EV_DART, i & 0xFFFF, 0, i, 0):
                    i += 1

        th = threading.Thread(target=writer)
        th.start()
        while len(got) < n:
            ev = q.pop()
            if ev is not None:
                got.append(ev[3])
        th.join()
        self.assertEqual(got, list(range(n)))

    def test_shared_params_versioned(self):
        s = xcore.Shared()
        self.assertEqual(s.get_params(), (0, None))
        p = P.default_params()
        s.set_params(p, cal=0)
        v, t = s.get_params()
        self.assertEqual(v, 1)
        self.assertEqual(t, (300, 20000, 400000, 0, 0, 2048, 0, 1, 50000))


class Base(unittest.TestCase):
    def setUp(self):
        self.sim = machine.reset_sim()
        self.clk = FakeClock()
        self.q = xcore.EventQueue(32)
        self.contacts = {}                     # colonne -> masque des lignes en contact
        self.sim.matrix = lambda col: self.contacts.get(col, 0)


class Pins(Base):
    def test_rows_without_pull_and_bench_pullup(self):
        for n in board.ROW_PINS:
            self.sim.pads[n] = machine.PAD_RESET | pads.PUE
        scanner.setup_pins(False)
        for n in board.ROW_PINS:
            self.assertFalse(self.sim.pad(n) & (pads.PUE | pads.PDE), n)
            self.assertEqual(self.sim.pins[n].mode, machine.Pin.IN)
        scanner.setup_pins(True)
        for n in board.ROW_PINS:
            self.assertEqual(self.sim.pad(n) & (pads.PUE | pads.PDE), pads.PUE, n)
        self.assertEqual(self.sim.column(), board.COL_IDLE)

    def test_select_is_one_write_and_keeps_other_bits(self):
        scanner.setup_pins(False)
        self.sim.out |= 1 << 16                  # voyant de vie : ne doit pas bouger
        for col in (0, 5, 6, 3, board.COL_IDLE):
            self.sim.mem_writes = []
            scanner.select(col)
            self.assertEqual(len(self.sim.mem_writes), 1)
            self.assertEqual(self.sim.mem_writes[0][0], scanner.GPIO_OUT_XOR)
            self.assertEqual(self.sim.column(), col)
            self.assertTrue(self.sim.out >> 16 & 1)

    def test_read_contacts_active_low(self):
        scanner.setup_pins(False)
        self.contacts = {2: 0b100000001001}
        scanner.select(2)
        self.assertEqual(scanner.read_contacts(), 0b100000001001)
        scanner.select(3)
        self.assertEqual(scanner.read_contacts(), 0)


class MatrixTest(Base):
    def setUp(self):
        super().setUp()
        scanner.setup_pins(False)
        self.m = scanner.Matrix(self.q, self.clk)
        self.m.configure(300, 20000, 400000, 0, 0)
        self.m.scan()                              # le firmware tourne déjà : cible ouverte

    def hold(self, row, col, us, gap_after=30000):
        """Contact maintenu pendant environ `us`, puis relâché pendant gap_after."""
        self.contacts = {col: 1 << row}
        end = self.clk.t + us
        while self.clk.t < end:
            self.m.scan()
            self.clk.us(50)
        self.contacts = {}
        end = self.clk.t + gap_after
        while self.clk.t < end:
            self.m.scan()
            self.clk.us(50)

    def test_scan_period_and_idle_after(self):
        t0 = self.clk.t
        self.m.scan()
        self.assertEqual(self.clk.t - t0, 7 * board.SETTLE_US)
        self.assertEqual(self.sim.column(), board.COL_IDLE)

    def test_valid_dart_sent_at_contact(self):
        t0 = self.clk.ticks_us()
        self.contacts = {4: 1 << 3}
        for _ in range(3):                         # 3 balayages ≈ 0,8 ms de contact vu
            self.m.scan()
            self.clk.us(50)
        ev = drain(self.q)
        self.assertEqual(len(ev), 1)               # parti avant la libération
        kind, r, c, t, d = ev[0]
        self.assertEqual((kind, r, c), (EV_DART, 3, 4))
        self.assertLess(abs(self.clk.ticks_diff(t, t0)), 500)   # début du contact
        self.assertTrue(300 <= d < 1000, d)        # durée observée à l'envoi
        self.hold(3, 4, 2000)                      # suite du même contact, puis libération
        self.assertEqual(drain(self.q), [])

    def test_seen_once_is_too_short(self):
        self.contacts = {0: 1}
        self.m.scan()
        self.contacts = {}
        for _ in range(80):
            self.m.scan()
        self.assertEqual(drain(self.q), [])

    def test_rearm_after_release(self):
        self.hold(3, 4, 2000, gap_after=19000)     # moins de release_ms ouvert
        self.hold(3, 4, 2000)
        self.assertEqual(len(drain(self.q)), 1)
        self.hold(3, 4, 2000)                      # après 20 ms ouvert : nouvelle fléchette
        self.assertEqual(len(drain(self.q)), 1)

    def test_short_contact_rejected_or_silent(self):
        self.hold(0, 0, 100)
        self.assertEqual(drain(self.q), [])
        self.m.configure(300, 20000, 400000, 0, 1)
        self.hold(0, 0, 100)
        ev = drain(self.q)
        self.assertEqual([(e[0], e[1], e[2]) for e in ev], [(EV_REJECT, 0, 0)])

    def test_stuck_contact_reported_once(self):
        self.hold(11, 6, 900000)
        ev = drain(self.q)
        self.assertEqual([(e[0], e[1], e[2]) for e in ev],
                         [(EV_DART, 11, 6), (EV_STUCK, 11, 6), (EV_STUCK_CLEAR, 11, 6)])  # libéré à la fin
        self.assertGreater(ev[1][4], 400000)

    def test_bounces_inside_release_make_one_dart(self):
        for _ in range(3):
            self.hold(5, 1, 600, gap_after=5000)
        self.hold(5, 1, 600)
        ev = drain(self.q)
        self.assertEqual(len(ev), 1)                                 # durée cumulée sur les rebonds
        self.m.configure(1500, 20000, 400000, 0, 0)
        for _ in range(3):
            self.hold(5, 1, 600, gap_after=5000)
        self.assertEqual(drain(self.q), [])                          # 3 × ≈ 400 µs < 1 500
        self.hold(5, 1, 600)
        self.assertEqual(len(drain(self.q)), 1)                      # le 4e rebond atteint le seuil

    def test_two_cells_at_once(self):
        self.contacts = {0: 1 << 2, 6: 1 << 9}
        for _ in range(10):
            self.m.scan()
        self.contacts = {}
        for _ in range(80):
            self.m.scan()
        cells = sorted((e[1], e[2]) for e in drain(self.q))
        self.assertEqual(cells, [(2, 0), (9, 6)])

    def test_calibration_raw_contacts(self):
        self.m.configure(300, 20000, 400000, 1, 0)
        for _ in range(2):
            self.hold(1, 1, 100, gap_after=1000)      # trop courts, rebonds proches
        ev = drain(self.q)
        self.assertEqual([(e[0], e[1], e[2]) for e in ev], [(EV_DART, 1, 1)] * 2)

    def test_clock_wrap(self):
        self.clk.t = (1 << 30) - 300
        self.hold(2, 2, 2000)
        ev = drain(self.q)
        self.assertEqual(len(ev), 1)
        self.assertTrue(0 <= ev[0][3] < (1 << 30))

    def test_no_contact_no_event(self):
        for _ in range(100):
            self.m.scan()
        self.assertEqual(drain(self.q), [])

    def scans(self, us):
        end = self.clk.t + us
        while self.clk.t < end:
            self.m.scan()
            self.clk.us(50)

    def fresh(self, reject=0):
        """Nouveau démarrage du firmware : aucun balayage encore fait."""
        self.m = scanner.Matrix(self.q, self.clk)
        self.m.configure(300, 20000, 400000, 0, reject)

    def test_closed_at_start_gives_stuck_not_dart(self):
        # § 7.2 point 3 : contact vu dès le premier balayage, E STUCK aussitôt, jamais de D.
        self.fresh(reject=1)
        self.contacts = {4: 1 << 3}
        self.m.scan()
        ev = drain(self.q)
        self.assertEqual([(e[0], e[1], e[2]) for e in ev], [(EV_STUCK, 3, 4)])
        self.scans(900000)                         # au-delà de hold_max_ms : pas de second E STUCK
        self.assertEqual(drain(self.q), [])
        self.contacts = {}
        self.scans(19000)                          # moins de release_ms ouvert : toujours bloqué
        self.assertEqual(drain(self.q), [])
        self.scans(2000)
        ev = drain(self.q)                         # libéré : fin du 5 Hz, ni D ni X
        self.assertEqual([(e[0], e[1], e[2]) for e in ev], [(EV_STUCK_CLEAR, 3, 4)])
        self.hold(3, 4, 2000)                      # contact suivant : normal
        self.assertEqual([(e[0], e[1], e[2]) for e in drain(self.q)], [(EV_DART, 3, 4)])

    def test_closed_at_start_opening_bounces_change_nothing(self):
        self.fresh()
        self.contacts = {0: 1}
        self.scans(5000)
        self.assertEqual([e[0] for e in drain(self.q)], [EV_STUCK])
        for _ in range(3):                         # rebonds de l'ouverture, < release_ms
            self.contacts = {}
            self.scans(5000)
            self.contacts = {0: 1}
            self.scans(1000)
        self.contacts = {}
        self.scans(15000)
        self.assertEqual(drain(self.q), [])
        self.scans(6000)
        self.assertEqual([e[0] for e in drain(self.q)], [EV_STUCK_CLEAR])

    def test_open_at_start_then_closed_gives_dart(self):
        self.fresh()
        self.m.scan()                              # premier balayage : cible ouverte
        self.hold(3, 4, 2000)
        self.assertEqual([(e[0], e[1], e[2]) for e in drain(self.q)], [(EV_DART, 3, 4)])

    def test_only_cells_closed_at_start_are_stuck(self):
        self.fresh()
        self.contacts = {6: 1 << 11}
        self.m.scan()
        self.contacts = {6: 1 << 11, 2: 1 << 5}    # (5, 2) se ferme au balayage suivant
        self.scans(2000)
        ev = sorted((e[0], e[1], e[2]) for e in drain(self.q))
        self.assertEqual(ev, [(EV_DART, 5, 2), (EV_STUCK, 11, 6)])

    def test_closed_at_start_never_dart_in_calibration(self):
        self.m = scanner.Matrix(self.q, self.clk)
        self.m.configure(300, 20000, 400000, 1, 0)
        self.contacts = {1: 1 << 1}
        self.scans(2000)
        self.contacts = {}
        self.scans(30000)
        self.assertEqual([e[0] for e in drain(self.q)], [EV_STUCK, EV_STUCK_CLEAR])

    def test_calibration_switch_ends_stuck_error(self):
        # CAL efface les contacts suivis : un contact bloqué doit aussi lever le 5 Hz.
        self.fresh()
        self.contacts = {4: 1 << 3}
        self.m.scan()
        self.m.configure(300, 20000, 400000, 1, 0)
        ev = drain(self.q)
        self.assertEqual([(e[0], e[1], e[2]) for e in ev], [(EV_STUCK, 3, 4), (EV_STUCK_CLEAR, 3, 4)])


class ShockTest(Base):
    def setUp(self):
        super().setUp()
        self.v = 0
        self.s = shock_mod.Shock(self.q, self.clk, lambda: self.v << 4)
        self.s.configure(1000, 0, 1, 50000)

    def feed(self, *values, step=500):
        for v in values:
            self.v = v
            self.s.poll()
            self.clk.us(step)

    def test_setup_adc_disables_digital_input(self):
        self.sim.pads[board.SHOCK_PIN] = machine.PAD_RESET | pads.IE
        adc, was_on = shock_mod.setup_adc()
        self.assertTrue(was_on)
        self.assertFalse(pads.input_enabled(board.SHOCK_PIN))
        self.assertFalse(self.sim.pad(board.SHOCK_PIN) & (pads.PDE | pads.PUE))
        self.sim.adc[board.SHOCK_PIN] = 0xFFF0
        self.assertEqual(adc.read_u16() >> 4, 4095)

    def test_threshold_equal_counts_digital(self):
        self.feed(0, 999, 0)
        self.assertEqual(drain(self.q), [])
        t = self.clk.ticks_us()
        self.feed(1000)
        self.assertEqual(drain(self.q), [(EV_SHOCK, 1, 0, t, 0)])

    def test_level_held_gives_one_shock_then_rearm(self):
        self.feed(0, *([4095] * 400))                 # 200 ms au niveau actif
        self.assertEqual(len(drain(self.q)), 1)
        self.feed(0, 4095)
        self.assertEqual(len(drain(self.q)), 1)

    def test_holdoff_absorbs_crossings(self):
        self.feed(0, 4095, 0, 4095, 0, 4095)          # rebonds en 2,5 ms
        self.assertEqual(len(drain(self.q)), 1)
        self.feed(*([4095] * 200))                    # reste actif après le blocage
        self.assertEqual(drain(self.q), [])
        self.feed(0, 4095)
        self.assertEqual(len(drain(self.q)), 1)

    def test_active_at_start_gives_error_once_not_shock(self):
        t = self.clk.ticks_us()
        self.feed(4095, 4095, 4095)
        self.assertEqual(drain(self.q), [(EV_SHOCK_STUCK, 4095, 0, t, 0)])
        self.feed(0, 4095)                             # fin de l'erreur, réarmé
        self.assertEqual([e[0] for e in drain(self.q)], [EV_SHOCK_CLEAR, EV_SHOCK])

    def test_idle_at_start_no_error(self):
        self.feed(0, 0)
        self.assertEqual(drain(self.q), [])

    def test_polarity_change_checks_again(self):
        self.feed(0, 0)
        self.s.configure(1000, 1, 1, 50000)            # 0 devient actif en polarité low
        self.feed(0)
        self.assertEqual([e[0] for e in drain(self.q)], [EV_SHOCK_STUCK])
        self.s.configure(1000, 0, 1, 50000)            # retour en high : 0 est le repos
        self.feed(0)
        self.assertEqual([e[0] for e in drain(self.q)], [EV_SHOCK_CLEAR])

    def test_low_polarity(self):
        self.s.configure(1000, 1, 1, 50000)
        self.feed(4095, 1001)
        self.assertEqual(drain(self.q), [])
        self.feed(1000)
        self.assertEqual(len(drain(self.q)), 1)

    def test_adc_peak_after_return(self):
        self.s.configure(1000, 0, 0, 50000)
        self.feed(0)
        t = self.clk.ticks_us()
        self.feed(1500, 3000, 2200)
        self.assertEqual(drain(self.q), [])           # crête en cours
        self.feed(10)
        self.assertEqual(drain(self.q), [(EV_SHOCK, 3000, 0, t, 0)])

    def test_adc_peak_cut_by_holdoff(self):
        self.s.configure(1000, 0, 0, 2000)
        self.feed(0, 1200, 1300, 1400, 1500, 1600)
        self.assertEqual([e[1] for e in drain(self.q)], [1600])   # lue à la fin du blocage

    def test_low_polarity_adc_peak_is_minimum(self):
        self.s.configure(1000, 1, 0, 50000)
        self.feed(4000, 900, 20, 400, 3000)
        self.assertEqual([e[1] for e in drain(self.q)], [20])

    def test_holdoff_survives_ticks_wrap(self):
        self.feed(0, 4095, 0)
        drain(self.q)
        self.feed(*([0] * 200))                       # le cœur 1 tourne : blocage fini
        self.clk.us(600 * 1000 * 1000)               # 10 min sans choc, au-delà de 2^29 µs
        self.feed(0, 4095)
        self.assertEqual(len(drain(self.q)), 1)


class Core1Test(Base):
    def setUp(self):
        super().setUp()
        scanner.setup_pins(False)
        self.shared = xcore.Shared()
        self.m = scanner.Matrix(self.q, self.clk)
        self.v = 0
        self.s = shock_mod.Shock(self.q, self.clk, lambda: self.v << 4)
        self.c1 = Core1(self.shared, self.m, self.s)

    def test_life_counter_and_params(self):
        p = P.default_params()
        p["min_contact_us"] = 1234
        p["shock_threshold"] = 77
        self.shared.set_params(p, cal=1)
        for i in range(3):
            self.c1.step()
        self.assertEqual(self.shared.life, 3)
        self.assertEqual((self.m.min_us, self.m.cal, self.s.threshold), (1234, 1, 77))
        self.shared.life = xcore.LIFE_MASK
        self.c1.step()
        self.assertEqual(self.shared.life, 0)

    def test_run_stops_and_records_exception(self):
        def boom():
            raise RuntimeError("panne")
        self.s.read_u16 = boom
        self.c1.run()
        self.assertIn("panne", self.shared.core1_error)
        self.assertEqual(self.shared.life, 0)

    def test_run_stops_on_request(self):
        calls = []

        def stop():
            calls.append(1)
            if len(calls) == 5:
                self.shared.running = False
            return 0
        self.s.read_u16 = stop
        self.c1.run()
        self.assertEqual(self.shared.life, 5)
        self.assertIsNone(self.shared.core1_error)

    def test_dart_and_shock_through_core1(self):
        self.shared.set_params(P.default_params(), cal=0)
        self.c1.step()
        self.contacts = {4: 1 << 7}
        self.v = 4095
        for _ in range(6):
            self.c1.step()
        self.contacts = {}
        self.v = 0
        for _ in range(60):
            self.c1.step()
        kinds = sorted((e[0], e[1], e[2]) for e in drain(self.q))
        self.assertEqual(kinds, [(EV_DART, 7, 4), (EV_SHOCK, 1, 0)])

    def test_contact_closed_at_first_step_is_stuck(self):
        self.shared.set_params(P.default_params(), cal=0)
        self.contacts = {4: 1 << 7}
        for _ in range(6):
            self.c1.step()
        self.assertEqual([(e[0], e[1], e[2]) for e in drain(self.q)], [(EV_STUCK, 7, 4)])


class RegistersMatchPicoSdk(unittest.TestCase):
    """Valeurs relevées dans le Pico SDK (src/rp2350/hardware_regs/include/hardware/regs/)."""

    def test_sio(self):
        self.assertEqual(scanner.SIO_BASE, 0xD0000000)              # addressmap.h SIO_BASE
        self.assertEqual(scanner.GPIO_IN - scanner.SIO_BASE, 0x004)  # SIO_GPIO_IN_OFFSET
        self.assertEqual(scanner.GPIO_OUT - scanner.SIO_BASE, 0x010)  # SIO_GPIO_OUT_OFFSET
        self.assertEqual(scanner.GPIO_OUT_XOR - scanner.SIO_BASE, 0x028)  # SIO_GPIO_OUT_XOR_OFFSET

    def test_pads(self):
        self.assertEqual(pads.PADS_BANK0, 0x40038000)               # PADS_BANK0_BASE
        self.assertEqual(pads.addr(0) - pads.PADS_BANK0, 0x04)      # PADS_BANK0_GPIO0_OFFSET
        self.assertEqual(pads.addr(16) - pads.PADS_BANK0, 0x44)     # PADS_BANK0_GPIO16_OFFSET
        self.assertEqual(pads.addr(26) - pads.PADS_BANK0, 0x6C)     # PADS_BANK0_GPIO26_OFFSET
        self.assertEqual((pads.PDE, pads.PUE, pads.IE), (0x04, 0x08, 0x40))
        self.assertEqual(machine.PAD_RESET, 0x116)                  # PADS_BANK0_GPIO0_RESET


if __name__ == "__main__":
    unittest.main()
