# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Section « Mise à jour » de docs/INSTALLATION.md (spécification, complément 88).

La section 14 existe, figure au plan et traite les cinq éléments, chacun dans sa sous-section
avec les arrêts avant, le contrôle après, le retour arrière et la mention « non vérifié » ;
docs/PROCEDURE.md y renvoie.
"""

import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
INSTALLATION = os.path.join(ROOT, "docs", "INSTALLATION.md")
PROCEDURE = os.path.join(ROOT, "docs", "PROCEDURE.md")
HEADING = "## 14. Mise à jour"

# Les cinq éléments du complément 88 : (titre de la sous-section, texte exigé dans son corps).
ELEMENTS = [
    ("### 14.2 Code du Pi", "/opt/dartspico-bridge"),
    ("### 14.3 Patchs du jeu", "tools/apply_patches.sh"),
    ("### 14.4 Firmware du Pico", "mpremote"),
    ("### 14.5 Firmware de l'Interstate 75 W", "i75w.sh flash"),
    ("### 14.6 Médias du DMD", "tools/sync_medias.sh"),
]
PARTS = ("Avant", "Contrôle", "Retour arrière", "non vérifié")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def section(text, heading, level="## "):
    """Corps de la section dont le titre est `heading` (suivi ou non d'une précision après une
    espace), jusqu'au titre suivant de même niveau ou plus haut ; None si absente ou double."""
    lines = text.split("\n")
    hits = [k for k, line in enumerate(lines) if line == heading or line.startswith(heading + " ")]
    if len(hits) != 1:
        return None
    start = hits[0] + 1
    stop = re.compile(r"^#{1,%d} " % (len(level) - 1))
    for k in range(start, len(lines)):
        if stop.match(lines[k]):
            return "\n".join(lines[start:k])
    return "\n".join(lines[start:])


class TestUpdateSection(unittest.TestCase):
    def setUp(self):
        self.text = read(INSTALLATION)
        self.body = section(self.text, HEADING)

    def test_section_exists(self):
        self.assertIsNotNone(self.body, "docs/INSTALLATION.md : titre « %s » absent" % HEADING)

    def test_in_plan(self):
        self.assertIn("\n14. Mise à jour\n", section(self.text, "## Plan") or "")

    def test_five_elements(self):
        self.assertIsNotNone(self.body)
        for heading, needle in ELEMENTS:
            with self.subTest(element=heading):
                body = section(self.body, heading, "### ")
                self.assertIsNotNone(body, "sous-section « %s » absente" % heading)
                self.assertIn(needle, body)
                title = [l for l in self.body.split("\n") if l.startswith(heading + " ")]
                for part in PARTS:
                    self.assertIn(part, "\n".join(title) + body,
                                  "« %s » absent de « %s »" % (part, heading))

    def test_coherent_set_rule(self):
        self.assertIsNotNone(self.body)
        self.assertIn("/opt/dartspico-bridge/REVISION", self.body)
        self.assertIn("même révision", self.body)

    def test_no_reset_hard(self):
        self.assertIsNotNone(self.body)
        self.assertNotIn("reset --hard", self.body.replace("jamais de `git reset --hard`", ""))

    def test_no_personal_path(self):
        self.assertIsNotNone(self.body)
        for needle in ("/Users/", "/home/pi", "~/RaspyDarts"):
            self.assertNotIn(needle, self.body)

    def test_procedure_points_to_section(self):
        self.assertRegex(read(PROCEDURE), r"\[`INSTALLATION\.md`\]\(INSTALLATION\.md\) section 14")


class TestSectionHelper(unittest.TestCase):
    def test_witness(self):
        text = "## A\nx\n### A.1\ny\n## B\nz"
        self.assertEqual(section(text, "## A"), "x\n### A.1\ny")
        self.assertEqual(section(text, "### A.1", "### "), "y")
        self.assertIsNone(section(text, "## C"))
        self.assertEqual(section("## A (précision)\nx", "## A"), "x")
        self.assertIsNone(section("## AB\nx", "## A"))
        self.assertIsNone(section("## A\nx\n## A\ny", "## A"))


if __name__ == "__main__":
    unittest.main()
