# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Registres de pads du RP2350 (PADS_BANK0) : tirages et entrée numérique.

Erratum E9 (§ 3.1.1) : le firmware fixe lui-même les bits des pads, sans
compter sur ce que fait `machine.Pin` ; il ne met jamais de tirage vers le bas.
Adresses et bits vérifiés contre le Pico SDK officiel
(src/rp2350/hardware_regs/include/hardware/regs/addressmap.h et
pads_bank0.h) : PADS_BANK0_BASE = 0x40038000, GPIO0 à l'offset 0x04,
GPIO26 à 0x6C ; PDE bit 2, PUE bit 3, IE bit 6 ; ISO (bit 8) n'est pas touché.
À la réinitialisation : 0x116 (ISO, DRIVE 4 mA, PDE, SCHMITT ; IE à 0).
"""

from machine import mem32

PADS_BANK0 = 0x40038000
PDE = 1 << 2                 # tirage vers le bas
PUE = 1 << 3                 # tirage vers le haut
IE = 1 << 6                  # entrée numérique


def addr(gpio):
    return PADS_BANK0 + 4 + 4 * gpio


def set_pull_up(gpio, on):
    """Tirage vers le haut selon `on` ; tirage vers le bas toujours coupé."""
    a = addr(gpio)
    v = mem32[a] & ~(PDE | PUE)
    if on:
        v |= PUE
    mem32[a] = v


def input_enabled(gpio):
    return bool(mem32[addr(gpio)] & IE)


def disable_input(gpio):
    """Coupe l'entrée numérique (et les tirages) ; renvoie True si elle était active."""
    a = addr(gpio)
    v = mem32[a]
    mem32[a] = v & ~(IE | PDE | PUE)
    return bool(v & IE)
