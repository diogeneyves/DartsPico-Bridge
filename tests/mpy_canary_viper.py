# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Témoin du job CI mpy-cross-pico (54e complément) : code viper volontairement faux.

mpy-cross -march=armv7m doit le refuser (ViperTypeError) : `x` reçoit d'abord un
entier, puis un pointeur ptr8, mélange de types que le compilateur viper refuse.
Si ce fichier se compile, le code viper de firmware/pico_target/ n'est pas
vérifié et le job échoue. Hors des tests unitaires (nom sans « test »), jamais
importé ni copié sur le Pico.
"""

import micropython


@micropython.viper
def mixed_types(buf):
    x = 0
    x = ptr8(buf)                            # noqa: F821 (refusé : x est déjà un int)
    return x
