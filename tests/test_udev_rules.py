# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Règles udev de /dev/cible et /dev/dmd (spécification § 2, complément 62).

Le fichier de règles est lu, jamais installé. install_services.sh n'est lancé qu'à blanc :
dans ce mode, il s'arrête avant tout appel à ssh (vérifié par tests/test_dmd_sre.py).
Numéros de série : marqueurs, remplacés à l'installation (complément 77,
tests/test_install_markers.py).
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "pi" / "udev" / "73-dartspico-bridge.rules"
INSTALL = ROOT / "tools" / "install_services.sh"
I75W_SH = ROOT / "tools" / "bench_i75w" / "i75w.sh"

# Marqueurs des numéros de série (complément 77) ; casse remplie à l'installation :
# minuscules sous MicroPython, majuscules sous le firmware C++ (complément 62).
EXPECTED = {
    "cible": {"idProduct": "0005", "serial": "<SERIE_PICO>"},
    "dmd": {"idProduct": "0009", "serial": "<SERIE_I75W>"},
}
DRY_OPTIONS = ["--user", "joueur", "--pico-serial", "0123456789abcdef",
               "--i75w-serial", "FEDCBA9876543210"]
KEY = re.compile(r'^\s*([A-Z_]+(?:\{[^}]+\})?)\s*(==|\+=|=)\s*"([^"]*)"\s*$')


def parse_rules(text):
    """Une règle par ligne non vide hors commentaire : liste de (clé, opérateur, valeur)."""
    rules = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        assert not line.rstrip().endswith("\\"), "pas de ligne continuée"
        pairs = []
        for part in line.split(","):
            match = KEY.match(part)
            assert match, f"élément illisible : {part!r}"
            pairs.append(match.groups())
        rules.append(pairs)
    return rules


def rule_for(rules, link):
    found = [r for r in rules if ("SYMLINK", "+=", link) in r]
    assert len(found) == 1, f"{len(found)} règle(s) pour {link}"
    return found[0]


class UdevRulesFileTest(unittest.TestCase):

    def setUp(self):
        self.assertTrue(RULES.is_file(), f"fichier absent : {RULES.relative_to(ROOT)}")
        self.rules = parse_rules(RULES.read_text(encoding="utf-8"))

    def test_two_rules_only(self):
        self.assertEqual(len(self.rules), 2)

    def test_each_rule_exact_values(self):
        for link, want in EXPECTED.items():
            with self.subTest(link=link):
                rule = rule_for(self.rules, link)
                self.assertIn(("SUBSYSTEM", "==", "tty"), rule)
                self.assertIn(("ATTRS{idVendor}", "==", "2e8a"), rule)
                self.assertIn(("ATTRS{idProduct}", "==", want["idProduct"]), rule)
                # Casse exacte : udev compare ATTRS{serial} octet par octet.
                self.assertIn(("ATTRS{serial}", "==", want["serial"]), rule)
                self.assertIn(("SYMLINK", "+=", link), rule)
                self.assertIn(("ENV{ID_MM_DEVICE_IGNORE}", "=", "1"), rule)

    def test_no_real_serial_in_rules(self):
        text = RULES.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"(?i)\b[0-9a-f]{16}\b")
        self.assertEqual(text.count("<SERIE_PICO>"), 1)
        self.assertEqual(text.count("<SERIE_I75W>"), 1)

    def test_no_mode_group_nor_name(self):
        for rule in self.rules:
            keys = {k for k, _, _ in rule}
            self.assertFalse(keys & {"MODE", "GROUP", "OWNER", "NAME", "RUN"}, keys)

    def test_i75w_sh_has_no_default_serial(self):
        text = I75W_SH.read_text(encoding="utf-8")
        for name in ("PICO_SERIAL", "I75W_SERIAL"):
            with self.subTest(name=name):
                self.assertRegex(text, r'(?m)^%s="\$\{%s:-\}"$' % (name, name))


class InstallServicesUdevTest(unittest.TestCase):
    """install_services.sh installe la règle, recharge udev, et laisse le trigger à la main."""

    def setUp(self):
        self.text = INSTALL.read_text(encoding="utf-8")
        self.code = "\n".join(l for l in self.text.splitlines() if not l.lstrip().startswith("#"))

    def test_dry_run_announces_rule_and_reload(self):
        result = subprocess.run(["bash", str(INSTALL)] + DRY_OPTIONS, cwd=ROOT, capture_output=True,
                                text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rien n'a été envoyé", result.stdout)
        self.assertIn("pi/udev/73-dartspico-bridge.rules", result.stdout)
        self.assertIn("/etc/udev/rules.d/73-dartspico-bridge.rules", result.stdout)
        self.assertIn("udevadm control --reload", result.stdout)

    def test_rule_required_installed_and_reloaded(self):
        self.assertIn('UDEV_NAME="73-dartspico-bridge.rules"', self.code)
        self.assertIn('UDEV_RULE="pi/udev/$UDEV_NAME"', self.code)
        self.assertIn('UDEV_DIR="/etc/udev/rules.d"', self.code)
        self.assertRegex(self.code, r'required=\([^)]*"\$UDEV_RULE"\)')     # absente : exit 2
        # Écrasée à chaque --apply (pas de test d'existence), à root, 644, puis rechargée.
        install = 'install -m 644 -o root -g root "\\$src/udev/$UDEV_NAME" "$UDEV_DIR/$UDEV_NAME"'
        self.assertIn(install, self.code)
        self.assertNotIn('-e "$UDEV_DIR', self.code)
        self.assertLess(self.code.index(install), self.code.index("udevadm control --reload\n"))

    def test_install_names(self):
        # Complément 84 : dossier d'installation et archive envoyée au Pi au nouveau nom.
        self.assertIn('OPT_DIR="/opt/dartspico-bridge"', self.code)
        self.assertIn('REMOTE_ARCHIVE="/tmp/dartspico-bridge-install.tgz"', self.code)
        self.assertNotIn("raspydarts-pico", self.code.lower())

    def test_trigger_left_to_hand(self):
        self.assertNotIn("udevadm trigger", self.code)


if __name__ == "__main__":
    unittest.main()
