#!/usr/bin/env python3
# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Migre une configuration RaspyDarts existante vers le mode « carte Pico ».

    python3 tools/migrate_to_pico.py [--config FICHIER] [--output FICHIER] [--in-place] [--dry-run]

Effet : celui du préréglage « Carte Pico » du patch 0002 (cconfig.py,
set_pico_card_config), appliqué ligne à ligne au fichier existant :
- [SectionAdvanced] hardware = pico ;
- [Raspberry] = correspondance de include/cpico.py (DEFAULTS : BTN1…BTN15, MISS,
  T1…T8, V1…V10 ; TOY9…TOY16 et PIN_MUSIC_* vides) ;
- [Raspberry_BoardPinsIns] (PIN_1…PIN_16) et [Raspberry_BoardPinsOuts] (PIN_1…PIN_10) vides ;
- [SectionKeys] vide : les segments sont à étalonner (codes R<ligne>C<colonne>).
- [SectionGlobals] sound_output = hdmi, seulement si la valeur est auto (majuscules et
  espaces ignorés), vide ou absente ; jack, usb ou hdmi sont conservés (§ 9.4, v0.3.11).
Rien d'autre n'est touché : commentaires, ordre, autres sections, [Raspberry_Leds].

Par défaut, une COPIE migrée est écrite (<fichier>.pico) ; --in-place remplace
l'original, toujours après une sauvegarde horodatée (<fichier>.avant-pico-AAAAMMJJ-HHMMSS).
--dry-run n'écrit rien. Une seconde exécution ne change rien.
"""

import argparse
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (os.path.join(_ROOT, "shared"), os.path.join(_ROOT, "pi", "hwd"),
           os.path.join(_ROOT, "pi", "raspydarts_layer", "include")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import cpico  # noqa: E402  (correspondance de [Raspberry], comme le patch 0002)

# ---------------------------------------------------------------- références
# Emplacement : include/cpaths.py:17-38 et 56-70 (RaspyDarts 5ae22c5, inchangé depuis 85c9f81).
USER_DIR_NAME, OLD_USER_DIR_NAME = ".raspydarts", ".pydarts"
CONFIG_NAME, OLD_CONFIG_NAME = "raspydarts.cfg", "pydarts.cfg"

# Clés du préréglage : PicoCardConfig du patch 0002 (pi/patches/0002-cconfig-mode-pico.patch),
# construites comme dans cconfig.py:440-460. Vérifiées par un test contre le vrai cconfig.py.
GPIO_BUTTONS = ("PLAYERBUTTON", "GAMEBUTTON", "BACKUPBUTTON", "EXTRABUTTON", "MISSDART", "SHOCKSENSOR")
SECTION_KEYS = ([f"{m}{n}" for m in ("s", "S", "D", "T") for n in range(1, 21)] + ["SB", "DB"]
                + list(GPIO_BUTTONS))
PINS_INS = [f"PIN_{n}" for n in range(1, 17)]
PINS_OUTS = [f"PIN_{n}" for n in range(1, 11)]

TARGET = {
    "Raspberry_BoardPinsIns": {k: "" for k in PINS_INS},
    "Raspberry_BoardPinsOuts": {k: "" for k in PINS_OUTS},
    "SectionKeys": {k: "" for k in SECTION_KEYS},
    "Raspberry": {k: str(v) for k, v in cpico.DEFAULTS.items()},
}
# Clés absentes du fichier : leur valeur par défaut dans cconfig.py vaut déjà '' pour les
# broches et [SectionKeys] (rien à ajouter) ; pour [Raspberry], elle vaut '0' ou '' :
# la ligne est ajoutée.
ADD_IF_MISSING = ("Raspberry",)

# Sortie du son : même règle que set_pico_card_config du patch 0002, recopiée à l'identique
# (l'outil ne peut pas importer le cconfig du jeu). Un test compare les deux conditions.
SOUND_OUTPUT_PICO = "hdmi"


def sound_output_wants_hdmi(value):
    """True si la sortie du son vaut auto, vide ou absente (None) : le préréglage écrit hdmi."""
    return str(value or 'auto').strip().lower() == 'auto'

SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
# Comme configparser : clé, séparateur ':' ou '=', valeur ; un « # » précédé d'un espace
# commence un commentaire en ligne (inline_comment_prefixes='#', cconfig.py:1668).
OPTION_RE = re.compile(r"^(?P<key>[^\s:=#;\[][^:=]*?)(?P<sep>\s*[:=])(?P<rest>.*)$")


def parse_option(raw):
    """Ligne « clé:valeur  # commentaire » -> dict(key, sep, lead, value, comment), ou None."""
    m = OPTION_RE.match(raw)
    if not m:
        return None
    rest = m.group("rest")
    c = re.search(r"\s+#", rest)          # toute la suite d'espaces reste avec le commentaire
    part, comment = (rest[:c.start()], rest[c.start():]) if c else (rest, "")
    value = part.strip()
    lead = part[:len(part) - len(part.lstrip())]
    return {"key": m.group("key"), "sep": m.group("sep"), "lead": lead,
            "value": value, "comment": comment}


def default_config_path(home=None):
    """Fichier de configuration du jeu, comme include/cpaths.py (sans rien renommer)."""
    home = home or os.path.expanduser("~" + os.environ.get("SUDO_USER", ""))
    user_dir = os.path.join(home, USER_DIR_NAME)
    if not os.path.isdir(user_dir) and os.path.isdir(os.path.join(home, OLD_USER_DIR_NAME)):
        user_dir = os.path.join(home, OLD_USER_DIR_NAME)
    new, old = os.path.join(user_dir, CONFIG_NAME), os.path.join(user_dir, OLD_CONFIG_NAME)
    if os.path.isfile(old) and (not os.path.isfile(new) or os.path.getmtime(old) > os.path.getmtime(new)):
        return old          # le jeu le renommerait en raspydarts.cfg à son prochain démarrage
    return new


def game_value(raw):
    """Valeur telle que cconfig.read_file la lit (cconfig.py:1709-1712)."""
    if raw == "''":
        return ""
    if len(raw) >= 2 and raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1]
    return raw


class Migration:
    def __init__(self, text):
        self.bom = text.startswith("﻿")
        self.lines = (text[1:] if self.bom else text).splitlines(keepends=True)
        self.newline = "\r\n" if self.lines and self.lines[0].endswith("\r\n") else "\n"
        self.changed = []     # (n° de ligne, avant, après)
        self.added = []       # (n° de ligne dans le résultat, texte)
        self.warnings = []

    # ------------------------------------------------------------- lecture
    def sections(self):
        """{section: (indice de l'en-tête, indice de fin exclu)} ; avertit des doublons."""
        found, order = {}, []
        for i, line in enumerate(self.lines):
            m = SECTION_RE.match(line.rstrip("\r\n"))
            if m:
                name = m.group(1).strip()
                if name in found:
                    self.warnings.append(f"section [{name}] en double (ligne {i + 1}) : le jeu "
                                         "refuserait ce fichier ; seule la première est migrée")
                    continue
                found[name] = i
                order.append((i, name))
        bounds = {}
        for k, (i, name) in enumerate(order):
            end = order[k + 1][0] if k + 1 < len(order) else len(self.lines)
            bounds[name] = (i, end)
        return bounds

    def options(self, start, end):
        """{clé: indice de ligne} dans une section."""
        out = {}
        for i in range(start + 1, end):
            raw = self.lines[i].rstrip("\r\n")
            if not raw.strip() or raw.lstrip().startswith(("#", ";")) or raw[:1].isspace():
                continue
            m = parse_option(raw)
            if m:
                key = m["key"].strip()
                if key in out:
                    self.warnings.append(f"clé {key} en double (ligne {i + 1}) : le jeu refuserait ce fichier")
                    continue
                out[key] = i
        return out

    # ---------------------------------------------------------- modification
    def set_value(self, index, value):
        raw = self.lines[index]
        ending = raw[len(raw.rstrip("\r\n")):]
        m = parse_option(raw.rstrip("\r\n"))
        if game_value(m["value"]) == value:
            return
        # Séparateur, espaces et commentaire en ligne gardés ; « PIN_1:24 » -> « PIN_1: ».
        new = m["key"] + m["sep"] + (m["lead"] if value else "") + value + m["comment"]
        if not m["comment"]:
            new = new.rstrip()
        self.changed.append((index + 1, raw.rstrip("\r\n"), new))
        self.lines[index] = new + ending

    def insert(self, index, text):
        self.lines.insert(index, text + self.newline)
        self.added.append((index, text))

    def insertion_point(self, start, end):
        """Après la dernière ligne non vide de la section (les lignes vides de fin restent après)."""
        last = start
        for i in range(start + 1, end):
            if self.lines[i].strip():
                last = i
        if not self.lines[last].endswith(("\n", "\r")):
            self.lines[last] += self.newline
        return last + 1

    def migrate_section(self, name, wanted):
        bounds = self.sections()
        if name not in bounds:
            if name in ADD_IF_MISSING:
                if self.lines and not self.lines[-1].endswith(("\n", "\r")):
                    self.lines[-1] += self.newline
                at = len(self.lines)
                self.insert(at, f"[{name}]")
                for k, (key, value) in enumerate(wanted.items()):
                    self.insert(at + 1 + k, f"{key}:{value}")
            return
        start, end = bounds[name]
        opts = self.options(start, end)
        for key, value in wanted.items():
            if key in opts:
                self.set_value(opts[key], value)
        if name in ADD_IF_MISSING:
            missing = [k for k in wanted if k not in opts]
            at = self.insertion_point(start, end)
            for k, key in enumerate(missing):
                self.insert(at + k, f"{key}:{wanted[key]}")

    def migrate_hardware(self):
        bounds = self.sections()
        if "SectionAdvanced" not in bounds:
            if self.lines and not self.lines[-1].endswith(("\n", "\r")):
                self.lines[-1] += self.newline
            at = len(self.lines)
            self.insert(at, "[SectionAdvanced]")
            self.insert(at + 1, "hardware:pico")
            return
        start, end = bounds["SectionAdvanced"]
        opts = self.options(start, end)
        if "hardware" in opts:
            raw = parse_option(self.lines[opts["hardware"]].rstrip("\r\n"))["value"]
            if game_value(raw).strip().lower() == "pico":
                return
            if game_value(raw).strip().lower() not in ("", "normal"):
                self.warnings.append(f"hardware valait {raw!r} (valeur inconnue) : remplacée par pico")
            self.set_value(opts["hardware"], "pico")
            return
        # Même place que write_file du patch 0002 : avant « ### For Richard's case ».
        at = None
        for i in range(start + 1, end):
            stripped = self.lines[i].strip()
            if stripped == "### For Richard's case" or (at is None and stripped.startswith("richard-mode")):
                at = i
                break
        if at is None:
            at = self.insertion_point(start, end)
        self.insert(at, "hardware:pico")

    def migrate_sound_output(self):
        bounds = self.sections()
        if "SectionGlobals" not in bounds:
            if self.lines and not self.lines[-1].endswith(("\n", "\r")):
                self.lines[-1] += self.newline
            at = len(self.lines)
            self.insert(at, "[SectionGlobals]")
            self.insert(at + 1, f"sound_output:{SOUND_OUTPUT_PICO}")
            return
        start, end = bounds["SectionGlobals"]
        opts = self.options(start, end)
        if "sound_output" in opts:
            raw = parse_option(self.lines[opts["sound_output"]].rstrip("\r\n"))["value"]
            if sound_output_wants_hdmi(game_value(raw)):
                self.set_value(opts["sound_output"], SOUND_OUTPUT_PICO)
            return
        self.insert(self.insertion_point(start, end), f"sound_output:{SOUND_OUTPUT_PICO}")

    def run(self):
        before = {name: dict(self.values(name)) for name in TARGET}
        self.migrate_hardware()
        self.migrate_sound_output()
        for name, wanted in TARGET.items():
            self.migrate_section(name, wanted)
        if self.bom:
            self.warnings.append("le fichier commence par une marque BOM : le jeu d'origine refuse "
                                 "de le lire (cconfig.py:1674-1677) ; la marque est conservée")
        cleared = sum(1 for v in before["SectionKeys"].values() if v)
        if cleared:
            self.warnings.append(f"{cleared} code(s) de segment effacé(s) dans [SectionKeys] : "
                                 "étalonnage de la cible à refaire (codes R<ligne>C<colonne>)")
        pins = sum(1 for n in ("Raspberry_BoardPinsIns", "Raspberry_BoardPinsOuts")
                   for v in before[n].values() if v)
        if pins:
            self.warnings.append(f"{pins} broche(s) GPIO de l'ancienne cible effacée(s)")
        events = self.values("Events")
        old_toys = sorted({t for v in events.values() for t in re.findall(r"\bTOY(?:9|1[0-6])\b", v)})
        if old_toys:
            self.warnings.append("[Events] cite " + ", ".join(old_toys) + " : absents de la carte Pico, ignorés")
        if self.changed or self.added:
            self.warnings.append("[Raspberry_Leds] non modifiée (serveurs LED : patch à venir)")
        return self

    def values(self, name):
        bounds = self.sections()
        if name not in bounds:
            return {}
        start, end = bounds[name]
        out = {}
        for key, i in self.options(start, end).items():
            out[key] = game_value(parse_option(self.lines[i].rstrip("\r\n"))["value"])
        return out

    def text(self):
        return ("﻿" if self.bom else "") + "".join(self.lines)


def migrate_text(text):
    return Migration(text).run()


def report(m, source, destination, mode):
    out = [f"Configuration : {source}", f"Mode : {mode}"]
    if not m.changed and not m.added:
        out.append("Déjà au format Pico : aucune modification.")
    else:
        out.append(f"Lignes modifiées : {len(m.changed)}")
        out += [f"  l.{n} : {a!r} -> {b!r}" for n, a, b in m.changed]
        out.append(f"Lignes ajoutées : {len(m.added)}")
        out += [f"  + {t!r}" for _, t in m.added]
    if destination:
        out.append(f"Écrit : {destination}")
    if m.warnings:
        out.append("Avertissements :")
        out += [f"  - {w}" for w in m.warnings]
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Migre une configuration RaspyDarts vers le mode carte Pico.")
    parser.add_argument("--config", help="fichier à migrer (défaut : celui du jeu, comme include/cpaths.py)")
    parser.add_argument("--output", help="copie migrée (défaut : <fichier>.pico)")
    parser.add_argument("--in-place", action="store_true", help="remplacer l'original, après une sauvegarde horodatée")
    parser.add_argument("--dry-run", action="store_true", help="n'écrire rien, afficher seulement le rapport")
    args = parser.parse_args(argv)
    if args.in_place and args.output:
        parser.error("--in-place et --output sont incompatibles")

    source = os.path.abspath(args.config or default_config_path())
    orig = os.path.realpath(os.environ.get("RASPYDARTS_ORIG", os.path.expanduser("~/RaspyDarts-ref465")))
    if os.path.realpath(source).startswith(orig + os.sep):
        print(f"Refusé : {source} est dans la copie de référence {orig}.", file=sys.stderr)
        return 2
    try:
        with open(source, "rb") as f:
            data = f.read()
        text = data.decode("utf-8")
    except FileNotFoundError:
        print(f"Fichier absent : {source}", file=sys.stderr)
        return 2
    except UnicodeDecodeError as e:
        print(f"Fichier illisible (pas de l'UTF-8) : {e}", file=sys.stderr)
        return 2
    if os.path.basename(source) == OLD_CONFIG_NAME:
        print(f"# Note : {OLD_CONFIG_NAME} ; le jeu le renommera en {CONFIG_NAME}.", file=sys.stderr)

    m = migrate_text(text)
    new = m.text().encode("utf-8")
    nothing = new == data
    if args.dry_run:
        print(report(m, source, None, "simulation (--dry-run) : rien n'est écrit"))
        return 0
    if args.in_place:
        if nothing:
            print(report(m, source, None, "remplacement (--in-place) : rien à faire, aucune sauvegarde"))
            return 0
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = f"{source}.avant-pico-{stamp}"
        n = 1
        while os.path.exists(backup):
            n += 1
            backup = f"{source}.avant-pico-{stamp}-{n}"
        with open(backup, "xb") as f:
            f.write(data)
        tmp = source + ".migration-tmp"
        with open(tmp, "wb") as f:
            f.write(new)
        os.replace(tmp, source)
        print(report(m, source, source, f"remplacement (--in-place), sauvegarde : {backup}"))
        return 0
    destination = os.path.abspath(args.output or source + ".pico")
    if os.path.realpath(destination) == os.path.realpath(source):
        print("Refusé : la copie serait l'original (utiliser --in-place).", file=sys.stderr)
        return 2
    with open(destination, "wb") as f:
        f.write(new)
    print(report(m, source, destination, "copie (l'original n'est pas modifié)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
