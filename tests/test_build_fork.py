# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""tools/build_fork.sh et tools/check_fork.sh (spécification, complément 80).

Les forks sont construits à partir de ~/RaspyDarts-ref465 (RASPYDARTS_ORIG), en lecture seule :
clone --shared et checkout de BASE seulement, donc sans réseau. Les refus sont essayés avec un
HOME temporaire, où les copies de référence et ~/RaspyDarts-work sont de faux dossiers vides :
les vraies copies ne sont jamais désignées.
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest

import _orig

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BUILD = os.path.join(ROOT, "tools", "build_fork.sh")
CHECK = os.path.join(ROOT, "tools", "check_fork.sh")
PATCHES = os.path.join(ROOT, "pi", "patches")
IDENTITY = "Gohy Yves <6154641+diogeneyves@users.noreply.github.com>"
ELEVENTH = "RaspyDarts-Pico: add new files (cpico, hwbus, pico_proto, rpi_ws281x)"
NEW_FILES = ("include/cpico.py", "include/hwbus.py", "include/pico_proto.py",
             "pico_shims/rpi_ws281x.py")
# Identité imposée par apply_patches.sh (lignes 95-96 et 116-117), qui doit céder devant
# GIT_AUTHOR_* et GIT_COMMITTER_*.
APPLY_IDENTITY = "RaspyDarts-Pico <noreply@invalid>"
TEST_IDENTITY = ("-c", "user.name=Test", "-c", "user.email=test@invalid")


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True).stdout


def run(script, *args, env=None):
    full = dict(os.environ, RASPYDARTS_ORIG=_orig.ORIG)
    full.update(env or {})
    return subprocess.run([script, *args], capture_output=True, text=True, env=full)


def build(where, name):
    path = os.path.join(where, name)
    out = run(BUILD, "--upstream", _orig.ORIG, path)
    assert out.returncode == 0, out.stdout + out.stderr
    return path, out


def patch_headers():
    """(nom, adresse, date) des en-têtes From: et Date: des patchs de series, dans l'ordre."""
    with open(os.path.join(PATCHES, "series"), encoding="utf-8") as f:
        names = [l.strip() for l in f if l.strip() and not l.lstrip().startswith("#")]
    result = []
    for name in names:
        with open(os.path.join(PATCHES, name), encoding="utf-8") as f:
            head = f.read(2000)
        author = re.search(r"^From: (.*)$", head, re.M).group(1).strip()
        date = re.search(r"^Date: (.*)$", head, re.M).group(1).strip()
        result.append((author, date))
    return result


class Usage(unittest.TestCase):
    def test_help_in_french(self):
        for script in (BUILD, CHECK):
            out = run(script, "--help")
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("Usage", out.stdout)

    def test_bad_usage(self):
        self.assertEqual(run(BUILD).returncode, 1)
        self.assertEqual(run(BUILD, "--inconnue", "x").returncode, 1)
        self.assertEqual(run(CHECK, "un").returncode, 1)


class Refusals(unittest.TestCase):
    """Refus avant toute écriture, avec un HOME temporaire (aucune vraie copie désignée)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        for name in ("RaspyDarts-orig", "RaspyDarts-ref", "RaspyDarts-ref465",
                     "RaspyDarts-work"):
            os.makedirs(os.path.join(self.home, name))     # vides : seul le nom les fait refuser
        self.env = {"HOME": self.home,
                    "RASPYDARTS_ORIG": os.path.join(self.home, "RaspyDarts-ref465")}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def refused(self, path, code, message):
        out = run(BUILD, "--upstream", _orig.ORIG, path, env=self.env)
        self.assertEqual(out.returncode, code, out.stdout + out.stderr)
        self.assertIn(message, out.stderr)
        self.assertNotIn("push --force", out.stdout)

    def test_refuses_references_and_work_copy(self):
        for name in ("RaspyDarts-orig", "RaspyDarts-ref", "RaspyDarts-ref465",
                     "RaspyDarts-work"):
            path = os.path.join(self.home, name)
            self.refused(path, 3, "copie de référence")
            self.refused(os.path.join(path, "sous-dossier"), 3, "copie de référence")
            self.assertEqual(os.listdir(path), [], name)
            self.assertFalse(os.path.exists(os.path.join(path, "sous-dossier")))

    def test_refuses_non_empty_folder_and_deletes_nothing(self):
        path = os.path.join(self.tmp, "plein")
        os.makedirs(path)
        with open(os.path.join(path, "garder.txt"), "w") as f:
            f.write("x")
        self.refused(path, 2, "n'est pas vide")
        self.assertEqual(os.listdir(path), ["garder.txt"])

    def test_refuses_folder_inside_the_project_repository(self):
        top = git("rev-parse", "--show-toplevel", cwd=ROOT).strip()
        path = os.path.join(top, "fork-essai-inexistant")
        self.refused(path, 2, "dans le dépôt")
        self.assertFalse(os.path.exists(path))

    def test_refuses_missing_parent(self):
        self.refused(os.path.join(self.tmp, "absent", "fork"), 2, "parent")

    def test_refuses_file(self):
        path = os.path.join(self.tmp, "fichier")
        open(path, "w").close()
        self.refused(path, 2, "n'est pas un dossier")


@unittest.skipUnless(_orig.available(), "copie de RaspyDarts absente (%s)" % _orig.ORIG)
class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = (_orig.head(), _orig.status())
        cls.tmp = tempfile.mkdtemp()
        cls.fork, cls.out = build(cls.tmp, "fork")
        cls.reference, _ = build(cls.tmp, "reference")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)
        assert (_orig.head(), _orig.status()) == cls.before, "la copie de référence a changé"

    def commits(self, path, fmt):
        out = git("log", "--reverse", "--format=" + fmt + "%x00",
                  _orig.BASE_COMMIT + "..HEAD", cwd=path)
        return [c.strip("\n") for c in out.split("\x00")[:-1]]

    def test_base_plus_eleven_commits(self):
        self.assertEqual(git("rev-parse", "HEAD~11", cwd=self.fork).strip(), _orig.BASE_COMMIT)
        self.assertEqual(len(self.commits(self.fork, "%H")), 11)
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all", cwd=self.fork), "")
        for rel in NEW_FILES:
            self.assertTrue(os.path.isfile(os.path.join(self.fork, rel)), rel)
        self.assertEqual(self.commits(self.fork, "%s")[-1], ELEVENTH)

    def test_identity_overrides_apply_patches(self):
        # GIT_AUTHOR_* et GIT_COMMITTER_* priment sur les -c user.* de apply_patches.sh.
        authors = self.commits(self.fork, "%an <%ae>")
        committers = self.commits(self.fork, "%cn <%ce>")
        self.assertEqual(authors[-1], IDENTITY)
        self.assertEqual(committers, [IDENTITY] * 11)
        self.assertNotIn(APPLY_IDENTITY, authors + committers)

    def test_patch_authors_and_dates_kept(self):
        # git am garde l'auteur et la date des en-têtes des patchs.
        got = list(zip(self.commits(self.fork, "%an <%ae>"),
                       self.commits(self.fork, "%aD")))[:10]
        self.assertEqual(len(patch_headers()), 10)
        for (author, date), (want_author, want_date) in zip(got, patch_headers()):
            self.assertEqual(author, want_author)
            self.assertEqual(date.replace(" 0", " "), want_date.replace(" 0", " "))

    def test_prints_push_command_without_running_it(self):
        self.assertIn("git -C %s push --force <URL-FORK> HEAD:main" % os.path.realpath(self.fork),
                      self.out.stdout)

    def test_check_accepts_fresh_build(self):
        out = run(CHECK, self.fork, self.reference)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("OK", out.stdout)

    def altered_copy(self, name):
        path = os.path.join(self.tmp, name)
        subprocess.run(["git", "clone", "--quiet", "--shared", self.fork, path], check=True)
        return path

    def test_witness_altered_tree(self):
        path = self.altered_copy("arbre-altere")
        with open(os.path.join(path, "include", "hwbus.py"), "a", encoding="utf-8") as f:
            f.write("# altéré\n")
        git(*TEST_IDENTITY, "commit", "--quiet", "--amend", "--no-edit", "-a", cwd=path)
        out = run(CHECK, path, self.reference)
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
        self.assertIn("commit 11/11 : arbre différent", out.stderr)
        self.assertNotIn("message différent", out.stderr)
        self.assertNotIn("auteur différent", out.stderr)

    def test_witness_altered_message(self):
        path = self.altered_copy("message-altere")
        # Sujet intact, corps ajouté : seul %B diffère (le sujet seul ne le verrait pas).
        git(*TEST_IDENTITY, "commit", "--quiet", "--amend", "--only",
            "-m", ELEVENTH, "-m", "Altered body.", cwd=path)
        self.assertEqual(git("log", "-1", "--format=%s", cwd=path).strip(), ELEVENTH)
        out = run(CHECK, path, self.reference)
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
        self.assertIn("commit 11/11 : message différent", out.stderr)
        self.assertNotIn("arbre différent", out.stderr)

    def test_witness_extra_commit(self):
        path = self.altered_copy("commit-en-trop")
        git(*TEST_IDENTITY, "commit", "--quiet", "--allow-empty", "-m", "extra", cwd=path)
        out = run(CHECK, path, self.reference)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("11 commits attendus", out.stderr)


if __name__ == "__main__":
    unittest.main()
