# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Patchs de RaspyDarts (pi/patches/) et tools/apply_patches.sh.

Tout se fait sur des clones temporaires de ~/RaspyDarts-ref465 (jamais modifié :
son état git est vérifié avant et après). Le craspberry.py PATCHÉ est chargé
avec des modules factices (pygame, RPi.GPIO, cvideos, cpaths, cgpio_extender,
cusbpads, ccoins) et vérifié dans les deux modes.
"""

import os
import queue
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

import _cplatform
import _orig
import _paths  # noqa: F401
from fake_mqtt import FakeMqttModule

ROOT = str(_paths.ROOT)
SCRIPT = os.path.join(ROOT, "tools", "apply_patches.sh")
PATCHES = os.path.join(ROOT, "pi", "patches")
PATCH_0001 = os.path.join(PATCHES, "0001-craspberry-mode-pico.patch")


# Identité git fournie par les tests : la CI n'en a aucune (HOME neuf, pas de configuration)
GIT_IDENTITY = ("-c", "user.name=RaspyDarts-Pico tests", "-c", "user.email=tests@invalid")


def git(*args, cwd):
    """Commande git ; un code de retour non nul fait échouer le test (avec stderr)."""
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError("git %s : code %d\n%s" % (" ".join(args), out.returncode, out.stderr))
    return out.stdout


def commit_above_base(path):
    """HEAD différent de BASE, BASE toujours contenu : un commit vide au-dessus de BASE.
    Jamais de checkout d'un autre commit : la copie de la CI est un clone partiel
    (--filter=blob:none) qui n'a que les contenus de BASE ; un checkout de HEAD~1 y laisse
    des fichiers supprimés (blobs absents) avec un code de retour 0."""
    git(*GIT_IDENTITY, "commit", "--quiet", "--allow-empty", "-m", "au-dessus de BASE", cwd=path)
    assert git("rev-parse", "HEAD~1", cwd=path).strip() == _orig.BASE_COMMIT
    status = git("status", "--porcelain", "--untracked-files=all", cwd=path)
    assert status == "", "copie modifiée par la préparation : %r" % status


def fresh_clone(where):
    path = os.path.join(where, "copie")
    # --shared : les objets du clone de référence sont lus, rien n'y est écrit.
    subprocess.run(["git", "clone", "--quiet", "--shared", _orig.ORIG, path], check=True)
    git("checkout", "--quiet", _orig.BASE_COMMIT, cwd=path)
    return path


def run_script(*args):
    env = dict(os.environ, RASPYDARTS_ORIG=_orig.ORIG)
    return subprocess.run([SCRIPT, *args], capture_output=True, text=True, env=env)


class Files(unittest.TestCase):
    def test_base_and_series(self):
        with open(os.path.join(PATCHES, "BASE")) as f:
            self.assertEqual(f.read().strip(), _orig.BASE_COMMIT)
        with open(os.path.join(PATCHES, "series")) as f:
            names = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        self.assertEqual(names, ["0001-craspberry-mode-pico.patch", "0002-cconfig-mode-pico.patch",
                                 "0003-ci2c-mode-pico.patch", "0004-cscreen-mode-pico.patch",
                                 "0005-raspydarts-serveurs-led-pico.patch",
                                 "0006-statistiques-option-send-stats.patch",
                                 "0007-mises-a-jour-mode-pico.patch",
                                 "0008-reglages-systeme-mode-pico.patch",
                                 "0009-langues-mode-pico.patch",
                                 "0010-galerie-lecture-seule-csrf.patch"])
        for n in names:
            self.assertTrue(os.path.isfile(os.path.join(PATCHES, n)))

    def test_patch_only_touches_craspberry(self):
        with open(PATCH_0001, encoding="utf-8") as f:
            text = f.read()
        files = [l[len("+++ b/"):] for l in text.splitlines() if l.startswith("+++ b/")]
        self.assertEqual(files, ["include/craspberry.py"])

    def test_removed_lines_only_reindented_under_a_condition(self):
        # Mode normal strictement identique : aucune ligne existante n'est changée ;
        # les seules lignes « retirées » reviennent telles quelles, plus indentées,
        # sous une branche conditionnelle ajoutée.
        with open(PATCH_0001, encoding="utf-8") as f:
            lines = f.read().splitlines()
        removed = [l[1:] for l in lines if l.startswith("-") and not l.startswith("---")]
        added = [l[1:] for l in lines if l.startswith("+") and not l.startswith("+++")]
        # Une ligne vide « retirée puis remise » est un simple alignement du diff.
        blank_removed = sum(1 for r in removed if not r.strip())
        self.assertLessEqual(blank_removed, sum(1 for a in added if not a.strip()))
        removed = [r for r in removed if r.strip()]
        # 4.6.5 : import de cgpio_extender, setwarnings et setmode, le lecteur de cible (commentaire
        # et deux appels) sous « if not PICO: », lui-même sous le « if pins_usable: » de l'amont,
        # gardé tel quel ; « from include import ci2c », son commentaire, Gpio_extender(...,
        # use_i2c=...) (deux lignes) sous le else ; ccoins.shared sous « if not PICO: » (11 lignes)
        self.assertEqual(len(removed), 11)
        moved = {r.strip() for r in removed}
        for r in removed:
            match = [a for a in added if a.strip() == r.strip()]
            self.assertTrue(match, r)
            indent = len(r) - len(r.lstrip())
            self.assertTrue(any(len(a) - len(a.lstrip()) > indent for a in match), r)
            # En remontant les lignes ajoutées (en sautant les autres lignes déplacées),
            # on trouve la condition sous laquelle la ligne d'origine a été placée.
            i = added.index(match[0])
            before = [a.strip() for a in added[:i] if a.strip() and a.strip() not in moved][-1]
            self.assertIn(before, ("if PICO:", "if not PICO:", "else:", "cgpio_extender = None"),
                          "%r : la ligne d'avant n'est pas une condition (%r)" % (r, before))


@unittest.skipUnless(_orig.available(), "copie de RaspyDarts absente (%s)" % _orig.ORIG)
class Script(unittest.TestCase):
    def setUp(self):
        self.assertEqual(_orig.status(), "")
        self.tmp = tempfile.mkdtemp()
        self.copy = fresh_clone(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)
        self.assertEqual(_orig.status(), "", "le clone de référence a été modifié")

    def empty_patches(self):
        d = os.path.join(self.tmp, "vide")
        os.mkdir(d)
        shutil.copy(os.path.join(PATCHES, "BASE"), d)
        with open(os.path.join(d, "series"), "w") as f:
            f.write("# aucun patch\n")
        return d

    def test_empty_patch_set_accepts_base(self):
        out = run_script("--check", "--patches", self.empty_patches(), self.copy)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_refuses_head_different_from_base(self):
        commit_above_base(self.copy)
        out = run_script("--patches", self.empty_patches(), self.copy)
        self.assertEqual(out.returncode, 2)
        self.assertIn("et non BASE", out.stderr)

    def test_check_needs_base_only(self):
        # --check : la copie peut avoir une autre tête (~/RaspyDarts-ref465 en 32edb12),
        # pourvu qu'elle contienne BASE ; le clone temporaire est placé sur BASE
        commit_above_base(self.copy)
        out = run_script("--check", self.copy)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn(_orig.BASE_COMMIT, out.stdout)
        other = os.path.join(self.tmp, "sans-base")
        os.makedirs(other)
        git("init", "--quiet", cwd=other)
        with open(os.path.join(other, "raspydarts.py"), "w") as f:
            f.write("# autre dépôt\n")
        git("add", "raspydarts.py", cwd=other)
        git(*GIT_IDENTITY, "commit", "--quiet", "-m", "x", cwd=other)
        out = run_script("--check", other)
        self.assertEqual(out.returncode, 2)
        self.assertIn("ne contient pas le commit BASE", out.stderr)

    def test_refuses_dirty_copy(self):
        with open(os.path.join(self.copy, "raspydarts.py"), "a") as f:
            f.write("# modifié\n")
        out = run_script(self.copy)
        self.assertEqual(out.returncode, 3)

    def test_refuses_reference_copy(self):
        out = run_script(_orig.ORIG)
        self.assertEqual(out.returncode, 4)
        out = run_script("--check", _orig.ORIG)          # vérification seule : autorisée
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_check_leaves_copy_untouched(self):
        out = run_script("--check", self.copy)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.copy).strip(), _orig.BASE_COMMIT)
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all", cwd=self.copy), "")

    def test_git_apply_check_on_frozen_commit(self):
        subprocess.run(["git", "apply", "--check", PATCH_0001], cwd=self.copy, check=True)

    def test_apply_then_refuse_second_time(self):
        out = run_script(self.copy)
        self.assertEqual(out.returncode, 0, out.stderr)
        # Dix patchs (0001 à 0010), puis le commit des fichiers nouveaux.
        self.assertEqual(git("rev-parse", "HEAD~11", cwd=self.copy).strip(), _orig.BASE_COMMIT)
        for rel in ("include/cpico.py", "include/hwbus.py", "include/pico_proto.py",
                    "pico_shims/rpi_ws281x.py"):
            self.assertTrue(os.path.isfile(os.path.join(self.copy, rel)), rel)
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all", cwd=self.copy), "")
        self.assertEqual(run_script(self.copy).returncode, 2)   # HEAD n'est plus BASE

    def test_broken_patch_rejected(self):
        d = self.empty_patches()
        with open(os.path.join(d, "series"), "a") as f:
            f.write("casse.patch\n")
        with open(PATCH_0001, encoding="utf-8") as src:
            text = src.read()
        broken = text.replace("\n from . import cusbpads\n", "\n from . import AUTRE_MODULE\n")
        self.assertNotEqual(broken, text)                    # ligne de contexte faussée
        with open(os.path.join(d, "casse.patch"), "w", encoding="utf-8") as dst:
            dst.write(broken)
        out = run_script("--patches", d, self.copy)
        self.assertEqual(out.returncode, 5)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.copy).strip(), _orig.BASE_COMMIT)


# ------------------------------------------- craspberry.py patché, deux modes

def cpico_defaults():
    sys.path.insert(0, os.path.join(ROOT, "pi", "raspydarts_layer", "include"))
    import cpico
    return dict(cpico.DEFAULTS)


class Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.isupper():
            return name
        return lambda *a, **k: self.calls.append((name, a, k))


class FakeProcess:
    def __init__(self, cmd, **kw):
        self.cmd = cmd
        self.stdout = types.SimpleNamespace(readline=lambda: "")

    def kill(self):
        pass


class Logs:
    def __init__(self):
        self.lines = []

    def __getattr__(self, name):
        return lambda m, *a: self.lines.append((name, m))


class FakeConfig:
    def __init__(self, buttons, code):
        self.leds = {}
        self.file_exists = True
        self.delay = 0.1
        self.GPIO_BUTTONS_CONFIG = {"PLAYERBUTTON": "", "MISSDART": ""}
        self.default_SectionKeys = {"S20": "", "DB": ""}
        self.config = {
            "Raspberry": dict(buttons),
            "Raspberry_BoardPinsOuts": {"PIN_1": "6"},
            "Raspberry_BoardPinsIns": {"PIN_1": "5"},
            "SectionKeys": {"S20": code, "DB": "", "PLAYERBUTTON": "17"},
            "SectionAdvanced": {"richard-mode": False, "mqtt-broker": "localhost"},
        }

    def get_value(self, section, key, required=True, default_value=False):
        return self.config[section].get(key, False)


@unittest.skipUnless(_orig.available(), "copie de RaspyDarts absente (%s)" % _orig.ORIG)
class PatchedCraspberry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert _orig.status() == ""
        cls.tmp = tempfile.mkdtemp()
        cls.copy = fresh_clone(cls.tmp)
        out = run_script(cls.copy)
        assert out.returncode == 0, out.stderr
        cls.original_include = os.path.join(cls.tmp, "origine", "include")
        os.makedirs(cls.original_include)
        # craspberry.py et les modules de include/ qu'il importe sans faux module (4.6.4)
        for name in ("craspberry.py", "cbuttonleds.py", "cautodarts.py", "ci2c.py"):
            shutil.copy(os.path.join(_orig.ORIG, "include", name), cls.original_include)
        cls.saved_modules = dict(sys.modules)

    @classmethod
    def tearDownClass(cls):
        for name in [n for n in sys.modules if n not in cls.saved_modules]:
            del sys.modules[name]
        sys.modules.update(cls.saved_modules)
        shutil.rmtree(cls.tmp)
        assert _orig.status() == "", "le clone de référence a été modifié"

    def load(self, hardware, original=False, gpio=True):
        """Charge include/craspberry.py patché (ou celui d'origine), avec une configuration donnée ;
        gpio : has_gpio() du faux cplatform (RaspyDarts 4.6.5)."""
        sys.dont_write_bytecode = True
        cfg = os.path.join(self.tmp, "raspydarts-%s.cfg" % hardware)
        with open(cfg, "w") as f:
            f.write("[SectionAdvanced]\nrichard-mode:False\n")
            if hardware is not None:
                f.write("hardware:%s\n" % hardware)
        return self.load_with(cfg, original, gpio)

    def load_with(self, cfg, original=False, gpio=True):
        for name in [n for n in sys.modules if n == "include" or n.startswith("include.")]:
            del sys.modules[name]
        pkg = types.ModuleType("include")
        pkg.__path__ = [self.original_include if original else os.path.join(self.copy, "include")]
        self.gpio = Recorder()
        rpi = types.ModuleType("RPi")
        rpi.GPIO = self.gpio
        pygame = types.ModuleType("pygame")
        pygame.USEREVENT = 24
        pygame.time = Recorder()
        pygame.event = Recorder()
        pygame.mouse = Recorder()
        self.created = []
        self.coins_calls = []
        fakes = {
            "cvideos": types.SimpleNamespace(stop_player=None, find_player=None),
            # user_dir : lu au chargement par cautodarts et cbuttonleds (RaspyDarts 4.6.4)
            "cpaths": types.SimpleNamespace(config_file=lambda: cfg, ROOT_DIR=self.copy,
                                            user_dir=lambda: os.path.dirname(cfg)),
            "cgpio_extender": types.SimpleNamespace(
                Gpio_extender=lambda logs, buttons, use_i2c=True: self.created.append(buttons) or
                types.SimpleNamespace(mcp1=None, set_io=lambda b: True),
                DEFAULTS={"ORIGINE": "A0"}),
            "cusbpads": types.SimpleNamespace(shared=lambda logs: types.SimpleNamespace(context="")),
            "ccoins": types.SimpleNamespace(
                shared=lambda logs, ext: self.coins_calls.append(ext) or types.SimpleNamespace()),
        }
        sys.modules.update({"include": pkg, "RPi": rpi, "RPi.GPIO": self.gpio, "pygame": pygame})
        _cplatform.install(pkg, sys.modules, gpio=gpio)   # RaspyDarts 4.6.5 : un Pi 4
        for name, mod in fakes.items():
            sys.modules["include." + name] = mod
            setattr(pkg, name, mod)
        # Le vrai cpico (copié par apply_patches.sh), avec un faux paho ; absent de l'origine.
        import importlib
        cpico = None
        if not original:
            cpico = importlib.import_module("include.cpico")
            cpico._reset_shared()
            FakeMqttModule.Client.instances = []
            self.link = cpico.shared(Logs(), mqtt_module=FakeMqttModule)
            self.link._on_status('{"pico": "connected"}')
        module = importlib.import_module("include.craspberry")
        self.popen = []
        module.subprocess = types.SimpleNamespace(
            Popen=lambda cmd, **kw: self.popen.append(cmd) or FakeProcess(cmd),
            DEVNULL=None, PIPE=None, TimeoutExpired=Exception)
        return module, cpico

    def published(self):
        return [c[2] for c in FakeMqttModule.Client.instances[-1].calls if c[0] == "publish"]

    def construct(self, hardware, original=False, buttons=None, code="56"):
        m, cpico = self.load(hardware, original)
        r = m.Craspberry(Logs(), FakeConfig(buttons or {"PIN_UP": "A0"}, code), {}, None)
        return m, cpico, r

    def trace(self, hardware, original):
        m, _, r = self.construct(hardware, original)
        trace = {"gpio": list(self.gpio.calls), "popen": list(self.popen),
                 "extender": list(self.created), "coins": len(self.coins_calls),
                 "reader": r.target_reader, "inputs": r.inputs, "outputs": r.outputs,
                 "pins": dict(r.pins), "keys": dict(r.keys)}
        self.gpio.calls.clear()
        r.read_buttons()
        r.gpio_flush()
        trace["after"] = list(self.gpio.calls)
        return trace

    def test_normal_mode_identical_to_original(self):
        # Différentiel : le fichier d'origine et le fichier patché, mêmes faux modules,
        # même configuration (anciennes broches de la cible, bouton sur GPIO 17).
        original = self.trace(None, original=True)
        self.assertIn(("setup", (5, "IN"), {}), original["gpio"])    # le faux gpio enregistre bien
        self.assertIn(("setup", (17, "IN"), {"pull_up_down": "PUD_UP"}), original["gpio"])
        for hardware in (None, "normal", "autre"):
            with self.subTest(hardware=hardware):
                self.assertEqual(self.trace(hardware, original=False), original)

    def test_pico_mode_never_touches_pi_gpio(self):
        m, cpico, r = self.construct("pico", buttons=cpico_defaults(), code="R3C4")
        self.assertEqual(self.gpio.calls, [])                        # ni setmode, ni setup
        self.assertEqual((r.inputs, r.outputs, r.pins), ([], [], {}))
        self.assertEqual(r.conf_inputs, {})
        r.read_buttons()
        r.read_target()
        r.gpio_flush()
        self.assertEqual(self.gpio.calls, [])                        # ni input, ni cleanup
        self.assertEqual(self.popen, [])

    def test_unknown_hardware_value_warns_at_start(self):
        m, _, r = self.construct("PICOO")
        self.assertFalse(m.PICO)
        self.assertTrue(any(level == "warning" and "unknown" in msg for level, msg in r.logs.lines))
        m, _, r = self.construct("normal")
        self.assertFalse([1 for level, msg in r.logs.lines if "hardware" in msg])

    def write_cfg(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_read_hardware_mode_cases(self):
        m, _ = self.load(None)
        cases = [
            (b"\xef\xbb\xbf[SectionAdvanced]\nhardware:pico\n", ("pico", False)),      # BOM
            (b"[SectionAdvanced]\nhardware =   Pico   \n", ("pico", False)),            # casse, espaces
            (b"[SectionAdvanced]\nhardware:PICO  # carte Pico\n", ("pico", False)),     # commentaire
            (b"[SectionAdvanced]\nhardware:normal\n", ("normal", False)),
            (b"[SectionAdvanced]\nhardware:pi\n", ("normal", True)),                    # inconnue
            (b"[SectionAdvanced]\nhardware:\n", ("normal", True)),                      # vide
            (b"[SectionAdvanced]\nhardware:pico\n[SectionAdvanced]\n", ("normal", True)),  # illisible
            (b"[SectionAdvanced]\nhardware:pico\n\xff\xfe\n", ("normal", True)),       # pas de l'UTF-8
            (b"[SectionAdvanced]\nx:1\n[SectionAdvanced]\n", ("normal", False)),         # illisible, sans hardware
            (b"[SectionGlobals]\nfont:x\n", ("normal", False)),                         # pas de clé
            (b"[SectionAdvanced]\nHardware:pico\n", ("normal", False)),                 # clé sensible à la casse
            (b"[SectionAdvanced]\nmotif:%(x)s\nhardware:pico\n", ("pico", False)),      # sans interpolation
        ]
        for i, (data, (mode, warns)) in enumerate(cases):
            with self.subTest(data=data):
                got = m.read_hardware_mode(self.write_cfg("cas%d.cfg" % i, data))
                self.assertEqual(got[0], mode)
                self.assertEqual(got[1] is not None, warns, got[1])
        absent = os.path.join(self.tmp, "absent.cfg")
        self.assertEqual(m.read_hardware_mode(absent, blinka=True), ("normal", None))
        self.assertEqual(m.read_hardware_mode(absent, blinka=False)[0], "pico")   # § 9.4, v0.3.10

    def test_normal_mode_unchanged(self):
        for hardware in (None, "normal", "autre"):
            with self.subTest(hardware=hardware):
                m, cpico = self.load(hardware)
                self.assertFalse(m.PICO)
                self.assertIs(m.cgpio_extender, sys.modules["include.cgpio_extender"])
                r = m.Craspberry(Logs(), FakeConfig({"PIN_UP": "A0"}, "56"), {}, None)
                self.assertEqual(len(self.popen), 1)                 # ctarget_reader lancé
                self.assertIn("ctarget_reader.py", self.popen[0][2])
                self.assertEqual(self.created, [{"PIN_UP": "A0"}])  # Gpio_extender créé
                self.assertEqual(len(self.coins_calls), 1)           # monnayeur créé
                self.assertIsNone(r.target_reader)                   # lecteur sans READY
                self.assertFalse(hasattr(r, "pico_link"))
                self.gpio.calls.clear()
                r.gpio_flush()
                self.assertEqual([c[0] for c in self.gpio.calls], ["cleanup"])
                self.assertEqual(r.get_defaults(), {"ORIGINE": "A0"})
                self.assertEqual(self.published(), [])               # rien vers hwd

    def test_pico_mode(self):
        m, cpico = self.load("pico")
        self.assertTrue(m.PICO)
        self.assertIsNone(m.cgpio_extender)                          # Blinka non chargé
        r = m.Craspberry(Logs(), FakeConfig(cpico.DEFAULTS, "R3C4"), {}, None)
        self.assertEqual(self.popen, [])                             # pas de ctarget_reader
        self.assertEqual(self.created, [])                           # pas de Gpio_extender
        self.assertEqual(self.coins_calls, [])                       # pas de monnayeur
        self.assertIsInstance(r.gpio, cpico.PicoExtender)
        self.assertIsInstance(r.target_reader, cpico.PicoTargetSource)
        self.assertIsInstance(r.coins, cpico.NoCoins)
        self.assertIsNone(r.coins.left())
        self.assertIs(r.gpio.mcp1, r.gpio)
        # Fléchette : code R3C4 -> segment de [SectionKeys] ; code brut pour l'assistant.
        self.link.on_event("D R3C4 10 2000")
        self.assertEqual(r.read_target(), "S20")
        self.link.on_event("D R3C4 20 2000")
        self.assertEqual(r.read_target(raw=True), "R3C4")
        # Sorties, puis fin du jeu.
        r.light_toys(["TOY1", "TOY9"])
        self.assertEqual(self.published(), ["TOY 1 1"])
        self.gpio.calls.clear()
        r.gpio_flush()
        self.assertEqual(self.published()[-1], "OFF")
        self.assertNotIn("cleanup", [c[0] for c in self.gpio.calls])
        self.assertIs(r.get_defaults(), cpico.DEFAULTS)

    def test_pico_mode_without_gpio(self):
        # Complément 21 : hardware:pico explicite = mode Pico, même si has_gpio() est faux
        m, cpico = self.load("pico", gpio=False)
        self.assertTrue(m.PICO)
        self.assertIsNone(m.cgpio_extender)
        r = m.Craspberry(Logs(), FakeConfig(cpico.DEFAULTS, "R3C4"), {}, None)
        self.assertEqual(self.popen, [])
        self.assertEqual(self.created, [])
        self.assertIsInstance(r.gpio, cpico.PicoExtender)
        self.assertIsInstance(r.target_reader, cpico.PicoTargetSource)
        self.link.on_event("D R3C4 10 2000")
        self.assertEqual(r.read_target(), "S20")

    def test_missing_config_file(self):
        # Spécification § 9.4 (v0.3.10) : sans configuration, Blinka décide (normal s'il est là)
        m, _ = self.load(None)
        os.remove(os.path.join(self.tmp, "raspydarts-None.cfg"))
        self.assertEqual(m.read_hardware_mode(blinka=True), ("normal", None))
        mode, warning = m.read_hardware_mode(blinka=False)
        self.assertEqual(mode, "pico")
        self.assertIn("no Blinka", warning)


if __name__ == "__main__":
    unittest.main()
