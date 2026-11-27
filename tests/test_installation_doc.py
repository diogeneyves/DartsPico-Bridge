# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Cohérence de docs/INSTALLATION.md et docs/PROCEDURE.md (complément 77) : marqueurs sous une
seule forme, aucun numéro de série réel, liens relatifs vers des fichiers présents, options de
tools/install_services.sh citées par INSTALLATION.md présentes dans le script.
"""

import re
import unittest
from pathlib import Path

import _paths

ROOT = Path(_paths.ROOT)
DOCS = ROOT / "docs"
INSTALLATION = DOCS / "INSTALLATION.md"
PROCEDURE = DOCS / "PROCEDURE.md"
INSTALL = ROOT / "tools" / "install_services.sh"

SERIAL_MARKERS = {"<SERIE_PICO>", "<SERIE_I75W>"}
USER_MARKER = "<utilisateur>"
# Suite isolée d'exactement 16 chiffres hexadécimaux : un numéro de série de carte. Une empreinte
# SHA-256 ou un SHA de commit complet (plus long) n'est pas visé, ni un SHA court (plus court).
SERIAL_RE = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{16}(?![0-9A-Fa-f])")
LINK_RE = re.compile(r"\]\(([^)\s]+)\)")


def serial_hits(text):
    return SERIAL_RE.findall(text)


def read(path):
    return path.read_text(encoding="utf-8")


class InstallationDocTest(unittest.TestCase):
    def test_serial_markers_single_form(self):
        for path in (INSTALLATION, PROCEDURE):
            found = set(re.findall(r"<s[eé]rie[^>\s]*>", read(path), re.IGNORECASE))
            with self.subTest(doc=path.name):
                self.assertTrue(found, "aucun marqueur de série")
                self.assertLessEqual(found, SERIAL_MARKERS)

    def test_user_marker_single_form(self):
        for path in (INSTALLATION, PROCEDURE):
            found = set(re.findall(r"<utilisateur>", read(path), re.IGNORECASE))
            with self.subTest(doc=path.name):
                self.assertEqual(found, {USER_MARKER})

    def test_no_real_serial(self):
        for path in (INSTALLATION, PROCEDURE):
            with self.subTest(doc=path.name):
                self.assertEqual(serial_hits(read(path)), [])

    def test_serial_pattern_witness(self):
        # Une fausse série est vue ; un SHA de commit (40) ou une empreinte SHA-256 (64) non.
        self.assertEqual(serial_hits("série 0000000000000000."), ["0000000000000000"])
        self.assertEqual(serial_hits("8fa61ffe9c1c264f49c3300ea61a37100b84b10c"), [])
        self.assertEqual(serial_hits("ab" * 32), [])
        self.assertEqual(serial_hits("commit 8fa61ff"), [])

    def test_relative_links_exist(self):
        for path in (INSTALLATION, PROCEDURE):
            for target in LINK_RE.findall(read(path)):
                if re.match(r"[a-z]+:", target) or target.startswith("#"):
                    continue
                with self.subTest(doc=path.name, link=target):
                    self.assertTrue((path.parent / target.split("#")[0]).exists())

    def test_install_options_exist(self):
        usage = read(INSTALL)
        cited = set(re.findall(r"tools/install_services\.sh[^`\n]*", read(INSTALLATION)))
        options = set()
        for line in cited:
            options.update(re.findall(r"--[a-z0-9-]+", line))
        options.update(("--user", "--pico-serial", "--i75w-serial", "--apply"))
        for option in sorted(options):
            with self.subTest(option=option):
                self.assertIn(option, usage)


if __name__ == "__main__":
    unittest.main()
