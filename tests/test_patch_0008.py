# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Patch 0008 : réglages du système neutralisés en mode carte Pico (spécification § 9.8).

RaspyDarts 4.6.4 appelle au démarrage cupdate.apply_system_settings (raspydarts.py:371), qui
lance « sudo -n bash scripts/reglages_systeme.sh » quand la version a changé ou quand l'écoute
MQTT est déclarée deux fois. Ce script ouvre mosquitto au réseau sans mot de passe et le
relance. En mode carte Pico ([SectionAdvanced] hardware = pico, ou craspberry.PICO), la
fonction n'est pas appelée du tout et une ligne est écrite dans le journal. En mode normal,
mêmes résultats que l'original (copie de référence, lecture seule).

Le vrai include/cupdate.py du clone patché est chargé, avec un module requests factice, et son
subprocess est remplacé par un enregistreur : aucun script n'est lancé, aucun accès réseau, rien
n'est écrit hors d'un dossier temporaire. Sautés si la copie de référence est absente.
"""

import importlib.util
import os
import shutil
import sys
import tempfile
import types
import unittest

import _cplatform
import _orig
import _paths  # noqa: F401
from test_patches import fresh_clone, run_script

ROOT = str(_paths.ROOT)
PATCH_0008 = os.path.join(ROOT, "pi", "patches", "0008-reglages-systeme-mode-pico.patch")
STARTUP = ("# After an update (the game restarted) : the settings of the system of the new version\n",
           "# The page Paramétrage of the themes")
LOG_LINE = "System settings : not applied in Pico card mode (patch 0008)"
VERSION = "V4.6.4_202611132000"


class Logs:
    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        return lambda message, *a: self.lines.append((level, str(message)))


class FakeConfig:
    def __init__(self, hardware):
        self.config = {"SectionAdvanced": {}}
        if hardware is not None:
            self.config["SectionAdvanced"]["hardware"] = hardware


class Recorder:
    """Remplace le module subprocess de cupdate : note les commandes, n'en lance aucune."""

    STDOUT = DEVNULL = object()
    TimeoutExpired = type("TimeoutExpired", (Exception,), {})

    def __init__(self):
        self.calls = []

    def run(self, command, **kwargs):
        self.calls.append(("run", list(command)))

    def Popen(self, command, **kwargs):  # noqa: N802 (nom de subprocess)
        self.calls.append(("Popen", list(command)))


def read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8").replace("\r\n", "\n")


def segment(source, markers):
    start, end = markers
    i = source.index(start)
    return source[i:source.index(end, i)]


@unittest.skipUnless(_orig.available(), "copie de RaspyDarts absente (%s)" % _orig.ORIG)
class Patch0008(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert _orig.status() == ""
        sys.dont_write_bytecode = True
        cls.tmp = tempfile.mkdtemp()
        cls.copy = fresh_clone(cls.tmp)
        out = run_script(cls.copy)
        assert out.returncode == 0, out.stderr
        cls.startup_patched = segment(read(os.path.join(cls.copy, "raspydarts.py")), STARTUP)
        cls.startup_original = segment(read(os.path.join(_orig.ORIG, "raspydarts.py")), STARTUP)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)
        assert _orig.status() == "", "le test a écrit dans la copie de référence"

    # --- fichiers du patch ---------------------------------------------------------------------

    def test_patch_files_and_line_endings(self):
        with open(PATCH_0008, "rb") as f:
            text = f.read().decode("utf-8")
        files = [l[len("+++ b/"):].strip() for l in text.splitlines() if l.startswith("+++ b/")]
        self.assertEqual(files, ["raspydarts.py"])
        with open(os.path.join(self.copy, "raspydarts.py"), "rb") as f:
            data = f.read()
        # Fins de ligne de la base gardées : LF depuis RaspyDarts 4.6.5
        self.assertEqual(data.count(b"\r\n"), 0, "CRLF dans un fichier LF")

    def test_single_call_in_game(self):
        # Le seul appel du jeu est celui du démarrage, entouré par le patch
        for root in (_orig.ORIG, self.copy):
            calls = []
            for folder, _, names in os.walk(root):
                if ".git" in folder:
                    continue
                for name in names:
                    if name.endswith(".py"):
                        path = os.path.join(folder, name)
                        if "apply_system_settings(" in read(path):
                            calls.append(os.path.relpath(path, root))
            with self.subTest(root=root):
                self.assertEqual(sorted(calls), ["include/cupdate.py", "raspydarts.py"])
                self.assertEqual(read(os.path.join(root, "raspydarts.py")).count("apply_system_settings("), 1)

    # --- démarrage, avec le vrai cupdate.py ----------------------------------------------------

    def load_cupdate(self, where, version_done=None, doubled=False):
        """include/cupdate.py du clone, ses chemins dans where, subprocess enregistreur."""
        saved = dict(sys.modules)
        package = types.ModuleType("include")
        package.__path__ = [os.path.join(self.copy, "include")]
        for name in [n for n in sys.modules if n == "include" or n.startswith("include.")]:
            del sys.modules[name]
        sys.modules["include"] = package
        sys.modules["requests"] = types.ModuleType("requests")
        try:
            spec = importlib.util.spec_from_file_location(
                "include.cupdate", os.path.join(self.copy, "include", "cupdate.py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            sys.modules.clear()
            sys.modules.update(saved)
        os.makedirs(where)
        script = os.path.join(where, "reglages_systeme.sh")
        with open(script, "w") as f:
            f.write("exit 1\n")
        module.SYSTEM_SETTINGS_SCRIPT = script
        module.SYSTEM_SETTINGS_STATE = os.path.join(where, "reglages-systeme")
        module.SYSTEM_SETTINGS_LOG = os.path.join(where, "reglages_systeme.log")
        conf_dir = os.path.join(where, "conf.d")
        os.makedirs(conf_dir)
        module.MQTT_CONF_DIR = conf_dir
        module.MQTT_OWN_CONF = os.path.join(conf_dir, "raspydarts.conf")
        if version_done is not None:
            with open(module.SYSTEM_SETTINGS_STATE, "w") as f:
                f.write(version_done + "\n")
        if doubled:
            with open(module.MQTT_OWN_CONF, "w") as f:
                f.write("listener 1883\nallow_anonymous true\n")
            # Écoute locale explicite ajoutée dans un autre fichier (§ 9.8, quatrième raison)
            with open(os.path.join(conf_dir, "local.conf"), "w") as f:
                f.write("listener 1883 127.0.0.1\n")
        module.subprocess = Recorder()
        return module

    def run_startup(self, source, hardware, case, craspberry_pico=False):
        where = tempfile.mkdtemp(dir=self.tmp)
        cupdate = self.load_cupdate(os.path.join(where, "sys"), **case)
        saved = dict(sys.modules)
        package = types.ModuleType("include")
        package.__path__ = [os.path.join(self.copy, "include")]
        for name in [n for n in sys.modules if n == "include" or n.startswith("include.")]:
            del sys.modules[name]
        sys.modules["include"] = package
        _cplatform.install(package, sys.modules)       # RaspyDarts 4.6.5 : un Pi 4
        if craspberry_pico:
            sys.modules["include.craspberry"] = types.SimpleNamespace(PICO=True)
        logs = Logs()
        namespace = {"Config": FakeConfig(hardware), "cupdate": cupdate, "logs": logs,
                     "version": VERSION, "os": os}
        try:
            exec(source, namespace)
        finally:
            sys.modules.clear()
            sys.modules.update(saved)
        return {"commands": [(kind, [arg.replace(where, "<tmp>") for arg in command])
                             for kind, command in cupdate.subprocess.calls],
                "logs": [(level, message.replace(where, "<tmp>")) for level, message in logs.lines]}

    # Les deux déclencheurs, et le cas où il n'y a rien à faire
    CASES = {"version changée": dict(),
             "écoute MQTT doublée": dict(version_done=VERSION, doubled=True),
             "déjà fait": dict(version_done=VERSION)}

    def test_normal_mode_identical_to_original(self):
        for hardware in (None, "normal"):
            for label, case in self.CASES.items():
                with self.subTest(hardware=hardware, case=label):
                    original = self.run_startup(self.startup_original, hardware, case)
                    patched = self.run_startup(self.startup_patched, hardware, case)
                    self.assertEqual(patched, original)

    def test_normal_mode_really_runs_script(self):
        # Garde-fou du test : en mode normal, les deux déclencheurs lancent bien le script
        for label, kind in (("version changée", "Popen"), ("écoute MQTT doublée", "run")):
            with self.subTest(case=label):
                result = self.run_startup(self.startup_patched, "normal", self.CASES[label])
                self.assertEqual(len(result["commands"]), 1)
                self.assertEqual(result["commands"][0][0], kind)
                self.assertEqual(result["commands"][0][1][:3], ["sudo", "-n", "bash"])
                self.assertTrue(result["commands"][0][1][3].endswith("reglages_systeme.sh"))
        result = self.run_startup(self.startup_patched, "normal", self.CASES["déjà fait"])
        self.assertEqual(result["commands"], [])

    def test_pico_mode_no_script_and_log_line(self):
        for label, case in self.CASES.items():
            for craspberry_pico, hardware in ((False, "pico"), (False, " Pico "), (True, None)):
                with self.subTest(case=label, hardware=hardware, craspberry=craspberry_pico):
                    result = self.run_startup(self.startup_patched, hardware, case, craspberry_pico)
                    self.assertEqual(result["commands"], [])
                    self.assertEqual(result["logs"], [("info", LOG_LINE)])


if __name__ == "__main__":
    unittest.main()
