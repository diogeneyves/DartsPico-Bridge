# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
""".uf2 reproductible de l'Interstate 75 W (spécification, complément 69).

Le Pico SDK 2.1.0 inscrit __DATE__ dans les informations binaires, sauf si
PICO_NO_BI_PROGRAM_BUILD_DATE est vrai (standard_binary_info.c, l. 30 à 34).
Le CMakeLists.txt du firmware est lu comme texte : commentaires « # » retirés, puis
appels target_compile_definitions(i75w_display …) isolés jusqu'à leur parenthèse fermante.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CMAKE = ROOT / "firmware" / "i75w_display" / "CMakeLists.txt"
TARGET = "i75w_display"
DEFINITION = "PICO_NO_BI_PROGRAM_BUILD_DATE=1"
SCOPES = ("PRIVATE", "PUBLIC", "INTERFACE")


def strip_comments(text):
    """Retire les commentaires de ligne « # » (le CMakeLists du firmware n'a pas de # dans ses chaînes)."""
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def definitions(text, target):
    """Liste de (portée, définition) des appels target_compile_definitions(target …)."""
    code = strip_comments(text)
    found = []
    for m in re.finditer(r"\btarget_compile_definitions\s*\(([^)]*)\)", code, re.IGNORECASE):
        tokens = m.group(1).split()
        if not tokens or tokens[0] != target:
            continue
        scope = None
        for tok in tokens[1:]:
            if tok in SCOPES:
                scope = tok
            else:
                found.append((scope, tok))
    return found


def build_date_definitions(text, target):
    """Définitions de PICO_NO_BI_PROGRAM_BUILD_DATE (toutes valeurs) pour la cible, avec leur portée."""
    return [(s, d) for s, d in definitions(text, target)
            if d.split("=", 1)[0] == "PICO_NO_BI_PROGRAM_BUILD_DATE"]


class BuildDateRemovedTest(unittest.TestCase):
    def test_cmakelists_removes_build_date(self):
        self.assertTrue(CMAKE.is_file(), f"CMakeLists.txt absent : {CMAKE}")
        found = build_date_definitions(CMAKE.read_text(encoding="utf-8"), TARGET)
        self.assertEqual(
            found, [("PRIVATE", DEFINITION)],
            f"{CMAKE.name} doit définir exactement une fois {DEFINITION} en PRIVATE dans "
            f"target_compile_definitions({TARGET} …) (complément 69 ; trouvé : {found})")


class ParserTest(unittest.TestCase):
    SAMPLE = (
        "target_compile_definitions(i75w_display PRIVATE\n"
        "    A=1\n"
        "    # commentaire (avec parenthèse) PICO_NO_BI_PROGRAM_BUILD_DATE=1\n"
        "    PICO_NO_BI_PROGRAM_BUILD_DATE=1\n"
        "    B=2\n"
        ")\n"
        "target_compile_definitions(other PRIVATE PICO_NO_BI_PROGRAM_BUILD_DATE=0)\n"
    )

    def test_found_once_with_scope(self):
        self.assertEqual(build_date_definitions(self.SAMPLE, TARGET), [("PRIVATE", DEFINITION)])

    def test_comment_and_other_target_ignored(self):
        text = self.SAMPLE.replace("    PICO_NO_BI_PROGRAM_BUILD_DATE=1\n", "")
        self.assertEqual(build_date_definitions(text, TARGET), [])

    def test_wrong_value_or_scope_seen(self):
        text = self.SAMPLE.replace("BUILD_DATE=1\n    B", "BUILD_DATE=0\n    B")
        self.assertEqual(build_date_definitions(text, TARGET), [("PRIVATE", "PICO_NO_BI_PROGRAM_BUILD_DATE=0")])
        text = self.SAMPLE.replace("    PICO_NO_BI", "    INTERFACE PICO_NO_BI")
        self.assertEqual(build_date_definitions(text, TARGET), [("INTERFACE", DEFINITION)])

    def test_duplicate_seen(self):
        text = self.SAMPLE + "target_compile_definitions(i75w_display PRIVATE PICO_NO_BI_PROGRAM_BUILD_DATE=1)\n"
        self.assertEqual(len(build_date_definitions(text, TARGET)), 2)


if __name__ == "__main__":
    unittest.main()
