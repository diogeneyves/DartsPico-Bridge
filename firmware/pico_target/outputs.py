# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""État des Toys et des voyants, stroboscopes (§ 5.3, P12), sur le cœur 0.

Logique seule : l'écriture vers les MCP23017 passe par deux fonctions
données à la construction, write_toys(masque) et write_lights(masque),
appelées seulement quand le masque change. Tout est à 0 au départ (§ 5.4).
"""

import proto as P

TOY = 0
LIGHT = 1


class Outputs:
    def __init__(self, clock, write_toys=None, write_lights=None):
        self.clock = clock
        self.state = [0, 0]                  # masques Toys, voyants
        self.counts = (P.TOYS, P.LIGHTS)
        self.writers = [write_toys, write_lights]
        self.written = [None, None]          # dernier masque écrit (None : à écrire)
        self.pulses = {}                     # (genre, n) -> [on, off, reste, début_ms]

    # ------------------------------------------------------------ commandes

    def set(self, kind, n, on):
        self.pulses.pop((kind, n), None)
        self._bit(kind, n, on)

    def set_mask(self, kind, mask):
        for k in [k for k in self.pulses if k[0] == kind]:
            del self.pulses[k]
        self.state[kind] = mask

    def pulse(self, kind, n, on_ms, off_ms, repeat):
        self.pulses[(kind, n)] = [on_ms, off_ms, repeat, self.clock.ticks_ms()]
        self._bit(kind, n, 1)

    def stop(self, kind, n):
        self.set(kind, n, 0)

    def all_off(self):
        """État de repos : tout à 0, stroboscopes arrêtés."""
        self.pulses = {}
        self.state = [0, 0]

    def is_zero(self):
        return not self.state[0] and not self.state[1] and not self.pulses

    def _bit(self, kind, n, on):
        m = 1 << (n - 1)
        self.state[kind] = (self.state[kind] | m) if on else (self.state[kind] & ~m)

    # ------------------------------------------------------------ temps

    def update(self):
        """Stroboscopes, puis écriture des masques qui ont changé."""
        if self.pulses:
            now = self.clock.ticks_ms()
            diff = self.clock.ticks_diff
            for key in list(self.pulses):
                p = self.pulses[key]
                on, off, rest, start = p
                period = on + off
                elapsed = diff(now, start)
                if elapsed >= period:
                    k = elapsed // period
                    if rest:
                        if k >= rest:
                            del self.pulses[key]
                            self._bit(key[0], key[1], 0)   # le Pico termine toujours à 0
                            continue
                        p[2] = rest - k
                    p[3] = start = (start + k * period) & 0x3FFFFFFF   # pas de dérive après des jours
                    elapsed = diff(now, start)
                self._bit(key[0], key[1], 1 if elapsed < on else 0)
        self.flush()

    def flush(self, force=False):
        for kind in (TOY, LIGHT):
            m = self.state[kind]
            if force or m != self.written[kind]:
                w = self.writers[kind]
                if w is None or w(m) is not False:
                    self.written[kind] = m

    def invalidate(self):
        """Puces remises à zéro ou retrouvées : tout réécrire au prochain flush."""
        self.written = [None, None]
