# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Patch 0005 (raspydarts.py) : serveurs LED en mode carte Pico.

En mode Pico, PIN_TARGETLED et PIN_STRIPLED sont des canaux de la carte (0 à 2 ; cible 0,
ruban 1) : les serveurs LED sont lancés sans sudo, avec le remplaçant de rpi_ws281x
(pico_shims/ en tête de PYTHONPATH), le canal dans DARTSPICO_BRIDGE_LED_CHANNEL et une
broche fictive (18) que StripLeds_Server.py accepte. En mode normal, mêmes commandes que
l'original.

raspydarts.py est un script : ses deux blocs (abonnés du dispatcher, lancement des
serveurs) sont extraits et exécutés avec des objets factices, dans le fichier patché (clone
temporaire de la copie de référence au commit BASE, série pi/patches/ appliquée) et dans
l'original (copie de référence, lecture seule). Sautés si la copie de référence est absente.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest

import _orig
import _paths  # noqa: F401
from test_patches import fresh_clone, run_script

ROOT_DIR = "/opt/raspydarts"
BROKER = "broker.local"
SUBSCRIBERS = ("    # dispatcher dispatcher\n", "    if Config.get_value('SectionAdvanced', 'use-dmd'):")
SERVERS = ("#################\n# Start Leds servers\n#################\n", "    p_target = None\n")

# Fausse bibliothèque paho : la connexion échoue (le serveur s'arrête après avoir lu ses
# arguments et créé sa bande) ; les publications sont écrites sur la sortie d'erreur.
FAKE_PAHO = '''
import sys
class CallbackAPIVersion:
    VERSION2 = 2
class Client:
    def __init__(self, *a, **k):
        pass
    def connect_async(self, *a, **k):
        pass
    def loop_start(self):
        pass
    def publish(self, topic, payload, qos=0):
        sys.stderr.write("PUBLISH %s %s\\n" % (topic, payload))
    def connect(self, *a, **k):
        raise OSError("pas de broker (test)")
'''


def read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8").replace("\r\n", "\n")


def segment(source, markers, dedent=False):
    start, end = markers
    i = source.index(start)
    j = source.index(end, i)
    if end.strip().startswith("p_target"):
        j += len(end)
    text = source[i:j]
    return textwrap.dedent(text) if dedent else text


class FakeConfig:
    def __init__(self, hardware, file_exists=True):
        self.file_exists = file_exists
        self.config = {"SectionAdvanced": {"use-dmd": False, "use-matrix": False,
                                           "use-other": False, "use-hue": False,
                                           "target-bgbrightness": 40}}
        if hardware is not None:
            self.config["SectionAdvanced"]["hardware"] = hardware

    def get_value(self, section, key, *args, **kwargs):
        return self.config[section].get(key, False)


class Process:
    def __init__(self, cmd):
        self.cmd = cmd


LEDS_NORMAL = {"PIN_TARGETLED": "18", "BRI_TARGETLED": "50", "TYP_TARGETLED": "grb",
               "PIN_STRIPLED": "21", "NBR_STRIPLED": "60", "BRI_STRIPLED": "30",
               "TYP_STRIPLED": "grb"}
LEDS_PICO = {"PIN_TARGETLED": "0", "BRI_TARGETLED": "50", "TYP_TARGETLED": "grb",
             "PIN_STRIPLED": "1", "NBR_STRIPLED": "60", "BRI_STRIPLED": "30",
             "TYP_STRIPLED": "rgb"}
TARGET_LEDS = {"S1": "0,1,2", "D1": "3"}


@unittest.skipUnless(_orig.available(), "copie de RaspyDarts absente (%s)" % _orig.ORIG)
class Patch0005(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert _orig.status() == ""
        sys.dont_write_bytecode = True
        cls.tmp = tempfile.mkdtemp()
        cls.copy = fresh_clone(cls.tmp)
        out = run_script(cls.copy)
        assert out.returncode == 0, out.stderr
        cls.patched_path = os.path.join(cls.copy, "raspydarts.py")
        cls.patched = read(cls.patched_path)
        cls.original = read(os.path.join(_orig.ORIG, "raspydarts.py"))
        cls.saved_modules = dict(sys.modules)
        # Paquet include du clone patché (ci2c, cpico), pour les lignes ajoutées
        for name in [n for n in sys.modules if n == "include" or n.startswith("include.")]:
            del sys.modules[name]
        package = types.ModuleType("include")
        package.__path__ = [os.path.join(cls.copy, "include")]
        sys.modules["include"] = package

    @classmethod
    def tearDownClass(cls):
        for name in [n for n in sys.modules if n not in cls.saved_modules]:
            del sys.modules[name]
        sys.modules.update(cls.saved_modules)
        shutil.rmtree(cls.tmp)
        assert _orig.status() == "", "le test a écrit dans la copie de référence"

    def run_blocks(self, source, hardware, leds, file_exists=True):
        """Les deux blocs de raspydarts.py ; renvoie ce qu'ils ont fait."""
        popen = []

        def fake_popen(cmd, **kwargs):
            popen.append((list(cmd), kwargs))
            return Process(cmd)

        dispatcher = types.SimpleNamespace(strip_topic="raspydarts/StripLeds",
                                           target_topic="raspydarts/TargetLeds")
        namespace = {
            "Config": FakeConfig(hardware, file_exists), "config_leds": dict(leds),
            "config_target_leds": dict(TARGET_LEDS), "cpaths": types.SimpleNamespace(ROOT_DIR=ROOT_DIR),
            "Popen": fake_popen, "open": lambda name, *a, **k: "log:" + name,
            "logs": types.SimpleNamespace(debug=lambda *a: None, info=lambda *a: None),
            "dispatcher": dispatcher, "mqtt_broker": BROKER, "bg_colors_sending": "red_green",
            "sleep": lambda s: None,
        }
        sys.modules.pop("include.craspberry", None)
        exec(segment(source, SUBSCRIBERS, dedent=True), namespace)
        exec(segment(source, SERVERS), namespace)
        for kwargs in [k for _, k in popen]:
            env = kwargs.get("env")
            if env is not None:
                kwargs["env"] = {k: v for k, v in env.items()
                                 if k in ("PYTHONPATH", "DARTSPICO_BRIDGE_LED_CHANNEL",
                                          "RASPYDARTS_MQTT_HOST")}
        return {"subscribers": namespace["subsribers"], "popen": popen,
                "strip": (dispatcher.strip_leds, namespace["p_strip"] is not None),
                "target": (dispatcher.target_leds, namespace["p_target"] is not None)}

    # --- mode normal ------------------------------------------------------------------------

    def test_normal_mode_identical_to_original(self):
        cases = [LEDS_NORMAL, LEDS_PICO, dict(LEDS_NORMAL, PIN_STRIPLED="0"),
                 dict(LEDS_NORMAL, PIN_TARGETLED="2", PIN_STRIPLED="10"),
                 dict(LEDS_NORMAL, PIN_TARGETLED="", PIN_STRIPLED="5")]
        for hardware in (None, "normal", "autre"):
            for leds in cases:
                for file_exists in (True, False):
                    with self.subTest(hardware=hardware, leds=leds, file_exists=file_exists):
                        self.assertEqual(self.run_blocks(self.patched, hardware, leds, file_exists),
                                         self.run_blocks(self.original, hardware, leds, file_exists))

    def test_normal_mode_really_launches(self):
        # Garde-fou du test : l'original lance bien les deux serveurs, avec sudo
        done = self.run_blocks(self.original, None, LEDS_NORMAL)
        self.assertEqual(done["subscribers"], ["StripLeds", "TargetLeds"])
        self.assertEqual([cmd[0] for cmd, _ in done["popen"]], ["sudo", "sudo"])
        self.assertEqual(done["popen"][0][1], {"stdout": "log:logs/StripLeds.log"})

    # --- mode Pico --------------------------------------------------------------------------

    def test_pico_mode_launches_servers_without_sudo(self):
        done = self.run_blocks(self.patched, "pico", LEDS_PICO)
        self.assertEqual(done["subscribers"], ["StripLeds", "TargetLeds"])
        self.assertEqual((done["strip"], done["target"]), ((True, True), (True, True)))
        (strip, strip_kw), (target, target_kw) = done["popen"]
        self.assertEqual(strip, ["python3", "addons/StripLeds_Server.py", "-host=" + BROKER,
                                 "-type=rgb", "9", "raspydarts/StripLeds", "18", "60", "30"])
        self.assertEqual(target, ["python3", ROOT_DIR + "/addons/TargetLeds_Server.py",
                                  "-host=" + BROKER, "-type=grb", "-bgcolors=red_green",
                                  "-bgbrightness=40", "10", "raspydarts/TargetLeds",
                                  str(TARGET_LEDS), "18", "50"])
        shims = os.path.join(ROOT_DIR, "pico_shims")
        self.assertEqual(strip_kw["stdout"], "log:logs/StripLeds.log")
        self.assertEqual(target_kw["stdout"], "log:logs/TargetLeds.log")
        for kwargs, channel in ((strip_kw, "1"), (target_kw, "0")):
            env = kwargs["env"]
            self.assertEqual(env["PYTHONPATH"].split(os.pathsep)[0], shims)
            self.assertEqual(env["DARTSPICO_BRIDGE_LED_CHANNEL"], channel)
            self.assertEqual(env["RASPYDARTS_MQTT_HOST"], BROKER)

    def test_pico_mode_channels(self):
        # Canaux 0 à 2 acceptés, dans les deux sens ; autre valeur : pas de serveur
        done = self.run_blocks(self.patched, "pico", dict(LEDS_PICO, PIN_TARGETLED="2",
                                                          PIN_STRIPLED="0"))
        channels = [k["env"]["DARTSPICO_BRIDGE_LED_CHANNEL"] for _, k in done["popen"]]
        self.assertEqual(channels, ["0", "2"])
        done = self.run_blocks(self.patched, "pico", dict(LEDS_PICO, PIN_TARGETLED="3",
                                                          PIN_STRIPLED="x"))
        self.assertEqual((done["subscribers"], done["popen"]), ([], []))
        self.assertEqual((done["strip"], done["target"]), ((False, False), (False, False)))

    def test_pico_mode_without_strip(self):
        # Ruban de 0 LED (assistant : pas de ruban) : StripLeds_Server le refuserait
        for count in ("0", 0, ""):
            with self.subTest(count=count):
                done = self.run_blocks(self.patched, "pico", dict(LEDS_PICO, NBR_STRIPLED=count))
                self.assertEqual(done["subscribers"], ["TargetLeds"])
                self.assertEqual([cmd[1] for cmd, _ in done["popen"]],
                                 [ROOT_DIR + "/addons/TargetLeds_Server.py"])

    def test_pico_mode_without_config_file(self):
        done = self.run_blocks(self.patched, "pico", LEDS_PICO, file_exists=False)
        self.assertEqual(done["popen"], [])

    def test_pico_mode_keeps_gpio_pins(self):
        # Broches du Pi (10, 12, 18, 21) en mode Pico : lancement d'origine, inchangé
        self.assertEqual(self.run_blocks(self.patched, "pico", LEDS_NORMAL),
                         self.run_blocks(self.original, None, LEDS_NORMAL))

    def test_pico_mode_type_option(self):
        # Ordre inconnu : pas d'option -type (le serveur garde GRB)
        done = self.run_blocks(self.patched, "pico", dict(LEDS_PICO, TYP_STRIPLED="bgr"))
        self.assertNotIn("-type=bgr", done["popen"][0][0])
        self.assertEqual(done["popen"][0][0][3], "9")

    # --- vrai serveur, remplaçant de rpi_ws281x ------------------------------------------------

    def test_strip_server_accepts_pico_command(self):
        done = self.run_blocks(self.patched, "pico", LEDS_PICO)
        cmd, kwargs = done["popen"][0]
        fake = os.path.join(self.tmp, "faux_paho", "paho", "mqtt")
        os.makedirs(fake, exist_ok=True)
        for package in (os.path.dirname(fake), fake):
            open(os.path.join(package, "__init__.py"), "w").close()
        with open(os.path.join(fake, "client.py"), "w") as f:
            f.write(FAKE_PAHO)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        env.update(kwargs["env"])
        # La copie installée : le préfixe ROOT_DIR remplacé par le clone
        env["PYTHONPATH"] = os.pathsep.join(
            [env["PYTHONPATH"].split(os.pathsep)[0].replace(ROOT_DIR, self.copy),
             os.path.dirname(os.path.dirname(fake))])
        out = subprocess.run([sys.executable] + cmd[1:], cwd=self.copy, env=env,
                             capture_output=True, text=True, timeout=60)
        self.assertIn("Cannot connect", out.stdout, out.stdout + out.stderr)
        self.assertNotIn("ERROR :", out.stdout)              # arguments acceptés
        # Bande créée par le remplaçant, sur le canal 1, ordre RGB
        self.assertIn("PUBLISH raspydarts/hw/cmd SET led1_order RGB", out.stderr)


if __name__ == "__main__":
    unittest.main()
