# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Boucle du cœur 1 (§ 7.1) : balayage de la cible, choc, compteur de vie.

Le cœur 1 ne fait aucune entrée-sortie vers le Pi : il dépose ses
événements dans la file partagée (xcore). À chaque tour, il incrémente le
compteur de vie relu par le cœur 0 (voyant de vie, chien de garde matériel,
§ 3.1.2, § 5.4). Une exception l'arrête : le compteur ne bouge plus, le
voyant se fige et le chien de garde matériel (s'il tourne) redémarre le Pico.
"""

from xcore import LIFE_MASK


class Core1:
    def __init__(self, shared, matrix, shock):
        self.shared = shared
        self.matrix = matrix
        self.shock = shock
        self.version = -1

    def _reload(self):
        v, p = self.shared.get_params()
        if v == self.version or p is None:
            return
        self.version = v
        min_us, release_us, hold_us, cal, reject, thr, low, digital, holdoff = p
        self.matrix.configure(min_us, release_us, hold_us, cal, reject)
        self.shock.configure(thr, low, digital, holdoff)

    def step(self):
        if self.shared.version != self.version:
            self._reload()
        self.matrix.scan()
        self.shock.poll()
        self.shared.life = (self.shared.life + 1) & LIFE_MASK

    def run(self):
        shared = self.shared
        try:
            while shared.running:
                self.step()
        except Exception as e:                   # noqa: BLE001 : rien ne doit sortir du cœur 1
            shared.core1_error = repr(e)
