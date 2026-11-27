# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""tools/migrate_to_pico.py : fichier exemple, idempotence, sauvegarde, dry-run, conformité.

Fichiers FABRIQUÉS dans un dossier temporaire (aucune configuration réelle).
~/RaspyDarts-ref465 n'est lu qu'en lecture seule, son état git est vérifié.
"""

import configparser
import contextlib
import io
import os
import re
import shutil
import sys
import tempfile
import types
import unittest

import _cplatform
import _orig
import _paths  # noqa: F401

sys.path.insert(0, os.path.join(str(_paths.ROOT), "pi", "raspydarts_layer", "include"))
import cpico  # noqa: E402
import migrate_to_pico as M  # noqa: E402

EXAMPLE = """[SectionGlobals]
### Blink time of main information (ms) (good is 3000)
blinktime:500
locale:fr_FR

[SectionAdvanced]
### Use DMD
use-dmd:True
mqtt-broker:localhost
### List of games for which options are saved
game-options-list:Cricket
### For Richard's case
richard-mode:False



[Raspberry_BoardPinsIns]
### Input GPIO section
PIN_1:24
PIN_2:25
PIN_3:

[Raspberry_BoardPinsOuts]
PIN_1:15   # colonne 1
PIN_2 = 17

[Raspberry_Leds]
PIN_TARGETLED:18
BRI_TARGETLED:0.5

[SectionKeys]
### Target settings
S20:2410
D20:1310
SB:
PLAYERBUTTON:17

[Raspberry]
### In case of MCP23017 extend card
PIN_UP:A0
PIN_DOWN:'A1'
PIN_MISSDART:B7
LIGHT_FLASH:C3
TOY1:C0
TOY9:D0

[Events]
newgame:{'TARGET': ['Rainbow,1,red,10'], 'STROBE': ['TOY12,2']}

[game-Cricket]
max_round:20
"""


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = M.main(list(args))
    return code, out.getvalue(), err.getvalue()


def parse(text):
    p = configparser.ConfigParser(inline_comment_prefixes="#", interpolation=None)
    p.optionxform = str
    p.read_string(text)
    return p


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.cfg = os.path.join(self.dir, "raspydarts.cfg")
        with open(self.cfg, "w", encoding="utf-8") as f:
            f.write(EXAMPLE)

    def files(self):
        return sorted(os.listdir(self.dir))


class ExampleFile(Base):
    def test_copy_is_migrated_and_original_untouched(self):
        code, out, _ = run("--config", self.cfg)
        self.assertEqual(code, 0)
        with open(self.cfg, encoding="utf-8") as f:
            self.assertEqual(f.read(), EXAMPLE)                        # original intact
        with open(self.cfg + ".pico", encoding="utf-8") as f:
            migrated = f.read()
        p = parse(migrated)
        self.assertEqual(p["SectionAdvanced"]["hardware"], "pico")
        self.assertEqual(dict(p["Raspberry"]), {k: str(v) for k, v in cpico.DEFAULTS.items()})
        for section in ("Raspberry_BoardPinsIns", "Raspberry_BoardPinsOuts", "SectionKeys"):
            self.assertTrue(all(v == "" for v in p[section].values()), section)
        # Sections non concernées : identiques ; [SectionGlobals] gagne seulement sound_output
        # (absente de l'exemple : hdmi, § 9.4 v0.3.11).
        e = parse(EXAMPLE)
        self.assertEqual(dict(p["SectionGlobals"]), dict(e["SectionGlobals"], sound_output="hdmi"))
        for section in ("Raspberry_Leds", "Events", "game-Cricket"):
            self.assertEqual(dict(p[section]), dict(e[section]), section)
        self.assertIn("Lignes modifiées", out)

    def test_only_necessary_lines_change(self):
        run("--config", self.cfg)
        with open(self.cfg + ".pico", encoding="utf-8") as f:
            new = f.read().splitlines()
        old = EXAMPLE.splitlines()
        # Toutes les lignes d'origine restent, dans le même ordre : intactes ou seulement
        # avec une nouvelle valeur pour la même clé ; les autres lignes sont des ajouts.
        changed = {}
        j = 0
        for line in new:
            if j < len(old) and line == old[j]:
                j += 1
            elif j < len(old) and line.split(":")[0].split("=")[0].strip() == \
                    old[j].split(":")[0].split("=")[0].strip() and not old[j].startswith(("#", "[")):
                changed[old[j]] = line
                j += 1
        self.assertEqual(j, len(old), "une ligne d'origine a disparu ou a été déplacée")
        for line in old:
            if line.startswith("#") or not line.strip() or line.startswith("["):
                self.assertIn(line, new)                               # commentaires, en-têtes, vides
        self.assertEqual(changed["PIN_1:15   # colonne 1"], "PIN_1:   # colonne 1")   # commentaire gardé
        self.assertEqual(changed["PIN_2 = 17"], "PIN_2 =")                           # séparateur gardé
        self.assertEqual(changed["PIN_DOWN:'A1'"], "PIN_DOWN:BTN2")
        self.assertEqual(changed["TOY9:D0"], "TOY9:")
        self.assertNotIn("PIN_3:", changed)                                         # déjà vide
        self.assertNotIn("SB:", changed)
        # hardware à la même place que write_file du patch 0002.
        self.assertLess(new.index("hardware:pico"), new.index("### For Richard's case"))
        self.assertGreater(new.index("hardware:pico"), new.index("game-options-list:Cricket"))

    def test_report_and_warnings(self):
        code, out, _ = run("--config", self.cfg, "--dry-run")
        self.assertRegex(out, r"l\.\d+ : 'PIN_UP:A0' -> 'PIN_UP:BTN1'")
        self.assertIn("+ 'hardware:pico'", out)
        self.assertIn("3 code(s) de segment effacé(s)", out)              # S20, D20, PLAYERBUTTON
        self.assertIn("4 broche(s) GPIO", out)
        self.assertIn("TOY12", out)                                      # cité dans [Events]
        self.assertIn("[Raspberry_Leds] non modifiée", out)

    def test_missing_sections_are_created(self):
        with open(self.cfg, "w", encoding="utf-8") as f:
            f.write("[SectionGlobals]\nlocale:fr_FR\n")
        run("--config", self.cfg)
        with open(self.cfg + ".pico", encoding="utf-8") as f:
            p = parse(f.read())
        self.assertEqual(p["SectionAdvanced"]["hardware"], "pico")
        self.assertEqual(dict(p["Raspberry"]), {k: str(v) for k, v in cpico.DEFAULTS.items()})
        self.assertEqual(p["SectionGlobals"]["locale"], "fr_FR")

    def test_crlf_and_bom_kept(self):
        data = ("﻿" + EXAMPLE.replace("\n", "\r\n")).encode("utf-8")
        with open(self.cfg, "wb") as f:
            f.write(data)
        code, out, _ = run("--config", self.cfg)
        with open(self.cfg + ".pico", "rb") as f:
            new = f.read()
        self.assertTrue(new.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\n", new.replace(b"\r\n", b""))              # que des CRLF
        self.assertIn("marque BOM", out)

    def test_unknown_hardware_value_replaced_with_warning(self):
        with open(self.cfg, "w", encoding="utf-8") as f:
            f.write(EXAMPLE.replace("richard-mode:False", "hardware:pi\nrichard-mode:False"))
        code, out, _ = run("--config", self.cfg, "--dry-run")
        self.assertIn("'hardware:pi' -> 'hardware:pico'", out)
        self.assertIn("valeur inconnue", out)


class Idempotence(Base):
    def test_second_run_changes_nothing(self):
        run("--config", self.cfg)
        with open(self.cfg + ".pico", "rb") as f:
            first = f.read()
        code, out, _ = run("--config", self.cfg + ".pico", "--output", os.path.join(self.dir, "b.cfg"))
        with open(os.path.join(self.dir, "b.cfg"), "rb") as f:
            self.assertEqual(f.read(), first)
        self.assertIn("aucune modification", out)

    def test_in_place_twice(self):
        run("--config", self.cfg, "--in-place")
        backups = [f for f in self.files() if ".avant-pico-" in f]
        with open(self.cfg, "rb") as f:
            after_first = f.read()
        code, out, _ = run("--config", self.cfg, "--in-place")
        self.assertIn("rien à faire", out)
        self.assertEqual([f for f in self.files() if ".avant-pico-" in f], backups)   # pas de 2e sauvegarde
        with open(self.cfg, "rb") as f:
            self.assertEqual(f.read(), after_first)


class Backup(Base):
    def test_in_place_makes_timestamped_backup_first(self):
        code, out, _ = run("--config", self.cfg, "--in-place")
        self.assertEqual(code, 0)
        backups = [f for f in self.files() if ".avant-pico-" in f]
        self.assertEqual(len(backups), 1)
        self.assertRegex(backups[0], r"^raspydarts\.cfg\.avant-pico-\d{8}-\d{6}$")
        with open(os.path.join(self.dir, backups[0]), encoding="utf-8") as f:
            self.assertEqual(f.read(), EXAMPLE)                        # sauvegarde = original
        with open(self.cfg, encoding="utf-8") as f:
            self.assertIn("hardware:pico", f.read())
        self.assertNotIn("raspydarts.cfg.pico", self.files())            # pas de copie
        self.assertNotIn("raspydarts.cfg.migration-tmp", self.files())

    def test_two_backups_in_the_same_second_do_not_collide(self):
        stamp = M.time.strftime("%Y%m%d-%H%M%S")
        open(self.cfg + ".avant-pico-" + stamp, "w").close()
        saved = M.time.strftime
        M.time.strftime = lambda fmt: stamp
        try:
            run("--config", self.cfg, "--in-place")
        finally:
            M.time.strftime = saved
        self.assertIn("raspydarts.cfg.avant-pico-%s-2" % stamp, self.files())

    def test_refusals(self):
        self.assertEqual(run("--config", self.cfg, "--output", self.cfg)[0], 2)   # copie = original
        self.assertEqual(run("--config", os.path.join(self.dir, "absent.cfg"))[0], 2)
        with open(self.cfg, "wb") as f:
            f.write(b"[SectionAdvanced]\nx:\xff\n")
        self.assertEqual(run("--config", self.cfg)[0], 2)
        self.assertNotIn("raspydarts.cfg.pico", self.files())


class DryRun(Base):
    def test_writes_nothing(self):
        before = {f: os.stat(os.path.join(self.dir, f)).st_mtime_ns for f in self.files()}
        for extra in ((), ("--in-place",)):
            with self.subTest(extra=extra):
                code, out, _ = run("--config", self.cfg, "--dry-run", *extra)
                self.assertEqual(code, 0)
                self.assertIn("simulation", out)
                self.assertIn("Lignes modifiées", out)
        after = {f: os.stat(os.path.join(self.dir, f)).st_mtime_ns for f in self.files()}
        self.assertEqual(after, before)                                 # aucun fichier créé ni modifié


class DefaultPath(unittest.TestCase):
    """Emplacement par défaut, comme include/cpaths.py (sans rien renommer)."""

    def test_like_cpaths(self):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home)
        self.assertEqual(M.default_config_path(home), os.path.join(home, ".raspydarts", "raspydarts.cfg"))
        os.makedirs(os.path.join(home, ".pydarts"))
        self.assertEqual(M.default_config_path(home), os.path.join(home, ".pydarts", "raspydarts.cfg"))
        old = os.path.join(home, ".pydarts", "pydarts.cfg")
        open(old, "w").close()
        self.assertEqual(M.default_config_path(home), old)              # seul : le plus récent
        self.assertTrue(os.path.exists(old))                            # pas renommé


@unittest.skipUnless(_orig.available(), "copie de RaspyDarts absente (%s)" % _orig.ORIG)
class MatchesRaspyDarts(unittest.TestCase):
    """Clés et emplacement repris de cconfig.py et cpaths.py, pas devinés."""

    def setUp(self):
        self.assertEqual(_orig.status(), "")

    def tearDown(self):
        self.assertEqual(_orig.status(), "", "le clone de référence a été modifié")

    def test_keys_match_real_cconfig(self):
        sys.dont_write_bytecode = True
        saved = {n: sys.modules.get(n) for n in ("include", "include.cpaths", "include.cplatform")}
        pkg = types.ModuleType("include")
        pkg.__path__ = []
        pkg.cpaths = types.SimpleNamespace()
        sys.modules.update({"include": pkg, "include.cpaths": pkg.cpaths})
        _cplatform.install(pkg, sys.modules)
        try:
            cconfig = _orig.load(os.path.join("include", "cconfig.py"), "cconfig_pour_migration")
        finally:
            for n, mod in saved.items():
                if mod is None:
                    sys.modules.pop(n, None)
                else:
                    sys.modules[n] = mod
            sys.modules.pop("cconfig_pour_migration", None)
        d = cconfig.DefaultConfig
        self.assertEqual(list(M.TARGET["SectionKeys"]), list(d["SectionKeys"]))
        self.assertEqual(list(M.TARGET["Raspberry_BoardPinsIns"]), list(d["Raspberry_BoardPinsIns"]))
        self.assertEqual(list(M.TARGET["Raspberry_BoardPinsOuts"]), list(d["Raspberry_BoardPinsOuts"]))
        self.assertEqual(set(M.TARGET["Raspberry"]), set(d["Raspberry"]))
        self.assertEqual(M.GPIO_BUTTONS, tuple(cconfig.GPIO_BUTTONS_CONFIG))

    def test_names_match_real_cpaths(self):
        with open(os.path.join(_orig.ORIG, "include", "cpaths.py"), encoding="utf-8") as f:
            src = f.read()
        for name, value in (("USER_DIR_NAME", M.USER_DIR_NAME), ("OLD_USER_DIR_NAME", M.OLD_USER_DIR_NAME),
                            ("CONFIG_NAME", M.CONFIG_NAME), ("OLD_CONFIG_NAME", M.OLD_CONFIG_NAME)):
            self.assertRegex(src, r"\n%s = '%s'\n" % (name, re.escape(value)))


@unittest.skipUnless(_orig.available(), "copie de RaspyDarts absente (%s)" % _orig.ORIG)
class MatchesPatch0002(unittest.TestCase):
    """Le fichier migré, relu par le cconfig PATCHÉ : set_pico_card_config ne change plus rien."""

    def test_migrated_file_equals_preset(self):
        import test_patch_0002 as T

        class Probe(T.Bench):
            def runTest(self):
                pass
        Probe.setUpClass()
        try:
            probe = Probe()
            default = probe.run_config(b"", original=True)[0]
            migrated = M.migrate_text(default.decode("utf-8")).text().encode("utf-8")
            plain = probe.run_config(migrated, False)[0]
            with_preset = probe.run_config(migrated, False,
                                           lambda config, m: config.set_pico_card_config())[0]
            self.assertIn(b"hardware:pico", plain)                      # la clé survit (patch 0002)
            self.assertIn(b"sound_output:hdmi\n", plain)                # auto -> hdmi (§ 9.4)
            self.assertEqual(with_preset, plain)                        # migration = préréglage
            # Sortie choisie à la main : conservée par la migration comme par le préréglage.
            for value in (b"jack", b"usb"):
                with self.subTest(value=value):
                    source = default.replace(b"sound_output:auto\n", b"sound_output:%s\n" % value)
                    self.assertNotEqual(source, default)
                    migrated = M.migrate_text(source.decode("utf-8")).text().encode("utf-8")
                    self.assertIn(b"sound_output:%s\n" % value, migrated)
                    plain = probe.run_config(migrated, False)[0]
                    with_preset = probe.run_config(migrated, False,
                                                   lambda config, m: config.set_pico_card_config())[0]
                    self.assertIn(b"sound_output:%s\n" % value, plain)
                    self.assertEqual(with_preset, plain)
        finally:
            Probe.tearDownClass()

    def test_sound_rule_copied_from_patch(self):
        # La condition de l'outil est celle du patch 0002, recopiée à l'identique.
        import inspect
        with open(os.path.join(str(_paths.ROOT), "pi", "patches", "0002-cconfig-mode-pico.patch"),
                  encoding="utf-8") as f:
            patch = f.read()
        condition = "str(globals_section.get('sound_output') or 'auto').strip().lower() == 'auto'"
        self.assertIn("+        if %s:\n" % condition, patch)
        self.assertIn("return str(value or 'auto').strip().lower() == 'auto'",
                      inspect.getsource(M.sound_output_wants_hdmi))


class SoundOutput(Base):
    """[SectionGlobals] sound_output : hdmi si auto, vide ou absente ; jack, usb, hdmi conservés."""

    def migrated(self, text):
        return configparser_values(M.migrate_text(text).text(), "SectionGlobals").get("sound_output")

    def test_rule(self):
        for value, expected in (("auto", "hdmi"), ("AUTO", "hdmi"), (" Auto ", "hdmi"), ("", "hdmi"),
                                ("''", "hdmi"), ("jack", "jack"), ("usb", "usb"), ("hdmi", "hdmi")):
            with self.subTest(value=value):
                text = "[SectionGlobals]\nlocale:fr_FR\nsound_output:%s\n" % value
                self.assertEqual(self.migrated(text), expected)

    def test_jack_kept_untouched(self):
        text = "[SectionGlobals]\nsound_output:jack  # carte son\n\n[SectionAdvanced]\nhardware:pico\n"
        m = M.migrate_text(text)
        self.assertIn("sound_output:jack  # carte son\n", m.text())
        self.assertFalse([c for c in m.changed if "sound_output" in c[1]])

    def test_absent_key_or_section_added(self):
        self.assertEqual(self.migrated("[SectionGlobals]\nlocale:fr_FR\n"), "hdmi")
        self.assertEqual(self.migrated("[SectionAdvanced]\nhardware:pico\n"), "hdmi")


def configparser_values(text, section):
    parser = configparser.ConfigParser(interpolation=None, inline_comment_prefixes="#")
    parser.optionxform = str
    parser.read_string(text)
    return {k: M.game_value(v) for k, v in parser[section].items()} if parser.has_section(section) else {}


if __name__ == "__main__":
    unittest.main()
