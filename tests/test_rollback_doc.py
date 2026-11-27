# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Retour arrière de tools/install_services.sh --apply (complément 86) : docs/PROCEDURE.md § 2.7.

Les chemins attendus ne sont pas écrits à la main : --apply est lancé avec un faux ssh qui garde
l'archive (aucune connexion), puis le install_remote.sh réellement généré est exécuté en local
avec de fausses commandes (install, cp, chown, find, udevadm, systemctl, pgrep) qui notent leurs
arguments sans rien écrire. Chaque destination notée, l'archive de /tmp et chaque rechargement
doivent figurer au § 2.7 ; les configurations /etc/raspydarts/*.toml et le dossier
/etc/raspydarts, gardés par défaut, seulement dans l'étape facultative.
"""

import os
import pwd
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

import _paths
from test_install_markers import FAKE_SSH, I75W, PICO, run_install

ROOT = Path(_paths.ROOT)
PROCEDURE = ROOT / "docs" / "PROCEDURE.md"
SECTION = "### 2.7 "
OPTIONAL = "#### Configurations : étape facultative"
CONFIG_DIR = "/etc/raspydarts"

# Commandes du install_remote.sh remplacées par une fausse qui note "nom arguments" et réussit.
FAKED = ("install", "cp", "chown", "find", "udevadm", "systemctl", "pgrep")
# Premiers mots admis dans install_remote.sh : une commande nouvelle fait échouer le test, pour
# qu'elle soit ajoutée ici, aux fausses commandes et au § 2.7 (rien ne doit tourner pour de vrai).
KNOWN_WORDS = set(FAKED) | {"set", "src=", "sh", "echo", "for", "do", "done", "if", "then",
                            "else", "fi"}
FAKE_CMD = """#!/bin/sh
echo "$(basename "$0") $*" >> "$FAKE_LOG"
case "$(basename "$0")" in pgrep) exit 1 ;; esac
exit 0
"""


def section(text, start):
    """Texte d'une section, du titre `start` au titre suivant de même niveau ou plus haut."""
    level = len(start.split(" ")[0])
    lines = text.split("\n")
    begin = next((i for i, line in enumerate(lines) if line.startswith(start)), None)
    if begin is None:
        return None
    end = len(lines)
    for i in range(begin + 1, len(lines)):
        m = re.match(r"(#+) ", lines[i])
        if m and len(m.group(1)) <= level:
            end = i
            break
    return "\n".join(lines[begin:end])


def missing(text, expected):
    """Éléments attendus absents du texte, dans l'ordre donné."""
    return [item for item in expected if item not in text]


def is_config(path):
    return path == CONFIG_DIR or path.startswith(CONFIG_DIR + "/")


class RollbackDocTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("git") is None or shutil.which("bash") is None:
            raise unittest.SkipTest("git ou bash absent")
        user = pwd.getpwuid(os.getuid()).pw_name
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user):
            raise unittest.SkipTest("utilisateur courant refusé par --user : %s" % user)
        for name in ("hwd.toml", "dmd.toml"):
            if os.path.exists(os.path.join(CONFIG_DIR, name)):
                # Le script garderait ce fichier sans l'installer : il ne serait pas noté.
                raise unittest.SkipTest("%s/%s existe sur cette machine" % (CONFIG_DIR, name))
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "ssh").write_text(FAKE_SSH, encoding="utf-8")
        for name in FAKED:
            (bin_dir / name).write_text(FAKE_CMD, encoding="utf-8")
        for path in bin_dir.iterdir():
            path.chmod(0o755)
        ssh_log, remote_log, archive = tmp / "ssh.log", tmp / "remote.log", tmp / "install.tgz"

        # 1. --apply avec le faux ssh seul (premier dans PATH) : l'archive est gardée.
        ssh_bin = tmp / "ssh_only"
        ssh_bin.mkdir()
        shutil.copy2(bin_dir / "ssh", ssh_bin / "ssh")
        env = dict(os.environ, PATH="%s:%s" % (ssh_bin, os.environ["PATH"]),
                   FAKE_LOG=str(ssh_log), FAKE_ARCHIVE=str(archive))
        result = run_install(["--apply", "--host", "essai@hote.invalid", "--user", user,
                              "--pico-serial", PICO, "--i75w-serial", I75W], env=env)
        if result.returncode != 0:
            raise AssertionError("--apply avec le faux ssh : %s" % result.stderr)
        calls = ssh_log.read_text(encoding="utf-8")
        cls.uploads = re.findall(r"cat > '([^']+)'", calls)

        # 2. install_remote.sh réellement généré, exécuté avec les fausses commandes.
        payload = tmp / "payload"
        payload.mkdir()
        with tarfile.open(archive, "r:gz") as tar:
            if hasattr(tarfile, "data_filter"):
                tar.extractall(payload, filter="data")
            else:
                tar.extractall(payload)
        cls.remote = (payload / "install_remote.sh").read_text(encoding="utf-8")
        env = dict(os.environ, PATH="%s:%s" % (bin_dir, os.environ["PATH"]),
                   FAKE_LOG=str(remote_log))
        result = subprocess.run(["sh", str(payload / "install_remote.sh"), str(payload)],
                                capture_output=True, text=True, timeout=60, env=env)
        if result.returncode != 0:
            raise AssertionError("install_remote.sh : %s" % result.stderr)
        cls.paths, cls.commands = [], []
        for line in remote_log.read_text(encoding="utf-8").splitlines():
            name, *args = line.split()
            if name == "install":
                targets = [a for a in args if a.startswith("/")] if "-d" in args else [args[-1]]
            elif name == "cp":
                targets = [args[-1]]
            elif name in ("udevadm", "systemctl"):
                cls.commands.append(line)
                continue
            else:
                continue  # chown, find : sur un dossier déjà noté ; pgrep : lecture seule
            for target in targets:
                target = target.rstrip("/")
                if target not in cls.paths:
                    cls.paths.append(target)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rollback(self):
        text = section(PROCEDURE.read_text(encoding="utf-8"), SECTION)
        self.assertIsNotNone(text, "docs/PROCEDURE.md : section « %s» (retour arrière, "
                                   "complément 86) absente" % SECTION)
        optional = section(text, OPTIONAL)
        self.assertIsNotNone(optional, "§ 2.7 : sous-section « %s » absente" % OPTIONAL)
        return text.replace(optional, ""), optional

    def test_harness_witness(self):
        # Sans ce témoin, un faux log vide ferait passer les autres tests sans rien vérifier.
        for path in ("/opt/dartspico-bridge", "/etc/systemd/system/raspydarts-hwd.service",
                     "/etc/systemd/system/raspydarts-dmd.service",
                     "/etc/udev/rules.d/73-dartspico-bridge.rules", CONFIG_DIR,
                     CONFIG_DIR + "/hwd.toml", CONFIG_DIR + "/dmd.toml"):
            with self.subTest(path=path):
                self.assertIn(path, self.paths)
        self.assertEqual(self.uploads, ["/tmp/dartspico-bridge-install.tgz"])
        self.assertEqual(sorted(self.commands),
                         ["systemctl daemon-reload", "udevadm control --reload"])

    def test_remote_script_commands_known(self):
        words = set()
        for line in self.remote.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            word = line.split()[0]
            words.add("src=" if word.startswith("src=") else word)
        self.assertEqual(sorted(words - KNOWN_WORDS), [])

    def test_main_step_covers_installed_paths(self):
        main, _ = self.rollback()
        expected = [p for p in self.paths if not is_config(p)] + self.uploads + self.commands
        self.assertEqual(missing(main, expected), [])

    def test_configs_only_in_optional_step(self):
        main, optional = self.rollback()
        configs = [p for p in self.paths if is_config(p)]
        self.assertEqual(missing(optional, configs), [])
        self.assertIn("rmdir " + CONFIG_DIR, optional)
        for name in ("hwd.toml", "dmd.toml"):
            with self.subTest(name=name):
                self.assertNotIn(name, main)  # gardés par défaut (complément 86, Q2)

    def test_game_rules_untouched(self):
        text = "".join(self.rollback())
        self.assertEqual(re.findall(r"rules\.d/7[12]-\S*", text), [])
        self.assertNotIn("list-unit-files \"raspydarts*\"", text)

    def test_witness_missing_rule(self):
        # La règle 73 retirée d'une copie du texte : la fonction renvoie exactement ce chemin.
        main, _ = self.rollback()
        expected = [p for p in self.paths if not is_config(p)] + self.uploads + self.commands
        broken = main.replace("73-dartspico-bridge.rules", "")
        self.assertEqual(missing(broken, expected), ["/etc/udev/rules.d/73-dartspico-bridge.rules"])


if __name__ == "__main__":
    unittest.main()
