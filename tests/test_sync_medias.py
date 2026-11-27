# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests sur PC de tools/sync_medias.sh (spécification § 12, 41e à 43e compléments).

Faux dossier de médias temporaire (en 777, comme sur le Mac), copie vers un dossier local
temporaire : aucun SSH, aucun Pi ; ces tests sont sautés si rsync est absent. Destination
distante : faux rsync et faux ssh qui notent leurs arguments.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest

import _paths

SCRIPT = os.path.join(str(_paths.ROOT), "tools", "sync_medias.sh")

# Fichiers du faux dossier : chemin relatif -> copié ou non
FILES = {
    "Gifs/x.gif": True,
    "Gifs/600 Gratos/a b.gif": True,
    "SpecialsMoves/bull.gif": True,
    "Scores/s1.png": True,
    "Images/Logos/logo.png": False,
    "Fonts/f.ttf": True,
    "Raspy2DMD/gifs_videos/v.gif": True,
    "Raspy2DMD/warn/w.png": True,
    "Gifs/.DS_Store": False,
    "Gifs/.cache/c.gif": False,
    "Gifs/r.cfg": False,
    "Scores/journal.log": False,
    "Fonts/followers.json": False,
    "Gifs/capture_20260605_173151.gif": False,
    "Gifs/capture-2026-10-07.png": False,
    "Gifs/c20261008.png": False,
    "Sounds/s.ogg": False,
    "Patterns/p.png": False,
    "Meteo/m.png": False,
    "EDFJoursTempo/e.png": False,
    "PerfVisualizer/v.png": False,
    "HDMI/.console_default_applied": False,
    ".config_backups/Raspy2DMD_20261007_170151.cfg": False,
    ".whatsnew_seen_version": False,
    "top.gif": False,
}
EMPTY_DIRS = ("Scores/vide", "Textes", "Videos")


def tree(root):
    """Fichiers et dossiers sous root, chemins relatifs."""
    found = set()
    for base, dirs, files in os.walk(root):
        for name in dirs + files:
            found.add(os.path.relpath(os.path.join(base, name), root))
    return found


def make_world_writable(root):
    """Droits 777 partout, comme la source du Mac (43e complément)."""
    for base, dirs, files in os.walk(root):
        for name in dirs + files:
            os.chmod(os.path.join(base, name), 0o777)
    os.chmod(root, 0o777)


def modes(root):
    """Droits (octal) des dossiers et des fichiers sous root, racine comprise."""
    found = {"dirs": {stat.S_IMODE(os.stat(root).st_mode)}, "files": set()}
    for base, dirs, files in os.walk(root):
        for name in dirs:
            found["dirs"].add(stat.S_IMODE(os.stat(os.path.join(base, name)).st_mode))
        for name in files:
            found["files"].add(stat.S_IMODE(os.stat(os.path.join(base, name)).st_mode))
    return found


@unittest.skipUnless(shutil.which("rsync"), "rsync absent")
class SyncMediasTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "Medias Raspy2DMD")
        self.dst = os.path.join(self.tmp.name, "dmd-medias")
        os.makedirs(self.dst)
        for relative, _ in FILES.items():
            path = os.path.join(self.src, relative)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as file:
                file.write(relative)
        for relative in EMPTY_DIRS:
            os.makedirs(os.path.join(self.src, relative))
        make_world_writable(self.src)

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, *args):
        return subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True, check=False)

    def expected_count_and_bytes(self):
        kept = [relative for relative, copied in FILES.items() if copied]
        return len(kept), sum(len(relative.encode("utf-8")) for relative in kept)

    def test_dry_run_copies_nothing(self):
        result = self.run_script("--source", self.src, "--dest", self.dst)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(tree(self.dst), set())
        self.assertIn("essai à blanc", result.stdout)
        count, size = self.expected_count_and_bytes()
        self.assertIn(f"Périmètre   : {count} fichiers, {size} octets", result.stdout)

    def test_apply_copies_only_the_perimeter(self):
        result = self.run_script("--apply", "--source", self.src, "--dest", self.dst)
        self.assertEqual(result.returncode, 0, result.stderr)
        files = {relative for relative in tree(self.dst) if os.path.isfile(os.path.join(self.dst, relative))}
        self.assertEqual(files, {relative for relative, copied in FILES.items() if copied})
        dirs = {relative for relative in tree(self.dst) if os.path.isdir(os.path.join(self.dst, relative))}
        self.assertFalse(dirs & set(EMPTY_DIRS), "dossier vide copié")
        self.assertNotIn("Sounds", dirs)
        self.assertNotIn("Images", dirs)
        count, size = self.expected_count_and_bytes()
        self.assertIn(f"Périmètre   : {count} fichiers, {size} octets", result.stdout)

    def test_apply_sets_755_and_644(self):
        result = self.run_script("--apply", "--source", self.src, "--dest", self.dst)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(modes(self.dst), {"dirs": {0o755}, "files": {0o644}})
        self.assertIn("dossiers 755, fichiers 644", result.stdout)

    def test_dry_run_after_apply_lists_nothing(self):
        self.assertEqual(self.run_script("--apply", "--source", self.src, "--dest", self.dst).returncode, 0)
        result = self.run_script("--source", self.src, "--dest", self.dst)
        self.assertEqual(result.returncode, 0, result.stderr)
        listed = result.stdout.split("Fichiers à transférer (absents ou différents sur la destination) :\n", 1)[1]
        self.assertEqual(listed.strip().splitlines(), ["Essai à blanc terminé : rien n'a été copié."])

    def test_dry_run_leaves_permissions(self):
        os.chmod(self.dst, 0o777)
        result = self.run_script("--source", self.src, "--dest", self.dst)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stat.S_IMODE(os.stat(self.dst).st_mode), 0o777)
        self.assertNotIn("Droits fixés", result.stdout)

    def test_apply_never_deletes(self):
        extra = os.path.join(self.dst, "Gifs", "deja_sur_le_pi.gif")
        os.makedirs(os.path.dirname(extra))
        with open(extra, "w", encoding="utf-8") as file:
            file.write("x")
        result = self.run_script("--apply", "--source", self.src, "--dest", self.dst)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.isfile(extra))

    def test_missing_source_is_refused(self):
        result = self.run_script("--apply", "--source", os.path.join(self.tmp.name, "absent"), "--dest", self.dst)
        self.assertEqual(result.returncode, 2)
        self.assertIn("dossier source introuvable", result.stderr)
        self.assertEqual(tree(self.dst), set())


FAKE_TOOL = """#!/bin/sh
echo "$(basename "$0") $*" >> "$FAKE_LOG"
"""


class SyncMediasRemoteTest(unittest.TestCase):
    """Destination distante : faux rsync et faux ssh, aucune connexion."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "Medias Raspy2DMD")
        os.makedirs(os.path.join(self.src, "Gifs"))
        with open(os.path.join(self.src, "Gifs", "x.gif"), "w", encoding="utf-8") as file:
            file.write("x")
        self.bin = os.path.join(self.tmp.name, "bin")
        os.makedirs(self.bin)
        for tool in ("rsync", "ssh"):
            path = os.path.join(self.bin, tool)
            with open(path, "w", encoding="utf-8") as file:
                file.write(FAKE_TOOL)
            os.chmod(path, 0o755)
        self.log = os.path.join(self.tmp.name, "log")

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, *args):
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", FAKE_LOG=self.log)
        result = subprocess.run(["bash", SCRIPT, "--source", self.src, *args], capture_output=True, text=True,
                                check=False, env=env)
        calls = []
        if os.path.exists(self.log):
            with open(self.log, encoding="utf-8") as file:
                calls = file.read().splitlines()
        return result, calls

    def test_default_destination_gets_chmod_by_ssh(self):
        result, calls = self.run_script("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 2, calls)
        self.assertTrue(calls[0].startswith("rsync "))
        # Complément 77 : chemin relatif, résolu dans le dossier personnel de l'utilisateur distant
        self.assertIn(" pi@raspydarts.local:dmd-medias/", calls[0])
        self.assertEqual(calls[1], "ssh -4 pi@raspydarts.local find 'dmd-medias' -type d -exec chmod 755 {} + "
                                   "&& find 'dmd-medias' -type f -exec chmod 644 {} +")

    def test_subfolder_of_media_root_is_allowed(self):
        for dest, path in (("pi@h:dmd-medias/essai/", "dmd-medias/essai"),
                           ("h:~/dmd-medias/essai/", "dmd-medias/essai"),
                           ("pi@h:/home/pi/dmd-medias/essai/", "/home/pi/dmd-medias/essai"),
                           ("joueur@h:/home/joueur/dmd-medias/", "/home/joueur/dmd-medias")):
            with self.subTest(dest=dest):
                if os.path.exists(self.log):
                    os.remove(self.log)
                result, calls = self.run_script("--apply", "--dest", dest)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("find '%s' -type d" % path, calls[-1])

    def test_other_remote_path_gets_no_chmod(self):
        for dest in ("pi@h:/home/pi/", "pi@h:/home/pi/dmd-medias-autre/", "pi@h:/home/pi/dmd-medias/../.raspydarts/",
                     "pi@h:/home/joueur/dmd-medias/", "h:/home/pi/dmd-medias/", "pi@h:dmd-medias/../x/",
                     "pi@h:dmd-medias-autre/"):
            with self.subTest(dest=dest):
                if os.path.exists(self.log):
                    os.remove(self.log)
                result, calls = self.run_script("--apply", "--dest", dest)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([call for call in calls if call.startswith("ssh")], [])
                self.assertIn("avertissement", result.stderr)

    def test_dry_run_gets_no_chmod(self):
        result, calls = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1, calls)
        self.assertTrue(calls[0].startswith("rsync "))


class SyncMediasScriptTest(unittest.TestCase):
    def test_help_in_french(self):
        result = subprocess.run(["bash", SCRIPT, "--help"], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn("essai à blanc", result.stdout)
        self.assertIn("--apply", result.stdout)

    def test_source_required(self):
        """Complément 77 : pas de dossier source par défaut."""
        result = subprocess.run(["bash", SCRIPT, "--dest", "/tmp/nulle-part/"], capture_output=True,
                                text=True, check=False)
        self.assertEqual(result.returncode, 64)
        self.assertIn("--source", result.stderr)

    def test_unknown_option(self):
        result = subprocess.run(["bash", SCRIPT, "--delete"], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 64)
        self.assertIn("option inconnue", result.stderr)

    def test_script_rules(self):
        with open(SCRIPT, "rb") as file:
            content = file.read()
        self.assertNotIn(b"\r", content)
        text = content.decode("utf-8")
        self.assertIn("set -euo pipefail", text)
        self.assertIn('-e "ssh -4"', text)
        self.assertIn('options=(-rlt ', text)
        self.assertIn('DEST="pi@raspydarts.local:dmd-medias/"', text)
        self.assertIn('SOURCE=""', text)
        self.assertNotIn("/Users/", text)
        self.assertNotIn("/home/pi", text)
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("--delete", code)
        self.assertTrue(os.access(SCRIPT, os.X_OK))


if __name__ == "__main__":
    unittest.main()
