# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""tools/export_repo.sh : dossier d'export du commit initial de diogeneyves/RaspyDarts-Pico
(spécification, complément 81).

L'export se fait dans un dossier temporaire, jamais dans ~/RaspyDarts-Pico-export. Le script
lit seulement le dépôt (HEAD) ; la suite de tests n'est jamais lancée dans l'export ici
(--run-tests n'est pas passé). Dans le dépôt exporté lui-même, le projet est à la racine :
il n'y a plus rien à exporter, les tests qui exportent sont sautés.
"""

import difflib
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(ROOT, "tools", "export_repo.sh")
WORKFLOW = ".github/workflows/raspydarts-pico-tests.yml"
IDENTITY = "Gohy Yves <6154641+diogeneyves@users.noreply.github.com>"
MESSAGE = "Initial commit: RaspyDarts-Pico"
# Règles ajoutées au .gitignore du projet (complément 81, Q7).
ADDED_RULES = ("__pycache__/", "*.py[codz]", "build/", "dist/", "*.egg-info/", "lib/",
               "!firmware/i75w_display/lib/", "*.mo", "*.log", ".env", ".venv", "venv/",
               ".DS_Store", "CLAUDE.md", ".claude/")
# Lignes du workflow d'origine retirées ou changées (Q6) : 2 blocs paths (2 × 3 lignes),
# 5 working-directory, le témoin actionlint et le commentaire « My_GIT_Projects ».
REMOVED_LINES = 13
ADDED_LINES = 7


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True).stdout


def prefix():
    """Chemin du projet dans son dépôt (« RaspyDarts-Pico/ » dans My_GIT_Projects, vide à la racine)."""
    return git("rev-parse", "--show-prefix", cwd=ROOT).strip()


def run(*args, env=None):
    full = dict(os.environ)
    full.update(env or {})
    return subprocess.run([SCRIPT, *args], capture_output=True, text=True, env=full)


def tree(rev_path, cwd):
    """{chemin: (mode, blob)} de git ls-tree -r."""
    out = {}
    for line in git("ls-tree", "-r", "-z", rev_path, cwd=cwd).split("\0"):
        if line:
            meta, path = line.split("\t", 1)
            mode, _kind, sha = meta.split()
            out[path] = (mode, sha)
    return out


class Usage(unittest.TestCase):
    def test_help_in_french(self):
        out = run("--help")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Usage", out.stdout)
        self.assertIn("dossier", out.stdout)

    def test_bad_usage(self):
        self.assertEqual(run().returncode, 1)
        self.assertEqual(run("--inconnue", "x").returncode, 1)
        self.assertEqual(run("a", "b").returncode, 1)


@unittest.skipUnless(prefix(), "projet à la racine de son dépôt : déjà exporté")
class Refusals(unittest.TestCase):
    """Mêmes refus que tools/build_fork.sh, avant toute écriture (HOME temporaire)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        for name in ("RaspyDarts-orig", "RaspyDarts-ref", "RaspyDarts-ref465",
                     "RaspyDarts-work"):
            os.makedirs(os.path.join(self.home, name))
        self.env = {"HOME": self.home,
                    "RASPYDARTS_ORIG": os.path.join(self.home, "RaspyDarts-ref465")}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def refused(self, path, code, message):
        out = run(path, env=self.env)
        self.assertEqual(out.returncode, code, out.stdout + out.stderr)
        self.assertIn(message, out.stderr)

    def test_refuses_references_and_work_copy(self):
        for name in ("RaspyDarts-orig", "RaspyDarts-ref", "RaspyDarts-ref465",
                     "RaspyDarts-work"):
            path = os.path.join(self.home, name)
            self.refused(path, 3, "copie de référence")
            self.refused(os.path.join(path, "export"), 3, "copie de référence")
            self.assertEqual(os.listdir(path), [], name)

    def test_refuses_non_empty_folder_and_deletes_nothing(self):
        path = os.path.join(self.tmp, "plein")
        os.makedirs(path)
        open(os.path.join(path, "garder.txt"), "w").close()
        self.refused(path, 2, "n'est pas vide")
        self.assertEqual(os.listdir(path), ["garder.txt"])

    def test_refuses_folder_inside_the_repository(self):
        top = git("rev-parse", "--show-toplevel", cwd=ROOT).strip()
        path = os.path.join(top, "export-essai-inexistant")
        self.refused(path, 2, "dans le dépôt")
        self.assertFalse(os.path.exists(path))

    def test_refuses_missing_parent_and_file(self):
        self.refused(os.path.join(self.tmp, "absent", "export"), 2, "parent")
        path = os.path.join(self.tmp, "fichier")
        open(path, "w").close()
        self.refused(path, 2, "n'est pas un dossier")


@unittest.skipUnless(prefix(), "projet à la racine de son dépôt : déjà exporté")
class Export(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.top = git("rev-parse", "--show-toplevel", cwd=ROOT).strip()
        cls.prefix = prefix().rstrip("/")
        cls.status_before = git("status", "--porcelain", cwd=cls.top)
        cls.tmp = tempfile.mkdtemp()
        cls.dest = os.path.join(cls.tmp, "export")
        cls.out = run(cls.dest)
        assert cls.out.returncode == 0, cls.out.stdout + cls.out.stderr

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)
        assert git("status", "--porcelain", cwd=cls.top) == cls.status_before, \
            "le dépôt source a changé"

    def exported_files(self):
        return git("ls-files", "-z", cwd=self.dest).split("\0")[:-1]

    def test_single_commit_on_main_with_identity(self):
        self.assertEqual(git("rev-list", "--count", "HEAD", cwd=self.dest).strip(), "1")
        self.assertEqual(git("symbolic-ref", "HEAD", cwd=self.dest).strip(), "refs/heads/main")
        self.assertEqual(git("branch", "--format=%(refname:short)", cwd=self.dest).split(), ["main"])
        log = git("log", "-1", "--format=%an <%ae>%n%cn <%ce>%n%B", cwd=self.dest).split("\n")
        self.assertEqual(log[0], IDENTITY)
        self.assertEqual(log[1], IDENTITY)
        self.assertEqual("\n".join(log[2:]).strip(), MESSAGE)
        self.assertEqual(git("remote", cwd=self.dest).strip(), "")        # rien à pousser ici
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all", "--ignored",
                             cwd=self.dest), "")

    def test_tree_is_head_without_claude_md(self):
        source = tree("HEAD:" + self.prefix, self.top)
        exported = tree("HEAD", self.dest)
        self.assertIn("CLAUDE.md", source)
        del source["CLAUDE.md"]
        self.assertEqual(set(exported) - set(source), {WORKFLOW})
        self.assertEqual(set(source) - set(exported), set())
        differ = sorted(p for p in source if source[p] != exported[p])
        self.assertEqual(differ, [".gitignore"])      # rien d'autre ne diffère (contenu et mode)

    def test_nothing_private(self):
        for path in self.exported_files():
            name = os.path.basename(path)
            self.assertNotEqual(name, "CLAUDE.md", path)
            self.assertNotIn("REPRISE", path)
            self.assertFalse(path.lower().endswith(".epro2"), path)
            self.assertFalse(path.startswith((".claude/", "hardware/easyeda/", "hardware/EasyEda/")),
                             path)
            parts = path.split("/")
            self.assertFalse(any(p.startswith("Medias") or p in ("dmd-medias", ".config_backups")
                                 for p in parts), path)
            self.assertFalse(path.lower().endswith((".gif", ".mp3", ".wav", ".ogg", ".ttf",
                                                    ".otf", ".mp4")), path)
        for dirpath, dirnames, filenames in os.walk(self.dest):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            self.assertNotIn("CLAUDE.md", filenames, dirpath)

    def test_shell_scripts_keep_exec_bit(self):
        scripts = [p for p in self.exported_files() if p.endswith(".sh")]
        self.assertTrue(scripts)
        modes = tree("HEAD", self.dest)
        for path in scripts:
            self.assertEqual(modes[path][0], "100755", path)
            mode = os.stat(os.path.join(self.dest, path)).st_mode
            self.assertTrue(mode & stat.S_IXUSR, path)

    def test_lf_line_endings(self):
        for path in self.exported_files():
            with open(os.path.join(self.dest, path), "rb") as f:
                data = f.read()
            if b"\0" in data:
                continue    # binaire (image)
            self.assertNotIn(b"\r", data, path)

    def test_gitignore_merged_and_nothing_ignored(self):
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
            original = f.read()
        with open(os.path.join(self.dest, ".gitignore"), encoding="utf-8") as f:
            merged = f.read()
        self.assertTrue(merged.startswith(original))
        rules = [l.strip() for l in merged.splitlines()]
        for rule in ADDED_RULES:
            self.assertIn(rule, rules)
        # La dernière règle qui correspond l'emporte : l'exception doit suivre le dernier lib/.
        last = lambda rule: len(rules) - 1 - rules[::-1].index(rule)
        self.assertLess(last("lib/"), last("!firmware/i75w_display/lib/"))
        files = self.exported_files()
        out = subprocess.run(["git", "check-ignore", "--no-index", "--stdin"], cwd=self.dest,
                             input="\n".join(files), capture_output=True, text=True)
        self.assertEqual(out.returncode, 1, "fichiers exportés ignorés : %s" % out.stdout)
        lib = [p for p in files if p.startswith("firmware/i75w_display/lib/")]
        self.assertTrue(lib)
        self.assertTrue(os.path.isfile(os.path.join(self.dest, lib[0])))
        self.assertEqual(git("ls-files", "-c", "-i", "--exclude-standard", cwd=self.dest), "")
        for name in ("CLAUDE.md", ".claude/x", ".DS_Store", "x/__pycache__/a.pyc", "lib/a",
                     "firmware/i75w_display/build/a"):
            ok = subprocess.run(["git", "check-ignore", "--no-index", "-q", name], cwd=self.dest)
            self.assertEqual(ok.returncode, 0, name)

    def test_workflow_transformed_by_patterns(self):
        original = git("show", "HEAD:" + WORKFLOW, cwd=self.top).splitlines()
        with open(os.path.join(self.dest, WORKFLOW), encoding="utf-8") as f:
            result = f.read().splitlines()
        text = "\n".join(result)
        self.assertNotIn("RaspyDarts-Pico/", text)
        self.assertNotIn("paths:", text)
        self.assertNotIn("My_GIT_Projects", text)
        self.assertNotIn("working-directory: RaspyDarts-Pico", text)
        # Nom, commentaire de tête et nom de l'étape actionlint inchangés.
        self.assertEqual(result[0], original[0])
        self.assertEqual(result[1], original[1])
        step = "      - name: actionlint sur le workflow de RaspyDarts-Pico"
        self.assertIn(step, original)
        self.assertIn(step, result)
        diff = list(difflib.unified_diff(original, result, lineterm="", n=0))
        removed = [l[1:] for l in diff if l.startswith("-") and not l.startswith("---")]
        added = [l[1:] for l in diff if l.startswith("+") and not l.startswith("+++")]
        self.assertEqual(len(removed), REMOVED_LINES, "\n".join(diff))
        self.assertEqual(len(added), ADDED_LINES, "\n".join(diff))
        for line in removed:
            self.assertTrue("RaspyDarts-Pico" in line or "My_GIT_Projects" in line
                            or line.strip() in ("paths:", '- ".github/workflows/raspydarts-pico-tests.yml"'),
                            line)
        for line in added:
            self.assertNotIn("RaspyDarts-Pico/", line)
        self.assertEqual(sorted(l.strip() for l in added if "working-directory" in l),
                         ["working-directory: .", "working-directory: .",
                          "working-directory: firmware/i75w_display",
                          "working-directory: firmware/i75w_display",
                          "working-directory: firmware/i75w_display"])

    def test_prints_next_steps_without_running_them(self):
        self.assertIn("git -C %s remote add origin https://github.com/diogeneyves/RaspyDarts-Pico.git"
                      % os.path.realpath(self.dest), self.out.stdout)
        self.assertIn("push -u origin main", self.out.stdout)
        self.assertNotIn("Ran ", self.out.stdout + self.out.stderr)   # --run-tests non passé


if __name__ == "__main__":
    unittest.main()
