# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Patch 0009 : textes du mode carte Pico dans les 6 langues (spécification, complément 23).

Les textes affichés par les patchs 0004 et 0007 passent par Screen.pico_text(clé, texte
français par défaut). Le patch 0009 ajoute leurs clés à la fin des 6 fichiers de langue
(locales/<langue>/LC_MESSAGES/pydarts.po) et recompile les .mo. Vérifié ici, sur un clone
temporaire de la copie de référence (lecture seule) où la série est appliquée jusqu'à 0009
(le patch 0010 change ensuite deux textes de la galerie, vérifiés par test_patch_0010) :
- le patch ne touche que les 12 fichiers de langue ; les .po sont seulement allongés
  (UTF-8, fins de ligne LF, même fin de fichier) ;
- chaque clé est présente une fois dans chaque langue, jamais avec un msgstr vide, et les
  trous ({button}, {device}) sont gardés ;
- les .mo donnent exactement les .po : les entrées de l'original, plus les nouvelles clés
  (lus par gettext.GNUTranslations, sans msgfmt) ;
- les clés appelées par pico_text dans cscreen.py sont celles des .po ; le texte français par
  défaut est le msgstr de fr_FR, et il n'apparaît nulle part ailleurs dans cscreen.py ;
- une traduction réelle (en_GB) arrive à l'écran, trous remplacés après la traduction.
Aucune dépendance à ~/RaspyDarts-work ; compatible avec le clone partiel de la CI (seuls les
fichiers de BASE et ceux des patchs sont lus). Sautés si la copie de référence est absente.
"""

import ast
import gettext
import os
import re
import shutil
import sys
import tempfile
import types
import unittest

import _orig
import _paths
import test_patch_0004
from test_patches import fresh_clone, run_script

ROOT = str(_paths.ROOT)
PATCH_0009 = os.path.join(ROOT, "pi", "patches", "0009-langues-mode-pico.patch")
LANGUAGES = ("de_DE", "en_GB", "es_ES", "fr_FR", "it_IT", "nl_NL")
KEYS = ("card-pico", "pico-ring-204", "pico-ring-244-before", "pico-ring-244-after",
        "pico-ring-none", "pico-ring-question", "pico-not-detected", "pico-absent",
        "pico-button-banned", "pico-button-leds-sub", "pico-no-coins", "pico-update-disabled")
HOLES = {"pico-not-detected": "{device}", "pico-button-banned": "{button}"}
ENTRY = re.compile(r'^msgid "([^"]*)"\nmsgstr "([^"]*)"$', re.M)


def po_path(base, language):
    return os.path.join(base, "locales", language, "LC_MESSAGES", "pydarts.po")


def mo_catalog(base, language):
    path = os.path.join(base, "locales", language, "LC_MESSAGES", "pydarts.mo")
    with open(path, "rb") as f:
        return gettext.GNUTranslations(f)._catalog


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def pico_text_calls(source):
    """Les (clé, texte par défaut) de cscreen.py : appels pico_text('clé', 'texte') et
    lignes [valeur, clé, texte] de PICO_RINGS."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "pico_text" and len(node.args) == 2
                and all(isinstance(a, ast.Constant) for a in node.args)):
            found.append((node.args[0].value, node.args[1].value))
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", None) == "PICO_RINGS"):
            for row in ast.literal_eval(node.value):
                found.append((row[1], row[2]))
    return found


def series_up_to_0009(where):
    """Dossier de patchs temporaire : BASE et series de pi/patches arrêtée à 0009 (les patchs
    sont copiés tels quels)."""
    source = os.path.join(ROOT, "pi", "patches")
    target = os.path.join(where, "patchs-0009")
    os.mkdir(target)
    shutil.copy(os.path.join(source, "BASE"), target)
    with open(os.path.join(source, "series"), encoding="utf-8") as f:
        names = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    names = names[:names.index(os.path.basename(PATCH_0009)) + 1]
    with open(os.path.join(target, "series"), "w", encoding="utf-8") as f:
        f.write("".join(n + "\n" for n in names))
    for name in names:
        shutil.copy(os.path.join(source, name), target)
    return target


@unittest.skipUnless(_orig.available(), "copie de RaspyDarts absente (%s)" % _orig.ORIG)
class Patch0009(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert _orig.status() == ""
        sys.dont_write_bytecode = True
        cls.tmp = tempfile.mkdtemp()
        cls.copy = fresh_clone(cls.tmp)
        out = run_script("--patches", series_up_to_0009(cls.tmp), cls.copy)
        assert out.returncode == 0, out.stderr
        cls.cscreen = read_bytes(os.path.join(cls.copy, "include", "cscreen.py")).decode("utf-8")
        cls.po = {l: read_bytes(po_path(cls.copy, l)).decode("utf-8") for l in LANGUAGES}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)
        assert _orig.status() == "", "le test a écrit dans la copie de référence"

    def entries(self, language):
        return ENTRY.findall(self.po[language])

    # --- fichiers du patch -------------------------------------------------------------------

    def test_patch_touches_only_language_files(self):
        text = read_bytes(PATCH_0009).decode("utf-8")
        files = sorted(l[len("diff --git a/"):].split(" b/")[0] for l in text.splitlines()
                       if l.startswith("diff --git a/"))
        expected = sorted("locales/%s/LC_MESSAGES/pydarts.%s" % (l, ext)
                          for l in LANGUAGES for ext in ("po", "mo"))
        self.assertEqual(files, expected)

    def test_po_only_appended_same_format(self):
        for language in LANGUAGES:
            with self.subTest(language=language):
                original = read_bytes(po_path(_orig.ORIG, language))
                patched = read_bytes(po_path(self.copy, language))
                self.assertTrue(patched.startswith(original))
                added = patched[len(original):].decode("utf-8")     # UTF-8 valide
                self.assertNotIn(b"\r", patched)
                self.assertTrue(patched.endswith(b'"\n') and not patched.endswith(b'\n\n'))
                self.assertEqual(len(ENTRY.findall(added)), len(KEYS))

    # --- clés et traductions -----------------------------------------------------------------

    def test_every_key_once_in_every_language_never_empty(self):
        for language in LANGUAGES:
            entries = self.entries(language)
            ids = [key for key, _ in entries]
            for key in KEYS:
                with self.subTest(language=language, key=key):
                    self.assertEqual(ids.count(key), 1)
                    text = dict(entries)[key]
                    self.assertTrue(text.strip())
                    self.assertNotEqual(text, key)

    def test_holes_kept(self):
        for language in LANGUAGES:
            entries = dict(self.entries(language))
            for key in KEYS:
                with self.subTest(language=language, key=key):
                    holes = re.findall(r"\{[a-z_]+\}", entries[key])
                    self.assertEqual(holes, [HOLES[key]] if key in HOLES else [])

    def test_mo_equals_po(self):
        for language in LANGUAGES:
            with self.subTest(language=language):
                original = mo_catalog(_orig.ORIG, language)
                patched = mo_catalog(self.copy, language)
                added = {key: text for key, text in self.entries(language) if key in KEYS}
                self.assertEqual(set(added), set(KEYS))
                self.assertEqual(patched, dict(original, **added))

    # --- cscreen.py (patchs 0004 et 0007) ----------------------------------------------------

    def test_pico_text_keys_and_french_defaults(self):
        calls = pico_text_calls(self.cscreen)
        self.assertEqual(sorted(key for key, _ in calls), sorted(KEYS))
        french = dict(self.entries("fr_FR"))
        for key, default in calls:
            with self.subTest(key=key):
                self.assertEqual(french[key], default)

    def test_french_texts_not_written_elsewhere(self):
        # Chaque texte français n'est écrit qu'une fois : comme texte par défaut de pico_text
        strings = [node.value for node in ast.walk(ast.parse(self.cscreen))
                   if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        for key, default in pico_text_calls(self.cscreen):
            with self.subTest(key=key):
                self.assertEqual(strings.count(default), 1)
        for old in ("Carte Pico absente", "Pas de monnayeur avec la carte Pico"):
            self.assertNotIn("['%s']" % old, self.cscreen)
            self.assertNotIn("'%s' if" % old, self.cscreen)

    def test_real_translation_on_screen(self):
        # en_GB réel (le .mo du clone patché) : la traduction, trous remplacés après elle
        translator = gettext.translation("pydarts", os.path.join(self.copy, "locales"),
                                         ["en_GB.UTF-8"])
        test_patch_0004.CI2C = os.path.join(self.copy, "include", "ci2c.py")
        saved = test_patch_0004.install_fakes()
        try:
            module = test_patch_0004.load_screen(os.path.join(self.copy, "include", "cscreen.py"),
                                                 "cscreen_patched_0009")
            screen = test_patch_0004.make_screen(module, hardware="pico")
            screen.lang = types.SimpleNamespace(translate=translator.gettext)
            self.assertEqual(screen.banned_text("PIN_UP"), "Button PIN_UP (Pico card) is banned.")
            self.assertEqual(screen.pico_text("pico-update-disabled", "défaut"),
                             "Update disabled in Pico card mode")
            # Clé inconnue des fichiers de langue : le texte français par défaut
            self.assertEqual(screen.pico_text("pico-inconnue", "défaut"), "défaut")
        finally:
            test_patch_0004.restore_fakes(saved)


if __name__ == "__main__":
    unittest.main()
