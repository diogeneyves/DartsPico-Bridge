# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Balayage de la cible (§ 7.2), sur le cœur 1.

Matériel : le 74HC154 (U6) met à 0 une colonne à la fois, choisie par son
adresse sur GP15 (A0) à GP12 (A3) ; une ligne lue à 0 signale un contact.
L'adresse s'écrit en un seul accès (registre GPIO_OUT_XOR du SIO), les 12
lignes se lisent en un seul accès (GPIO_IN).

Validation d'un contact (§ 7.2, point 3), par case (ligne, colonne) :
- la durée **cumulée** du contact augmente de l'écart entre deux balayages
  **consécutifs** où il est vu (un contact vu une seule fois dure 0 ; la
  résolution est d'une période de balayage, environ 0,4 ms, § 7.2 point 5) ;
- **`D` part au contact**, dès que la durée cumulée atteint
  `min_contact_us` (comme RaspyDarts 4.6.4), une seule fois par contact ;
  `t_µs` = début du balayage où le contact a été vu pour la première fois,
  durée = durée cumulée au moment de l'envoi (diagnostic seulement) ;
- il est **libéré** (réarmé) quand il n'a pas été vu pendant `release_ms` ;
  s'il n'a pas atteint `min_contact_us`, `X` part alors (si `reject_report`) ;
- au-delà de `hold_max_ms` de durée cumulée, contact bloqué : pas de nouveau
  `D`, une erreur `E STUCK <ligne> <colonne>`, une seule fois par contact ;
  sa libération le signale au cœur 0 (voyant de vie, § 3.1.2) ;
- contact **déjà fermé au démarrage** (vu dès le premier balayage) : jamais
  de `D`, `E STUCK <ligne> <colonne>` aussitôt, une seule fois, puis traité
  comme un contact bloqué (ni `X` ni second `E STUCK`) jusqu'à sa libération
  après `release_ms` ouvert ; le contact suivant est normal.
En calibration (`CAL 1`) : un `D` par contact brut, dès qu'il n'est plus vu,
sans aucun filtre.

Aucune allocation dans la boucle : tableaux alloués une fois.
"""

from array import array

import micropython
from machine import Pin, mem32

import board
import pads
from xcore import EV_DART, EV_REJECT, EV_STUCK, EV_STUCK_CLEAR

# Vérifiés contre le Pico SDK (rp2350 : addressmap.h, sio.h).
SIO_BASE = 0xD0000000
GPIO_IN = SIO_BASE + 0x004
GPIO_OUT = SIO_BASE + 0x010
GPIO_OUT_XOR = SIO_BASE + 0x028

SENT = 1
STUCK_SENT = 2
AT_START = 4          # contact vu dès le premier balayage : jamais de D

ROWS = 12
COLS = board.COLS
TICKS_MASK = (1 << 30) - 1
DUR_MAX = TICKS_MASK


def _addr_bits(a):
    """Bits de GPIO_OUT pour l'adresse a du 74HC154."""
    v = 0
    for i in range(4):
        if a >> i & 1:
            v |= 1 << board.COL_ADDR_PINS[i]
    return v


ADDR_MASK = _addr_bits(15)
ADDR_BITS = array("i", [_addr_bits(c) for c in range(16)])


def setup_pins(bench_pullup):
    """Lignes en entrée sans tirage (sauf banc), adresse au repos."""
    for n in board.ROW_PINS:
        Pin(n, Pin.IN, None)
        pads.set_pull_up(n, bench_pullup)
    for n in board.COL_ADDR_PINS:
        Pin(n, Pin.OUT, value=1)          # adresse 15 = aucune colonne (repos)


def set_row_pullups(on):
    for n in board.ROW_PINS:
        pads.set_pull_up(n, on)


@micropython.native
def select(col):
    """Une seule écriture : bascule les bits d'adresse qui changent."""
    cur = mem32[GPIO_OUT]
    mem32[GPIO_OUT_XOR] = (cur ^ ADDR_BITS[col]) & ADDR_MASK


@micropython.native
def read_contacts():
    """Masque des lignes à 0 (contacts) sur la colonne choisie."""
    return ~mem32[GPIO_IN] & board.ROW_MASK


class Matrix:
    def __init__(self, queue, clock):
        self.queue = queue
        self.clock = clock
        n = ROWS * COLS
        self.start = array("i", [0] * n)
        self.last = array("i", [0] * n)
        self.cum = array("i", [0] * n)
        self.flags = array("i", [0] * n)      # SENT, STUCK_SENT, AT_START
        self.active = array("i", [0] * COLS)   # masque des lignes suivies, par colonne
        self.prev = None                       # début du balayage précédent
        self.min_us = 300
        self.release_us = 20000
        self.hold_us = 400000
        self.cal = 0
        self.reject = 0

    def configure(self, min_us, release_us, hold_us, cal, reject):
        if cal != self.cal:
            self.clear()
        self.min_us = min_us
        self.release_us = release_us
        self.hold_us = hold_us
        self.cal = cal
        self.reject = reject

    def clear(self):
        """Oublie les contacts suivis ; un contact bloqué lève son erreur (fin du 5 Hz)."""
        for c in range(COLS):
            act = self.active[c]
            r = 0
            while act:
                if act & 1:
                    i = r * COLS + c
                    if self.flags[i] & STUCK_SENT:
                        self.queue.push(EV_STUCK_CLEAR, r, c, 0, self.cum[i])
                act >>= 1
                r += 1
            self.active[c] = 0

    @micropython.native
    def scan(self):
        """Un balayage complet des 7 colonnes, puis retour au repos."""
        clock = self.clock
        now = clock.ticks_us()
        prev = self.prev
        dt = 0 if prev is None else clock.ticks_diff(now, prev)
        if dt > 100000:
            dt = 100000                        # cœur 1 retardé : ne pas gonfler les durées
        for c in range(COLS):
            select(c)
            clock.sleep_us(board.SETTLE_US)
            seen = read_contacts()
            if seen or self.active[c]:
                self.process(c, seen, now, prev, dt)
        select(board.COL_IDLE)
        self.prev = now

    def process(self, c, seen, now, prev, dt):
        act = self.active[c]
        bits = seen | act
        clock = self.clock
        r = 0
        while bits:
            if bits & 1:
                i = r * COLS + c
                m = 1 << r
                if seen & m:
                    if not act & m:
                        act |= m
                        self.start[i] = now
                        self.cum[i] = 0
                        if prev is None:           # fermé dès le démarrage du firmware
                            self.flags[i] = SENT | STUCK_SENT | AT_START
                            self.queue.push(EV_STUCK, r, c, now, 0)
                        else:
                            self.flags[i] = 0
                    elif self.last[i] == prev:     # vu aussi au balayage précédent
                        d = self.cum[i] + dt
                        self.cum[i] = d if d < DUR_MAX else DUR_MAX
                    self.last[i] = now
                    if not self.cal:
                        self.check(r, c, i)
                elif self.cal or clock.ticks_diff(now, self.last[i]) >= self.release_us:
                    act &= ~m
                    self.finish(r, c, i)
            bits >>= 1
            r += 1
        self.active[c] = act

    def check(self, r, c, i):
        """Contact vu : envoi de D au seuil, puis contact bloqué au-delà de hold_max_ms."""
        f = self.flags[i]
        dur = self.cum[i]
        if not f & SENT:
            if dur >= self.min_us:
                self.flags[i] = f | SENT
                self.queue.push(EV_DART, r, c, self.start[i], dur)
        elif not f & STUCK_SENT and dur > self.hold_us:
            self.flags[i] = f | STUCK_SENT
            self.queue.push(EV_STUCK, r, c, self.start[i], dur)

    def finish(self, r, c, i):
        """Libération : D brut en calibration, X si le contact est resté trop court."""
        f = self.flags[i]
        if f & STUCK_SENT:
            self.queue.push(EV_STUCK_CLEAR, r, c, 0, self.cum[i])   # fin du 5 Hz
        if self.cal and not f & AT_START:
            self.queue.push(EV_DART, r, c, self.start[i], self.cum[i])
        elif not f & SENT and self.reject:
            self.queue.push(EV_REJECT, r, c, 0, self.cum[i])
