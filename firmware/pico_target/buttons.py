# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Boutons BTN1 à BTN15 (§ 3.3, § 7.3), sur le cœur 0.

- BTN1 à BTN14 sur U3 (0x20), contact à la masse, tirages internes du MCP :
  bit à 0 = appuyé. Lecture quand MCP_INT (GP19, actif à 0) le demande,
  pendant un antirebond en cours, et au moins toutes les 10 ms (secours).
- BTN15 sur GP18, tirage interne vers le haut, lu directement.
- Antirebond `btn_debounce_ms` : un changement n'est retenu que s'il dure ;
  `B <n> <0|1> <t_µs>` part alors, avec l'instant du premier changement vu.
- U3 absent : BTN1 à BTN14 relâchés ; un bouton appuyé à ce moment donne un
  seul `B <n> 0` (§ 3.2).
"""

import proto as P
import board
from mcp23017 import GPIO

FALLBACK_MS = 10
U3_MASK = (1 << 14) - 1          # BTN1 à BTN14 = bits 0 à 13 du masque BS


class Buttons:
    def __init__(self, clock, bus, btn15_pin, int_pin, emit):
        self.clock = clock
        self.bus = bus
        self.btn15 = btn15_pin
        self.int_pin = int_pin
        self.emit = emit
        self.debounce_ms = 10
        self.mask = 0                         # état retenu (bit n-1 = BTN n appuyé)
        self.raw = 0                          # dernier état lu
        self.since_us = [0] * P.BUTTONS       # instant du premier changement vu
        self.since_ms = [0] * P.BUTTONS
        self.pending = 0                      # boutons dont l'état lu diffère de l'état retenu
        self.u3_raw = 0
        self.last_read = clock.ticks_ms()

    def _read_u3(self, now_ms):
        word = self.bus.read_word(board.MCP_BUTTONS, GPIO)
        self.last_read = now_ms
        if word is None:
            return 0
        m = 0
        for i in range(14):
            if not word >> board.BUTTON_BITS[i] & 1:
                m |= 1 << i
        return m

    def u3_lost(self):
        """U3 vient de disparaître : BTN1 à BTN14 relâchés tout de suite, sans antirebond."""
        t = self.clock.ticks_us()
        released = self.mask & U3_MASK
        self.u3_raw = 0
        self.raw &= ~U3_MASK
        self.pending &= ~U3_MASK
        for i in range(14):
            if released >> i & 1:
                self.mask &= ~(1 << i)
                self.emit(P.msg_button(i + 1, 0, t))

    def poll(self):
        clock = self.clock
        now_ms = clock.ticks_ms()
        now_us = clock.ticks_us()
        if self.bus.present[board.MCP_BUTTONS]:
            if (self.int_pin.value() == 0 or self.pending & U3_MASK
                    or clock.ticks_diff(now_ms, self.last_read) >= FALLBACK_MS):
                self.u3_raw = self._read_u3(now_ms)
        raw = self.u3_raw
        if self.btn15.value() == 0:
            raw |= 1 << 14
        changed = raw ^ self.raw
        self.raw = raw
        diff = raw ^ self.mask
        for i in range(P.BUTTONS):
            b = 1 << i
            if not diff & b:
                self.pending &= ~b
                continue
            if changed & b or not self.pending & b:
                self.pending |= b
                self.since_us[i] = now_us
                self.since_ms[i] = now_ms
            if clock.ticks_diff(now_ms, self.since_ms[i]) >= self.debounce_ms:
                self.mask ^= b
                self.pending &= ~b
                self.emit(P.msg_button(i + 1, self.mask & b, self.since_us[i]))
