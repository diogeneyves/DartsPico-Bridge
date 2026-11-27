# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Licences du dépôt (spécification § 10, complément 76) : LICENSE GPL v3 complet à la racine
du projet, notice MIT de Pimoroni dans gamma.hpp identique à celle de hub75.cpp, aucune
mention de code repris de Raspy2DMD dans pi/dmd/README.md (§ 9.3 : rendu réimplémenté)."""

import unittest

from _paths import ROOT

FW = ROOT / "firmware" / "i75w_display"


def mit_notice(path):
    """Bloc « MIT License » … « SOFTWARE. » d'un fichier C++ (lignes de commentaire)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    start = lines.index("// MIT License")
    end = next(i for i in range(start, len(lines)) if lines[i].rstrip().endswith("SOFTWARE."))
    return lines[start:end + 1]


class LicenseFileTest(unittest.TestCase):
    def test_license_is_full_gpl_v3(self):
        path = ROOT / "LICENSE"
        self.assertTrue(path.is_file(), "LICENSE absent à la racine du projet")
        text = path.read_text(encoding="utf-8")
        head = [line.strip() for line in text.splitlines() if line.strip()][:2]
        self.assertEqual(head, ["GNU GENERAL PUBLIC LICENSE", "Version 3, 29 June 2007"])
        # Texte complet : préambule, conditions (sections 0 à 17) et annexe
        for marker in ("Preamble", "TERMS AND CONDITIONS", "0. Definitions.",
                       "17. Interpretation of Sections 15 and 16.", "END OF TERMS AND CONDITIONS",
                       "How to Apply These Terms to Your New Programs"):
            self.assertIn(marker, text)

    def test_license_is_plain_ascii_with_lf(self):
        """Texte de la FSF tel que distribué (complément 76) : ASCII seulement, fins de ligne LF."""
        data = (ROOT / "LICENSE").read_bytes()
        self.assertEqual(data.count(b"\r"), 0, "fins de ligne CR ou CRLF dans LICENSE")
        bad = sorted({b for b in data if b > 0x7E or (b < 0x20 and b not in (0x09, 0x0A, 0x0C))})
        self.assertEqual(bad, [], "octets non ASCII dans LICENSE")


class PimoroniNoticeTest(unittest.TestCase):
    def test_gamma_has_same_mit_notice_as_hub75(self):
        notice = mit_notice(FW / "src" / "hub75.cpp")
        self.assertIn("// Copyright (c) 2021 Pimoroni Ltd", notice)
        self.assertEqual(mit_notice(FW / "lib" / "gamma.hpp"), notice)

    def test_versioned_pimoroni_license_equals_hub75_notices(self):
        """Licence jointe à l'artefact du .uf2 (complément 79, Q5) : texte de l'en-tête MIT de
        hub75.hpp, sans les « // », copyright compris ; hub75.cpp porte le même texte."""
        path = FW / "LICENSES" / "pimoroni-MIT.txt"
        self.assertTrue(path.is_file(), f"licence de Pimoroni absente : {path}")
        text = path.read_text(encoding="utf-8")
        for src in ("hub75.hpp", "hub75.cpp"):
            notice = mit_notice(FW / "src" / src)
            self.assertIn("// Copyright (c) 2021 Pimoroni Ltd", notice, src)
            plain = "\n".join(line[3:] if line.startswith("// ") else line[2:] for line in notice) + "\n"
            self.assertEqual(text, plain, f"{path.name} diffère de l'en-tête MIT de src/{src}")


class Raspy2dmdNoteTest(unittest.TestCase):
    def test_dmd_readme_claims_no_copied_raspy2dmd_code(self):
        text = (ROOT / "pi" / "dmd" / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("Le code repris de Raspy2DMD garde sa mention MIT", text)
        self.assertIn("aucun code de Raspy2DMD", text)


if __name__ == "__main__":
    unittest.main()
