# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Banc jetable de l'Interstate 75 W : réglages des dalles, communs à la mire et au récepteur.

S'exécute sous le MicroPython de Pimoroni (i75w_rp2350). Ce n'est pas le firmware de M4.
À changer ici seulement, puis recopier sur la carte (voir PROCEDURE.md) :
- couleurs fausses à la mire (page 3) : COLOR_ORDER ;
- dalle noire ou lignes parasites : PANEL_TYPE = "FM6126A", puis STB_INVERT = True.
"""

WIDTH = 128
HEIGHT = 32
PANEL_WIDTH = 64            # 2 dalles P4 64 × 32, balayage 1/16, chaînées

PANEL_TYPE = "GENERIC"      # "GENERIC" ou "FM6126A"
COLOR_ORDER = "RGB"         # "RGB", "RBG", "GRB", "GBR", "BRG" ou "BGR"
STB_INVERT = False

ESCAPE_PIN = 14             # bouton A de la carte : maintenu au démarrage, le récepteur ne part pas
START_DELAY_MS = 2000       # fenêtre pendant laquelle Ctrl-C arrête encore le récepteur


def hub75_options(hub75):
    """Arguments nommés de hub75.Hub75 et de Interstate75, d'après les réglages ci-dessus."""
    return {
        "panel_type": getattr(hub75, "PANEL_" + PANEL_TYPE),
        "stb_invert": STB_INVERT,
        "color_order": getattr(hub75, "COLOR_ORDER_" + COLOR_ORDER),
    }
