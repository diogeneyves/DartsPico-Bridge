# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Patch 0002 (include/cconfig.py) : pureté, différentiel du mode normal, mode Pico.

Banc d'essai sans aucun fichier réel de la machine : faux include.cpaths
(dossier temporaire), faux /proc et /sys pour Config.__init__ (cconfig.py:1042-1049),
faux fichier VERSION. Le cconfig d'origine est lu dans ~/RaspyDarts-ref465 (copié,
jamais modifié) ; le cconfig patché vient d'un clone temporaire préparé par
tools/apply_patches.sh. Chaque cas recharge le module : self.config est
DefaultConfig lui-même (cconfig.py:983), modifié par read_file.
"""

import builtins
import configparser
import importlib.util
import itertools
import json
import os
import shutil
import sys
import tempfile
import types
import unittest

import _cplatform
import _orig
import _paths  # noqa: F401
from fake_mqtt import FakeMqttModule
from test_patches import FakeProcess, Recorder, fresh_clone, run_script

ROOT = str(_paths.ROOT)
PATCH_0002 = os.path.join(ROOT, "pi", "patches", "0002-cconfig-mode-pico.patch")
COUNTER = itertools.count()

# Lecture des sections dans l'ordre de raspydarts.py:55-80, puis les sections game-* (R:106-107).
READ_ORDER = [("SectionGlobals", None), ("SectionAdvanced", None), ("SectionKeys", None),
              ("Raspberry", None), ("Raspberry_Leds", None), ("LEDTarget", None),
              ("Favorites", ""), ("Events", None), ("SectionGlobals", None),
              ("SectionAdvanced", None), ("Raspberry_BoardPinsIns", None),
              ("Raspberry_BoardPinsOuts", None), ("Camera", None), ("Gallery", None)]

# Ancienne configuration de type pydarts.cfg, FABRIQUÉE pour le test (aucune donnée réelle).
OLD_PYDARTS_CFG = """[SectionGlobals]
locale:fr_FR
font:Casino.ttf
colorset:'sombre'
fullscreen:True
soundvolume:80
cle_inconnue:42

[SectionAdvanced]
bypass-stats:1
use-dmd:False
mqtt-broker:localhost
localplayers:Alice,Bob
game-options-list:Cricket
servername:ancien.example.invalid
prefered_game:X01 50%
richard-mode:True
option_disparue:1

[Raspberry]
PIN_UP:0
PIN_DOWN:0
LIGHT_FLASH:C3
TOY1:C0

[SectionKeys]
S20:2410
D20:1310
PLAYERBUTTON:17

[Raspberry_BoardPinsIns]
PIN_1:24
PIN_2:25

[Raspberry_BoardPinsOuts]
PIN_1:15

[LEDTarget]
S20:1,2

[Events]
newgame:{'TARGET': ['Rainbow,1,red,10'], 'LIGHT': ['LIGHT_FLASH,500']}

[game-Cricket]
max_round:20
"""


# Clés ajoutées à DefaultConfig['SectionAdvanced'] par les patchs : 0002 (hardware) et
# 0006 (send-stats), avec leur valeur par défaut telle que read_file la journalise.
ADDED_DEFAULTS = {"hardware": "normal", "send-stats": True}


def without_hardware_default(lines):
    """Retire les 3 lignes que read_file journalise pour chaque clé par défaut ajoutée.

    Quand le fichier n'a pas de section [SectionAdvanced], read_file parcourt toutes les
    clés par défaut et journalise, pour chacune, une erreur et deux lignes de débogage
    (cconfig.py:1689-1692 puis 1721-1725). Les clés ajoutées (hardware, patch 0002 ;
    send-stats, patch 0006) en produisent autant.
    De plus, la ligne de débogage Config[SectionAdvanced]={...} (cconfig.py:1752) affiche
    tout le dictionnaire, qui contient maintenant ces clés : ces fragments-là sont retirés
    des messages. Ce sont les seuls écarts de journal attendus.
    """
    fragments = [f"{key!r}: {value!r}, " for key, value in ADDED_DEFAULTS.items()]

    def strip(message):
        for fragment in fragments:
            message = message.replace(fragment, "")
        return message
    lines = [(level, strip(m) if m.startswith("Config[SectionAdvanced]=") else m)
             for level, m in lines]
    errors = {("error", f"Configuration issue with this option : {key}"): ("debug", f"Use default:  {value}")
              for key, value in ADDED_DEFAULTS.items()}
    out, i, removed = [], 0, 0
    while i < len(lines):
        if lines[i] in errors:
            assert lines[i + 1][0] == "debug" and lines[i + 1][1].startswith("Error was:"), lines[i + 1]
            assert lines[i + 2] == errors[lines[i]], lines[i + 2]
            i += 3
            removed += 1
            continue
        out.append(lines[i])
        i += 1
    return out, removed


class Logs:
    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        return lambda message, *a: self.lines.append((level, str(message)))


def fake_open_factory():
    fakes = {"/proc/device-tree/model": "Raspberry Pi 4 Model B Rev 1.5\x00",
             "/sys/firmware/devicetree/base/serial-number": "100000000badcafe\x00"}

    def fake_open(path, *args, **kwargs):
        if path in fakes:
            import io
            return io.StringIO(fakes[path])
        return builtins.open(path, *args, **kwargs)
    return fake_open


@unittest.skipUnless(_orig.available(), "copie de RaspyDarts absente (%s)" % _orig.ORIG)
class Bench(unittest.TestCase):
    """Prépare les deux cconfig.py et le dossier du faux utilisateur."""

    @classmethod
    def setUpClass(cls):
        assert _orig.status() == ""
        sys.dont_write_bytecode = True
        cls.tmp = tempfile.mkdtemp()
        cls.copy = fresh_clone(cls.tmp)
        out = run_script(cls.copy)
        assert out.returncode == 0, out.stderr
        cls.original_include = os.path.join(cls.tmp, "origine", "include")
        os.makedirs(cls.original_include)
        shutil.copy(os.path.join(_orig.ORIG, "include", "cconfig.py"), cls.original_include)
        cls.user = os.path.join(cls.tmp, "utilisateur")
        cls.game_root = os.path.join(cls.tmp, "jeu")
        os.makedirs(cls.user)
        os.makedirs(cls.game_root)
        with open(os.path.join(cls.game_root, "VERSION"), "w") as f:
            f.write("V4.6.4_202611132000\n")
        cls.cfg_path = os.path.join(cls.user, "raspydarts.cfg")
        cls.saved_modules = dict(sys.modules)

    @classmethod
    def tearDownClass(cls):
        for name in [n for n in sys.modules if n not in cls.saved_modules]:
            del sys.modules[name]
        sys.modules.update(cls.saved_modules)
        shutil.rmtree(cls.tmp)
        assert _orig.status() == "", "le clone de référence a été modifié"

    # ------------------------------------------------------------- chargement

    def package(self, original):
        """Faux paquet include : cpaths factice, fichiers réels de la version choisie."""
        for name in [n for n in sys.modules if n == "include" or n.startswith("include.")]:
            del sys.modules[name]
        pkg = types.ModuleType("include")
        pkg.__path__ = [self.original_include if original else os.path.join(self.copy, "include")]
        cpaths = types.SimpleNamespace(
            user_dir=lambda: self.user, config_file=lambda: self.cfg_path,
            ROOT_DIR=self.game_root, CONFIG_NAME="raspydarts.cfg", OLD_CONFIG_NAME="pydarts.cfg")
        sys.modules["include"] = pkg
        sys.modules["include.cpaths"] = cpaths
        pkg.cpaths = cpaths
        _cplatform.install(pkg, sys.modules)           # RaspyDarts 4.6.5 : un Pi 4 avec GPIO
        return pkg

    def load_cconfig(self, original):
        self.package(original)
        path = os.path.join(self.original_include if original else os.path.join(self.copy, "include"),
                            "cconfig.py")
        name = "cconfig_cas_%d" % next(COUNTER)       # module neuf : DefaultConfig neuf
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.open = fake_open_factory()             # seulement /proc et /sys simulés
        return module

    def run_config(self, data, original, action=None):
        """Écrit data, puis la séquence du jeu : lecture de toutes les sections, action, write_file.

        Renvoie (octets écrits ou ('exit', code), configuration en mémoire, journal, objet Config).
        """
        if os.path.exists(self.cfg_path):
            os.remove(self.cfg_path)
        if data is not None:
            with open(self.cfg_path, "wb") as f:
                f.write(data)
        m = self.load_cconfig(original)
        logs = Logs()
        config = None
        try:
            config = m.Config(logs)
            config.check_config_file()
            for section, none in READ_ORDER:
                config.read_file(section, none=none) if none is not None else config.read_file(section)
            for game in config.config["SectionAdvanced"]["game-options-list"].split(","):
                config.read_file(f"game-{game.replace(' ', '_')}")
            if action:
                action(config, m)
            config.write_file()
        except SystemExit as e:
            return ("exit", e.code), None, logs.lines, config
        with open(self.cfg_path, "rb") as f:
            written = f.read()
        memory = json.loads(json.dumps(config.config, sort_keys=True, default=repr))
        return written, memory, logs.lines, config


class Purity(unittest.TestCase):
    def test_patch_only_adds_lines_to_cconfig(self):
        with open(PATCH_0002, encoding="utf-8") as f:
            text = f.read()
        # Le diff seulement : le message de commit, avant « diff --git », a ses propres « - ».
        lines = text[text.index("\ndiff --git "):].splitlines()
        files = [l[len("+++ b/"):] for l in lines if l.startswith("+++ b/")]
        self.assertEqual(files, ["include/cconfig.py"])
        removed = [l for l in lines if l.startswith("-") and not l.startswith("---")]
        added = [l for l in lines if l.startswith("+") and not l.startswith("+++")]
        self.assertEqual(removed, [])                                  # 0 ligne retirée
        self.assertEqual(len(added), 33)

    def test_author_is_noreply(self):
        with open(PATCH_0002, encoding="utf-8") as f:
            head = f.read(400)
        self.assertIn("From: Gohy Yves <6154641+diogeneyves@users.noreply.github.com>", head)


class NormalModeDifferential(Bench):
    """Mode normal : fichiers écrits identiques octet pour octet, origine contre patché."""

    def default_file(self):
        written, _, _, _ = self.run_config(b"", original=True)
        return written

    def inputs(self):
        default = self.default_file()
        old = OLD_PYDARTS_CFG.encode()
        return {
            "vide": b"",
            "par défaut": default,
            "ancien pydarts.cfg": old,
            "ancien avec hardware:normal": old.replace(b"richard-mode:True\n",
                                                       b"richard-mode:True\nhardware:normal\n"),
            "marque BOM": b"\xef\xbb\xbf" + default,
        }

    def actions(self):
        def buttons(config, m):
            # Paramétrage > Boutons (raspydarts.py:1060)
            config.set_config("Raspberry", dict(config.config["Raspberry"], PIN_UP="A1", TOY2="C5"))

        def game(config, m):
            config.set_game_config("Shanghai", {"max_round": "7"})

        def presets(name):
            return lambda config, m: getattr(config, name)()

        acts = {"lecture puis écriture": None, "boutons": buttons, "options d'un jeu": game}
        for name in ("set_clk_card_config", "set_jt_card_config", "set_jt2_card_config",
                     "set_ji_card_config", "set_ji2_card_config", "set_jiz_card_config",
                     "set_gb_card_config", "set_ed900_card_config", "set_jo_416_card_config",
                     "set_jo_card_config", "set_ji_leds_config", "set_mmworks_vdartscard_config",
                     "set_mmworks_leds_config"):
            acts[name] = presets(name)
        return acts

    def test_files_written_are_identical(self):
        inputs = self.inputs()
        self.assertGreater(len(inputs["par défaut"]), 5000)
        for (label, data), (act_name, action) in itertools.product(inputs.items(), self.actions().items()):
            with self.subTest(fichier=label, scenario=act_name):
                o_bytes, o_mem, o_logs, _ = self.run_config(data, True, action)
                p_bytes, p_mem, p_logs, _ = self.run_config(data, False, action)
                self.assertEqual(p_bytes, o_bytes)                       # octet pour octet
                if p_mem is not None:
                    for key, value in ADDED_DEFAULTS.items():
                        self.assertEqual(p_mem["SectionAdvanced"].pop(key), value)
                    self.assertEqual(p_mem, o_mem)
                p_logs, extra = without_hardware_default(p_logs)
                # Lignes en plus pour hardware seulement si le fichier n'a pas de [SectionAdvanced].
                has_advanced = data.lstrip(b"\xef\xbb\xbf").count(b"[SectionAdvanced]") > 0
                self.assertEqual(extra > 0, not has_advanced and p_mem is not None)
                unexpected = [l for l in o_logs if "Unexpected hardware" in l[1]]
                if label == "ancien avec hardware:normal":
                    self.assertTrue(unexpected)                         # l'origine l'écarte…
                    self.assertEqual([l for l in o_logs if l not in unexpected], p_logs)  # … seul écart
                else:
                    self.assertEqual(p_logs, o_logs)

    def test_bom_stops_both_versions(self):
        data = b"\xef\xbb\xbf" + self.default_file()
        o = self.run_config(data, True)[0]
        p = self.run_config(data, False)[0]
        self.assertEqual(o, ("exit", 1))
        self.assertEqual(p, o)                                         # défaut d'origine inchangé

    def test_old_file_really_exercises_read_file(self):
        written, mem, logs, _ = self.run_config(OLD_PYDARTS_CFG.encode(), False)
        self.assertEqual(mem["SectionGlobals"]["font"], "MaPolice.ttf")          # Casino.ttf converti
        self.assertEqual(mem["SectionGlobals"]["colorset"], "sombre")           # guillemets retirés
        self.assertEqual(mem["SectionAdvanced"]["prefered_game"], "")           # « % » : défaut
        self.assertTrue(any("Unexpected cle_inconnue" in l[1] for l in logs))
        self.assertIn("[game-Cricket]", written.decode())
        self.assertNotIn("servername", written.decode())                       # jamais réécrite


class PicoMode(Bench):
    def with_hardware(self, value):
        default = self.run_config(b"", original=True)[0]
        return default.replace(b"richard-mode:False\n", b"richard-mode:False\nhardware:%s\n" % value)

    def advanced_lines(self, written):
        text = written.decode()
        block = text.split("[SectionAdvanced]\n", 1)[1].split("\n[", 1)[0]
        return block.splitlines()

    def test_key_survives_and_is_idempotent(self):
        data = self.with_hardware(b"pico")
        first, mem, _, _ = self.run_config(data, False)
        self.assertEqual(mem["SectionAdvanced"]["hardware"], "pico")
        lines = self.advanced_lines(first)
        self.assertIn("hardware:pico", lines)
        self.assertLess(lines.index("hardware:pico"), lines.index("### For Richard's case"))
        second, mem2, _, _ = self.run_config(first, False)
        self.assertEqual(second, first)                                # idempotence
        self.assertEqual(mem2["SectionAdvanced"]["hardware"], "pico")
        # L'origine, elle, perdait la clé à la première écriture.
        lost = self.run_config(data, True)[0]
        self.assertNotIn(b"hardware:", lost)

    def test_values_written_as_read(self):
        for value, expected in ((b"  Pico  ", "hardware:Pico"), (b"PICO", "hardware:PICO"),
                                (b"pi", "hardware:pi"), (b"NORMAL", None), (b"normal", None)):
            with self.subTest(value=value):
                lines = self.advanced_lines(self.run_config(self.with_hardware(value), False)[0])
                written = [l for l in lines if l.startswith("hardware:")]
                self.assertEqual(written, [expected] if expected else [])

    def test_preset_applied(self):
        sys.path.insert(0, os.path.join(ROOT, "pi", "raspydarts_layer", "include"))
        import cpico
        def preset(config, m):
            config.set_pico_card_config()
        default = self.run_config(b"", original=True)[0]
        written, mem, logs, _ = self.run_config(default, False, preset)
        self.assertFalse([l for l in logs if "No config for" in l[1]])  # préréglage complet
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read_string(written.decode())
        self.assertEqual(dict(parser["Raspberry"]), {k: str(v) for k, v in cpico.DEFAULTS.items()})
        self.assertTrue(all(v == "" for v in parser["Raspberry_BoardPinsIns"].values()))
        self.assertTrue(all(v == "" for v in parser["Raspberry_BoardPinsOuts"].values()))
        self.assertTrue(all(v == "" for v in parser["SectionKeys"].values()))
        self.assertEqual(parser["SectionAdvanced"]["hardware"], "pico")
        # Ordre des clés inchangé par rapport au fichier d'origine.
        orig = configparser.ConfigParser(interpolation=None)
        orig.optionxform = str
        orig.read_string(default.decode())
        for section in ("Raspberry", "Raspberry_BoardPinsIns", "Raspberry_BoardPinsOuts", "SectionKeys"):
            self.assertEqual(list(parser[section]), list(orig[section]), section)
        # Correspondance de la spécification (hardware/pinout.json).
        with open(os.path.join(ROOT, "hardware", "pinout.json"), encoding="utf-8") as f:
            pin = json.load(f)
        for b in pin["buttons"]:
            self.assertEqual(parser["Raspberry"]["PIN_" + b["function"]], b["id"])
        for name, v in pin["lights_default_mapping_raspydarts"].items():
            if not name.startswith("_"):
                self.assertEqual(parser["Raspberry"][name], v)
        self.assertEqual([parser["Raspberry"]["TOY%d" % i] for i in range(1, 9)],
                         ["T%d" % i for i in range(1, 9)])
        # Les autres sections ne bougent pas, sauf la sortie du son (§ 9.4, v0.3.11).
        self.assertEqual(orig["SectionGlobals"]["sound_output"], "auto")
        expected = dict(orig["SectionGlobals"], sound_output="hdmi")
        self.assertEqual(dict(parser["SectionGlobals"]), expected)
        for section in ("Favorites", "Events", "Raspberry_Leds", "LEDTarget"):
            self.assertEqual(dict(parser[section]), dict(orig[section]), section)

    def sound_output_after_preset(self, data):
        def preset(config, m):
            config.set_pico_card_config()
        sys.path.insert(0, os.path.join(ROOT, "pi", "raspydarts_layer", "include"))
        written, mem, _, _ = self.run_config(data, False, preset)
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read_string(written.decode())
        return parser["SectionGlobals"].get("sound_output"), mem["SectionGlobals"]["sound_output"]

    def test_preset_sound_output_hdmi_on_new_config(self):
        # Config neuve (aucun fichier) et valeurs « auto » ou vides : HDMI (§ 9.4, v0.3.11).
        self.assertEqual(self.sound_output_after_preset(None), ("hdmi", "hdmi"))
        default = self.run_config(b"", original=True)[0]
        self.assertIn(b"sound_output:auto\n", default)
        for value in (b"auto", b"AUTO", b" Auto ", b""):
            with self.subTest(value=value):
                data = default.replace(b"sound_output:auto\n", b"sound_output:%s\n" % value)
                self.assertEqual(self.sound_output_after_preset(data), ("hdmi", "hdmi"))

    def test_preset_keeps_chosen_sound_output(self):
        # Une sortie choisie à la main (jack, usb, hdmi) est conservée.
        default = self.run_config(b"", original=True)[0]
        for value in ("jack", "usb", "hdmi"):
            with self.subTest(value=value):
                data = default.replace(b"sound_output:auto\n", b"sound_output:%s\n" % value.encode())
                self.assertEqual(self.sound_output_after_preset(data), (value, value))

    def test_normal_mode_never_imports_cpico(self):
        self.run_config(self.run_config(b"", original=True)[0], False)
        self.assertNotIn("include.cpico", sys.modules)

    def test_written_file_drives_patched_craspberry(self):
        # Lien avec le patch 0001 : le fichier écrit par le préréglage fait passer
        # craspberry.py patché en mode Pico, avec l'objet Config réel du cconfig patché.
        def preset(config, m):
            config.set_pico_card_config()
        default = self.run_config(b"", original=True)[0]
        written = self.run_config(default, False, preset)[0]
        self.assertIn(b"hardware:pico", written)
        _, _, _, config = self.run_config(written, False)       # Config réel, relu du fichier
        pkg = sys.modules["include"]
        gpio = Recorder()
        rpi = types.ModuleType("RPi")
        rpi.GPIO = gpio
        pygame = types.ModuleType("pygame")
        pygame.USEREVENT = 24
        pygame.time = Recorder()
        created, coins = [], []
        fakes = {
            "cvideos": types.SimpleNamespace(),
            "cgpio_extender": types.SimpleNamespace(Gpio_extender=lambda *a: created.append(a)),
            "cusbpads": types.SimpleNamespace(shared=lambda logs: types.SimpleNamespace()),
            "ccoins": types.SimpleNamespace(shared=lambda *a: coins.append(a)),
        }
        sys.modules.update({"RPi": rpi, "RPi.GPIO": gpio, "pygame": pygame})
        for name, mod in fakes.items():
            sys.modules["include." + name] = mod
            setattr(pkg, name, mod)
        import importlib
        cpico = importlib.import_module("include.cpico")
        cpico._reset_shared()
        FakeMqttModule.Client.instances = []
        link = cpico.shared(Logs(), mqtt_module=FakeMqttModule)
        link._on_status('{"pico": "connected"}')
        craspberry = importlib.import_module("include.craspberry")
        popen = []
        craspberry.subprocess = types.SimpleNamespace(
            Popen=lambda cmd, **kw: popen.append(cmd) or FakeProcess(cmd), DEVNULL=None, PIPE=None)
        self.assertTrue(craspberry.PICO)
        r = craspberry.Craspberry(Logs(), config, config.config["LEDTarget"], None)
        self.assertEqual(gpio.calls, [])                       # aucun GPIO du Pi
        self.assertEqual((created, coins, popen), ([], [], []))
        self.assertEqual(len(r.gpio.buttons_list), 16)         # 15 boutons + MISS
        self.assertEqual(len(r.gpio.toys_list), 18)            # 8 Toys + 10 voyants
        self.assertEqual(r.keys, {})                           # segments à étalonner
        self.assertIsInstance(r.coins, cpico.NoCoins)
        link.on_event("B 1 1 0")
        self.assertEqual(r.gpio.read_entries(), "PIN_UP")


if __name__ == "__main__":
    unittest.main()
