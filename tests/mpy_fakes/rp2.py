# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Module `rp2` simulé : machines PIO et DMA, juste assez pour leds.py."""


class PIO:
    OUT_LOW = 0
    OUT_HIGH = 1
    SHIFT_LEFT = 0
    SHIFT_RIGHT = 1


def asm_pio(**kw):
    def deco(f):
        f.asm_pio_options = kw
        return f
    return deco


machines = {}


class StateMachine:
    def __init__(self, sm_id, prog=None, freq=0, sideset_base=None, **kw):
        self.sm_id = sm_id
        self.prog = prog
        self.freq = freq
        self.pin = sideset_base
        self.running = False
        self.words = []                  # mots envoyés par put()
        machines[sm_id] = self

    def active(self, v=None):
        if v is None:
            return self.running
        self.running = bool(v)
        return None

    def put(self, value, shift=0):
        if isinstance(value, int):
            self.words.append((value << shift) & 0xFFFFFFFF if shift else value)
        else:
            for w in value:
                self.words.append(w >> shift if shift else w)

    def tx_fifo(self):
        return 0


dmas = []


class DMA:
    """rp2.DMA simulé : config() enregistre le transfert ; busy règle active()."""

    def __init__(self):
        self.busy = False
        self.transfers = []              # (mots envoyés, état cible)
        dmas.append(self)

    def pack_ctrl(self, **kw):
        return dict(kw)

    def config(self, read=None, write=None, count=None, ctrl=None, trigger=False):
        if trigger:
            words = list(read[:count])
            self.transfers.append((words, write, ctrl))
            if isinstance(write, StateMachine):
                write.words.extend(words)

    def active(self, v=None):
        return self.busy

    def close(self):
        pass
