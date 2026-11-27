# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Noms de médias personnels absents des fichiers suivis (spécification, complément 82).

Les six noms sont rangés ici en ROT13, pour que ce fichier ne les contienne pas lui-même.
Chaque nom est cherché sans extension (ce qui couvre aussi le nom complet), sans tenir
compte de la casse, dans le contenu et dans le chemin de chaque fichier suivi par git.
"""

import codecs
import hashlib
import os
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Noms sans extension, en ROT13 (extensions d'origine : .gif, sauf le cinquième, .png).
_ROT13_STEMS = (
    "farfznevb",
    "arohyhf",
    "onqqhqrf",
    "qzqgurfunqbj",
    "FHAFRG3pbcl",
    "Enfcl2QZQ_20260605_173151",
)
FORBIDDEN = tuple(codecs.decode(s, "rot13").lower() for s in _ROT13_STEMS)


def _tracked_files():
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout
    return [p.decode("utf-8") for p in out.split(b"\0") if p]


class TestMediaNames(unittest.TestCase):
    def test_six_names(self):
        # Empreinte des six noms décodés, joints par « | » : une faute de ROT13 la change.
        self.assertEqual(len(set(FORBIDDEN)), 6)
        self.assertEqual(
            hashlib.sha256("|".join(FORBIDDEN).encode()).hexdigest(),
            "3bb4cc65c347a0cc2ea849bf57d25bf5b9caacbd53848425bb0efb290a7461fd",
        )

    def test_absent_from_tracked_files(self):
        hits = []
        for rel in _tracked_files():
            path = os.path.join(ROOT, rel)
            if not os.path.isfile(path) or os.path.islink(path):
                continue
            with open(path, "rb") as f:
                lines = f.read().decode("utf-8", errors="replace").lower().splitlines()
            for name in FORBIDDEN:
                if name in rel.lower():
                    hits.append(f"{rel} (chemin) : {name}")
                for no, line in enumerate(lines, 1):
                    if name in line:
                        hits.append(f"{rel}:{no} : {name}")
        self.assertEqual(hits, [], "noms de médias personnels trouvés :\n" + "\n".join(hits))


if __name__ == "__main__":
    unittest.main()
