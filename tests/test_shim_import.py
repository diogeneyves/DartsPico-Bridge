# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""« import rpi_ws281x » charge le remplaçant quand shims/ est en tête du chemin.

Vérifié dans un processus Python neuf (-I : ni PYTHONPATH ni dossier courant),
comme le lancement d'un serveur LED. Le jeu, lui, n'a jamais shims/ dans son
chemin : cpico n'importe pas rpi_ws281x.
"""

import os
import subprocess
import sys
import unittest

import _paths  # noqa: F401

ROOT = str(_paths.ROOT)
SHIMS = os.path.join(ROOT, "pi", "raspydarts_layer", "shims")
INCLUDE = os.path.join(ROOT, "pi", "raspydarts_layer", "include")


def run(code):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                         env=env)
    return out


class ShimImport(unittest.TestCase):
    def test_shims_first_loads_replacement(self):
        out = run("import sys; sys.path.insert(0, %r); import rpi_ws281x; "
                  "print(rpi_ws281x.__file__); print(rpi_ws281x.PixelStrip.__module__)" % SHIMS)
        self.assertEqual(out.returncode, 0, out.stderr)
        path, module = out.stdout.split()
        self.assertEqual(os.path.realpath(path), os.path.realpath(os.path.join(SHIMS, "rpi_ws281x.py")))
        self.assertEqual(module, "rpi_ws281x")

    def test_shims_first_wins_over_another_rpi_ws281x(self):
        # Un « vrai » rpi_ws281x placé plus loin dans le chemin n'est pas pris.
        import tempfile
        other = tempfile.mkdtemp()
        self.addCleanup(lambda: (os.remove(os.path.join(other, "rpi_ws281x.py")), os.rmdir(other)))
        with open(os.path.join(other, "rpi_ws281x.py"), "w") as f:
            f.write("REAL = True\n")
        out = run("import sys; sys.path[:0] = [%r, %r]; import rpi_ws281x; "
                  "print(hasattr(rpi_ws281x, 'REAL'))" % (SHIMS, other))
        self.assertEqual(out.stdout.strip(), "False", out.stderr)

    def test_game_side_never_imports_the_shim(self):
        shared = os.path.join(ROOT, "shared")
        hwd = os.path.join(ROOT, "pi", "hwd")
        out = run("import sys; sys.path[:0] = [%r, %r, %r]; import cpico; "
                  "print('rpi_ws281x' in sys.modules); "
                  "print(any(p.rstrip('/').endswith('shims') for p in sys.path))"
                  % (INCLUDE, hwd, shared))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.split(), ["False", "False"])


if __name__ == "__main__":
    unittest.main()
