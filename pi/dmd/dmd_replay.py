# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Mode rejeu du service de rendu (spécification § 9.9, 47e complément, sous-phase SR-d).

Journal texte UTF-8, une ligne par message :

    <délai en secondes depuis le message précédent> <sujet> <contenu>

Le contenu va jusqu'à la fin de la ligne (espaces permises, vide possible).
Lignes vides et lignes « # » ignorées ; une ligne invalide est journalisée une
fois, au chargement, et sautée. Aucun accès à MQTT.

    entries = load(chemin)            ou parse(lignes)
    replay = Replayer(entries, clock, speed=1.0, loop=False)
    replay.due()     -> [(sujet, contenu en octets), ...] arrivés à échéance
    replay.wait_us() -> µs avant le prochain message, ou None si terminé
"""

import logging
import time
from collections import namedtuple

log = logging.getLogger("dmd")

Entry = namedtuple("Entry", "delay_s topic payload")


def parse(lines, source="journal"):
    entries = []
    for number, line in enumerate(lines, 1):
        line = line.rstrip("\r\n")
        if line.strip() == "" or line.lstrip().startswith("#"):
            continue
        parts = line.split(" ", 2)
        try:
            if len(parts) < 2 or parts[1] == "":
                raise ValueError("sujet manquant")
            delay = float(parts[0])
            if not 0 <= delay < float("inf"):
                raise ValueError("délai invalide")
        except ValueError as error:
            log.warning("DMD replay : %s line %d ignored (%s)", source, number, error)
            continue
        payload = parts[2] if len(parts) > 2 else ""
        entries.append(Entry(delay, parts[1], payload.encode("utf-8")))
    return entries


def load(path):
    with open(path, encoding="utf-8") as stream:
        return parse(stream, source=str(path))


def _monotonic_us():
    return time.monotonic_ns() // 1000


class Replayer:
    def __init__(self, entries, clock=None, speed=1.0, loop=False):
        if not speed > 0:
            raise ValueError("speed doit être > 0")
        self.entries = list(entries)
        self.clock = clock or _monotonic_us
        self.speed = speed
        self.loop = loop
        self.index = 0
        self.played = 0
        self._next_us = None
        if self.entries:
            self._next_us = self.clock() + self._delay_us(0)

    def _delay_us(self, index):
        return int(round(self.entries[index].delay_s * 1e6 / self.speed))

    @property
    def finished(self):
        return self._next_us is None

    def due(self):
        out = []
        now = self.clock()
        # Au plus un tour du journal par appel : un journal en boucle à délais nuls ne bloque pas.
        while self._next_us is not None and now >= self._next_us and len(out) < len(self.entries):
            entry = self.entries[self.index]
            out.append((entry.topic, entry.payload))
            self.played += 1
            self.index += 1
            if self.index == len(self.entries):
                if not self.loop:
                    self._next_us = None
                    log.info("DMD replay : end of the log (%d messages)", self.played)
                    break
                self.index = 0
            self._next_us += self._delay_us(self.index)
        return out

    def wait_us(self):
        if self._next_us is None:
            return None
        return max(0, self._next_us - self.clock())
