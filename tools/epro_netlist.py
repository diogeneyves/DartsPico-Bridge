# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Netlist d'un projet EasyEDA Pro (.epro2) et contrôle avant commande de la carte.

Spécification, complément 66. Le fichier .epro2 est une archive zip qui
contient un journal (`*.epru`) : une ligne par enregistrement, « en-tête JSON
|| corps JSON | ». Ce script en tire, pour chaque repère (R97, U1…) :
- la valeur, la référence du fabricant et le code LCSC (attributs du schéma,
  à défaut ceux du composant de la bibliothèque) ;
- le réseau de chaque pastille du PCB (enregistrements PAD_NET) ;
- le réseau de chaque broche du schéma. Le journal ne l'enregistre pas : il est
  reconstruit à partir des fils (attribut NET), de la position des broches des
  symboles et des marques « non connecté ». Un cas que l'outil ne sait pas
  traiter de façon sûre (composant en miroir, rotation autre qu'un quart de
  tour, deux noms sur des fils reliés) arrête le contrôle.

Seuls ces champs sont lus et écrits : rien d'autre du projet (compte EasyEDA,
identifiants, chemins) ne passe dans la sortie. Le .epro2 lui-même n'est jamais
commité (hardware/EasyEda/ est dans .gitignore).

Sous-commandes :
    python3 tools/epro_netlist.py extract hardware/EasyEda/Darts.epro2 -o hardware/netlist.json
    python3 tools/epro_netlist.py check hardware/EasyEda/Darts.epro2
    python3 tools/epro_netlist.py dump hardware/EasyEda/Darts.epro2 --ref R97 --ref U1

`check` (à lancer avant toute commande de la carte) compare le schéma au PCB,
le projet à hardware/pinout.json, puis vérifie que hardware/netlist.json est à
jour. Code de sortie 0 si tout concorde, 1 sinon, avec un message par écart.
"""

import argparse
import collections
import json
import math
import re
import sys
import zipfile
from pathlib import Path

FIELDS = ("Value", "Manufacturer Part", "Supplier Part")
HARDWARE = Path(__file__).resolve().parent.parent / "hardware"
EPS = 1e-3                                           # tolérance des coordonnées du schéma

# Repères cités dans pinout.json : préfixes des composants de la carte. « D0 » est
# un réseau (sortie du LM393), pas une diode.
REF_RE = re.compile(r"\b((?:JP|SW|[RCDUJPFQ])\d{1,3})\b")
NOT_REFS = {"D0"}


def _records(lines):
    """(type de document, uuid du document, en-tête, corps) pour chaque ligne du journal."""
    doc = None
    doc_uuid = None
    for line in lines:
        line = line.rstrip("\n")
        if not line:
            continue
        head, _, rest = line.partition("||")
        hd = json.loads(head)
        body = rest[:-1] if rest.endswith("|") else rest
        body = json.loads(body) if body else None
        if hd.get("type") == "DOCHEAD":
            doc = (body or {}).get("docType")
            doc_uuid = (body or {}).get("uuid")
            continue
        yield doc, doc_uuid, hd, body


def _ref_key(ref):
    letters = ref.rstrip("0123456789")
    num = ref[len(letters):]
    return (letters, int(num) if num else -1)


def _pad_key(pad):
    return (len(pad), pad)


# --- Lecture du journal --------------------------------------------------------

Project = collections.namedtuple("Project", "parts sch errors")


def parse(lines):
    """Repère -> attributs et réseaux des pastilles du PCB."""
    return parse_project(lines).parts


def parse_project(lines):
    """Projet complet : pièces (PCB), réseaux des broches du schéma, erreurs de lecture."""
    devices = {}                                     # uuid -> attributs de bibliothèque
    sch = collections.defaultdict(dict)              # objet du schéma -> attributs
    pcb = collections.defaultdict(dict)              # composant du PCB -> attributs
    pcb_comps = set()
    pads = collections.defaultdict(dict)
    sym_pins = collections.defaultdict(dict)         # symbole -> broche -> corps
    sym_attr = collections.defaultdict(lambda: collections.defaultdict(dict))
    sch_comps = {}
    wires = set()
    lines_of = collections.defaultdict(list)
    for doc, doc_uuid, hd, body in _records(lines):
        t = hd.get("type")
        if doc == "DEVICE" and t == "META" and body:
            devices[doc_uuid] = body.get("attributes", {})
        elif doc == "SYMBOL" and t == "PIN" and body:
            sym_pins[doc_uuid][hd["id"]] = body
        elif doc == "SYMBOL" and t == "ATTR" and body and body.get("parentId"):
            sym_attr[doc_uuid][body["parentId"]][body["key"]] = body["value"]
        elif t == "ATTR" and body and body.get("parentId") and doc in ("SCH_PAGE", "PCB"):
            (sch if doc == "SCH_PAGE" else pcb)[body["parentId"]][body["key"]] = body["value"]
        elif doc == "SCH_PAGE" and t == "COMPONENT" and body:
            sch_comps[hd["id"]] = body
        elif doc == "SCH_PAGE" and t == "WIRE":
            wires.add(hd["id"])
        elif doc == "SCH_PAGE" and t == "LINE" and body and body.get("lineGroup"):
            lines_of[body["lineGroup"]].append(body)
        elif doc == "PCB" and t == "COMPONENT" and body:
            pcb_comps.add(hd["id"])
        elif doc == "PCB" and t == "PAD_NET" and body:
            comp, pad = json.loads(hd["id"])[1:3]
            pads[comp][pad] = body.get("padNet") or ""

    out = {}
    for a in sch.values():
        ref = a.get("Designator")
        if not ref or "Symbol" not in a or "Device" not in a:
            continue                                 # symboles de réseau, alimentations
        dev = devices.get(a["Device"], {})
        entry = out.setdefault(ref, {})
        for k in FIELDS:
            v = a.get(k) or dev.get(k)
            if v:
                entry[k] = v
        if a.get("Add into BOM", dev.get("Add into BOM")) == "no":
            entry["bom"] = False
    for comp in pcb_comps:
        ref = pcb[comp].get("Designator")
        if ref:
            out.setdefault(ref, {})["pads"] = dict(sorted(pads[comp].items(), key=lambda kv: _pad_key(kv[0])))
    parts = dict(sorted(out.items(), key=lambda kv: _ref_key(kv[0])))

    pins, errors = _schematic_nets(sch, sch_comps, sym_pins, sym_attr, wires, lines_of)
    return Project(parts, pins, errors)


class _UnionFind:
    def __init__(self):
        self.up = {}

    def find(self, x):
        self.up.setdefault(x, x)
        while self.up[x] != x:
            self.up[x] = self.up[self.up[x]]
            x = self.up[x]
        return x

    def union(self, a, b):
        self.up[self.find(a)] = self.find(b)


def _on_segment(x, y, seg):
    x1, y1, x2, y2 = seg
    if abs((x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)) > EPS * max(1.0, abs(x2 - x1) + abs(y2 - y1)):
        return False
    return min(x1, x2) - EPS <= x <= max(x1, x2) + EPS and min(y1, y2) - EPS <= y <= max(y1, y2) + EPS


class _SegmentIndex:
    """Segments rangés par cases, pour trouver vite ceux qui passent par un point."""
    CELL = 50.0

    def __init__(self, segs):
        self.cells = collections.defaultdict(list)
        for item in segs:
            x1, y1, x2, y2 = item[1]
            for cx in range(self._c(min(x1, x2) - EPS), self._c(max(x1, x2) + EPS) + 1):
                for cy in range(self._c(min(y1, y2) - EPS), self._c(max(y1, y2) + EPS) + 1):
                    self.cells[(cx, cy)].append(item)

    def _c(self, v):
        return math.floor(v / self.CELL)

    def at(self, x, y):
        seen = set()
        for cx in (self._c(x - EPS), self._c(x + EPS)):
            for cy in (self._c(y - EPS), self._c(y + EPS)):
                for item in self.cells.get((cx, cy), ()):
                    if id(item) not in seen and _on_segment(x, y, item[1]):
                        seen.add(id(item))
                        yield item


def _schematic_nets(sch, sch_comps, sym_pins, sym_attr, wires, lines_of):
    """Repère -> broche -> ("net", nom) | ("anon", n) | ("nc", None) | ("open", None)."""
    errors = []
    segs = []                                        # (fil, segment)
    for w in sorted(wires):
        for ln in lines_of.get(w, ()):
            segs.append((w, (ln["startX"], ln["startY"], ln["endX"], ln["endY"])))
    uf = _UnionFind()
    for w in wires:
        uf.find(("w", w))
    # Fils reliés : une extrémité d'un segment sur un segment d'un autre fil.
    index = _SegmentIndex(segs)
    for w, s in segs:
        for x, y in ((s[0], s[1]), (s[2], s[3])):
            for w2, _ in index.at(x, y):
                if w2 != w:
                    uf.union(("w", w), ("w", w2))

    pin_pos = []                                     # (repère, numéro, clé, x, y)
    for cid in sorted(sch_comps):
        c = sch_comps[cid]
        a = sch.get(cid, {})
        ref, sym = a.get("Designator"), a.get("Symbol")
        if not ref or sym not in sym_pins:
            continue
        rot = c.get("rotation") or 0
        if c.get("isMirror"):
            errors.append("%s : composant en miroir au schéma, non pris en charge (contrôle arrêté)" % ref)
            continue
        if rot % 90:
            errors.append("%s : rotation de %s° au schéma, non prise en charge (contrôle arrêté)" % (ref, rot))
            continue
        # Rotation du schéma EasyEDA Pro : sens horaire à l'écran (axe y vers le bas),
        # vérifiée sur le projet réel (complément 66).
        th = math.radians(-rot)
        cos, sin = round(math.cos(th)), round(math.sin(th))
        for pid in sorted(sym_pins[sym]):
            p = sym_pins[sym][pid]
            if p.get("partId") != c.get("partId"):
                continue                             # autre partie d'un symbole multiple
            num = sym_attr[sym][pid].get("Pin Number")
            if not num:
                errors.append("%s : broche %s sans numéro dans le symbole" % (ref, pid))
                continue
            x = c["x"] + p["x"] * cos - p["y"] * sin
            y = c["y"] + p["x"] * sin + p["y"] * cos
            nc = sch.get("%s-%s" % (cid, pid), {}).get("NO_CONNECT") == "yes"
            pin_pos.append((ref, num, ("p", cid, pid), x, y, nc))

    for ref, num, key, x, y, nc in pin_pos:
        uf.find(key)
        for w, _ in index.at(x, y):
            uf.union(key, ("w", w))
    for i, (_, _, k1, x1, y1, _) in enumerate(pin_pos):  # broche posée sur une broche
        for _, _, k2, x2, y2, _ in pin_pos[i + 1:]:
            if abs(x1 - x2) <= EPS and abs(y1 - y2) <= EPS:
                uf.union(k1, k2)

    names = collections.defaultdict(set)
    for w in wires:
        n = sch.get(w, {}).get("NET") or ""
        if n:
            names[uf.find(("w", w))].add(n)
    for root, ns in sorted(names.items(), key=lambda kv: sorted(kv[1])):
        if len(ns) > 1:
            errors.append("fils reliés au schéma avec plusieurs noms de réseau : %s (contrôle arrêté)"
                          % ", ".join(sorted(ns)))
    # Même nom = même réseau, même sans fil entre les deux (étiquettes de réseau).
    by_name = {}
    for root, ns in names.items():
        n = min(ns)
        if n in by_name:
            uf.union(root, by_name[n])
        else:
            by_name[n] = root
    net_of = {}
    for n, root in by_name.items():
        net_of[uf.find(root)] = n

    count = collections.Counter(uf.find(k) for _, _, k, _, _, _ in pin_pos)
    anon = {}
    result = collections.defaultdict(dict)
    for ref, num, key, x, y, nc in pin_pos:
        root = uf.find(key)
        if root in net_of:
            v = ("net", net_of[root])
        elif count[root] > 1:
            v = ("anon", anon.setdefault(root, len(anon) + 1))
        else:
            v = ("nc" if nc else "open", None)
        if nc and v[0] not in ("nc",):
            errors.append("%s.%s : marquée non connectée au schéma, mais reliée" % (ref, num))
        old = result[ref].get(num)
        if old is not None and old != v:
            errors.append("%s.%s : deux broches du même numéro sur des réseaux différents" % (ref, num))
        result[ref][num] = v
    return dict(result), errors


# --- Schéma ↔ PCB ---------------------------------------------------------------

def _show(v):
    kind, name = v
    return {"net": name, "anon": "réseau anonyme", "nc": "non connectée", "open": "non connectée"}[kind]


def compare_sch_pcb(proj):
    """Écarts entre les réseaux du schéma et ceux des pastilles du PCB (contrôle (1))."""
    errs = list(proj.errors)
    if errs:
        return errs
    sch, parts = proj.sch, proj.parts
    pcb_refs = {r for r, e in parts.items() if "pads" in e}
    for ref in sorted(set(sch) - pcb_refs, key=_ref_key):
        errs.append("%s : au schéma, absent du PCB" % ref)
    for ref in sorted(pcb_refs - set(sch), key=_ref_key):
        if any(parts[ref]["pads"].values()):
            errs.append("%s : au PCB, absent du schéma" % ref)

    anon_to_pcb, pcb_to_anon = {}, {}
    pcb_members = collections.Counter(n for e in parts.values() for n in e.get("pads", {}).values() if n)
    for ref in sorted(set(sch) & pcb_refs, key=_ref_key):
        pads = parts[ref]["pads"]
        for num in sorted(set(sch[ref]) | set(pads), key=_pad_key):
            where = "%s.%s" % (ref, num)
            if num not in sch[ref]:
                if pads[num]:
                    errs.append("%s : pastille du PCB sur %s, sans broche au schéma" % (where, pads[num]))
                continue
            v = sch[ref][num]
            if num not in pads:
                errs.append("%s : broche du schéma (%s), sans pastille au PCB" % (where, _show(v)))
                continue
            p = pads[num]
            kind, name = v
            if kind == "net":
                if p != name:
                    errs.append("%s : réseau %s au schéma, %s au PCB" % (where, name, p or "non connectée"))
            elif kind == "anon":
                if not p.startswith("$"):
                    errs.append("%s : réseau anonyme au schéma, %s au PCB" % (where, p or "non connectée"))
                elif anon_to_pcb.setdefault(name, p) != p or pcb_to_anon.setdefault(p, name) != name:
                    errs.append("%s : réseau anonyme du schéma qui ne correspond pas à %s au PCB "
                                "(liaison oubliée ou en trop)" % (where, p))
            else:                                    # broche isolée au schéma
                if p and not (p.startswith("$") and pcb_members[p] == 1):
                    errs.append("%s : non connectée au schéma, %s au PCB" % (where, p))
    return errs


# --- Extrait versionné (hardware/netlist.json) ---------------------------------

def stable_nets(parts):
    """Copie où chaque réseau anonyme ($1N…) prend le nom `~` + sa première pastille."""
    first = {}
    for ref in sorted(parts, key=_ref_key):
        for pad, net in sorted(parts[ref].get("pads", {}).items(), key=lambda kv: _pad_key(kv[0])):
            if net.startswith("$"):
                first.setdefault(net, "~%s.%s" % (ref, pad))
    out = {}
    for ref, e in parts.items():
        e = dict(e)
        if "pads" in e:
            e["pads"] = {p: first.get(n, n) for p, n in e["pads"].items()}
        out[ref] = e
    return out


def _strings(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)
    elif isinstance(o, str):
        yield o


def cited_refs(pinout):
    """Repères de composants cités dans pinout.json (clés et textes)."""
    refs = set()
    for s in _strings(pinout):
        refs.update(m for m in REF_RE.findall(s) if m not in NOT_REFS)
    return refs


def extract(parts, pinout):
    """Contenu de hardware/netlist.json : repères cités dans pinout.json seulement."""
    stable = stable_nets(parts)
    refs = {r: stable[r] for r in sorted(cited_refs(pinout), key=_ref_key) if r in stable}
    return {
        "_comment": "Produit par tools/epro_netlist.py extract depuis le projet EasyEDA "
                    "(hardware/EasyEda/Darts.epro2, non commite) ; ne pas modifier a la main. "
                    "Reperes cites dans hardware/pinout.json ; reseaux anonymes nommes ~ + premiere "
                    "pastille. Specification, complement 66.",
        "refs": refs,
    }


def dumps(netlist):
    """Mise en forme unique de netlist.json (diff stable)."""
    refs = {r: dict(sorted(e.items())) for r, e in sorted(netlist["refs"].items(), key=lambda kv: _ref_key(kv[0]))}
    for e in refs.values():
        if "pads" in e:
            e["pads"] = dict(sorted(e["pads"].items(), key=lambda kv: _pad_key(kv[0])))
    return json.dumps({"_comment": netlist["_comment"], "refs": refs}, ensure_ascii=False, indent=1) + "\n"


def compare_extract(saved, fresh):
    """Écarts entre netlist.json et l'extrait tiré du .epro2 (contrôle (3))."""
    errs = []
    a, b = saved.get("refs", {}), fresh["refs"]
    for ref in sorted(set(a) | set(b), key=_ref_key):
        if ref not in a:
            errs.append("netlist.json : %s manque (relancer extract)" % ref)
        elif ref not in b:
            errs.append("netlist.json : %s en trop (relancer extract)" % ref)
        elif a[ref] != b[ref]:
            errs.append("netlist.json : %s pas à jour (relancer extract)" % ref)
    return errs


# --- Projet ↔ pinout.json ------------------------------------------------------

def _norm_value(v):
    v = v.replace("Ω", "").replace("ohm", "").replace(" ", "").replace("µ", "u").lower()
    return v[:-1] if v.endswith("r") else v


# « R30, R31 4.7k », « R96 330R », « C25 100 nF » ; « U2 fusible 0501020.WR », « F1 1206L110/16NR ».
VALUE_RE = re.compile(r"\b((?:[RC]\d{1,3}, )*[RC]\d{1,3}) (\d+(?:\.\d+)?(?: ?(?:k|M|R|nF|uF|pF))?)(?![\w.])")
PART_RE = re.compile(r"\b([FU]\d{1,3}) (?:fusible )?([0-9][0-9A-Z./-]{5,})")


def compare_pinout(refs, pinout):
    """Écarts entre les repères du projet (netlist) et hardware/pinout.json (contrôle (2))."""
    errs = []

    def absent(ref):
        if ref not in refs or "pads" not in refs[ref]:
            return True
        return False

    def pad(ref, num):
        return refs[ref]["pads"].get(str(num))

    def expect(ref, num, net, what=""):
        if absent(ref):
            return
        got = pad(ref, num)
        if got is None:
            errs.append("%s.%s%s : pastille absente du projet" % (ref, num, what))
        elif got != net:
            errs.append("%s.%s%s : réseau %s attendu (pinout.json), trouvé %s"
                        % (ref, num, what, net or "non connectée", got or "non connectée"))

    for ref in sorted(cited_refs(pinout), key=_ref_key):
        if ref not in refs:
            errs.append("%s : cité dans pinout.json, absent du projet" % ref)

    pico = pinout.get("pico", {})
    u1 = pico.get("ref", "U1")
    for gp, e in pico.get("pins", {}).items():
        expect(u1, e["physical_pin"], e["net"], " (%s)" % gp)
    power = pico.get("power", {})
    if "3V3_pin36" in power:
        expect(u1, 36, "+3V3", " (3V3)")
    if "VBUS_pin40" in power:
        expect(u1, 40, "", " (VBUS)")
    m = re.search(r"\b(D\d+)\b.*depuis (\S+)", power.get("VSYS_pin39", ""))
    if m and not absent(u1) and not absent(m.group(1)):
        d, src = m.groups()
        dp = refs[d]["pads"]
        if pad(u1, 39) not in dp.values() or src not in dp.values():
            errs.append("%s.39 (VSYS) : doit aller à %s, dont l'autre broche est sur %s ; trouvé %s, %s %s"
                        % (u1, d, src, pad(u1, 39), d, dp))

    hb = pinout.get("heartbeat")
    if hb:
        expect(u1, hb["physical_pin"], hb["net"], " (%s)" % hb["gpio"])
        r, led = hb["resistor"]["ref"], hb["led"]["ref"]
        if not absent(r) and not absent(led):
            rp, lp = refs[r]["pads"], refs[led]["pads"]
            if hb["net"] not in rp.values():
                errs.append("%s : aucune pastille sur %s" % (r, hb["net"]))
            if hb["led"]["anode"] not in lp.values():
                errs.append("%s : anode sur %s attendue, trouvé %s" % (led, hb["led"]["anode"], lp))
            r_other = {n for n in rp.values() if n != hb["net"]}
            l_other = {n for n in lp.values() if n != hb["led"]["anode"]}
            if not r_other or r_other != l_other:
                errs.append("%s : cathode à relier à l'autre pastille de %s (%s), trouvé %s"
                            % (led, r, sorted(r_other), sorted(l_other)))
        _value(errs, refs, r, hb["resistor"].get("value"))

    for j, pins in pinout.get("button_connectors", {}).items():
        if j.startswith("_"):
            continue
        for num, net in pins.items():
            expect(j, num, net)

    for section in ("toys", "lights", "led_outputs"):
        for item in pinout.get(section, []):
            jp = item.get("supply_jumper")
            if not jp:
                continue
            expect(jp["ref"], 1, jp["pin1"])
            expect(jp["ref"], 3, jp["pin3"])
            if "common" in jp:
                c_ref, c_pin = jp["common"].split(".")
                if not absent(c_ref) and not absent(jp["ref"]):
                    expect(jp["ref"], 2, pad(c_ref, c_pin), " (commun, %s)" % jp["common"])

    matrix = pinout.get("matrix", {})
    p2 = matrix.get("connector")
    if p2:
        for group in ("rows", "columns", "other"):
            for net, num in p2.get(group, {}).items():
                expect(p2["ref"], num, net)
        for num in p2.get("not_connected", []):
            expect(p2["ref"], num, "")
    rows = matrix.get("rows", {})
    for ref, e in rows.get("pullup_resistors", {}).items():
        if absent(ref):
            continue
        for num, net in e["pads"].items():
            expect(ref, num, net)
        _value(errs, refs, ref, e.get("value"))
        _field(errs, refs, ref, "Manufacturer Part", e.get("part"), exact=True)
        _field(errs, refs, ref, "Supplier Part", e.get("lcsc"), exact=True)

    # Composants décrits par un repère et une référence : {"ref": "U6", "part": "74HC154"}.
    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("ref"), str) and isinstance(o.get("part"), str):
                _field(errs, refs, o["ref"], "Manufacturer Part", o["part"], exact=False)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(pinout)

    # Valeurs et références écrites dans les textes sous la forme « repère valeur ».
    for s in _strings(pinout):
        for group, value in VALUE_RE.findall(s):
            for ref in group.split(", "):
                _value(errs, refs, ref, value)
        for ref, part in PART_RE.findall(s):
            _field(errs, refs, ref, "Manufacturer Part", part, exact=True)
    return sorted(set(errs), key=errs.index)


def _value(errs, refs, ref, value):
    if not value or ref not in refs:
        return
    got = refs[ref].get("Value")
    if got is None or _norm_value(got) != _norm_value(value):
        errs.append("%s : valeur %s attendue (pinout.json), trouvé %s" % (ref, value, got))


def _field(errs, refs, ref, key, want, exact):
    if not want or ref not in refs:
        return
    got = refs[ref].get(key) or ""
    ok = got == want if exact else want.lower() in got.lower()
    if not ok:
        errs.append("%s : %s %s attendu (pinout.json), trouvé %s" % (ref, key, want, got or "rien"))


# --- Ligne de commande ---------------------------------------------------------

def load_project(path):
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.endswith(".epru")]
        if len(names) != 1:
            raise SystemExit("# %s : un seul fichier .epru attendu, trouvé %d" % (path, len(names)))
        with z.open(names[0]) as f:
            return parse_project(line.decode("utf-8") for line in f)


def load_epro2(path):
    return load_project(path).parts


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class _Formatter(argparse.HelpFormatter):
    """Aide en français (argparse écrit « usage: » en anglais)."""

    def add_usage(self, usage, actions, groups, prefix=None):
        super().add_usage(usage, actions, groups, "utilisation : " if prefix is None else prefix)


def _parser(**kw):
    ap = argparse.ArgumentParser(formatter_class=_Formatter, add_help=False, **kw)
    ap._positionals.title = "arguments"
    ap._optionals.title = "options"
    ap.add_argument("-h", "--help", action="help", help="afficher cette aide et quitter")
    return ap


def main(argv=None, out=None):
    out = out or sys.stdout
    ap = _parser(description="Netlist d'un projet EasyEDA Pro (.epro2) et contrôle "
                             "avant commande de la carte (spécification, complément 66).")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="commande", title="commandes",
                            parser_class=lambda **kw: _parser(**kw))
    common = {"pinout": HARDWARE / "pinout.json", "netlist": HARDWARE / "netlist.json"}

    p = sub.add_parser("extract", help="écrire l'extrait versionné hardware/netlist.json")
    p.add_argument("epro2", help="projet exporté (hardware/EasyEda/Darts.epro2)")
    p.add_argument("--pinout", default=common["pinout"], help="pinout.json (défaut : hardware/pinout.json)")
    p.add_argument("-o", "--output", help="fichier de sortie (défaut : sortie standard)")

    p = sub.add_parser("check", help="contrôle avant commande : schéma ↔ PCB ↔ pinout.json, extrait à jour")
    p.add_argument("epro2", help="projet exporté (hardware/EasyEda/Darts.epro2)")
    p.add_argument("--pinout", default=common["pinout"], help="pinout.json (défaut : hardware/pinout.json)")
    p.add_argument("--netlist", default=common["netlist"], help="netlist.json (défaut : hardware/netlist.json)")

    p = sub.add_parser("dump", help="afficher la netlist complète (ou quelques repères)")
    p.add_argument("epro2", help="projet exporté")
    p.add_argument("--ref", action="append", help="repère à afficher (plusieurs possibles)")

    args = ap.parse_args(argv)
    proj = load_project(args.epro2)

    if args.cmd == "dump":
        net = proj.parts
        if args.ref:
            net = {r: net.get(r) for r in args.ref}
        json.dump(net, out, ensure_ascii=False, indent=1)
        out.write("\n")
        return 0

    pinout = _read_json(args.pinout)
    fresh = extract(proj.parts, pinout)
    if args.cmd == "extract":
        text = dumps(fresh)
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
        else:
            out.write(text)
        return 0

    report = []
    report += [("schéma ↔ PCB", e) for e in compare_sch_pcb(proj)]
    report += [("projet ↔ pinout.json", e) for e in compare_pinout(stable_nets(proj.parts), pinout)]
    try:
        saved = _read_json(args.netlist)
    except FileNotFoundError:
        saved = {}
        report.append(("extrait", "%s absent (lancer extract)" % Path(args.netlist).name))
    if saved:
        report += [("extrait", e) for e in compare_extract(saved, fresh)]
    for where, e in report:
        out.write("ÉCART [%s] %s\n" % (where, e))
    n_sch = sum(len(p) for p in proj.sch.values())
    n_pads = sum(len(e.get("pads", {})) for e in proj.parts.values())
    out.write("%s : %d repères, %d broches au schéma, %d pastilles au PCB, %d repères dans l'extrait ; %s\n"
              % (Path(args.epro2).name, len(proj.parts), n_sch, n_pads, len(fresh["refs"]),
                 "%d écart(s)" % len(report) if report else "aucun écart"))
    return 1 if report else 0


if __name__ == "__main__":
    sys.exit(main())
