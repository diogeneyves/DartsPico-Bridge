# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Marqueurs des données propres à l'installation (complément 77) : numéros de série de la
règle udev, utilisateur et groupe des unités systemd.

tools/install_services.sh : options --user, --pico-serial et --i75w-serial obligatoires, casse
des séries contrôlée, marqueurs udev remplis dans la copie envoyée. --apply est lancé avec un
faux ssh qui garde l'archive : aucune connexion. pi/systemd/fill_units.sh (lancé sur le Pi par
--apply) est essayé en local, sur une copie des unités, avec l'utilisateur courant.
"""

import grp
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

ROOT = Path(_paths.ROOT)
INSTALL = ROOT / "tools" / "install_services.sh"
FILL = ROOT / "pi" / "systemd" / "fill_units.sh"
UNITS = ("raspydarts-hwd.service", "raspydarts-dmd.service")
PICO = "0123456789abcdef"          # fictif, minuscules (MicroPython)
I75W = "FEDCBA9876543210"          # fictif, majuscules (firmware C++)
GOOD = ["--user", "joueur", "--pico-serial", PICO, "--i75w-serial", I75W]

FAKE_SSH = """#!/bin/sh
echo "ssh $*" >> "$FAKE_LOG"
case "$*" in
  *"cat >"*) cat > "$FAKE_ARCHIVE" ;;
esac
exit 0
"""


def run_install(args, cwd=ROOT, script=INSTALL, env=None):
    return subprocess.run(["bash", str(script)] + list(args), cwd=cwd, capture_output=True,
                          text=True, timeout=60, env=env)


class OptionsTest(unittest.TestCase):

    def test_each_option_required(self):
        for i in range(0, len(GOOD), 2):
            args = GOOD[:i] + GOOD[i + 2:]
            with self.subTest(missing=GOOD[i]):
                result = run_install(args)
                self.assertEqual(result.returncode, 64, result.stdout)
                self.assertIn(GOOD[i], result.stderr)
                self.assertNotIn("Essai à blanc terminé", result.stdout)

    def test_serial_case_checked(self):
        for option, value, word in (("--pico-serial", PICO.upper(), "minuscules"),
                                    ("--i75w-serial", I75W.lower(), "majuscules"),
                                    ("--pico-serial", "0123456789abcde", "minuscules"),
                                    ("--i75w-serial", "FEDCBA987654321G", "majuscules"),
                                    ("--pico-serial", "<SERIE_PICO>", "minuscules")):
            with self.subTest(option=option, value=value):
                args = list(GOOD)
                args[args.index(option) + 1] = value
                result = run_install(args)
                self.assertEqual(result.returncode, 64)
                self.assertIn(word, result.stderr)

    def test_user_name_checked(self):
        for user in ("", "Pi", "1joueur", "jo ueur", "jo;ueur", "@RASPYDARTS_USER@", "a" * 33):
            with self.subTest(user=user):
                args = list(GOOD)
                args[1] = user
                self.assertEqual(run_install(args).returncode, 64)

    def test_no_default_user_nor_serial_in_script(self):
        text = INSTALL.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"(?i)\b[0-9a-f]{16}\b")
        self.assertIn('RUN_USER=""', text)
        self.assertIn('PICO_SERIAL=""', text)
        self.assertIn('I75W_SERIAL=""', text)

    def test_dry_run_shows_markers_as_they_are(self):
        result = run_install(GOOD)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        self.assertIn("rien n'a été envoyé", out)
        self.assertIn("User=@RASPYDARTS_USER@ -> joueur", out)
        self.assertIn("Group=@RASPYDARTS_GROUP@ -> groupe principal de joueur, lu sur le Pi à --apply", out)
        self.assertIn("<SERIE_PICO> -> %s" % PICO, out)
        self.assertIn("<SERIE_I75W> -> %s" % I75W, out)


class ApplyArchiveTest(unittest.TestCase):
    """--apply avec un faux ssh (premier dans PATH, hôte .invalid) : l'archive est gardée."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin = os.path.join(self.tmp.name, "bin")
        os.makedirs(self.bin)
        ssh = os.path.join(self.bin, "ssh")
        with open(ssh, "w", encoding="utf-8") as f:
            f.write(FAKE_SSH)
        os.chmod(ssh, 0o755)
        self.log = os.path.join(self.tmp.name, "log")
        self.archive = os.path.join(self.tmp.name, "install.tgz")
        self.env = dict(os.environ, PATH="%s:%s" % (self.bin, os.environ["PATH"]),
                        FAKE_LOG=self.log, FAKE_ARCHIVE=self.archive)

    def apply(self):
        result = run_install(["--apply", "--host", "essai@hote.invalid"] + GOOD, env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(self.log, encoding="utf-8") as f:
            calls = f.read().splitlines()
        self.assertEqual(len(calls), 2, calls)
        self.assertTrue(all("essai@hote.invalid" in c for c in calls))
        files = {}
        with tarfile.open(self.archive, "r:gz") as tar:
            for member in tar.getmembers():
                if member.isfile():
                    files[os.path.normpath(member.name)] = tar.extractfile(member).read().decode("utf-8")
        return files

    def test_udev_rule_filled_with_exact_case(self):
        rule = self.apply()["udev/73-dartspico-bridge.rules"]
        self.assertNotIn("<SERIE_", rule)
        self.assertIn('ATTRS{idProduct}=="0005", ATTRS{serial}=="%s", SYMLINK+="cible"' % PICO, rule)
        self.assertIn('ATTRS{idProduct}=="0009", ATTRS{serial}=="%s", SYMLINK+="dmd"' % I75W, rule)

    def test_units_filled_on_the_pi_before_any_write(self):
        files = self.apply()
        for unit in UNITS:
            self.assertIn("User=@RASPYDARTS_USER@", files["systemd/" + unit])
        self.assertIn("fill_units.sh", files)
        remote = files["install_remote.sh"]
        call = 'sh "$src/fill_units.sh" "$src/systemd" joueur'
        self.assertIn(call, remote)
        self.assertLess(remote.index(call), remote.index("install -d"))


class UdevMarkerRefusalTest(unittest.TestCase):
    """Marqueur absent de la règle, ou restant après remplacement : refus (code 2)."""

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git absent")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "depot"
        for part in ("tools/install_services.sh", "pi/hwd", "pi/dmd", "pi/systemd", "pi/udev", "shared"):
            src = ROOT / part
            dst = self.repo / part
            if src.is_dir():
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                dst.parent.mkdir(parents=True)
                shutil.copy2(src, dst)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)
        self.rule = self.repo / "pi" / "udev" / "73-dartspico-bridge.rules"
        self.script = self.repo / "tools" / "install_services.sh"

    def test_control_copy_runs(self):
        self.assertEqual(run_install(GOOD, cwd=self.repo, script=self.script).returncode, 0)

    def test_missing_marker_refused(self):
        self.rule.write_text(self.rule.read_text(encoding="utf-8").replace("<SERIE_I75W>", "X"),
                             encoding="utf-8")
        result = run_install(GOOD, cwd=self.repo, script=self.script)
        self.assertEqual(result.returncode, 2)
        self.assertIn("<SERIE_I75W>", result.stderr)

    def test_leftover_marker_refused(self):
        with open(self.rule, "a", encoding="utf-8") as f:
            f.write('# <SERIE_AUTRE>\n')
        result = run_install(GOOD, cwd=self.repo, script=self.script)
        self.assertEqual(result.returncode, 2)
        self.assertIn("marqueur", result.stderr)


class FillUnitsTest(unittest.TestCase):
    """pi/systemd/fill_units.sh <dossier> <utilisateur>, essayé avec l'utilisateur courant."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        for unit in UNITS:
            shutil.copy2(ROOT / "pi" / "systemd" / unit, self.dir / unit)
        entry = pwd.getpwuid(os.getuid())
        self.user = entry.pw_name
        self.group = grp.getgrgid(entry.pw_gid).gr_name

    def fill(self, user):
        return subprocess.run(["sh", str(FILL), str(self.dir), user], capture_output=True, text=True,
                              timeout=30)

    def contents(self):
        return {unit: (self.dir / unit).read_text(encoding="utf-8") for unit in UNITS}

    def test_user_and_primary_group(self):
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", self.user):
            self.skipTest("nom d'utilisateur courant hors format : %s" % self.user)
        result = self.fill(self.user)
        self.assertEqual(result.returncode, 0, result.stderr)
        for unit, text in self.contents().items():
            with self.subTest(unit=unit):
                self.assertIn("\nUser=%s\n" % self.user, text)
                self.assertIn("\nGroup=%s\n" % self.group, text)
                self.assertNotIn("@RASPYDARTS_", text)

    def test_unknown_user_refused_nothing_changed(self):
        before = self.contents()
        result = self.fill("absent-complement-77")
        self.assertEqual(result.returncode, 1)
        self.assertIn("absent-complement-77", result.stderr)
        self.assertEqual(self.contents(), before)

    def test_bad_user_name_refused(self):
        before = self.contents()
        for user in ("", "Pi Root", "jo;ueur"):
            with self.subTest(user=user):
                self.assertEqual(self.fill(user).returncode, 1)
        self.assertEqual(self.contents(), before)

    def test_leftover_marker_refused_nothing_changed(self):
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", self.user):
            self.skipTest("nom d'utilisateur courant hors format : %s" % self.user)
        path = self.dir / UNITS[1]
        path.write_text(path.read_text(encoding="utf-8") + "Environment=X=@RASPYDARTS_AUTRE@\n",
                        encoding="utf-8")
        before = self.contents()
        result = self.fill(self.user)
        self.assertEqual(result.returncode, 1)
        self.assertIn("marqueur", result.stderr)
        self.assertEqual(self.contents(), before)

    def test_empty_folder_refused(self):
        for unit in UNITS:
            (self.dir / unit).unlink()
        self.assertEqual(self.fill("root").returncode, 1)


if __name__ == "__main__":
    unittest.main()
