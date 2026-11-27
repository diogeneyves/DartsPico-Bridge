# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Détecteur de choc sur GP26 (§ 7.4), sur le cœur 1.

GP26 est toujours lu par l'ADC, entrée numérique coupée (erratum E9,
§ 3.1.1), quel que soit `shock_mode`. Valeur sur 12 bits (0 à 4 095).

- **Actif** : valeur ≥ `shock_threshold` en polarité `high`, ≤ en `low`
  (une valeur égale au seuil compte, v0.3.14).
- **Choc** = passage du côté repos au côté actif, hors du temps de blocage
  `shock_holdoff_ms`, qui part de chaque choc émis. Un niveau qui reste actif
  ne donne qu'un choc : il faut repasser côté repos pour réarmer. Un passage
  pendant le blocage est absorbé (il ne donne pas de choc à la fin du blocage).
- **Actif dès la première lecture** (au démarrage, ou après un changement de
  `shock_polarity`) : aucun choc, mais une erreur `E SHOCK active at start`
  (événement EV_SHOCK_STUCK), une seule fois ; le détecteur se réarme au
  premier retour au repos, qui met fin à l'erreur (EV_SHOCK_CLEAR : le voyant
  de vie revient à 1 Hz, § 3.1.2).
- `t_µs` = instant du passage. En mode `digital`, `S` part tout de suite
  (crête 1). En mode `adc`, la crête est la valeur la plus éloignée du repos
  (maximum en `high`, minimum en `low`) entre le passage et le retour au
  repos, ou la fin du temps de blocage si elle vient avant ; `S` part à ce
  moment-là.
"""

from machine import ADC, Pin

import board
import pads
from xcore import EV_SHOCK, EV_SHOCK_CLEAR, EV_SHOCK_STUCK


def setup_adc():
    """ADC sur GP26, puis entrée numérique coupée. Renvoie (adc, IE était actif)."""
    adc = ADC(Pin(board.SHOCK_PIN))
    was_on = pads.disable_input(board.SHOCK_PIN)
    return adc, was_on


class Shock:
    def __init__(self, queue, clock, read_u16):
        self.queue = queue
        self.clock = clock
        self.read_u16 = read_u16
        self.threshold = 2048
        self.low = 0
        self.digital = 1
        self.holdoff_us = 50000
        self.armed = False           # devient vrai après une lecture côté repos
        self.fresh = True            # prochaine lecture = première (démarrage)
        self.stuck = False           # erreur « active at start » en cours
        self.last = 0                # instant du dernier choc émis
        self.has_last = False
        self.pending = False         # mode adc : crête en cours de mesure
        self.t = 0
        self.peak = 0

    def configure(self, threshold, low, digital, holdoff_us):
        if low != self.low:
            self.armed = False
            self.fresh = True
        if self.pending and digital:
            self._emit()
        self.threshold = threshold
        self.low = low
        self.digital = digital
        self.holdoff_us = holdoff_us

    def _emit(self):
        self.pending = False
        self.queue.push(EV_SHOCK, self.peak, 0, self.t, 0)

    def poll(self):
        v = self.read_u16() >> 4
        now = self.clock.ticks_us()
        diff = self.clock.ticks_diff
        active = v <= self.threshold if self.low else v >= self.threshold
        if self.fresh:
            self.fresh = False
            if active and not self.stuck:
                self.stuck = True
                self.queue.push(EV_SHOCK_STUCK, v, 0, now, 0)
        if self.stuck and not active:
            self.stuck = False
            self.queue.push(EV_SHOCK_CLEAR, v, 0, now, 0)
        if self.has_last and diff(now, self.last) >= self.holdoff_us:
            self.has_last = False    # blocage fini (évite le repli de ticks_diff après 2^29 µs)
        if self.pending:
            if active:
                if (v < self.peak) if self.low else (v > self.peak):
                    self.peak = v
            if not active or diff(now, self.t) >= self.holdoff_us:
                self._emit()
        if not active:
            self.armed = True
            return
        if not self.armed:
            return
        if self.has_last:
            self.armed = False       # passage pendant le blocage : absorbé
            return
        self.armed = False
        self.has_last = True
        self.last = now
        self.t = now
        self.peak = 1 if self.digital else v
        if self.digital:
            self.queue.push(EV_SHOCK, 1, 0, now, 0)
        else:
            self.pending = True
