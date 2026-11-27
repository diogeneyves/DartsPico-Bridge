# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Banc jetable de l'Interstate 75 W : mire 128 × 32 (2 dalles P4 64 × 32, balayage 1/16).

MicroPython de Pimoroni (i75w_rp2350) ; ce n'est pas le firmware de M4.
Lancement : mpremote run testcard.py ; la carte n'est alimentée que par l'USB du Pi
(complément 29, bornes à vis vides).
Quatre pages, 5 s chacune ; le bouton A passe à la suivante.

1. Dalles : dalle 1 fond rouge sombre, grand « 1 » ; dalle 2 fond bleu sombre, grand « 2 ».
   Cadre blanc d'un pixel autour de chaque dalle. Coins de chaque dalle :
   rouge = haut gauche, vert = haut droit, bleu = bas gauche, jaune = bas droit.
2. Abscisses : un trait vertical tous les 8 pixels, plus long tous les 32, repères 0, 32, 64, 96.
3. Couleurs : barres rouge, vert, bleu, blanc, puis dégradé (ordre des couleurs).
4. Balayage 1/16 : lignes 0 à 15 en rouge, 16 à 31 en vert ; lignes 0, 15, 16, 31 en blanc.
"""

import time

import hub75
from interstate75 import Interstate75, SWITCH_A

import bench_config as C

PAGE_MS = 5000


def page_panels(g, pen):
    for n in range(2):
        x0 = n * C.PANEL_WIDTH
        g.set_pen(pen(60, 0, 0) if n == 0 else pen(0, 0, 60))
        g.rectangle(x0, 0, C.PANEL_WIDTH, C.HEIGHT)
        g.set_pen(pen(255, 255, 255))
        g.line(x0, 0, x0 + C.PANEL_WIDTH - 1, 0)
        g.line(x0, C.HEIGHT - 1, x0 + C.PANEL_WIDTH - 1, C.HEIGHT - 1)
        g.line(x0, 0, x0, C.HEIGHT - 1)
        g.line(x0 + C.PANEL_WIDTH - 1, 0, x0 + C.PANEL_WIDTH - 1, C.HEIGHT - 1)
        label = str(n + 1)
        w = g.measure_text(label, 3)
        g.text(label, x0 + (C.PANEL_WIDTH - w) // 2, 6, -1, 3)
        x1 = x0 + C.PANEL_WIDTH - 4
        y1 = C.HEIGHT - 4
        for (x, y, rgb) in ((x0 + 1, 1, (255, 0, 0)), (x1, 1, (0, 255, 0)),
                            (x0 + 1, y1, (0, 0, 255)), (x1, y1, (255, 255, 0))):
            g.set_pen(pen(*rgb))
            g.rectangle(x, y, 3, 3)


def page_columns(g, pen):
    g.set_pen(pen(255, 255, 255))
    for x in range(0, C.WIDTH, 8):
        g.line(x, 0, x, 31 if x % 32 == 0 else 8)
    g.set_pen(pen(255, 255, 0))
    for x in range(0, C.WIDTH, 32):
        g.text(str(x), x + 2, 14, -1, 1)


def page_colors(g, pen):
    bar = C.WIDTH // 4
    for i, rgb in enumerate(((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255))):
        g.set_pen(pen(*rgb))
        g.rectangle(i * bar, 0, bar, 16)
    for x in range(C.WIDTH):
        v = x * 255 // (C.WIDTH - 1)
        g.set_pen(pen(v, v, v))
        g.line(x, 16, x, 23)
        g.set_pen(pen(v, 0, 255 - v))
        g.line(x, 24, x, 31)


def page_scan(g, pen):
    g.set_pen(pen(255, 0, 0))
    g.rectangle(0, 0, C.WIDTH, 16)
    g.set_pen(pen(0, 255, 0))
    g.rectangle(0, 16, C.WIDTH, 16)
    g.set_pen(pen(255, 255, 255))
    for y in (0, 15, 16, 31):
        g.line(0, y, C.WIDTH - 1, y)


PAGES = (page_panels, page_columns, page_colors, page_scan)


def main():
    i75 = Interstate75(display=Interstate75.DISPLAY_INTERSTATE75_128X32,
                       **C.hub75_options(hub75))
    g = i75.display
    g.set_font("bitmap8")
    pen = g.create_pen
    page = 0
    while True:
        g.set_pen(pen(0, 0, 0))
        g.clear()
        PAGES[page](g, pen)
        i75.update()
        start = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), start) < PAGE_MS:
            if i75.switch_pressed(SWITCH_A):
                while i75.switch_pressed(SWITCH_A):
                    time.sleep_ms(20)
                break
            time.sleep_ms(20)
        page = (page + 1) % len(PAGES)


main()
