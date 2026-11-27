#!/usr/bin/env python3
# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""GIF et PPM de test du service de rendu du DMD (spécification § 9.9, 48e complément).

Écrit dans le dossier donné (créé s'il manque), jamais dans le dépôt :

    python3 tools/make_test_gifs.py /tmp/gifs-dmd

Cas : tailles 128 × 32, 117 × 32 et 128 × 30, chacune avec et sans extension
NETSCAPE (6 GIF) ; chaque GIF a trois images unies, rouge, vert et bleu, de
délais 0, 2 et 10 (en 1/100 s) ; plus un PPM P6 de 117 × 32, blanc. Aucun
média de Raspy2DMD n'est utilisé. Pillow est demandé (lancé par les tests,
sautés sans Pillow).
"""

import argparse
import os
import sys

SIZES = ((128, 32), (117, 32), (128, 30))
DELAYS_CS = (0, 2, 10)                      # délais écrits, en 1/100 s
COLORS = ((255, 0, 0), (0, 255, 0), (0, 0, 255))
PPM_SIZE = (117, 32)
PPM_COLOR = (255, 255, 255)
PPM_NAME = "still_117x32.ppm"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Failure(Exception):
    """Erreur d'utilisation ou d'écriture, message en français."""


def gif_name(size, netscape):
    return "test_%dx%d_%s.gif" % (size[0], size[1], "netscape" if netscape else "sans_netscape")


def cases():
    """[(nom, taille, avec NETSCAPE)] des GIF, dans l'ordre d'écriture."""
    return [(gif_name(size, netscape), size, netscape) for size in SIZES for netscape in (False, True)]


def _inside(path, folder):
    path, folder = os.path.realpath(path), os.path.realpath(folder)
    return path == folder or path.startswith(folder + os.sep)


def generate(dest):
    """Écrit les 6 GIF et le PPM dans dest ; rend la liste des chemins écrits."""
    if _inside(dest, REPO):
        raise Failure("dossier %s dans le dépôt : rien n'est écrit dans le dépôt (48e complément)" % dest)
    if os.path.exists(dest) and not os.path.isdir(dest):
        raise Failure("%s existe et n'est pas un dossier" % dest)
    try:
        from PIL import Image
    except ImportError:
        raise Failure("Pillow absent : impossible de fabriquer les GIF de test") from None
    try:
        os.makedirs(dest, exist_ok=True)
        written = []
        for name, size, netscape in cases():
            frames = [Image.new("RGB", size, color) for color in COLORS]
            options = {"save_all": True, "append_images": frames[1:],
                       "duration": [d * 10 for d in DELAYS_CS]}     # Pillow : millisecondes
            if netscape:
                options["loop"] = 0                                   # NETSCAPE, en boucle
            path = os.path.join(dest, name)
            frames[0].save(path, **options)
            written.append(path)
        path = os.path.join(dest, PPM_NAME)
        Image.new("RGB", PPM_SIZE, PPM_COLOR).save(path, format="PPM")      # P6 (binaire)
        written.append(path)
    except OSError as error:
        raise Failure("écriture impossible dans %s : %s" % (dest, error.strerror or error)) from None
    return written


class _Formatter(argparse.HelpFormatter):
    def add_usage(self, usage, actions, groups, prefix=None):
        return super().add_usage(usage, actions, groups, "Utilisation : ")


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        if "required" in message:
            message = "le dossier de destination est obligatoire"
        elif "unrecognized arguments" in message:
            message = "arguments inconnus :" + message.split(":", 1)[1]
        self.exit(2, "%s : erreur : %s\n" % (self.prog, message))


def parse_args(argv=None):
    parser = _Parser(description="Fabrique les GIF et le PPM de test du DMD (§ 9.9, 48e complément), "
                                 "hors du dépôt.",
                     formatter_class=_Formatter, add_help=False)
    parser.add_argument("dest", metavar="DOSSIER", help="dossier de destination (créé s'il manque)")
    parser.add_argument("-h", "--help", action="help", help="affiche cette aide et quitte")
    parser._positionals.title = "argument"
    parser._optionals.title = "options"
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        written = generate(args.dest)
    except Failure as error:
        print("make_test_gifs : erreur : %s" % error, file=sys.stderr)
        return 1
    for path in written:
        print("écrit : %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
