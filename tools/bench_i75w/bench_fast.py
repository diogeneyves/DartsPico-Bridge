# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Banc jetable de l'Interstate 75 W : copie rapide des pixels, en code natif « viper ».

MicroPython seulement (le décorateur viper n'existe pas sous CPython). Si ce module ne se
charge pas, le récepteur prend la version en Python pur (rx_bench.swap16_py), plus lente.

Les trames du § 6.2 portent des pixels RGB565 petit-boutistes ; le tampon RGB565 de
PicoGraphics les range octets permutés (__builtin_bswap16 dans pico_graphics.hpp).
"""

import micropython


@micropython.viper
def swap16(dst, src, n: int):
    d = ptr8(dst)
    s = ptr8(src)
    i = 0
    while i < n:
        d[i] = s[i + 1]
        d[i + 1] = s[i]
        i += 2
