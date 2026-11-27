# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Patch 0006 : option [SectionAdvanced] send-stats pour couper l'envoi des statistiques.

RaspyDarts 4.6.4 envoie les statistiques de chaque partie à raspystats.raspyhub.fr
(include/cupdate.py, send_infos, appelée par raspydarts.py au début, à l'annulation, à la fin
et à l'interruption d'une manche). C'est le seul envoi réseau de début ou de fin de partie.
Option send-stats, activée par défaut : désactivée, aucun envoi, aucune erreur, une ligne
de journal. Activée : comportement identique à l'original (lu dans ~/RaspyDarts-ref465, sans
y écrire). Aucun accès réseau : requests et Thread sont remplacés par des enregistreurs.
"""

import datetime as real_datetime
import importlib.util
import itertools
import os
import shutil
import sys
import tempfile
import types
import unittest

import _cplatform
import _orig
import _paths  # noqa: F401
from test_patch_0002 import Bench
from test_patches import fresh_clone, run_script

ROOT = str(_paths.ROOT)
PATCH_0006 = os.path.join(ROOT, "pi", "patches", "0006-statistiques-option-send-stats.patch")
COUNTER = itertools.count()
HOOK = "cupdate.set_send_stats(config_advanced.get('send-stats', True))"


class Logs:
    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        return lambda message, *a: self.lines.append((level, str(message)))


class FixedDatetime:
    @staticmethod
    def now():
        return real_datetime.datetime(2026, 11, 19, 12, 0, 0, tzinfo=real_datetime.timezone.utc)


class FakeRequests:
    """requests simulé : chaque post est noté, la réponse vaut 200."""

    class exceptions:
        class RequestException(Exception):
            pass

    def __init__(self):
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return types.SimpleNamespace(status_code=200, text="ok")

    def get(self, *a, **k):
        raise AssertionError("aucun get attendu")


def game_args(event="start"):
    return (Logs(), "X01", 2, {"startingat": 301, "double-out": True}, "Raspberry Pi 4", event,
            False, "play-1", "V4.6.4_202611132000")


@unittest.skipUnless(_orig.available(), "copie de RaspyDarts absente (%s)" % _orig.ORIG)
class Patch0006Cupdate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert _orig.status() == ""
        sys.dont_write_bytecode = True
        cls.tmp = tempfile.mkdtemp()
        cls.copy = fresh_clone(cls.tmp)
        out = run_script(cls.copy)
        assert out.returncode == 0, out.stderr
        cls.saved_modules = dict(sys.modules)

    @classmethod
    def tearDownClass(cls):
        for name in [n for n in sys.modules if n not in cls.saved_modules]:
            del sys.modules[name]
        sys.modules.update(cls.saved_modules)
        shutil.rmtree(cls.tmp)
        assert _orig.status() == "", "le clone de référence a été modifié"

    def load(self, original):
        """cupdate.py d'origine ou patché ; réseau, fil et horloge remplacés."""
        requests = FakeRequests()
        sys.modules["requests"] = requests
        pkg = types.ModuleType("include")
        pkg.__path__ = []
        cpaths = types.SimpleNamespace(ROOT_DIR=self.tmp, user_dir=lambda: self.tmp)
        pkg.cpaths = cpaths
        sys.modules["include"] = pkg
        sys.modules["include.cpaths"] = cpaths
        _cplatform.install(pkg, sys.modules)           # RaspyDarts 4.6.5 : un Pi 4 avec GPIO
        base = _orig.ORIG if original else self.copy
        name = "cupdate_cas_%d" % next(COUNTER)
        spec = importlib.util.spec_from_file_location(name, os.path.join(base, "include", "cupdate.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        threads = []

        class SyncThread:
            def __init__(self, target, args=(), daemon=None):
                threads.append(target)
                self.target, self.args = target, args

            def start(self):
                self.target(*self.args)

        module.Thread = SyncThread
        module.datetime = FixedDatetime
        module.machine_id = lambda: "0123456789abcdef"
        return module, requests, threads

    def send(self, module, event="start"):
        args = game_args(event)
        module.send_infos(*args, set_number=2, network=False)
        return args[0].lines

    def test_default_identical_to_original(self):
        for event in ("start", "canceled", "finished", "interrupted"):
            with self.subTest(event=event):
                results = []
                for original in (True, False):
                    module, requests, threads = self.load(original)
                    logs = self.send(module, event)
                    results.append((requests.posts, len(threads), logs))
                self.assertEqual(results[1], results[0])
                self.assertEqual(len(results[0][0]), 1)                 # garde-fou : un envoi
                self.assertIn("raspystats.raspyhub.fr", results[0][0][0][0])

    def test_default_value_is_enabled(self):
        module, _, _ = self.load(False)
        self.assertIs(module.SEND_STATS, True)

    def test_disabled_sends_nothing(self):
        for value in (False, "False", "false", " FALSE ", "0", 0, "no", "non", "off", "n"):
            with self.subTest(value=value):
                module, requests, threads = self.load(False)
                module.set_send_stats(value)
                for event in ("start", "finished"):
                    logs = self.send(module, event)
                    self.assertEqual(requests.posts, [])
                    self.assertEqual(threads, [])                     # aucun fil lancé
                    self.assertEqual(len(logs), 1)                    # une ligne de journal
                    level, message = logs[0]
                    self.assertEqual(level, "info")
                    self.assertIn("send-stats", message)
                    self.assertIn(event, message)

    def test_enabled_values_send(self):
        for value in (True, "True", "true", "1", 1, "yes", "oui", "on", "", None):
            with self.subTest(value=value):
                module, requests, _ = self.load(False)
                module.set_send_stats(False)
                module.set_send_stats(value)
                self.send(module)
                self.assertEqual(len(requests.posts), 1)

    def test_post_infos_untouched(self):
        # Un envoi déjà préparé n'est pas concerné : seul send_infos décide.
        module, requests, _ = self.load(False)
        module.set_send_stats(False)
        module.post_infos(Logs(), {"machine_id": "x", "event": "start"})
        self.assertEqual(len(requests.posts), 1)


@unittest.skipUnless(_orig.available(), "copie de RaspyDarts absente (%s)" % _orig.ORIG)
class Patch0006Config(Bench):
    def advanced(self, written):
        text = written.decode()
        start = text.index("[SectionAdvanced]")
        return text[start:text.index("\n[", start + 1)].splitlines()

    def test_default_enabled_and_not_written(self):
        written, mem, logs, _ = self.run_config(b"", False)
        self.assertIs(mem["SectionAdvanced"]["send-stats"], True)
        self.assertNotIn(b"send-stats", written)                      # fichier d'origine inchangé

    def test_disabled_read_and_kept(self):
        default, _, _, _ = self.run_config(b"", False)
        data = default.replace(b"richard-mode:False\n", b"richard-mode:False\nsend-stats:False\n")
        written, mem, logs, _ = self.run_config(data, False)
        self.assertIs(mem["SectionAdvanced"]["send-stats"], False)
        self.assertFalse([l for l in logs if "Unexpected send-stats" in l[1]])
        lines = self.advanced(written)
        self.assertIn("send-stats:False", lines)
        self.assertLess(lines.index("send-stats:False"), lines.index("### For Richard's case"))
        again, mem2, _, _ = self.run_config(written, False)            # relu puis réécrit
        self.assertIs(mem2["SectionAdvanced"]["send-stats"], False)
        self.assertEqual(again, written)

    def test_enabled_written_as_default(self):
        default, _, _, _ = self.run_config(b"", False)
        data = default.replace(b"richard-mode:False\n", b"richard-mode:False\nsend-stats:True\n")
        written, mem, _, _ = self.run_config(data, False)
        self.assertIs(mem["SectionAdvanced"]["send-stats"], True)
        self.assertEqual(written, default)


@unittest.skipUnless(_orig.available(), "copie de RaspyDarts absente (%s)" % _orig.ORIG)
class Patch0006Game(unittest.TestCase):
    """raspydarts.py patché : la valeur lue est passée à cupdate avant toute partie."""

    @classmethod
    def setUpClass(cls):
        assert _orig.status() == ""
        cls.tmp = tempfile.mkdtemp()
        cls.copy = fresh_clone(cls.tmp)
        out = run_script(cls.copy)
        assert out.returncode == 0, out.stderr
        with open(os.path.join(cls.copy, "raspydarts.py"), encoding="utf-8") as f:
            cls.lines = f.read().splitlines()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)
        assert _orig.status() == ""

    def index(self, text):
        found = [i for i, l in enumerate(self.lines) if l.strip() == text]
        self.assertEqual(len(found), 1, text)
        return found[0]

    def test_hook_after_config_before_games(self):
        hook = self.index(HOOK)
        self.assertEqual(self.lines[hook], HOOK)                       # niveau du module
        self.assertLess(self.index('config_advanced = Config.read_file("SectionAdvanced")'), hook)
        first_send = min(i for i, l in enumerate(self.lines) if "cupdate.send_infos(" in l)
        self.assertLess(hook, first_send)


class Patch0006Purity(unittest.TestCase):
    def test_files_and_only_added_lines(self):
        with open(PATCH_0006, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("From: Gohy Yves <6154641+diogeneyves@users.noreply.github.com>", text[:400])
        lines = text[text.index("\ndiff --git "):].splitlines()
        files = [l[len("+++ b/"):] for l in lines if l.startswith("+++ b/")]
        self.assertEqual(files, ["include/cconfig.py", "include/cupdate.py", "raspydarts.py"])
        removed = [l for l in lines if l.startswith("-") and not l.startswith("---")]
        self.assertEqual(removed, [])


if __name__ == "__main__":
    unittest.main()
