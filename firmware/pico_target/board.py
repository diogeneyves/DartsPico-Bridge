# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Brochage de la carte Pico, d'après hardware/pinout.json (spécification § 3).

Ce fichier ne fait que recopier pinout.json : tests/test_fw_board.py vérifie
qu'ils concordent. Toute divergence est un bogue de ce fichier.
"""

# Matrice de la cible (§ 3.1, § 7.2) : lignes en entrée, actives à 0.
ROW_PINS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)
ROW_MASK = 0xFFF                    # GP0 à GP11 dans GPIO_IN
# Adresse du 74HC154 (U6) : A0 = GP15, A1 = GP14, A2 = GP13, A3 = GP12.
COL_ADDR_PINS = (15, 14, 13, 12)    # broche de A0, A1, A2, A3
COLS = 7
COL_IDLE = 15                       # adresse sans colonne câblée (repos)
SETTLE_US = 50                      # stabilisation après changement de colonne

HEARTBEAT_PIN = 16                  # voyant de vie, allumé au niveau bas (§ 3.1.2)
LED_PINS = (17, 22, 27)             # canaux LED 0, 1, 2 (vers U7)
BTN15_PIN = 18
MCP_INT_PIN = 19
I2C_ID = 0
SDA_PIN = 20
SCL_PIN = 21
MCP_RESET_PIN = 28                  # actif à 0, tirage R32 vers +3V3
SHOCK_PIN = 26                      # ADC0

# MCP23017 (§ 3.2)
MCP_BUTTONS = 0x20                  # U3
MCP_TOYS = 0x21                     # U4
MCP_LIGHTS = 0x22                   # U5
MCP_ADDRS = (MCP_BUTTONS, MCP_TOYS, MCP_LIGHTS)

# Bit d'un registre 16 bits du MCP23017 : port A = bits 0 à 7, port B = bits 8 à 15.
# BTN1 à BTN7 = GPA0 à GPA6 ; BTN8 à BTN14 = GPB6 à GPB0.
BUTTON_BITS = (0, 1, 2, 3, 4, 5, 6, 14, 13, 12, 11, 10, 9, 8)
# Toys T1 à T8 = GPA0 à GPA7 de U4.
TOY_BITS = (0, 1, 2, 3, 4, 5, 6, 7)
# Voyants V1 à V8 = GPA0 à GPA7, V9 à V12 = GPB0 à GPB3 de U5.
LIGHT_BITS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)

# Sens des broches (IODIR, 1 = entrée). GPA7 et GPB7 jamais en entrée
# (erratum Microchip) ; broches non connectées en sorties au niveau bas.
IODIR = {
    MCP_BUTTONS: 0x7F7F,            # GPA0-6 et GPB0-6 en entrée ; GPA7, GPB7 en sortie
    MCP_TOYS: 0x0000,
    MCP_LIGHTS: 0x0000,
}
