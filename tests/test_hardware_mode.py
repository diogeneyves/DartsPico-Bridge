# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Patch 0001 : choix du mode au démarrage (read_hardware_mode, spécification § 9.4, v0.3.10).

La fonction est lue dans le patch lui-même (lignes ajoutées), sans copie de RaspyDarts :
ces tests tournent aussi dans la CI. Trois cas :
- pas de fichier de configuration et Blinka absent : mode Pico, avec un avertissement ;
- pas de fichier de configuration, Blinka présent : mode normal, comme avant ;
- fichier présent : lui seul décide, que Blinka soit là ou non.
"""

import os
import shutil
import sys
import tempfile
import types
import unittest

import _cplatform
import _paths  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_0001 = os.path.join(ROOT, "pi", "patches", "0001-craspberry-mode-pico.patch")


def function_source(patch_path, name):
    """Source d'une fonction entièrement ajoutée par le patch (lignes « + »)."""
    with open(patch_path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    start = lines.index("+def %s(path=None, blinka=None):" % name)
    body = []
    for line in lines[start:]:
        if not line.startswith("+"):
            break
        text = line[1:]
        if body and text and not text[0].isspace():
            break                                   # instruction suivante, au niveau du module
        body.append(text)
    return "\n".join(body) + "\n"


def load_function(config_path, platform=None):
    """read_hardware_mode du patch, avec un faux cpaths qui renvoie config_path et un faux
    cplatform (RaspyDarts 4.6.5 ; par défaut un Pi 4 avec GPIO)."""
    namespace = {"os": os, "sys": sys,
                 "cpaths": types.SimpleNamespace(config_file=lambda: config_path),
                 "cplatform": platform or _cplatform.make()}
    exec(compile(function_source(PATCH_0001, "read_hardware_mode"), PATCH_0001, "exec"),
         namespace)
    return namespace["read_hardware_mode"]


class HardwareModeAtStart(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = os.path.join(self.tmp, "raspydarts.cfg")
        self.read = load_function(self.cfg)

    def tearDown(self):
        shutil.rmtree(self.tmp)
        sys.modules.pop("board", None)

    def write(self, text):
        with open(self.cfg, "w", encoding="utf-8") as f:
            f.write(text)

    def test_no_config_without_blinka_is_pico(self):
        mode, warning = self.read(blinka=False)
        self.assertEqual(mode, "pico")
        self.assertIn("no Blinka", warning)
        self.assertIn(self.cfg, warning)
        self.assertIn("Pico card mode", warning)

    def test_no_config_with_blinka_is_normal(self):
        self.assertEqual(self.read(blinka=True), ("normal", None))

    def test_config_decides(self):
        for blinka in (False, True):
            for text, expected in (("[SectionAdvanced]\nhardware:pico\n", "pico"),
                                   ("[SectionAdvanced]\nhardware:normal\n", "normal"),
                                   ("[SectionAdvanced]\nrichard-mode:False\n", "normal"),
                                   ("", "normal")):
                with self.subTest(blinka=blinka, text=text):
                    self.write(text)
                    self.assertEqual(self.read(blinka=blinka), (expected, None))

    def test_explicit_pico_whatever_gpio(self):
        # Complément 21 : un hardware:pico écrit dans la configuration donne le mode Pico, que
        # la machine ait des GPIO ou non (has_gpio() n'est lu que sans fichier de configuration)
        self.write("[SectionAdvanced]\nhardware:pico\n")
        for model, gpio in ((_cplatform.PI4, True), (_cplatform.PI4, False), ("", False)):
            read = load_function(self.cfg, _cplatform.make(model=model, gpio=gpio))
            for blinka in (None, False, True):
                with self.subTest(model=model, gpio=gpio, blinka=blinka):
                    self.assertEqual(read(blinka=blinka), ("pico", None))

    def test_explicit_path(self):
        absent = os.path.join(self.tmp, "absent.cfg")
        self.assertEqual(self.read(absent, blinka=False)[0], "pico")
        self.assertEqual(self.read(absent, blinka=True), ("normal", None))

    def test_pc_without_gpio_stays_normal(self):
        # Spécification, 21e complément de la v0.3.14 : sur un PC (pas de GPIO), sans
        # configuration ni Blinka, mode normal comme RaspyDarts 4.6.5 sans patch
        read = load_function(self.cfg, _cplatform.make(model="", gpio=False))
        absent = os.path.join(self.tmp, "vide")
        os.makedirs(absent)
        saved = sys.path[:]
        sys.path[:] = [absent]                                 # aucun module board trouvable
        try:
            self.assertEqual(read(), ("normal", None))
        finally:
            sys.path[:] = saved
        # Pi sans accès aux GPIO : même règle
        read = load_function(self.cfg, _cplatform.make(gpio=False))
        self.assertEqual(read(), ("normal", None))

    def test_blinka_looked_for(self):
        # blinka non donné : module board cherché sans être importé
        sys.modules["board"] = types.ModuleType("board")      # Blinka déjà chargé
        self.assertEqual(self.read(), ("normal", None))
        del sys.modules["board"]
        absent = os.path.join(self.tmp, "vide")
        os.makedirs(absent)
        saved = sys.path[:]
        sys.path[:] = [absent]                                 # aucun module board trouvable
        try:
            self.assertEqual(self.read()[0], "pico")
        finally:
            sys.path[:] = saved

    def test_board_without_spec(self):
        # module board présent sans __spec__ (find_spec lèverait ValueError) : Blinka présent
        board = types.ModuleType("board")
        board.__spec__ = None
        sys.modules["board"] = board
        self.assertEqual(self.read(), ("normal", None))


if __name__ == "__main__":
    unittest.main()
