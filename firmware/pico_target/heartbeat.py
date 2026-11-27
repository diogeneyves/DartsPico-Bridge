# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Voyant de vie sur GP16 (§ 3.1.2) : LED vers +3V3, allumée au niveau bas.

- 1 Hz (500 ms / 500 ms) en fonctionnement normal, 5 Hz (100 ms / 100 ms)
  tant qu'une erreur dure : `E MCP` (puce absente ou erreur I2C) et
  `E SHOCK active at start` (jusqu'au premier retour au repos du choc) et
  `E STUCK` (contact bloqué ou fermé au démarrage, jusqu'à sa libération).
- Basculement dans la boucle principale du cœur 0, jamais par une minuterie :
  si la boucle se bloque, le voyant se fige. Au plus un basculement par tour ;
  après un retard, la référence repart de l'instant courant.
- Le cœur 0 ne bascule le voyant que si le compteur de vie du cœur 1 a avancé
  depuis son tour précédent : un blocage du cœur 1 fige aussi le voyant.
"""

HALF_MS_NORMAL = 500
HALF_MS_ERROR = 100


class Heartbeat:
    def __init__(self, clock, pin):
        self.clock = clock
        self.pin = pin
        pin.value(1)                         # éteinte
        self.level = 1
        self.errors = {}                     # source -> True
        self.last = clock.ticks_ms()

    def set_error(self, source, on):
        if on:
            self.errors[source] = True
        else:
            self.errors.pop(source, None)

    def in_error(self):
        return bool(self.errors)

    @property
    def lit(self):
        return self.level == 0

    def step(self, life_moved):
        if not life_moved:
            return                           # cœur 1 immobile : voyant figé
        now = self.clock.ticks_ms()
        half = HALF_MS_ERROR if self.errors else HALF_MS_NORMAL
        if self.clock.ticks_diff(now, self.last) >= half:
            self.level ^= 1
            self.pin.value(self.level)
            self.last = now
