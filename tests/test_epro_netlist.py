# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests de tools/epro_netlist.py (spécification, complément 66).

- lecteur du journal EasyEDA Pro sur de petits journaux synthétiques
  (schéma et PCB), dont des cas qui doivent échouer ;
- comparaison de hardware/netlist.json (extrait versionné) à
  hardware/pinout.json : c'est le contrôle que la CI peut faire sans le .epro2.
"""

import copy
import io
import json
import unittest
import zipfile
from pathlib import Path

import _paths  # noqa: F401
import epro_netlist

HW = Path(__file__).resolve().parent.parent / "hardware"


def rec(head, body=None):
    return json.dumps(head) + "||" + (json.dumps(body) if body is not None else "") + "|"


def attr(parent, key, value):
    return {"parentId": parent, "key": key, "value": value}


LINES = [
    rec({"type": "DOCHEAD"}, {"docType": "DEVICE", "uuid": "dev1"}),
    rec({"type": "META"}, {"attributes": {"Value": "1kΩ", "Manufacturer Part": "0603WAF1001T5E"}}),
    rec({"type": "DOCHEAD"}, {"docType": "SCH_PAGE", "uuid": "s"}),
    rec({"type": "ATTR"}, attr("c1", "Designator", "R109")),
    rec({"type": "ATTR"}, attr("c1", "Symbol", "sym")),
    rec({"type": "ATTR"}, attr("c1", "Device", "dev1")),
    rec({"type": "ATTR"}, attr("c2", "Designator", "R97")),
    rec({"type": "ATTR"}, attr("c2", "Symbol", "sym")),
    rec({"type": "ATTR"}, attr("c2", "Device", "dev1")),
    rec({"type": "ATTR"}, attr("c2", "Value", "8.2kΩ")),          # remplace la bibliothèque
    rec({"type": "ATTR"}, attr("n1", "NET", "GND")),               # symbole de réseau : ignoré
    rec({"type": "DOCHEAD"}, {"docType": "PCB", "uuid": "p"}),
    rec({"type": "COMPONENT", "id": "p1"}, {"x": 0}),
    rec({"type": "ATTR"}, attr("p1", "Designator", "R109")),
    rec({"type": "PAD_NET", "id": json.dumps(["PAD_NET", "p1", "2", "e2"])}, {"padNet": "$1N1647"}),
    rec({"type": "PAD_NET", "id": json.dumps(["PAD_NET", "p1", "1", "e1"])}, {"padNet": "$1N1649"}),
    rec({"type": "PAD_NET", "id": json.dumps(["PAD_NET", "zz", "1", "e"])}),   # corps vide
]


class EproNetlist(unittest.TestCase):
    def test_parse(self):
        net = epro_netlist.parse(LINES)
        self.assertEqual(list(net), ["R97", "R109"])               # tri par numéro
        self.assertEqual(net["R109"], {"Value": "1kΩ", "Manufacturer Part": "0603WAF1001T5E",
                                       "pads": {"1": "$1N1649", "2": "$1N1647"}})
        self.assertEqual(net["R97"]["Value"], "8.2kΩ")
        self.assertNotIn("pads", net["R97"])

    def test_load_epro2(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Darts.epru", "\n".join(LINES) + "\n")
            z.writestr("project2.json", "{}")
        buf.seek(0)
        self.assertIn("R109", epro_netlist.load_epro2(buf))


# --- Schéma ↔ PCB --------------------------------------------------------------
# Petit projet : symbole à deux broches (x = -20 et x = +20), R1 à (0, 0) sans
# rotation, R2 à (100, 0) tourné d'un quart de tour (broches en (100, 20) et
# (100, -20)), R3 à (200, 0) dont la broche 2 est marquée « non connectée ».
# Fils : VCC sur R1.1 ; fil anonyme R1.2 -> R2.2 ; GND sur R2.1 et R3.1.

def project(pcb_pads=None, wires=None, rotation_r2=90, mirror_r2=False):
    pcb_pads = pcb_pads or {
        "R1": {"1": "VCC", "2": "$1N5"},
        "R2": {"1": "GND", "2": "$1N5"},
        "R3": {"1": "GND", "2": ""},
    }
    wires = wires if wires is not None else [
        ("w1", "VCC", [(-20, 0, -40, 0)]),
        ("w2", "", [(20, 0, 100, 0), (100, 0, 100, -20)]),
        ("w3", "GND", [(100, 20, 100, 40), (100, 40, 180, 40), (180, 40, 180, 0)]),
    ]
    out = [
        rec({"type": "DOCHEAD"}, {"docType": "SYMBOL", "uuid": "sym"}),
        rec({"type": "PIN", "id": "p1"}, {"partId": "sym.1", "x": -20, "y": 0, "rotation": 0}),
        rec({"type": "ATTR", "id": "a1"}, attr("p1", "Pin Number", "1")),
        rec({"type": "PIN", "id": "p2"}, {"partId": "sym.1", "x": 20, "y": 0, "rotation": 180}),
        rec({"type": "ATTR", "id": "a2"}, attr("p2", "Pin Number", "2")),
        rec({"type": "DOCHEAD"}, {"docType": "DEVICE", "uuid": "dev"}),
        rec({"type": "META"}, {"attributes": {"Value": "10kΩ"}}),
        rec({"type": "DOCHEAD"}, {"docType": "SCH_PAGE", "uuid": "s"}),
    ]
    for cid, ref, x, rot, mir in (("c1", "R1", 0, 0, False), ("c2", "R2", 100, rotation_r2, mirror_r2),
                                  ("c3", "R3", 200, 0, False)):
        out.append(rec({"type": "COMPONENT", "id": cid},
                       {"partId": "sym.1", "x": x, "y": 0, "rotation": rot, "isMirror": mir}))
        for k, v in (("Designator", ref), ("Symbol", "sym"), ("Device", "dev")):
            out.append(rec({"type": "ATTR"}, attr(cid, k, v)))
    out.append(rec({"type": "ATTR"}, attr("c3-p2", "NO_CONNECT", "yes")))
    for wid, net, segs in wires:
        out.append(rec({"type": "WIRE", "id": wid}, {"zIndex": 1}))
        out.append(rec({"type": "ATTR"}, attr(wid, "NET", net)))
        for i, (x1, y1, x2, y2) in enumerate(segs):
            out.append(rec({"type": "LINE", "id": "%s-%d" % (wid, i)},
                           {"startX": x1, "startY": y1, "endX": x2, "endY": y2, "lineGroup": wid}))
    out.append(rec({"type": "DOCHEAD"}, {"docType": "PCB", "uuid": "p"}))
    for i, (ref, pads) in enumerate(pcb_pads.items()):
        cid = "pc%d" % i
        out.append(rec({"type": "COMPONENT", "id": cid}, {"x": 0}))
        out.append(rec({"type": "ATTR"}, attr(cid, "Designator", ref)))
        for pad, net in pads.items():
            out.append(rec({"type": "PAD_NET", "id": json.dumps(["PAD_NET", cid, pad, "e"])},
                           {"padNet": net}))
    return out


def sch_pcb_errors(lines):
    proj = epro_netlist.parse_project(lines)
    return epro_netlist.compare_sch_pcb(proj)


class SchematicVsPcb(unittest.TestCase):
    def test_consistent_project(self):
        proj = epro_netlist.parse_project(project())
        self.assertEqual(proj.errors, [])
        sch = proj.sch
        self.assertEqual(sch["R1"]["1"], ("net", "VCC"))
        self.assertEqual(sch["R2"]["1"], ("net", "GND"))              # quart de tour
        self.assertEqual(sch["R3"]["1"], ("net", "GND"))
        self.assertEqual(sch["R1"]["2"][0], "anon")
        self.assertEqual(sch["R1"]["2"], sch["R2"]["2"])              # même réseau anonyme
        self.assertEqual(sch["R3"]["2"][0], "nc")
        self.assertEqual(epro_netlist.compare_sch_pcb(proj), [])

    def test_named_net_differs(self):
        errs = sch_pcb_errors(project(pcb_pads={
            "R1": {"1": "VCC", "2": "$1N5"}, "R2": {"1": "+3V3", "2": "$1N5"}, "R3": {"1": "GND", "2": ""}}))
        self.assertEqual(len(errs), 1)
        self.assertIn("R2.1", errs[0])
        self.assertIn("GND", errs[0])
        self.assertIn("+3V3", errs[0])

    def test_missing_link_on_pcb(self):
        # Liaison du schéma oubliée au PCB : R2.2 n'est plus sur le réseau de R1.2.
        errs = sch_pcb_errors(project(pcb_pads={
            "R1": {"1": "VCC", "2": "$1N5"}, "R2": {"1": "GND", "2": "$1N6"}, "R3": {"1": "GND", "2": ""}}))
        self.assertTrue(errs)
        self.assertTrue(any("R2.2" in e or "R1.2" in e for e in errs), errs)

    def test_pcb_link_absent_from_schematic(self):
        # Le PCB relie R3.2 (non connectée au schéma) au réseau anonyme de R1.2.
        errs = sch_pcb_errors(project(pcb_pads={
            "R1": {"1": "VCC", "2": "$1N5"}, "R2": {"1": "GND", "2": "$1N5"}, "R3": {"1": "GND", "2": "$1N5"}}))
        self.assertTrue(any("R3.2" in e for e in errs), errs)

    def test_ref_only_on_one_side(self):
        errs = sch_pcb_errors(project(pcb_pads={
            "R1": {"1": "VCC", "2": "$1N5"}, "R2": {"1": "GND", "2": "$1N5"}}))
        self.assertTrue(any("R3" in e and "PCB" in e for e in errs), errs)

    def test_wire_not_reaching_pin(self):
        # Le fil GND s'arrête avant R3.1 : broche isolée au schéma, sur GND au PCB.
        wires = [("w1", "VCC", [(-20, 0, -40, 0)]),
                 ("w2", "", [(20, 0, 100, 0), (100, 0, 100, -20)]),
                 ("w3", "GND", [(100, 20, 100, 40), (100, 40, 180, 40), (180, 40, 185, 0)])]
        errs = sch_pcb_errors(project(wires=wires))
        self.assertTrue(any("R3.1" in e for e in errs), errs)

    def test_unsupported_geometry_stops(self):
        for kw in ({"mirror_r2": True}, {"rotation_r2": 45}):
            with self.subTest(**kw):
                proj = epro_netlist.parse_project(project(**kw))
                self.assertTrue(proj.errors)
                self.assertIn("R2", proj.errors[0])

    def test_two_names_on_connected_wires_stops(self):
        wires = [("w1", "VCC", [(-20, 0, -40, 0)]),
                 ("w4", "VDD", [(-40, 0, -40, 20)]),
                 ("w2", "", [(20, 0, 100, 0), (100, 0, 100, -20)]),
                 ("w3", "GND", [(100, 20, 100, 40), (100, 40, 180, 40), (180, 40, 180, 0)])]
        proj = epro_netlist.parse_project(project(wires=wires))
        self.assertTrue(any("VCC" in e and "VDD" in e for e in proj.errors), proj.errors)

    def test_anonymous_nets_renamed_for_stable_diff(self):
        proj = epro_netlist.parse_project(project())
        parts = epro_netlist.stable_nets(proj.parts)
        self.assertEqual(parts["R1"]["pads"]["2"], "~R1.2")
        self.assertEqual(parts["R2"]["pads"]["2"], "~R1.2")
        self.assertEqual(parts["R1"]["pads"]["1"], "VCC")


# --- netlist.json ↔ pinout.json ------------------------------------------------

class NetlistVsPinout(unittest.TestCase):
    """Contrôle (2) du complément 66, sur l'extrait versionné : tourne en CI sans le .epro2."""

    @classmethod
    def setUpClass(cls):
        cls.pinout = json.loads((HW / "pinout.json").read_text(encoding="utf-8"))
        cls.text = (HW / "netlist.json").read_text(encoding="utf-8")
        cls.netlist = json.loads(cls.text)

    def refs(self):
        return copy.deepcopy(self.netlist["refs"])

    def test_extract_matches_pinout(self):
        self.assertEqual(epro_netlist.compare_pinout(self.netlist["refs"], self.pinout), [])

    def test_extract_format_is_stable(self):
        # Trié, une seule mise en forme possible : un diff ne montre que les vrais changements.
        self.assertEqual(self.text, epro_netlist.dumps(self.netlist))
        self.assertEqual(list(self.netlist["refs"]),
                         sorted(self.netlist["refs"], key=epro_netlist._ref_key))
        self.assertNotIn("$1N", self.text)
        self.assertNotIn("/Users/", self.text)

    def test_extract_limited_to_cited_refs(self):
        cited = epro_netlist.cited_refs(self.pinout)
        self.assertEqual(set(self.netlist["refs"]), cited)
        for ref in ("U1", "P2", "R22", "R23", "R24", "R109", "D58", "J1", "JP18", "F5"):
            self.assertIn(ref, cited)
        for word in ("D0", "GPA0", "GP16", "Y0", "A0", "E1", "ED110", "ROW10", "C25804"):
            self.assertNotIn(word, cited)

    def test_r22_r24_in_pinout(self):
        res = self.pinout["matrix"]["rows"]["pullup_resistors"]
        self.assertEqual(res["R22"]["pads"], {"1": "+3V3", "2": "ROW9"})
        self.assertEqual(res["R23"]["pads"], {"1": "ROW10", "2": "+3V3"})
        self.assertEqual(res["R24"]["pads"], {"1": "ROW11", "2": "+3V3"})
        for ref in ("R22", "R23", "R24"):
            self.assertEqual((res[ref]["value"], res[ref]["part"], res[ref]["lcsc"]),
                             ("10k", "0603WAF1002T5E", "C25804"))

    def test_gpio_on_wrong_net_fails(self):
        refs = self.refs()
        refs["U1"]["pads"]["21"] = "LED1_IN"                         # GP16
        errs = epro_netlist.compare_pinout(refs, self.pinout)
        self.assertTrue(any("U1.21" in e and "GP16" in e for e in errs), errs)

    def test_p2_pins_swapped_fails(self):
        refs = self.refs()
        pads = refs["P2"]["pads"]
        pads["1"], pads["3"] = pads["3"], pads["1"]
        errs = epro_netlist.compare_pinout(refs, self.pinout)
        self.assertTrue(any("P2.1" in e for e in errs), errs)
        self.assertTrue(any("P2.3" in e for e in errs), errs)

    def test_missing_ref_fails(self):
        refs = self.refs()
        del refs["R23"]
        errs = epro_netlist.compare_pinout(refs, self.pinout)
        self.assertTrue(any("R23" in e and "absent" in e for e in errs), errs)

    def test_wrong_value_fails(self):
        refs = self.refs()
        refs["R96"]["Value"] = "470Ω"
        errs = epro_netlist.compare_pinout(refs, self.pinout)
        self.assertTrue(any("R96" in e and "330" in e for e in errs), errs)

    def test_wrong_fuse_fails(self):
        refs = self.refs()
        refs["F1"]["Manufacturer Part"] = "0501015.WR1"
        errs = epro_netlist.compare_pinout(refs, self.pinout)
        self.assertTrue(any("F1" in e for e in errs), errs)

    def test_jumper_swapped_fails(self):
        refs = self.refs()
        pads = refs["JP18"]["pads"]
        pads["1"], pads["3"] = pads["3"], pads["1"]
        errs = epro_netlist.compare_pinout(refs, self.pinout)
        self.assertTrue(any("JP18.1" in e for e in errs), errs)

    def test_check_fails_on_stale_extract(self):
        stale = copy.deepcopy(self.netlist)
        stale["refs"]["R22"]["Value"] = "1kΩ"
        errs = epro_netlist.compare_extract(stale, self.netlist)
        self.assertTrue(any("R22" in e for e in errs), errs)
        self.assertEqual(epro_netlist.compare_extract(self.netlist, self.netlist), [])


class CommandLine(unittest.TestCase):
    def test_check_exit_codes(self):
        good = project()
        bad = project(pcb_pads={"R1": {"1": "VCC", "2": "$1N5"}, "R2": {"1": "+3V3", "2": "$1N5"},
                                "R3": {"1": "GND", "2": ""}})
        pinout = {"pico": {"ref": "R1", "pins": {}}, "note": "R2 et R3"}
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "pinout.json").write_text(json.dumps(pinout), encoding="utf-8")
            for name, lines in (("good", good), ("bad", bad)):
                with zipfile.ZipFile(d / (name + ".epro2"), "w") as z:
                    z.writestr("x.epru", "\n".join(lines) + "\n")
            out = io.StringIO()
            rc = epro_netlist.main(["extract", str(d / "good.epro2"), "--pinout", str(d / "pinout.json"),
                                    "-o", str(d / "netlist.json")], out=out)
            self.assertEqual(rc, 0)
            args = ["--pinout", str(d / "pinout.json"), "--netlist", str(d / "netlist.json")]
            self.assertEqual(epro_netlist.main(["check", str(d / "good.epro2")] + args, out=out), 0)
            out = io.StringIO()
            self.assertEqual(epro_netlist.main(["check", str(d / "bad.epro2")] + args, out=out), 1)
            self.assertIn("R2.1", out.getvalue())


if __name__ == "__main__":
    unittest.main()
