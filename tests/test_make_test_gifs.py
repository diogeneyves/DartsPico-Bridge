# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests de tools/make_test_gifs.py sans Pillow (§ 9.9, 48e complément) : liste des cas,
aide et erreurs en français, refus d'écrire dans le dépôt. Le rendu des fichiers
fabriqués est vérifié dans test_dmd_render.py (GeneratedMediaTest, sauté sans Pillow).
"""

import os
import subprocess
import sys
import tempfile
import unittest

import _paths  # noqa: F401
import make_test_gifs as MG

SCRIPT = MG.__file__


def run(*args, block_pillow=False):
    if block_pillow:
        code = ("import sys; sys.modules['PIL'] = None; sys.path.insert(0, %r); "
                "import make_test_gifs; sys.exit(make_test_gifs.main(%r))"
                % (os.path.dirname(SCRIPT), list(args)))
        cmd = [sys.executable, "-c", code]
    else:
        cmd = [sys.executable, SCRIPT, *args]
    return subprocess.run(cmd, capture_output=True, text=True)


class CasesTest(unittest.TestCase):
    def test_cases_of_complement_48(self):
        cases = MG.cases()
        self.assertEqual(len(cases), 6)
        self.assertEqual({size for _, size, _ in cases}, {(128, 32), (117, 32), (128, 30)})
        for size in MG.SIZES:
            self.assertEqual(sorted(n for _, s, n in cases if s == size), [False, True])
        self.assertEqual(len({name for name, _, _ in cases}), 6)
        self.assertTrue(all(name.endswith(".gif") for name, _, _ in cases))
        self.assertEqual(MG.DELAYS_CS, (0, 2, 10))
        self.assertEqual(len(MG.COLORS), len(MG.DELAYS_CS))
        self.assertEqual(len(set(MG.COLORS)), 3)        # Pillow fusionne les images identiques
        self.assertTrue(MG.PPM_NAME.endswith(".ppm"))


class CommandLineTest(unittest.TestCase):
    def test_help_in_french(self):
        out = run("--help")
        self.assertEqual(out.returncode, 0)
        self.assertIn("Utilisation : ", out.stdout)
        self.assertIn("affiche cette aide et quitte", out.stdout)
        self.assertIn("dossier de destination", out.stdout)
        self.assertNotIn("usage:", out.stdout)
        self.assertNotIn("positional arguments", out.stdout)

    def test_missing_folder(self):
        out = run()
        self.assertEqual(out.returncode, 2)
        self.assertIn("erreur : le dossier de destination est obligatoire", out.stderr)
        self.assertNotIn("required", out.stderr)

    def test_unknown_argument(self):
        out = run("a", "b")
        self.assertEqual(out.returncode, 2)
        self.assertIn("erreur : arguments inconnus", out.stderr)

    def test_refuses_repository(self):
        for dest in (MG.REPO, os.path.join(MG.REPO, "tests", "gifs")):
            with self.subTest(dest=dest):
                out = run(dest)
                self.assertEqual(out.returncode, 1)
                self.assertIn("rien n'est écrit dans le dépôt", out.stderr)
        self.assertFalse(os.path.exists(os.path.join(MG.REPO, "tests", "gifs")))

    def test_refuses_a_file(self):
        with tempfile.NamedTemporaryFile() as f:
            out = run(f.name)
        self.assertEqual(out.returncode, 1)
        self.assertIn("n'est pas un dossier", out.stderr)

    def test_without_pillow(self):
        with tempfile.TemporaryDirectory() as dest:
            out = run(os.path.join(dest, "gifs"), block_pillow=True)
            self.assertEqual(out.returncode, 1)
            self.assertIn("Pillow absent", out.stderr)
            self.assertFalse(os.path.exists(os.path.join(dest, "gifs")))
        self.assertEqual(out.stdout, "")


if __name__ == "__main__":
    unittest.main()
