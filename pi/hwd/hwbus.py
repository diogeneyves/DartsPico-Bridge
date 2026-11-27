# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Protocole entre hwd et le jeu sur MQTT (spécification § 9.5).

Module du Pi seulement (pas MicroPython). La future couche du jeu
(include/cpico.py) l'utilisera aussi : il ne dépend ni de paho ni de hwd.
"""

import pico_proto as P

TOPIC_BASE = "raspydarts"


def topics(base=TOPIC_BASE):
    """Noms des sujets pour une base donnée."""
    return {
        "events": base + "/hw/events",
        "cmd": base + "/hw/cmd",
        "status": base + "/hw/status",
        "led": base + "/hw/led/",          # suivi du numéro de canal
        "dmd_frame": base + "/hw/dmd/frame",   # images complètes du service de rendu
    }


KEEPALIVE_S = 5      # durée de maintien MQTT du jeu et de hwd (R6)
OFF = "OFF"          # commande « tout éteindre » (testament du jeu)

# Commandes du jeu relayées au Pico (grammaire du § 5.3, sans préfixe @n).
ALLOWED_COMMANDS = ("TOY", "TOYS", "TOYPULSE", "TOYSTOP", "LGT", "LGTS", "LGTPULSE",
                    "LGTSTOP", "LEDB", "LEDOFF", "CAL", "BTNPOLL", "SET")
# SET depuis le jeu : seulement ce qui n'est pas un paramètre du fichier de hwd.
ALLOWED_SET_KEYS = tuple("led%d_order" % ch for ch in range(P.LED_CHANNELS))


class BusError(ValueError):
    """Message du bus refusé."""


# ------------------------------------------------------- codes de segment

def segment_code(row, col):
    """Contact (ligne, colonne) -> code de [SectionKeys], par exemple R3C4."""
    if not (0 <= row < P.ROWS and 0 <= col < P.COLS):
        raise ValueError("contact hors matrice : %r %r" % (row, col))
    return "R%dC%d" % (row, col)


def parse_segment_code(code):
    """R3C4, R11C6 -> (ligne, colonne) ; BusError si le code n'est pas un contact de la matrice.

    Ligne et colonne : un ou deux chiffres, sans zéro en tête (R03C4 refusé : un seul code
    par contact, comme en écrit segment_code).
    """
    if not (code.startswith("R") and code.count("C") == 1):
        raise BusError("Invalid contact code : %r" % code)
    row_txt, col_txt = code[1:].split("C")
    for part in (row_txt, col_txt):
        if not 1 <= len(part) <= 2 or (len(part) == 2 and part[0] == "0"):
            raise BusError("Invalid contact code : %r" % code)
    try:
        row = P.parse_dec(row_txt, 0, P.ROWS - 1)
        col = P.parse_dec(col_txt, 0, P.COLS - 1)
    except P.ProtocolError:
        raise BusError("Invalid contact code : %r" % code) from None
    return row, col


# ------------------------------------------------- événements hwd -> jeu

def ev_dart(row, col, t, dur):
    return "D %s %d %d" % (segment_code(row, col), t, dur)


def ev_reject(row, col, dur):
    return "X %s %d" % (segment_code(row, col), dur)


def ev_button(n, state, t):
    return "B %d %d %d" % (n, state, t)


def ev_buttons_state(mask):
    return "BS %04X" % mask


def ev_miss(t):
    return "MISS %d" % t


def ev_shock(t, peak):
    return "S %d %d" % (t, peak)


def ev_error(code, text=""):
    return ("E %s %s" % (code, text)) if text else ("E %s" % code)


def parse_event(line):
    """Ligne de raspydarts/hw/events -> tuple ; BusError si invalide.

    ('D', code, t, dur) ; ('X', code, dur) ; ('B', n, state, t) ; ('BS', mask) ;
    ('MISS', t) ; ('S', t, peak) ; ('E', code, texte).
    """
    f = line.split(" ")
    kw, a = f[0], f[1:]
    try:
        if kw == "D" and len(a) == 3:
            parse_segment_code(a[0])
            return ("D", a[0], P.parse_dec(a[1], 0, P.TICKS_MASK),
                    P.parse_dec(a[2], 0, P.TICKS_MASK))
        if kw == "X" and len(a) == 2:
            parse_segment_code(a[0])
            return ("X", a[0], P.parse_dec(a[1], 0, P.TICKS_MASK))
        if kw == "B" and len(a) == 3:
            return ("B", P.parse_dec(a[0], 1, P.BUTTONS), P.parse_bool(a[1]),
                    P.parse_dec(a[2], 0, P.TICKS_MASK))
        if kw == "BS" and len(a) == 1:
            return ("BS", P.parse_hex(a[0], P.BUTTONS))
        if kw == "MISS" and len(a) == 1:
            return ("MISS", P.parse_dec(a[0], 0, P.TICKS_MASK))
        if kw == "S" and len(a) == 2:
            return ("S", P.parse_dec(a[0], 0, P.TICKS_MASK), P.parse_dec(a[1], 0, P.SHOCK_MAX))
        if kw == "E" and a:
            return ("E", a[0], " ".join(a[1:]))
    except P.ProtocolError as e:
        raise BusError("Invalid event (%s) : %r" % (e.code, line)) from None
    raise BusError("Unknown event : %r" % line)


# --------------------------------------------------- commandes jeu -> hwd

def parse_command(line):
    """Commande de raspydarts/hw/cmd -> (nom, arguments typés), ou ('OFF', ()).

    Validée par pico_proto (§ 5.3), sans préfixe. BusError si refusée : commande
    interdite (SAVE, REBOOT…), syntaxe, plage (TOY 9 à 16 compris : P4).
    """
    if line == OFF:
        return (OFF, ())
    if line.startswith("@"):
        raise BusError("Prefix @n reserved for hwd : %r" % line)
    try:
        tag, name, args = P.parse_command(line)
    except P.ProtocolError as e:
        raise BusError("Command rejected (%s) : %r" % (e.code, line)) from None
    if name not in ALLOWED_COMMANDS:
        raise BusError("Command not allowed from the game : %s" % name)
    if name == "SET" and args[0] not in ALLOWED_SET_KEYS:
        raise BusError("Parameter reserved for the hwd file : %s" % args[0])
    return (name, args)
