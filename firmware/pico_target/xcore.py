# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Données partagées entre les deux cœurs (§ 7.1).

MicroPython v1.29.0 sur RP2350 fait tourner `_thread` **sans GIL** : tout ce
qui passe d'un cœur à l'autre est ici, et tout ce qui peut être lu à moitié
écrit est protégé par un verrou tenu quelques microsecondes.

- `EventQueue` : file bornée, le cœur 1 écrit, le cœur 0 lit. Tampon alloué
  une fois pour toutes (pas d'allocation sur le cœur 1). File pleine :
  l'événement est perdu et compté (`E OVERFLOW`, § 5.2).
- `Shared` : paramètres utiles au cœur 1 (copiés sous verrou, avec un numéro
  de version), compteur de vie du cœur 1 (un seul écrivain : le cœur 1),
  demande d'arrêt.
"""

from array import array

import _thread

# Types d'événements du cœur 1
EV_DART = 1          # a = ligne, b = colonne, t = début du contact, d = durée à l'envoi
EV_REJECT = 2        # contact trop court (X)
EV_SHOCK = 3         # a = crête (1 en mode digital), t = instant du choc
EV_SHOCK_STUCK = 4   # choc actif dès la première lecture (démarrage, changement de polarité)
EV_SHOCK_CLEAR = 5   # fin de EV_SHOCK_STUCK : premier retour au repos
EV_STUCK = 6         # contact bloqué au-delà de hold_max_ms : a = ligne, b = colonne
EV_STUCK_CLEAR = 7   # contact bloqué enfin libéré : a = ligne, b = colonne

_SLOT = 5
LIFE_MASK = 0x3FFFFFFF


class EventQueue:
    def __init__(self, size=64):
        self.size = size
        self._buf = array("i", [0] * (size * _SLOT))
        self._head = 0               # prochain emplacement libre
        self._count = 0
        self.dropped = 0             # événements perdus, remis à 0 par take_dropped()
        self._lock = _thread.allocate_lock()

    def push(self, kind, a, b, t, d):
        """Cœur 1. Renvoie False si la file est pleine (événement perdu)."""
        lock = self._lock
        lock.acquire()
        if self._count >= self.size:
            self.dropped += 1
            lock.release()
            return False
        i = self._head * _SLOT
        buf = self._buf
        buf[i] = kind
        buf[i + 1] = a
        buf[i + 2] = b
        buf[i + 3] = t
        buf[i + 4] = d
        self._head = (self._head + 1) % self.size
        self._count += 1
        lock.release()
        return True

    def pop(self):
        """Cœur 0. Renvoie (type, a, b, t, d) ou None."""
        lock = self._lock
        lock.acquire()
        if not self._count:
            lock.release()
            return None
        i = ((self._head - self._count) % self.size) * _SLOT
        buf = self._buf
        ev = (buf[i], buf[i + 1], buf[i + 2], buf[i + 3], buf[i + 4])
        self._count -= 1
        lock.release()
        return ev

    def take_dropped(self):
        """Cœur 0. Nombre d'événements perdus depuis le dernier appel."""
        lock = self._lock
        lock.acquire()
        n = self.dropped
        self.dropped = 0
        lock.release()
        return n

    def __len__(self):
        return self._count


class Shared:
    """Paramètres du cœur 1, compteur de vie, arrêt."""

    def __init__(self):
        self._lock = _thread.allocate_lock()
        self.version = 0
        self._p = None
        self.life = 0                # écrit par le cœur 1 seulement
        self.running = True
        self.core1_error = None      # texte de l'exception qui a arrêté le cœur 1

    def set_params(self, params, cal):
        """Cœur 0 : publie les valeurs utiles au cœur 1, déjà converties."""
        p = (
            params["min_contact_us"],
            params["release_ms"] * 1000,
            params["hold_max_ms"] * 1000,
            1 if cal else 0,
            params["reject_report"],
            params["shock_threshold"],
            1 if params["shock_polarity"] == "low" else 0,
            1 if params["shock_mode"] == "digital" else 0,
            params["shock_holdoff_ms"] * 1000,
        )
        self._lock.acquire()
        self._p = p
        self.version = (self.version + 1) & LIFE_MASK
        self._lock.release()

    def get_params(self):
        """Cœur 1 : (version, tuple des valeurs), lus ensemble."""
        self._lock.acquire()
        v, p = self.version, self._p
        self._lock.release()
        return v, p
