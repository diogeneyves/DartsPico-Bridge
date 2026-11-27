# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Module `micropython` simulé (tests du firmware sur PC)."""


def const(x):
    return x


def native(f):
    return f


def viper(f):
    return f


kbd_intr_value = 3


def kbd_intr(c):
    global kbd_intr_value
    kbd_intr_value = c


def alloc_emergency_exception_buf(n):
    pass
