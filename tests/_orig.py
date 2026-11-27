# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Accès en LECTURE SEULE à la copie de RaspyDarts (RASPYDARTS_ORIG, défaut ~/RaspyDarts-ref465) depuis les tests.

Les modules sont chargés sans écrire de bytecode (sys.dont_write_bytecode),
et l'état git du clone peut être relevé avant et après pour vérifier que rien
n'y a été écrit. Sans copie (raspydarts.py introuvable), les tests qui en
dépendent sont sautés ; une copie présente mais sans le commit BASE, ou dont les
fichiers Python diffèrent de BASE, lève une RuntimeError. La CI clone RaspyDarts
au commit de pi/patches/BASE et passe RASPYDARTS_ORIG : rien n'y est sauté.
"""

import importlib.util
import os
import subprocess
import sys

ORIG = os.environ.get("RASPYDARTS_ORIG", os.path.expanduser("~/RaspyDarts-ref465"))
_BASE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pi", "patches", "BASE")
with open(_BASE_FILE, encoding="utf-8") as _f:
    BASE_COMMIT = _f.readline().strip()     # pi/patches/BASE, seule source


def _git(*args):
    return subprocess.run(["git", "-C", ORIG, *args], capture_output=True, text=True)


def available():
    """Copie présente et utilisable : le commit BASE y existe, et ses fichiers Python extraits
    sont ceux de BASE (la tête peut être plus récente si aucun fichier Python ne change ;
    ~/RaspyDarts-ref465 doit rester extraite sur 8fa61ff, sa tête 32edb12 ne convient pas).
    Copie présente mais sans
    BASE, ou code différent : erreur, plutôt que des tests sautés en silence."""
    if not os.path.isfile(os.path.join(ORIG, "raspydarts.py")):
        return False
    if _git("cat-file", "-e", BASE_COMMIT + "^{commit}").returncode != 0:
        raise RuntimeError("%s ne contient pas le commit BASE %s (pi/patches/BASE) : "
                           "RASPYDARTS_ORIG doit désigner une copie qui le contient"
                           % (ORIG, BASE_COMMIT))
    diff = _git("diff", "--name-only", BASE_COMMIT, "--", "*.py").stdout.split()
    if diff:
        raise RuntimeError("%s : fichiers Python différents de BASE %s : %s"
                           % (ORIG, BASE_COMMIT, ", ".join(diff[:5])))
    return True


def status():
    """Sortie de git status --porcelain --ignored --untracked-files=all (vide = intact)."""
    return subprocess.run(["git", "-C", ORIG, "status", "--porcelain", "--ignored",
                           "--untracked-files=all"], capture_output=True, text=True,
                          check=True).stdout


def head():
    return subprocess.run(["git", "-C", ORIG, "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()


def load(relpath, name):
    """Charge ORIG/relpath sous le nom name, sans écrire de bytecode."""
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    path = os.path.join(ORIG, relpath)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module
