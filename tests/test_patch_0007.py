# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Patch 0007 : mises à jour automatiques neutralisées en mode carte Pico.

RaspyDarts 4.6.4 lit les versions publiées sur GitHub (include/cupdate.py) au démarrage
(check_for_updates) et par le bouton « Vérifier les mises à jour » de Paramètres, puis
remplace les fichiers qui diffèrent de l'archive, donc ceux des patchs 0001 à 0006. En mode
carte Pico ([SectionAdvanced] hardware = pico) :
- raspydarts.py : ni installation en attente (backup/update_pending.json) ni vérification au
  démarrage, une ligne de journal ;
- cscreen.py : le bouton affiche « Mise à jour désactivée en mode carte Pico » (clé
  pico-update-disabled, texte français par défaut) et revient au menu, sans cupdate ;
- cdmdmenu.py : boutons Raspy2DMD install, start, update et uninstall masqués, et la sélection
  reste en place quand aucune commande ne reste ; masqués aussi (complément 89) le texte
  raspy2dmd-remote-unknown et la note de version (version_note : écran principal et sous-menu
  « Affichage du score ») ; l'en-tête raspy2dmd-remote et le reste du menu ne changent pas.
En mode normal, mêmes résultats que l'original (copie de référence, lecture seule).
Aucun accès réseau : cupdate est remplacé par un enregistreur. Sautés si la copie de
référence est absente.
"""

import os
import shutil
import sys
import tempfile
import types
import unittest

import _orig
import _paths  # noqa: F401
import test_patch_0004 as t4
from test_patches import fresh_clone, run_script

ROOT = str(_paths.ROOT)
PATCH_0007 = os.path.join(ROOT, "pi", "patches", "0007-mises-a-jour-mode-pico.patch")
STARTUP = ("# An update downloaded in the background (Mise à jour prête : Demander) : installed now,\n",
           "##############\n# INIT\n##############\n")
DISABLED = "Mise à jour désactivée en mode carte Pico"
HIDDEN = ("raspy2dmd-install-here", "raspy2dmd-start", "raspy2dmd-check-update", "raspy2dmd-uninstall")
# Complément 89 : textes masqués en mode Pico (clés, rendues telles quelles par la fausse traduction)
REMOTE_UNKNOWN = "raspy2dmd-remote-unknown"
VERSION_NOTES = ("dmd-version-needed", "dmd-version-old", "dmd-version-needed-1624", "dmd-version-old-1624")


class Stop(Exception):
    """Arrêt d'une boucle une fois ce qu'on voulait relevé."""


class Logs:
    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        return lambda message, *a: self.lines.append((level, str(message)))


def read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8").replace("\r\n", "\n")


def segment(source, markers):
    start, end = markers
    i = source.index(start)
    return source[i:source.index(end, i)]


# ------------------------------------------------------------------ raspydarts.py, démarrage

class FakeConfig:
    def __init__(self, hardware, check):
        self.config = {"SectionAdvanced": {}, "SectionGlobals": {"check_for_updates": check}}
        if hardware is not None:
            self.config["SectionAdvanced"]["hardware"] = hardware

    def get_value(self, section, key, *args, **kwargs):
        return self.config[section].get(key, False)


class FakeCupdate:
    """cupdate simulé : chaque appel est noté."""

    def __init__(self, pending, last, pending_file):
        self.calls = []
        self.pending, self.last = pending, last
        self.PENDING_FILE = pending_file

    def load_pending(self, logs, version):
        self.calls.append("load_pending")
        return self.pending

    def apply_update(self, logs, update, progress=None):
        self.calls.append(("apply_update", update["version"]))
        return update["version"] + "_202611200000"

    def clear_pending(self):
        self.calls.append("clear_pending")

    def get_last(self, logs, version):
        self.calls.append("get_last")
        return self.last

    def download_update(self, logs, update, progress=None):
        self.calls.append("download_update")
        return True


class FakeDisplay:
    def __init__(self, answer):
        self.calls = []
        self.answer = answer

    def message(self, texts, *args, **kwargs):
        self.calls.append(("message", list(texts)))

    def wait_validation(self, key):
        self.calls.append(("wait_validation", key))
        return self.answer

    def update_mode(self):
        return "ask"

    def start_update_download(self, update):
        self.calls.append(("start_update_download", update["version"]))


@unittest.skipUnless(_orig.available(), "copie de RaspyDarts absente (%s)" % _orig.ORIG)
class Patch0007(unittest.TestCase):
    # Réponses de la fausse cdmdscore : styles connus (True, False ou None) et adresse distante
    known = True
    address = None
    paragraphs = None

    @classmethod
    def setUpClass(cls):
        assert _orig.status() == ""
        sys.dont_write_bytecode = True
        cls.tmp = tempfile.mkdtemp()
        cls.copy = fresh_clone(cls.tmp)
        out = run_script(cls.copy)
        assert out.returncode == 0, out.stderr
        cls.saved_modules = dict(sys.modules)
        cls.startup_patched = segment(read(os.path.join(cls.copy, "raspydarts.py")), STARTUP)
        cls.startup_original = segment(read(os.path.join(_orig.ORIG, "raspydarts.py")), STARTUP)
        # cscreen.py, original et patché, avec les modules factices du test du patch 0004
        t4.CI2C = os.path.join(cls.copy, "include", "ci2c.py")
        cls.saved_fakes = t4.install_fakes()
        cls.screen_original = t4.load_screen(t4.ORIGINAL, "cscreen_original_0007")
        cls.screen_patched = t4.load_screen(os.path.join(cls.copy, "include", "cscreen.py"),
                                            "cscreen_patched_0007")
        # Les modules factices restent en place : pico_mode importe include.ci2c à l'appel
        # (tearDownClass rétablit sys.modules)
        cls.dmdmenu = {name: os.path.join(root, "include", "cdmdmenu.py")
                       for name, root in (("original", _orig.ORIG), ("patched", cls.copy))}

    @classmethod
    def tearDownClass(cls):
        sys.modules.clear()
        sys.modules.update(cls.saved_modules)
        shutil.rmtree(cls.tmp)
        assert _orig.status() == "", "le test a écrit dans la copie de référence"

    # --- fichiers du patch ---------------------------------------------------------------------

    def test_patch_files_and_line_endings(self):
        with open(PATCH_0007, "rb") as f:
            text = f.read().decode("utf-8")
        files = [l[len("+++ b/"):].strip() for l in text.splitlines() if l.startswith("+++ b/")]
        self.assertEqual(files, ["include/cdmdmenu.py", "include/cscreen.py", "raspydarts.py"])
        # Fins de ligne de la base gardées : les trois en LF depuis RaspyDarts 4.6.5
        for rel, crlf in (("include/cscreen.py", False), ("raspydarts.py", False),
                          ("include/cdmdmenu.py", False)):
            with open(os.path.join(self.copy, rel), "rb") as f:
                data = f.read()
            bare_lf = data.count(b"\n") - data.count(b"\r\n")
            with self.subTest(rel=rel):
                if crlf:
                    self.assertEqual(bare_lf, 0, "fin de ligne LF dans un fichier CRLF")
                else:
                    self.assertEqual(data.count(b"\r\n"), 0)

    # --- raspydarts.py : démarrage -------------------------------------------------------------

    def run_startup(self, source, hardware, check=True, pending=None, last=None, answer=False,
                    pending_exists=False):
        pending_file = os.path.join(self.tmp, "update_pending.json")
        if pending_exists:
            with open(pending_file, "w") as f:
                f.write("{}")
        elif os.path.exists(pending_file):
            os.remove(pending_file)
        package = types.ModuleType("include")
        package.__path__ = [os.path.join(self.copy, "include")]
        saved = dict(sys.modules)
        for name in [n for n in sys.modules if n == "include" or n.startswith("include.")]:
            del sys.modules[name]
        sys.modules["include"] = package
        cupdate, display, logs = FakeCupdate(pending, last, pending_file), FakeDisplay(answer), Logs()
        namespace = {"Config": FakeConfig(hardware, check), "cupdate": cupdate, "display": display,
                     "logs": logs, "version": "V4.6.4_202611132000", "os": os,
                     "update_progress": lambda *a: None, "game_type": "game"}
        try:
            exec(source, namespace)
        finally:
            sys.modules.clear()
            sys.modules.update(saved)
        return {"cupdate": cupdate.calls, "display": display.calls, "logs": logs.lines,
                "version": namespace["version"], "game_type": namespace["game_type"]}

    CASES = [dict(), dict(check=False), dict(pending={"version": "V4.6.5"}),
             dict(last={"version": "V4.6.5"}), dict(last={"version": "V4.6.5"}, answer=True)]

    def test_startup_normal_mode_identical_to_original(self):
        for hardware in (None, "normal"):
            for case in self.CASES:
                with self.subTest(hardware=hardware, case=case):
                    original = self.run_startup(self.startup_original, hardware, **case)
                    patched = self.run_startup(self.startup_patched, hardware, **case)
                    self.assertEqual(patched, original)

    def test_startup_normal_mode_really_updates(self):
        # Garde-fou du test : en mode normal, la mise à jour en attente est bien installée
        result = self.run_startup(self.startup_patched, "normal", pending={"version": "V4.6.5"})
        self.assertIn(("apply_update", "V4.6.5"), result["cupdate"])
        self.assertEqual(result["game_type"], "restart")
        result = self.run_startup(self.startup_patched, "normal", last={"version": "V4.6.5"})
        self.assertIn("get_last", result["cupdate"])

    def test_startup_pico_mode_nothing(self):
        for case in self.CASES:
            with self.subTest(case=case):
                result = self.run_startup(self.startup_patched, "pico", **case)
                self.assertEqual(result["cupdate"], [])
                self.assertEqual(result["display"], [])
                self.assertEqual(result["game_type"], "game")
                self.assertEqual(result["version"], "V4.6.4_202611132000")
                infos = [m for level, m in result["logs"] if level == "info"]
                self.assertEqual(len(infos), 1)
                self.assertIn("Pico card mode", infos[0])
                self.assertFalse([m for level, m in result["logs"] if level == "warning"])

    def test_startup_pico_mode_pending_file_left_and_logged(self):
        result = self.run_startup(self.startup_patched, "pico", pending={"version": "V4.6.5"},
                                  pending_exists=True)
        self.assertEqual(result["cupdate"], [])
        warnings = [m for level, m in result["logs"] if level == "warning"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("update_pending.json", warnings[0])
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "update_pending.json")))

    def test_startup_pico_mode_from_craspberry(self):
        # Mode lu par craspberry (PICO), même si la configuration ne le dit pas
        package = types.ModuleType("include")
        package.__path__ = [os.path.join(self.copy, "include")]
        saved = dict(sys.modules)
        for name in [n for n in sys.modules if n == "include" or n.startswith("include.")]:
            del sys.modules[name]
        sys.modules["include"] = package
        sys.modules["include.craspberry"] = types.SimpleNamespace(PICO=True)
        cupdate = FakeCupdate({"version": "V4.6.5"}, None, os.path.join(self.tmp, "absent.json"))
        namespace = {"Config": FakeConfig(None, True), "cupdate": cupdate, "display": FakeDisplay(True),
                     "logs": Logs(), "version": "V4.6.4", "os": os, "update_progress": lambda *a: None,
                     "game_type": "game"}
        try:
            exec(self.startup_patched, namespace)
        finally:
            sys.modules.clear()
            sys.modules.update(saved)
        self.assertEqual(cupdate.calls, [])

    # --- cscreen.py : bouton « Vérifier les mises à jour » ------------------------------------

    def make_misc_screen(self, module, hardware, translations=None):
        screen = t4.make_screen(module, hardware)
        if translations is not None:
            screen.lang = types.SimpleNamespace(translate=lambda key: translations.get(key, key))
        chosen = [{"action": "update"}, {"action": "back"}]
        screen.dmd = types.SimpleNamespace(send_text=lambda text: None)
        menus = types.SimpleNamespace(buttons=lambda name: list(chosen),
                                      result=lambda button: "resultat:" + button["action"])
        screen.menus = lambda: menus
        screen.run_menu = lambda name, buttons, all_buttons: chosen.pop(0)
        screen.update_screen = lambda *a, **k: None
        return screen

    def fake_update(self):
        calls = []

        def get_version(logs):
            calls.append("get_version")
            raise Stop()

        module = types.SimpleNamespace(get_version=get_version, get_last=get_version,
                                       load_pending=get_version)
        return [module], calls

    def test_button_pico_mode_message_and_back(self):
        screen = self.make_misc_screen(self.screen_patched, "pico")
        update, calls = self.fake_update()
        self.assertEqual(screen.miscellaneous(update), "resultat:back")
        self.assertEqual(calls, [])
        self.assertEqual(screen.messages, [[DISABLED]])
        self.assertTrue(any("Pico card mode" in line for line in screen.logs.lines))

    def test_button_pico_mode_translated(self):
        screen = self.make_misc_screen(self.screen_patched, "pico",
                                       {"pico-update-disabled": "Update disabled in Pico card mode"})
        update, calls = self.fake_update()
        self.assertEqual(screen.miscellaneous(update), "resultat:back")
        self.assertEqual(screen.messages, [["Update disabled in Pico card mode"]])

    def test_pico_text(self):
        screen = self.make_misc_screen(self.screen_patched, "pico", {"cle": "traduit"})
        self.assertEqual(screen.pico_text("cle", "défaut"), "traduit")
        self.assertEqual(screen.pico_text("inconnue", "défaut"), "défaut")

    def test_button_normal_mode_checks_like_original(self):
        for hardware in (None, "normal"):
            for module in (self.screen_original, self.screen_patched):
                with self.subTest(hardware=hardware, module=module.__name__):
                    screen = self.make_misc_screen(module, hardware)
                    update, calls = self.fake_update()
                    with self.assertRaises(Stop):
                        screen.miscellaneous(update)
                    self.assertEqual(calls, ["get_version"])
                    self.assertEqual(screen.messages, [])

    def test_other_buttons_unchanged(self):
        for hardware in (None, "pico"):
            screen = self.make_misc_screen(self.screen_patched, hardware)
            screen.run_menu = lambda name, buttons, all_buttons: {"action": "back"}
            self.assertEqual(screen.miscellaneous(self.fake_update()[0]), "resultat:back")

    # --- cdmdmenu.py : boutons Raspy2DMD -------------------------------------------------------

    def load_dmdmenu(self, which):
        """cdmdmenu.py chargé avec des modules factices ; renvoie (module, état d'avant)."""
        saved = dict(sys.modules)

        class Rect:
            def __init__(self, x, y, width, height):
                self.x, self.y, self.width, self.height = x, y, width, height
                self.centerx = x + width // 2

            def __iter__(self):
                return iter((self.x, self.y, self.width, self.height))

        pygame = types.ModuleType("pygame")
        pygame.Rect = Rect
        pygame.transform = types.SimpleNamespace(scale=lambda picture, size: picture)
        pygame.image = types.SimpleNamespace(fromstring=lambda data, size, mode: "image")
        picture = types.SimpleNamespace(size=(1, 1), tobytes=lambda: b"")
        picture.convert = lambda mode: picture
        qrcode = types.SimpleNamespace(make=lambda url: picture)
        include = types.ModuleType("include")
        include.__path__ = []
        cdmdscore = types.SimpleNamespace(local=lambda: self.here, general=lambda: {},
                                          remote_address=lambda: self.address,
                                          styles_known=lambda: self.known,
                                          style_known=lambda style: self.known,
                                          NEEDS={"modern": (1, 6, 2, 3), "duck": (1, 6, 2, 3),
                                                 "ring": (1, 6, 2, 4)},
                                          NEEDED=(1, 6, 2, 3), local_version=lambda: "1.6.2.2")

        def paragraph(screen, text, *a, **k):
            if self.paragraphs is not None:
                self.paragraphs.append(text)

        cmenueditor = types.SimpleNamespace(paragraph=paragraph,
                                            value_item=lambda screen, rect, text, value, chosen: None)
        cscreen = types.SimpleNamespace(DOWN_KEYS=["down"], ENTER_KEYS=["enter"],
                                        ESCAPE_KEYS=["escape"], UP_KEYS=["up"])
        include.cdmdscore, include.cmenueditor = cdmdscore, cmenueditor
        include.cgallery = types.SimpleNamespace(local_ip=lambda: "192.168.1.2")
        sys.modules.update({"pygame": pygame, "include": include, "include.cdmdscore": cdmdscore,
                            "include.cmenueditor": cmenueditor, "include.cgallery": include.cgallery,
                            "include.cscreen": cscreen, "qrcode": qrcode})
        import importlib.util
        spec = importlib.util.spec_from_file_location("cdmdmenu_" + which, self.dmdmenu[which])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, saved

    def dmd_screen(self, pico, running, keys):
        drawn = []
        screen = types.SimpleNamespace()
        screen.res = {"x": 1920, "y": 1080}
        screen.margin = 10
        screen.colorset = {"menu-text-white": "w", "menu-ko": "ko", "menu-ok": "ok",
                           "menu-selected": "s", "menu-buttons": "b"}
        screen.lang = types.SimpleNamespace(translate=lambda key: key)
        screen.config = types.SimpleNamespace(get_value=lambda *a, **k: False)
        screen.raspy2dmd_installing = lambda: False
        screen.raspy2dmd_running = lambda: running
        screen.RASPY2DMD_PICO_SEUL = os.path.join(self.tmp, "absent")
        for name in ("display_background", "menu_header", "blit", "blit_text", "update_screen"):
            setattr(screen, name, lambda *a, **k: None)
        screen.display_button = lambda *a, **k: (0, 0, 1, 1)
        screen.is_clicked = lambda zones, key: None
        keys = list(keys)
        screen.rpi = types.SimpleNamespace(listen_inputs=lambda *a, **k: keys.pop(0))
        if pico is not None:
            screen.pico_mode = lambda: pico
        return screen, drawn

    def dmd_commands(self, which, pico, here, running, keys=("escape",)):
        """Les textes des commandes affichées par raspy2dmd_menu, à chaque tour de boucle."""
        self.here = here
        module, saved = self.load_dmdmenu(which)
        try:
            screen, drawn = self.dmd_screen(pico, running, keys)
            turns = []

            def button(screen_, rect, text, chosen):
                turns[-1].append(text)

            module.button = button
            original_header = screen.menu_header
            screen.menu_header = lambda *a, **k: (turns.append([]), original_header())
            module.raspy2dmd_menu(screen, None)
            return turns
        finally:
            sys.modules.clear()
            sys.modules.update(saved)

    def test_dmd_pico_mode_hides_raspy2dmd_buttons(self):
        for here in (False, True):
            for running in (False, True):
                with self.subTest(here=here, running=running):
                    turns = self.dmd_commands("patched", True, here, running)
                    shown = turns[0]
                    self.assertFalse(set(shown) & set(HIDDEN), shown)
                    normal = self.dmd_commands("original", None, here, running)[0]
                    self.assertEqual(shown, [t for t in normal if t not in HIDDEN])

    def test_dmd_normal_mode_identical_to_original(self):
        for here in (False, True):
            for running in (False, True):
                for pico in (None, False):
                    with self.subTest(here=here, running=running, pico=pico):
                        self.assertEqual(self.dmd_commands("patched", pico, here, running),
                                         self.dmd_commands("original", None, here, running))

    def test_dmd_normal_mode_really_shows_install(self):
        # Garde-fou du test : le bouton d'installation est bien là en mode normal
        self.assertIn("raspy2dmd-install-here", self.dmd_commands("patched", False, False, False)[0])

    def test_dmd_pico_mode_no_command_keeps_selection(self):
        # Mode Pico, Raspy2DMD installé ici mais arrêté : aucune commande ; gauche et droite
        # ne plantent pas (l'original prenait other[-1] dans une liste vide). Raspy2DMD absent
        # d'ici : il est supposé en marche sur un autre Raspberry, ses commandes de test restent
        turns = self.dmd_commands("patched", True, True, False, keys=("right", "left", "escape"))
        self.assertEqual(turns, [["dmd-score-by-game"]] * 3)

    # --- cdmdmenu.py : textes masqués en mode Pico (complément 89) -------------------------------

    def dmd_paragraphs(self, which, pico, here, known, address=None, running=True):
        """Les paragraphes dessinés au premier tour de raspy2dmd_menu."""
        self.known, self.address, self.paragraphs = known, address, []
        try:
            self.dmd_commands(which, pico, here, running)
            return self.paragraphs
        finally:
            self.known, self.address, self.paragraphs = True, None, None

    def test_dmd_pico_mode_hides_remote_texts(self):
        # Raspy2DMD absent d'ici, aucune adresse distante (cas de la carte Pico)
        for known in (None, False, True):
            with self.subTest(known=known):
                shown = self.dmd_paragraphs("patched", True, False, known)
                self.assertNotIn(REMOTE_UNKNOWN, shown)
                self.assertFalse(set(shown) & set(VERSION_NOTES), shown)

    def test_dmd_normal_mode_shows_remote_texts(self):
        # Garde-fou du test : en mode normal, les deux textes sont bien dessinés
        shown = self.dmd_paragraphs("patched", False, False, None)
        self.assertIn(REMOTE_UNKNOWN, shown)
        self.assertIn("dmd-version-needed", shown)

    def test_dmd_paragraphs_normal_mode_identical_to_original(self):
        for here in (False, True):
            for known in (None, False, True):
                for address in (None, "192.168.1.50"):
                    for pico in (None, False):
                        with self.subTest(here=here, known=known, address=address, pico=pico):
                            self.assertEqual(
                                self.dmd_paragraphs("patched", pico, here, known, address),
                                self.dmd_paragraphs("original", None, here, known, address))

    def test_version_note_pico_mode_none(self):
        # Écran principal (style None) et sous-menu « Affichage du score » (style choisi)
        self.here = False
        for which in ("patched", "original"):
            for known in (None, False):
                self.known = known
                module, saved = self.load_dmdmenu(which)
                try:
                    for style in (None, "modern", "ring"):
                        with self.subTest(which=which, known=known, style=style):
                            pico = types.SimpleNamespace(pico_mode=lambda: True,
                                                         lang=types.SimpleNamespace(translate=lambda k: k))
                            normal = types.SimpleNamespace(pico_mode=lambda: False, lang=pico.lang)
                            bare = types.SimpleNamespace(lang=pico.lang)
                            expected = module.version_note(bare, style)
                            self.assertIn(expected.split(" ")[0], VERSION_NOTES)
                            self.assertEqual(module.version_note(normal, style), expected)
                            if which == "patched":
                                self.assertIsNone(module.version_note(pico, style))
                finally:
                    self.known = True
                    sys.modules.clear()
                    sys.modules.update(saved)

    def test_score_submenu_uses_version_note(self):
        # Le sous-menu « Affichage du score » n'a pas de note à lui : seulement version_note
        source = read(self.dmdmenu["patched"])
        self.assertEqual(source.count("dmd-version-"), 4)
        self.assertEqual(source.count("note = version_note(screen"), 2)

    def test_pico_commands(self):
        self.here = False
        module, saved = self.load_dmdmenu("patched")
        try:
            commands = [("message", "m"), ("start", "s"), ("update", "u"), ("uninstall", "x"),
                        ("install", "i"), ("try", "t")]
            pico = types.SimpleNamespace(pico_mode=lambda: True)
            normal = types.SimpleNamespace(pico_mode=lambda: False)
            self.assertEqual(module.pico_commands(pico, commands), [("message", "m"), ("try", "t")])
            self.assertIs(module.pico_commands(normal, commands), commands)
            self.assertIs(module.pico_commands(types.SimpleNamespace(), commands), commands)
            self.assertEqual(module.PICO_HIDDEN_COMMANDS, ("install", "start", "update", "uninstall"))
        finally:
            sys.modules.clear()
            sys.modules.update(saved)


if __name__ == "__main__":
    unittest.main()
