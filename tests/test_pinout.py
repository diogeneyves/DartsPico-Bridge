# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Cohérence de hardware/pinout.json avec le § 3 de docs/SPECIFICATION.md.

Les valeurs attendues ci-dessous sont recopiées de la spécification.
pinout.json est la source de vérité du matériel : si un test échoue,
c'est la spécification ou le code qui doit être revu, pas ce fichier
(sauf décision de l'auteur).
"""

import json
import unittest

import _paths  # noqa: F401  (shared/ pour pico_proto)
from pathlib import Path

PINOUT = Path(__file__).resolve().parent.parent / "hardware" / "pinout.json"

# § 3.1 : GPIO -> (broche physique, net)
EXPECTED_PICO = {
    **{f"GP{i}": (p, f"ROW{i}") for i, p in enumerate([1, 2, 4, 5, 6, 7, 9, 10, 11, 12, 14, 15])},
    # Adresse de colonne du 74HC154 (U6), ordre inversé : A0 = GP15 (v0.3.6). GP16 : voyant de vie (v0.3.14).
    # Réseaux nommés GP12 à GP15 dans EasyEDA.
    "GP12": (16, "GP12"),
    "GP13": (17, "GP13"),
    "GP14": (19, "GP14"),
    "GP15": (20, "GP15"),
    # Voyant de vie (v0.3.14) : réseau LED_VIE dans EasyEDA.
    "GP16": (21, "LED_VIE"),
    "GP17": (22, "LED1_IN"),
    "GP18": (24, "BTN15"),
    "GP19": (25, "MCP_INT"),
    "GP20": (26, "SDA"),
    "GP21": (27, "SCL"),
    "GP22": (29, "LED2_IN"),
    "GP26": (31, "PIEZO_ADC"),
    "GP27": (32, "LED3_IN"),
    "GP28": (34, "MCP_RST"),
}

# § 3.2 : puce -> (adresse, A0/A1/A2)
EXPECTED_MCP = {
    "MCP1_U3": ("0x20", "GND/GND/GND"),
    "MCP2_U4": ("0x21", "3V3/GND/GND"),
    "MCP3_U5": ("0x22", "GND/3V3/GND"),
}

# § 3.3 : bouton -> (fonction, puce, broche, connecteur) ; netlist du 7 novembre 2026
EXPECTED_BUTTONS = [
    ("BTN1", "UP", "GPA0", "J1.1"),
    ("BTN2", "DOWN", "GPA1", "J1.2"),
    ("BTN3", "LEFT", "GPA2", "J2.1"),
    ("BTN4", "RIGHT", "GPA3", "J2.2"),
    ("BTN5", "PLUS", "GPA4", "J3.1"),
    ("BTN6", "MINUS", "GPA5", "J3.2"),
    ("BTN7", "VALIDATE", "GPA6", "J4.2"),
    ("BTN8", "CANCEL", "GPB6", "J5.1"),
    ("BTN9", "NEXTPLAYER", "GPB5", "J5.2"),
    ("BTN10", "BACK", "GPB4", "J6.1"),
    ("BTN11", "GAMEBUTTON", "GPB3", "J6.2"),
    ("BTN12", "VOLUME_UP", "GPB2", "J7.1"),
    ("BTN13", "VOLUME_DOWN", "GPB1", "J8.1"),
    ("BTN14", "VOLUME_MUTE", "GPB0", "J8.2"),
    ("BTN15", "CPTPLAYER", "GP18", "J9.1"),
]

# § 3.4 : correspondance proposée V1..V10 <-> LIGHT_*
EXPECTED_LIGHT_MAPPING = {
    "LIGHT_NEXTPLAYER": "V1",
    "LIGHT_BACK": "V2",
    "LIGHT_NAVIGATE": "V3",
    "LIGHT_VALIDATE": "V4",
    "LIGHT_LASER": "V5",
    "LIGHT_FLASH": "V6",
    "LIGHT_PLAYERS": "V7",
    "LIGHT_CELEBRATION": "V8",
    "LIGHT_CELEBRATION2": "V9",
    "LIGHT_LIGHT": "V10",
}

# § 3.5 : canal -> GPIO, connecteur
EXPECTED_LEDS = [(0, "GP17", "J31"), (1, "GP22", "J32"), (2, "GP27", "J33")]


def load():
    with PINOUT.open(encoding="utf-8") as f:
        return json.load(f)


class PinoutVsSpecification(unittest.TestCase):
    def setUp(self):
        self.p = load()

    def test_pico_pins(self):
        pins = self.p["pico"]["pins"]
        self.assertEqual(set(pins), set(EXPECTED_PICO))
        for gpio, (phys, net) in EXPECTED_PICO.items():
            with self.subTest(gpio=gpio):
                self.assertEqual(pins[gpio]["physical_pin"], phys)
                self.assertEqual(pins[gpio]["net"], net)

    def test_i2c(self):
        i2c = self.p["i2c"]
        self.assertEqual((i2c["bus"], i2c["sda"], i2c["scl"]), ("I2C0", "GP20", "GP21"))
        self.assertEqual(i2c["mcp_reset"]["gpio"], "GP28")
        self.assertEqual(i2c["mcp_int"]["gpio"], "GP19")

    def test_mcp_addresses(self):
        mcp = self.p["mcp23017"]
        self.assertEqual(set(mcp), set(EXPECTED_MCP))
        for chip, (addr, straps) in EXPECTED_MCP.items():
            with self.subTest(chip=chip):
                self.assertEqual(mcp[chip]["address"], addr)
                self.assertEqual(mcp[chip]["A0A1A2"], straps)
                # Adresse = 0x20 + A0 + 2*A1 + 4*A2
                bits = [s == "3V3" for s in straps.split("/")]
                self.assertEqual(int(addr, 16), 0x20 + bits[0] + 2 * bits[1] + 4 * bits[2])

    def test_buttons(self):
        got = [(b["id"], b["function"], b["source"]["pin"], b["connector"])
               for b in self.p["buttons"]]
        self.assertEqual(got, EXPECTED_BUTTONS)

    def test_gpa7_gpb7_never_inputs(self):
        # Erratum Microchip : GPA7 et GPB7 jamais en entrée.
        for b in self.p["buttons"]:
            self.assertNotIn(b["source"]["pin"], ("GPA7", "GPB7"), b["id"])
        self.assertEqual(sorted(self.p["mcp23017"]["MCP1_U3"]["unused"]), ["GPA7", "GPB7"])

    def test_toys(self):
        toys = self.p["toys"]
        self.assertEqual(len(toys), 8)
        for i, t in enumerate(toys):
            self.assertEqual((t["id"], t["chip"], t["pin"]), (f"TOY{i + 1}", "MCP2_U4", f"GPA{i}"))

    def test_lights(self):
        lights = self.p["lights"]
        self.assertEqual(len(lights), 12)
        expected_pins = [f"GPA{i}" for i in range(8)] + [f"GPB{i}" for i in range(4)]
        for i, (v, pin) in enumerate(zip(lights, expected_pins)):
            self.assertEqual((v["id"], v["chip"], v["pin"]), (f"V{i + 1}", "MCP3_U5", pin))

    def test_light_mapping(self):
        m = {k: v for k, v in self.p["lights_default_mapping_raspydarts"].items()
             if not k.startswith("_")}
        self.assertEqual(m, EXPECTED_LIGHT_MAPPING)

    def test_led_outputs(self):
        got = [(o["channel"], o["gpio"], o["connector"].split()[0])
               for o in self.p["led_outputs"]]
        self.assertEqual(got, EXPECTED_LEDS)

    def test_button_connectors(self):
        # § 3.3 : borniers J1 à J9, broche 1 / broche 2 ; cohérents avec buttons[].connector.
        bc = {k: v for k, v in self.p["button_connectors"].items() if not k.startswith("_")}
        self.assertEqual(sorted(bc, key=lambda j: int(j[1:])), [f"J{i}" for i in range(1, 10)])
        pins = {f"{j}.{n}": net for j, v in bc.items() for n, net in v.items()}
        for b in self.p["buttons"]:
            self.assertEqual(pins[b["connector"]], b["id"])
        others = sorted(net for net in pins.values() if not net.startswith("BTN"))
        self.assertEqual(others, ["GND", "GND", "GND"])                        # J4.1, J7.2, J9.2

    def test_toy_and_light_outputs(self):
        # § 3.4 : T1-T4 J10-J13 + JP1-JP4 ; T5-T8 J14-J17 en 12 V fixe ; V1-V12 J18-J29 + JP5-JP16.
        for i, t in enumerate(self.p["toys"]):
            with self.subTest(toy=t["id"]):
                self.assertEqual(t["connector"], f"J{10 + i}")
                if i < 4:
                    j = t["supply_jumper"]
                    self.assertEqual((j["ref"], j["pin1"], j["pin3"]), (f"JP{1 + i}", "+5V_TOY", "+12V_TOY"))
                    self.assertIn("detruite", t["warning"])
                else:
                    self.assertNotIn("supply_jumper", t)
                    self.assertTrue(t["supply"].startswith("+12V_TOY"))
                    self.assertIn("AON6324", t["mosfet"])
        for i, v in enumerate(self.p["lights"]):
            with self.subTest(light=v["id"]):
                self.assertEqual(v["connector"], f"J{18 + i}")
                j = v["supply_jumper"]
                self.assertEqual((j["ref"], j["pin1"], j["pin3"]), (f"JP{5 + i}", "+5V_LEDS", "+12V_TOY"))
                self.assertIn("detruite", v["warning"])
        self.assertEqual(self.p["shock"]["jumper"]["ref"], "JP17")

    def test_board_refs_unique(self):
        # Chaque bornier et chaque cavalier ne sert qu'une fois.
        conns = [b["connector"].split(".")[0] for b in self.p["buttons"]]
        conns = list(dict.fromkeys(conns))
        conns += [t["connector"] for t in self.p["toys"]] + [v["connector"] for v in self.p["lights"]]
        conns += [o["connector"].split()[0] for o in self.p["led_outputs"]]
        self.assertEqual(len(conns), len(set(conns)))
        jps = [x["supply_jumper"]["ref"] for x in self.p["toys"] + self.p["lights"] + self.p["led_outputs"]
               if "supply_jumper" in x] + [self.p["shock"]["jumper"]["ref"]]
        self.assertEqual(sorted(jps, key=lambda r: int(r[2:])), [f"JP{i}" for i in range(1, 20)])

    def test_strip_supply_jumpers(self):
        # § 3.5 : JP18 (J32) et JP19 (J33), broche 1 = +5V_LEDT, broche 3 = +12V_TOY.
        leds = self.p["led_outputs"]
        self.assertNotIn("supply_jumper", leds[0])                             # J31 toujours en 5 V
        for ch, jp in ((1, "JP18"), (2, "JP19")):
            with self.subTest(channel=ch):
                j = leds[ch]["supply_jumper"]
                self.assertEqual((j["ref"], j["pin1"], j["pin3"]), (jp, "+5V_LEDT", "+12V_TOY"))
                self.assertIn(jp, leds[ch]["connector"])
                self.assertIn("detruit", leds[ch]["warning"])

    def test_power_fuses(self):
        # § 3.7 : fusible général U2, F1 à F5 (netlist du 7 novembre 2026).
        pw = self.p["power"]
        self.assertIn("U2", pw["input"])
        self.assertIn("P3", pw["input"])
        got = {rail: txt.split()[0] for rail, txt in pw["branches"].items()}
        self.assertEqual(got, {"+5V_LOG": "F1", "+5V_LEDT": "F2", "+5V_LEDS": "F3",
                               "+5V_TOY": "F4", "+12V_TOY": "F5"})

    def test_shock_and_matrix(self):
        self.assertEqual(self.p["shock"]["gpio"], "GP26")
        m = self.p["matrix"]
        self.assertEqual((m["rows"]["count"], m["rows"]["gpio"]), (12, "GP0..GP11"))
        self.assertEqual(m["columns"]["count"], 7)
        u6 = m["columns"]["driver"]
        self.assertEqual((u6["ref"], u6["part"]), ("U6", "74HC154"))
        self.assertEqual(u6["address"], {"A0": "GP15", "A1": "GP14", "A2": "GP13", "A3": "GP12"})
        self.assertTrue(m["logic"].startswith("active a 0"))
        self.assertEqual(m["settle_us"], 50)
        self.assertEqual(self.p["pico"]["free_gpio"], [])                     # GP16 utilisé (v0.3.14)
        self.assertEqual(self.p["pico"]["ref"], "U1")

    def test_heartbeat_gp16(self):
        # § 3.1.2 : D58 (LED rouge) + R109 (1 kΩ) entre +3V3 et GP16, active au niveau bas.
        hb = self.p["heartbeat"]
        self.assertEqual((hb["gpio"], hb["physical_pin"], hb["active"], hb["net"]),
                         ("GP16", 21, "bas", "LED_VIE"))
        self.assertEqual((hb["led"]["ref"], hb["led"]["anode"]), ("D58", "+3V3"))
        self.assertEqual((hb["resistor"]["ref"], hb["resistor"]["value"]), ("R109", "1k"))

    def test_erratum_e9_values(self):
        # § 3.5 et § 3.6 : tirages 4,7 kΩ R95, R98, R101 ; diviseur R102 5,1 kΩ + R97 8,2 kΩ.
        got = [(o["gpio"], o["pulldown"].split()[:2]) for o in self.p["led_outputs"]]
        self.assertEqual(got, [("GP17", ["R95", "4.7k"]), ("GP22", ["R98", "4.7k"]),
                               ("GP27", ["R101", "4.7k"])])
        self.assertIn("R102 5.1k + R97 8.2k", self.p["shock"]["path"])
        self.assertEqual(self.p["shock"]["comparator"]["gp26_high_v"],
                         {"carte": 2.65, "module_externe_5V": 3.08})

    def test_target_connector_p2(self):
        # § 3.1, complément 65 : P2 (2 x 10) relevé sur la nappe de l'ED110, vers un PCB de
        # répartition ; ROW0_IN..ROW9_IN sur 1, 3 .. 19, COL00..COL06 sur 4, 6 .. 16,
        # LED1_OUT sur 20, broches 2 et 18 non connectées.
        p2 = self.p["matrix"]["connector"]
        self.assertEqual(p2["ref"], "P2")
        self.assertEqual(p2["rows"], {f"ROW{i}_IN": 2 * i + 1 for i in range(10)})
        self.assertEqual(p2["columns"], {f"COL0{i}": 2 * i + 4 for i in range(7)})
        self.assertEqual(p2["other"], {"LED1_OUT": 20})
        self.assertEqual(p2["not_connected"], [2, 18])
        self.assertIn("repartition", p2["via"])
        used = (list(p2["rows"].values()) + list(p2["columns"].values())
                + list(p2["other"].values()) + p2["not_connected"])
        self.assertEqual(sorted(used), list(range(1, 21)))                    # chaque broche une fois
        import pico_proto
        self.assertEqual(len(p2["columns"]), pico_proto.COLS)
        # Carte limitée à 7 x 10 (ED110) ; le logiciel garde 12 rangées.
        self.assertEqual(pico_proto.ROWS, 12)
        self.assertLess(len(p2["rows"]), pico_proto.ROWS)

    def test_led1_out_on_p2_and_j31(self):
        # Complément 65 : LED1_OUT sort sur J31.2 et sur P2.20.
        led1 = self.p["led_outputs"][0]
        self.assertIn("2:LED1_OUT", led1["connector"])
        self.assertIn("P2.20", led1["connector"])

    def test_rows_10_11_pullup_only(self):
        # Complément 65 : GP10 et GP11 gardent ROW10 et ROW11, tirage 10 kOhm vers +3V3
        # seulement (0603WAF1002T5E, C25804), pas de résistance série ni de condensateur.
        rows = self.p["matrix"]["rows"]
        self.assertEqual((rows["count"], rows["on_connector"]), (12, 10))
        for gp, net in (("GP10", "ROW10"), ("GP11", "ROW11")):
            with self.subTest(gpio=gp):
                self.assertEqual(self.p["pico"]["pins"][gp]["net"], net)
                self.assertEqual(rows["pullup_only"][net],
                                 {"gpio": gp, "pullup": "10k vers +3V3", "part": "0603WAF1002T5E",
                                  "lcsc": "C25804"})

    def test_targets_7x10_only(self):
        self.assertTrue(self.p["matrix"]["targets"].startswith("7x10 (ED110) seulement"))

    def test_no_gpio_used_twice(self):
        nets = [v["net"] for v in self.p["pico"]["pins"].values()]
        self.assertEqual(len(nets), len(set(nets)))


class PinoutInternalConsistency(unittest.TestCase):
    """Cohérence interne de pinout.json (références entre sections)."""

    def test_button_chip_refers_to_known_chip(self):
        p = load()
        known = set(p["mcp23017"]) | {"PICO"}
        for b in p["buttons"]:
            self.assertIn(b["source"]["chip"], known, b["id"])


if __name__ == "__main__":
    unittest.main()
