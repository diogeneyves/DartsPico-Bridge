# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Patch 0004 (include/cscreen.py) : écrans du jeu en mode carte Pico.

- calibration_needed : en mode Pico, l'étalonnage est demandé tant qu'aucun segment n'est
  connu (la cible n'utilise aucune broche GPIO du Pi, rpi.inputs et rpi.outputs sont vides) ;
- monnayeur : bouton retiré de Paramétrage, et coins_menu refusé, en mode Pico ;
- textes « MCP » (sous-titre de LED des boutons, bouton banni) remplacés en mode Pico ;
- menus Boutons et Toys (test_toys_menu contre le faux Pico) en mode Pico ;
- assistant : choix « Carte Pico » (préréglage, LED sur les canaux 0 et 1, table de l'anneau) ;
- calibrate_target : CAL 0 au début en mode Pico, jamais CAL 1.
En mode normal, le cscreen.py patché donne les mêmes résultats que l'original.

cscreen.py est chargé avec des modules factices (pygame, netifaces, PIL et les modules
include.* qu'il importe) : ces tests tournent sans pygame. Le cscreen.py patché vient d'un
clone temporaire de la copie de référence, au commit BASE, où la série pi/patches/ est
appliquée (tools/apply_patches.sh, comme test_patch_0003) ; l'original est lu dans la copie
de référence (lecture seule). Sautés si la copie de référence est absente.
"""

import importlib.util
import os
import shutil
import sys
import tempfile
import types
import unittest
from collections import OrderedDict
from unittest import mock

import _cplatform
import _orig
import _paths
from test_patches import fresh_clone, run_script

sys.path.insert(0, os.path.join(str(_paths.ROOT), "pi", "raspydarts_layer", "include"))
import cpico  # noqa: E402

ORIGINAL = os.path.join(_orig.ORIG, "include", "cscreen.py")
# Dans le clone patché (Patch0004.setUpClass)
PATCHED = None
CI2C = None

# Modules include.* importés par cscreen.py au chargement
INCLUDE_MODULES = ("ColorSets", "cpaths", "ccamera", "ccoins", "cmenus", "cfonts", "cmenustyle",
                   "cfavorites", "cbganim", "cactivity")
SEGMENTS = ["S%d" % i for i in range(1, 63)] + ["X1", "X2", "X3", "X4"]


def fake_module(name, **attrs):
    module = types.ModuleType(name)

    def getattr_(attr):
        if attr.startswith("__"):
            raise AttributeError(attr)
        return mock.MagicMock(name="%s.%s" % (name, attr))

    module.__getattr__ = getattr_
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


class Surface:
    """pygame.Surface factice : Screen en hérite."""


def install_fakes():
    """Modules factices dans sys.modules. Renvoie l'état d'avant (restore_fakes)."""
    saved = dict(sys.modules)
    pygame = fake_module("pygame", Surface=Surface, version=types.SimpleNamespace(vernum=(2, 6, 1)))
    pygame.locals = fake_module("pygame.locals")
    sys.modules.update({"pygame": pygame, "pygame.locals": pygame.locals,
                        "netifaces": fake_module("netifaces"), "PIL": fake_module("PIL")})
    include = types.ModuleType("include")
    include.__path__ = []
    sys.modules["include"] = include
    _cplatform.install(include, sys.modules)        # RaspyDarts 4.6.5 : un Pi 4 avec GPIO
    for name in INCLUDE_MODULES:
        module = fake_module("include." + name)
        if name == "ColorSets":
            module = types.ModuleType("include.ColorSets")
        sys.modules["include." + name] = module
        setattr(include, name, module)
    sys.modules.pop("include.cqrcode", None)
    sys.modules.pop("include.craspberry", None)
    # ci2c.pico_mode réel (patch 0003), lu dans le clone patché
    spec = importlib.util.spec_from_file_location("include.ci2c", CI2C)
    ci2c = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ci2c)
    sys.modules["include.ci2c"] = ci2c
    include.ci2c = ci2c
    return saved


def restore_fakes(saved):
    sys.modules.clear()
    sys.modules.update(saved)


def load_screen(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Logs:
    def __init__(self):
        self.lines = []

    def info(self, text):
        self.lines.append(text)

    debug = warning = error = info


def make_screen(module, hardware=None, inputs=(), outputs=(), keys=None):
    """Un Screen sans pygame : seuls les attributs lus par les méthodes testées."""
    screen = module.Screen.__new__(module.Screen)
    config = {"SectionKeys": dict(keys or {})}
    if hardware is not None:
        config["SectionAdvanced"] = {"hardware": hardware}
    screen.config = types.SimpleNamespace(config=config, keys=list(SEGMENTS))
    screen.rpi = types.SimpleNamespace(inputs=list(inputs), outputs=list(outputs))
    screen.logs = Logs()
    screen.lang = types.SimpleNamespace(translate=lambda key: key)
    screen.colorset = {"menu-ko": "ko", "menu-ok": "ok"}
    screen.messages = []
    screen.message = lambda texts, *args, **kwargs: screen.messages.append(list(texts))
    return screen


def rig_cleanup():
    """Comme EndToEnd.tearDown (test_layer_integration) : plus de transport pour rpi_ws281x."""
    import rpi_ws281x
    rpi_ws281x.set_transport(None)


class Stop(Exception):
    """Arrêt d'une boucle d'écran une fois ce qu'on voulait relevé."""


@unittest.skipUnless(_orig.available(), "copie de RaspyDarts absente (%s)" % _orig.ORIG)
class Patch0004(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global PATCHED, CI2C
        assert _orig.status() == ""
        sys.dont_write_bytecode = True
        cls.tmp = tempfile.mkdtemp()
        cls.copy = fresh_clone(cls.tmp)
        out = run_script(cls.copy)
        assert out.returncode == 0, out.stderr
        PATCHED = os.path.join(cls.copy, "include", "cscreen.py")
        CI2C = os.path.join(cls.copy, "include", "ci2c.py")
        cls.saved = install_fakes()
        cls.original = load_screen(ORIGINAL, "cscreen_original")
        cls.patched = load_screen(PATCHED, "cscreen_patched")

    @classmethod
    def tearDownClass(cls):
        restore_fakes(cls.saved)
        shutil.rmtree(cls.tmp)
        assert _orig.status() == "", "le test a écrit dans la copie de référence"

    def tearDown(self):
        sys.modules.pop("include.craspberry", None)

    # --- calibration_needed -------------------------------------------------------------

    def test_calibration_needed_pico_no_segment(self):
        for keys in ({}, {s: "" for s in SEGMENTS}, {"X1": "R0C0"}):
            with self.subTest(keys=keys):
                screen = make_screen(self.patched, hardware="pico", keys=keys)
                self.assertTrue(screen.calibration_needed())

    def test_calibration_needed_pico_segments_known(self):
        screen = make_screen(self.patched, hardware="pico", keys={"S1": "R0C0"})
        self.assertFalse(screen.calibration_needed())

    def test_calibration_needed_pico_from_craspberry(self):
        # Mode lu par craspberry (PICO), même si la configuration ne le dit pas
        sys.modules["include.craspberry"] = types.SimpleNamespace(PICO=True)
        self.assertTrue(make_screen(self.patched).calibration_needed())

    def test_calibration_needed_normal_identical(self):
        wirings = [((), ()), ((5, 6), ()), ((), (7,)), ((5, 6), (7, 8))]
        keysets = [{}, {"S1": "2410"}, {"X1": "2410"}, {s: "" for s in SEGMENTS}]
        for hardware in (None, "normal", "autre"):
            for inputs, outputs in wirings:
                for keys in keysets:
                    with self.subTest(hardware=hardware, inputs=inputs, outputs=outputs, keys=keys):
                        results = [make_screen(m, hardware, inputs, outputs, keys).calibration_needed()
                                   for m in (self.original, self.patched)]
                        self.assertEqual(results[1], results[0])

    def test_calibration_needed_normal_really_asks(self):
        # Garde-fou du test : le mode normal demande bien l'étalonnage avec une cible câblée
        self.assertTrue(make_screen(self.patched, "normal", (5,), (7,)).calibration_needed())

    # --- monnayeur ------------------------------------------------------------------------

    SETTINGS = [{"id": "setup-buttons", "action": "setup_buttons"},
                {"id": "coins", "action": "coins", "text": "coins"},
                {"id": "test-toys", "action": "test_toys", "visible": False}]

    def run_setup_menu(self, module, hardware):
        screen = make_screen(module, hardware)
        calls = []

        class Menus:
            def buttons(_self, name):
                return [dict(button) for button in self.SETTINGS]

            def result(_self, button):
                return button["id"]

        screen.menus = Menus
        screen.run_menu = lambda name, items, everything: calls.append((name, items, everything)) or items[0]
        result = screen.setup_menu()
        return result, calls

    def test_setup_menu_pico_without_coins(self):
        result, calls = self.run_setup_menu(self.patched, "pico")
        self.assertEqual(result, "setup-buttons")
        (name, items, everything), = calls
        self.assertEqual(name, "settings")
        self.assertEqual([b["id"] for b in items], ["setup-buttons"])
        # Ni bouton ni raccourci (everything : boutons masqués compris)
        self.assertEqual([b["id"] for b in everything], ["setup-buttons", "test-toys"])

    def test_setup_menu_normal_identical(self):
        for hardware in (None, "normal"):
            with self.subTest(hardware=hardware):
                self.assertEqual(self.run_setup_menu(self.patched, hardware),
                                 self.run_setup_menu(self.original, hardware))
                _result, calls = self.run_setup_menu(self.patched, hardware)
                self.assertIn("coins", [b["id"] for b in calls[0][1]])

    def run_coins_menu(self, module, hardware):
        screen = make_screen(module, hardware)
        screen.rpi.coins = types.SimpleNamespace(settings={"mode": "off", "password": ""})
        asked = []
        screen.coins_password = lambda coins: asked.append(coins) or False
        result = screen.coins_menu()
        return screen, asked, result

    def test_coins_menu_pico_refused(self):
        screen, asked, result = self.run_coins_menu(self.patched, "pico")
        self.assertIsNone(result)
        self.assertEqual(asked, [])
        self.assertEqual(screen.messages, [["Pas de monnayeur avec la carte Pico"]])

    def test_coins_menu_normal_identical(self):
        for module in (self.original, self.patched):
            with self.subTest(module=module.__name__):
                screen, asked, result = self.run_coins_menu(module, "normal")
                self.assertEqual(len(asked), 1)
                self.assertIsNone(result)
                self.assertEqual(screen.messages, [])

    # --- textes « MCP » ---------------------------------------------------------------------

    def button_leds_subtitle(self, module, hardware):
        screen = make_screen(module, hardware)
        screen.rpi.gpio = types.SimpleNamespace(toys_list={"LIGHT_A": None})
        cbuttonleds = types.ModuleType("include.cbuttonleds")
        cbuttonleds.LEDS = ["LIGHT_A"]
        cbuttonleds.load = lambda logs: {"LIGHT_A": {}}
        sys.modules["include.cbuttonleds"] = cbuttonleds
        sys.modules["include"].cbuttonleds = cbuttonleds
        headers = []

        def header(title, subtitle=None):
            headers.append((title, subtitle))
            raise Stop

        screen.display_background = lambda *a, **k: None
        screen.menu_header = header
        try:
            with self.assertRaises(Stop):
                screen.button_leds_menu()
        finally:
            del sys.modules["include.cbuttonleds"]
            del sys.modules["include"].cbuttonleds
        return headers

    def test_button_leds_subtitle_pico(self):
        self.assertEqual(self.button_leds_subtitle(self.patched, "pico"),
                         [("button-leds", "Les LED des boutons branchées sur la carte Pico")])

    def test_button_leds_subtitle_normal_identical(self):
        self.assertEqual(self.button_leds_subtitle(self.patched, "normal"),
                         self.button_leds_subtitle(self.original, "normal"))
        self.assertEqual(self.button_leds_subtitle(self.patched, "normal"),
                         [("button-leds", "button-leds-sub")])

    def test_banned_text(self):
        self.assertEqual(make_screen(self.patched, "pico").banned_text("PIN_UP"),
                         "Bouton PIN_UP (carte Pico) banni.")
        self.assertEqual(make_screen(self.patched, "normal").banned_text("A3"),
                         "Button A3 (MCP) is banned.")

    def test_banned_text_used_in_main_menu(self):
        # Le texte du bouton banni de main_menu passe par banned_text (plus de « (MCP) » en dur)
        with open(PATCHED, "rb") as f:
            source = f.read().decode("utf-8")
        self.assertFalse("f'Button {self.rpi.gpio.banned_button} (MCP) is banned.'" in source)
        self.assertTrue("self.message([self.banned_text(self.rpi.gpio.banned_button)]" in source)

    # --- menu Boutons (setup_buttons_menu) ---------------------------------------------------

    NOT_ATTRIBUTED = [("", "notattributed")]
    BTN = [("BTN%d" % n, "BTN%d" % n) for n in range(1, 16)]
    LIGHTS = [("V%d" % n, "V%d" % n) for n in range(1, 13)]
    TOYS = [("T%d" % n, "T%d" % n) for n in range(1, 9)]

    def run_setup_buttons(self, module, hardware, keys, choices=(), present=True, buttons=None):
        """setup_buttons_menu avec les touches keys ; choices : réponses des listes déroulantes.
        Renvoie (résultat, [(bouton, options proposées)], messages)."""
        screen = make_screen(module, hardware)
        defaults = dict(cpico.DEFAULTS)
        screen.rpi.gpio = types.SimpleNamespace(mcp1=object() if present else None,
                                                get_defaults=lambda: defaults)
        keys, choices, dropdowns = list(keys), list(choices), []

        def listen(*args, **kwargs):
            if not keys:
                raise Stop
            return keys.pop(0)

        def dropdown(x, y, values, special=None, navigate=None, **kwargs):
            dropdowns.append((navigate, list(values)))
            return choices.pop(0) if choices else "escape"

        screen.rpi.listen_inputs = listen
        screen.res = {"x": 1280, "y": 720}
        screen.alpha = screen.alpha2 = 0
        for name in ("display_background", "menu_header", "display_button", "button_item",
                     "update_screen"):
            setattr(screen, name, lambda *a, **k: None)
        screen.is_clicked = lambda zones, key: None
        screen.navigate = lambda selected, *a, **k: selected
        screen.dropdown = dropdown
        config = dict(buttons if buttons is not None else defaults)
        result = screen.setup_buttons_menu(config, defaults)
        return result, dropdowns, screen.messages

    @staticmethod
    def shortcut(name, tab):
        """Raccourci F<n> du bouton name dans son onglet (ext1 : tout sauf TOY*, ext2 : TOY*)."""
        return "F%d" % (tab.index(name) + 1)

    def tabs(self):
        names = list(cpico.DEFAULTS)
        return ([n for n in names if not n.startswith("TOY")], [n for n in names if n.startswith("TOY")])

    def test_setup_buttons_pico_absent(self):
        result, dropdowns, messages = self.run_setup_buttons(self.patched, "pico", [], present=False)
        self.assertEqual((result, dropdowns, messages), ("escape", [], [["Carte Pico absente"]]))

    def test_setup_buttons_absent_normal_identical(self):
        for module in (self.original, self.patched):
            with self.subTest(module=module.__name__):
                self.assertEqual(self.run_setup_buttons(module, "normal", [], present=False),
                                 ("escape", [], [["nogpio"]]))

    def test_setup_buttons_pico_choices(self):
        ext1, ext2 = self.tabs()
        keys = [self.shortcut("PIN_UP", ext1), self.shortcut("PIN_MISSDART", ext1),
                self.shortcut("PIN_MUSIC_PLAY", ext1), self.shortcut("LIGHT_FLASH", ext1),
                "F0", self.shortcut("TOY1", ext2), self.shortcut("TOY16", ext2), "escape"]
        result, dropdowns, messages = self.run_setup_buttons(self.patched, "pico", keys)
        self.assertEqual(result, "escape")
        self.assertEqual(messages, [])
        expected = {"PIN_UP": self.BTN, "PIN_MISSDART": self.BTN + [("MISS", "MISS")],
                    "PIN_MUSIC_PLAY": self.BTN, "LIGHT_FLASH": self.LIGHTS,
                    "TOY1": self.TOYS, "TOY16": self.TOYS}
        self.assertEqual([name for name, _ in dropdowns], list(expected))
        for name, options in dropdowns:
            with self.subTest(button=name):
                self.assertEqual(options, self.NOT_ATTRIBUTED + expected[name])

    def test_setup_buttons_pico_list_does_not_grow(self):
        # Changer d'onglet ne fait plus grossir la liste des choix
        ext1, _ext2 = self.tabs()
        first = self.shortcut("PIN_UP", ext1)
        keys = [first] + ["F0", "F-1"] * 3 + [first, "escape"]
        _result, dropdowns, _messages = self.run_setup_buttons(self.patched, "pico", keys)
        self.assertEqual(len(dropdowns), 2)
        self.assertEqual(dropdowns[1], dropdowns[0])

    def test_setup_buttons_pico_swap_and_save(self):
        ext1, _ext2 = self.tabs()
        keys = [self.shortcut("PIN_UP", ext1), self.shortcut("PIN_MISSDART", ext1), "save"]
        # PIN_UP prend BTN2 (PIN_DOWN récupère BTN1), puis PIN_MISSDART prend BTN1 : PIN_DOWN
        # ne peut pas recevoir MISS, il passe à « non attribué »
        result, _dropdowns, _messages = self.run_setup_buttons(self.patched, "pico", keys,
                                                               choices=["BTN2", "BTN1"])
        self.assertEqual((result["PIN_UP"], result["PIN_DOWN"], result["PIN_MISSDART"]),
                         ("BTN2", "", "BTN1"))
        self.assertNotIn("MISS", [value for name, value in result.items() if name != "PIN_MISSDART"])

    def test_setup_buttons_normal_identical(self):
        normal = dict(cpico.DEFAULTS)
        normal.update({"PIN_UP": "A0", "PIN_DOWN": "A1", "TOY1": "C3"})
        ext1, ext2 = self.tabs()
        keys = [self.shortcut("PIN_UP", ext1), "F0", "F-1", "F0", self.shortcut("TOY1", ext2),
                "F-1", self.shortcut("PIN_UP", ext1), "save"]
        for hardware in (None, "normal"):
            with self.subTest(hardware=hardware):
                runs = [self.run_setup_buttons(m, hardware, keys, choices=["A1", "D7", "escape"],
                                               buttons=normal)
                        for m in (self.original, self.patched)]
                self.assertEqual(runs[1], runs[0])
                self.assertEqual(runs[1][1][0][1][1], ("A0", "A0"))   # choix MCP d'origine

    # --- test_toys_menu contre le faux Pico ---------------------------------------------------

    def test_toys_menu_with_fake_pico(self):
        # Chaîne complète : cpico <-> broker en mémoire <-> hwd <-> faux Pico
        from test_layer_integration import Rig
        rig = Rig(self)
        self.addCleanup(rig_cleanup)
        screen = make_screen(self.patched, "pico")
        screen.rpi.buttons = dict(cpico.DEFAULTS)
        screen.rpi.gpio = rig.ext
        # Craspberry.light_toys : les éléments hors LED de boutons vont tels quels à gpio
        screen.rpi.light_toys = lambda toys, state=True, delay=None, raw=False: rig.ext.light_toys(toys, state)
        screen.wait_event_time = 1000
        items, seen = [], []
        keys = ["F1", "F11", "escape"]

        def menu_item(position, item, exit=False):
            items.append(item[1])

        def listen(*args, **kwargs):
            rig.pump()
            seen.append(([p for t, p in rig.broker.log if t == "raspydarts/hw/cmd"],
                         list(rig.pico.toys), list(rig.pico.lights), set(rig.pico.pulses)))
            return keys.pop(0)

        screen.rpi.listen_inputs = listen
        for name in ("display_background", "menu_header", "update_screen"):
            setattr(screen, name, lambda *a, **k: None)
        screen.menu_item = menu_item
        screen.display_button = lambda *a, **k: None
        screen.is_clicked = lambda zones, key: None
        screen.navigate = lambda selected, *a, **k: selected

        self.assertEqual(screen.test_toys_menu(), "escape")
        self.assertEqual(screen.messages, [])
        names = ["LIGHT_NEXTPLAYER", "LIGHT_BACK", "LIGHT_NAVIGATE", "LIGHT_VALIDATE", "LIGHT_LASER",
                 "LIGHT_FLASH", "LIGHT_PLAYERS", "LIGHT_CELEBRATION", "LIGHT_CELEBRATION2", "LIGHT_LIGHT"]
        expected = (["V%d  --  %s" % (n + 1, name) for n, name in enumerate(names)]
                    + ["T%d  --  TOY%d" % (n, n) for n in range(1, 9)])
        self.assertEqual(items[:18], expected)                 # TOY9..TOY16 vides : absents
        # Avant tout choix : rien d'envoyé (light_toys reçoit des tuples, ignorés), sorties à zéro
        self.assertEqual(seen[0], ([], [0] * 8, [0] * 12, set()))
        # F1 : voyant V1 ; F11 : Toy T1, 3 éclats de 1200 ms séparés de 400 ms
        self.assertEqual(seen[1][0], [b"LGTPULSE 1 1200 400 3"])
        self.assertIn(("L", 1), seen[1][3])
        self.assertEqual(seen[2][0], [b"LGTPULSE 1 1200 400 3", b"TOYPULSE 1 1200 400 3"])
        self.assertIn(("T", 1), seen[2][3])
        self.assertEqual(seen[2][1][0], 1)                     # T1 allumé pendant l'éclat
        self.assertFalse(any(p.startswith(b"E ") for t, p in rig.broker.log))
        rig.advance(4790)                                      # avant la fin des éclats…
        self.assertEqual(set(rig.pico.pulses), {("L", 1), ("T", 1)})
        rig.advance(20)                                        # … 3 × (1200 + 400) ms au Pico
        self.assertTrue(rig.pico.all_outputs_zero())

    def test_toys_menu_pico_without_toys(self):
        screen = make_screen(self.patched, "pico")
        screen.rpi.buttons = {name: "" for name in cpico.DEFAULTS}
        screen.rpi.light_toys = lambda *a, **k: None
        for name in ("display_background", "menu_header"):
            setattr(screen, name, lambda *a, **k: None)
        self.assertIsNone(screen.test_toys_menu())
        self.assertEqual(screen.messages, [["notoyset"]])

    # --- assistant (wizard) : « Carte Pico » ------------------------------------------------

    LEDS = OrderedDict([("PIN_TARGETLED", 0), ("BRI_TARGETLED", 0.5), ("TYP_TARGETLED", "grb"),
                        ("PIN_STRIPLED", 0), ("NBR_STRIPLED", 0), ("BRI_STRIPLED", 0.5),
                        ("TYP_STRIPLED", "grb")])

    def run_wizard(self, module, hardware, choices, values, device=True):
        """wizard avec les réponses choices (listes déroulantes) et values (saisies).
        Renvoie (résultat, trace) ; trace : appels relevés, dans l'ordre."""
        screen = make_screen(module, hardware)
        trace = []
        choices, values = list(choices), list(values)
        header = [None]

        def record(name):
            return lambda *a, **k: trace.append((name,) + a)

        def dropdown(x, y, items, *a, **k):
            trace.append(("dropdown", header[0], [list(i) for i in items]))
            return choices.pop(0) if choices else "escape"

        def input_value_item(v_type, value, v_min, v_max, **kwargs):
            trace.append(("input", header[0], v_type, v_min, v_max))
            return values.pop(0) if values else "escape"

        def menu_header(title, subtitle=None):
            header[0] = title

        config = screen.config
        config.leds = OrderedDict(self.LEDS)
        config.inputs, config.outputs, config.buttons = {}, {}, {}
        for name in ("set_pico_card_config", "set_ji_leds_config", "set_dartcab_leds_config",
                     "set_mmworks_leds_config", "set_jo_card_config",
                     "write_file"):
            setattr(config, name, record(name))
        config.set_config = record("set_config")
        screen.rpi.set_leds_conf = lambda leds: trace.append(("set_leds_conf", dict(leds)))
        for name in ("init_gpio", "gpio_flush", "set_inputs", "set_outputs"):
            setattr(screen.rpi, name, record("rpi." + name))
        for name in ("display_background", "display_ips", "update_screen", "play_sound"):
            setattr(screen, name, lambda *a, **k: None)
        screen.menu_header = menu_header
        screen.dropdown = dropdown
        screen.input_value_item = input_value_item
        screen.message = lambda texts, *a, **k: trace.append(("message", list(texts)))
        screen.setup_io_extender = record("setup_io_extender")
        screen.calibrate_target = record("calibrate_target")
        screen.res = {"x": 1280, "y": 720}
        screen.res_x_16, screen.res_y_16 = 80, 45
        screen.run_with_look = lambda name, method, *a, **k: method(screen, *a, **k)
        real_exists = os.path.exists
        exists = lambda path: device if path == "/dev/cible" else real_exists(path)  # noqa: E731
        with mock.patch("os.path.exists", exists):
            result = screen.wizard()
        return result, trace

    @staticmethod
    def calls(trace, name):
        return [step for step in trace if step[0] == name]

    @staticmethod
    def messages(trace):
        """Messages hors invites des étapes (« conf-fillin <clé> »)."""
        return [step for step in trace
                if step[0] == "message" and not step[1][0].startswith("conf-fillin")]

    def test_wizard_offers_pico_card(self):
        for hardware in (None, "normal", "pico"):
            with self.subTest(hardware=hardware):
                result, trace = self.run_wizard(self.patched, hardware, ["escape"], [])
                self.assertFalse(result)
                cards = trace[0][2]
                self.assertIn(["Pico", "Carte Pico"], cards)
                self.assertEqual(cards[-1], ["Au", "card-other"])

    def test_wizard_pico_card(self):
        # Carte Pico, anneau 204 LED, cible : luminosité 40 et RGB ; ruban : 60 LED, 30, GRB
        result, trace = self.run_wizard(self.patched, "normal", ["Pico", "none", "rgb", "grb"],
                                        ["40", "60", "30"])
        self.assertTrue(result)
        self.assertEqual(self.calls(trace, "set_pico_card_config"), [("set_pico_card_config",)])
        self.assertEqual(self.calls(trace, "set_leds_conf"), [("set_leds_conf", {
            "PIN_TARGETLED": "0", "BRI_TARGETLED": "40", "TYP_TARGETLED": "rgb",
            "PIN_STRIPLED": "1", "NBR_STRIPLED": "60", "BRI_STRIPLED": "30",
            "TYP_STRIPLED": "grb"})])
        # Table de l'anneau du canal 0 ([LEDTarget]) : 204 LED, sans pneu
        self.assertEqual(self.calls(trace, "set_dartcab_leds_config"),
                         [("set_dartcab_leds_config", "none")])
        self.assertEqual(self.calls(trace, "set_ji_leds_config"), [])
        # Ni broche demandée, ni boutons (setup_io_extender), ni étalonnage, ni GPIO
        inputs = self.calls(trace, "input")
        self.assertEqual([step[1:3] for step in inputs],
                         [("conf-leds", "number")] * 3)
        self.assertEqual(inputs[1][4], 300)                  # 300 LED au plus par canal
        for name in ("setup_io_extender", "calibrate_target", "rpi.init_gpio", "rpi.gpio_flush"):
            self.assertEqual(self.calls(trace, name), [], name)
        self.assertNotIn("config-buttons_choice", [step[1] for step in self.calls(trace, "dropdown")])
        # Configuration écrite une fois, après le préréglage, puis redémarrage annoncé
        names = [step[0] for step in trace]
        self.assertEqual(names.count("write_file"), 1)
        self.assertLess(names.index("set_pico_card_config"), names.index("write_file"))
        self.assertLess(names.index("set_leds_conf"), names.index("write_file"))
        self.assertEqual(trace[-1], ("message", ["config-restart"]))
        self.assertEqual(self.messages(trace), [("message", ["config-restart"])])

    def test_wizard_pico_without_strip(self):
        # Anneau : aucune table ; ruban : 0 LED (ou rien) -> ni luminosité ni ordre demandés
        for count in ("0", "", "space"):
            with self.subTest(count=count):
                result, trace = self.run_wizard(self.patched, "pico", ["Pico", "no", "grb"],
                                                ["50", count])
                self.assertTrue(result)
                self.assertEqual(self.calls(trace, "set_leds_conf"), [("set_leds_conf", {
                    "PIN_TARGETLED": "0", "BRI_TARGETLED": "50", "TYP_TARGETLED": "grb",
                    "PIN_STRIPLED": "1", "NBR_STRIPLED": 0, "BRI_STRIPLED": 0,
                    "TYP_STRIPLED": "grb"})])
                self.assertEqual(self.calls(trace, "set_ji_leds_config"), [])
                self.assertEqual(self.calls(trace, "set_dartcab_leds_config"), [])
                self.assertEqual(len(self.calls(trace, "input")), 2)

    RING_CHOICES = [["none", "Anneau 204 LED (sans pneu)"], ["before", "244 LED, pneu avant les quarts"],
                    ["after", "244 LED, pneu après"], ["no", "Aucune"]]

    def test_wizard_pico_ring_tables(self):
        # Liste des tables de l'anneau (canal 0), 204 LED en premier ; Échap : 204 LED
        for choice, tire in (("none", "none"), ("before", "before"), ("after", "after"),
                             ("escape", "none"), ("no", None)):
            with self.subTest(choice=choice):
                result, trace = self.run_wizard(self.patched, "pico", ["Pico", choice, "grb"],
                                                ["50", "0"])
                self.assertTrue(result)
                ring = [step for step in self.calls(trace, "dropdown")
                        if step[1] == "config-leds_choice"]
                self.assertEqual([step[2] for step in ring], [self.RING_CHOICES])
                expected = [] if tire is None else [("set_dartcab_leds_config", tire)]
                self.assertEqual(self.calls(trace, "set_dartcab_leds_config"), expected)
                for name in ("set_ji_leds_config", "set_mmworks_leds_config"):
                    self.assertEqual(self.calls(trace, name), [], name)
                names = [step[0] for step in trace]
                if tire is not None:
                    self.assertLess(names.index("set_dartcab_leds_config"), names.index("write_file"))

    def test_wizard_pico_escape(self):
        # Échap sur la première étape des LED : assistant quitté, rien d'écrit
        result, trace = self.run_wizard(self.patched, "pico", ["Pico", "no"], ["escape"])
        self.assertFalse(result)
        self.assertEqual(self.calls(trace, "write_file"), [])
        self.assertEqual(self.calls(trace, "set_leds_conf"), [])
        # Échap plus loin : l'étape d'avant, demandée de nouveau
        result, trace = self.run_wizard(self.patched, "pico", ["Pico", "no", "escape", "grb", "grb"],
                                        ["40", "45", "10", "20"])
        self.assertTrue(result)
        self.assertEqual(self.calls(trace, "set_leds_conf")[0][1]["BRI_TARGETLED"], "45")
        self.assertEqual(self.calls(trace, "set_leds_conf")[0][1]["NBR_STRIPLED"], "10")
        # Nombre de LED hors plage : demandé de nouveau
        result, trace = self.run_wizard(self.patched, "pico", ["Pico", "no", "grb", "grb"],
                                        ["40", "301", "300", "20"])
        self.assertTrue(result)
        self.assertEqual(self.calls(trace, "set_leds_conf")[0][1]["NBR_STRIPLED"], "300")

    def test_wizard_pico_device_absent_warns_without_blocking(self):
        result, trace = self.run_wizard(self.patched, "pico", ["Pico", "no", "grb"], ["50", "0"],
                                        device=False)
        self.assertTrue(result)
        messages = self.messages(trace)
        self.assertEqual(len(messages), 2)
        self.assertIn("/dev/cible", messages[0][1][0])
        self.assertEqual(messages[1], ("message", ["config-restart"]))
        self.assertEqual(len(self.calls(trace, "write_file")), 1)

    def test_wizard_normal_identical(self):
        # Carte de Joffrey, sans LED de Jimmy, sans LED, sans boutons
        for hardware in (None, "normal"):
            with self.subTest(hardware=hardware):
                runs = [self.run_wizard(m, hardware, ["Jo", "Jo", "no", "no"], ["", ""])
                        for m in (self.original, self.patched)]
                original, patched = runs[0][1], runs[1][1]
                self.assertTrue(runs[0][0])
                self.assertEqual(runs[1][0], runs[0][0])
                # Seule différence : le choix « Carte Pico » dans la première liste
                self.assertEqual(patched[0][2], [c for c in original[0][2] if c != ["Au", "card-other"]]
                                 + [["Pico", "Carte Pico"], ["Au", "card-other"]])
                self.assertEqual(patched[1:], original[1:])
                self.assertIn(("dropdown", "config-buttons_choice", [["yes", "yes"], ["no", "no"]]),
                              patched)

    # --- calibrate_target : CAL 0 au début en mode Pico ----------------------------------------

    def run_calibrate(self, module, hardware, keys, link=True, listen=None):
        screen = make_screen(module, hardware)
        sent = []
        if link:
            screen.rpi.pico_link = types.SimpleNamespace(send=lambda line: sent.append(line))
        keys = list(keys)

        def default_listen(*args, **kwargs):
            sent.append(("listen",))
            return keys.pop(0)

        screen.rpi.listen_inputs = listen or default_listen
        screen.draw_calibration = lambda *a, **k: None
        screen.play_sound = lambda *a, **k: None
        return screen.calibrate_target(), sent

    def test_calibrate_target_pico_cal0_first(self):
        result, sent = self.run_calibrate(self.patched, "pico", ["escape"])
        self.assertIsNone(result)
        self.assertEqual(sent, ["CAL 0", ("listen",)])      # avant toute lecture, rien après
        keys = ["R%dC%d" % (n // 7, n % 7) for n in range(62)]
        result, sent = self.run_calibrate(self.patched, "pico", keys)
        self.assertEqual(len(result), 62)
        self.assertEqual([s for s in sent if s != ("listen",)], ["CAL 0"])

    def test_calibrate_target_pico_never_cal1(self):
        # Erreur pendant l'étalonnage : CAL 0 déjà envoyé, jamais CAL 1 (spécification § 7.2)
        sent = []

        def broken(*args, **kwargs):
            raise RuntimeError("lecture impossible")

        screen = make_screen(self.patched, "pico")
        screen.rpi.pico_link = types.SimpleNamespace(send=sent.append)
        screen.rpi.listen_inputs = broken
        screen.draw_calibration = lambda *a, **k: None
        with self.assertRaises(RuntimeError):
            screen.calibrate_target()
        self.assertEqual(sent, ["CAL 0"])
        with open(PATCHED, "rb") as f:
            self.assertNotRegex(f.read().decode("utf-8"), r"send\(\s*['\"]CAL 1")

    def test_calibrate_target_pico_without_link(self):
        # Mode Pico sans lien (cas anormal) : étalonnage quand même, sans erreur
        result, sent = self.run_calibrate(self.patched, "pico", ["escape"], link=False)
        self.assertIsNone(result)
        self.assertEqual(sent, [("listen",)])

    def test_calibrate_target_normal_identical(self):
        keys = ["2410", "escape", "2410", "space", "2411", "2411", "enter", "escape", "escape",
                "escape", "escape"]
        for hardware in (None, "normal"):
            with self.subTest(hardware=hardware):
                runs = [self.run_calibrate(m, hardware, keys, link=False)
                        for m in (self.original, self.patched)]
                self.assertEqual(runs[1], runs[0])

    def test_calibrate_target_with_fake_pico(self):
        # Un diagnostic a laissé CAL 1 (hwd le renverrait au Pico à chaque reconnexion) :
        # l'étalonnage remet CAL 0, au Pico et dans hwd
        from test_layer_integration import Rig
        rig = Rig(self)
        self.addCleanup(rig_cleanup)
        rig.link.send("CAL 1")
        rig.pump()
        self.assertEqual((rig.pico.cal, rig.core.cal), (1, 1))
        screen = make_screen(self.patched, "pico")
        screen.rpi.pico_link = rig.link
        screen.draw_calibration = lambda *a, **k: None
        seen = []

        def listen(*args, **kwargs):
            rig.pump()
            seen.append((rig.pico.cal, rig.core.cal))
            return "escape"

        screen.rpi.listen_inputs = listen
        self.assertIsNone(screen.calibrate_target())
        self.assertEqual(seen, [(0, 0)])

    # --- fichier ----------------------------------------------------------------------------

    def test_line_endings_kept(self):
        # Fins de ligne de la base gardées : LF depuis RaspyDarts 4.6.5 (CRLF en 4.6.4)
        with open(ORIGINAL, "rb") as f:
            original = f.read()
        with open(PATCHED, "rb") as f:
            data = f.read()
        self.assertEqual(original.count(b"\r\n"), 0)
        self.assertEqual(data.count(b"\r\n"), 0, "CRLF dans un fichier LF")


if __name__ == "__main__":
    unittest.main()
