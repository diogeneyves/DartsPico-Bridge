# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Firmware de la carte Pico : board.py concorde avec hardware/pinout.json, proto.py avec shared/."""

import json
import unittest

import _fw_paths
import board

PINOUT = json.loads((_fw_paths.ROOT / "hardware" / "pinout.json").read_text(encoding="utf-8"))


def gp(name):
    assert name.startswith("GP")
    return int(name[2:])


def bit(port_pin):
    """'GPA3' -> 3, 'GPB2' -> 10."""
    return (0 if port_pin[2] == "A" else 8) + int(port_pin[3])


class Board(unittest.TestCase):
    def test_rows_and_columns(self):
        pins = PINOUT["pico"]["pins"]
        rows = sorted((int(v["net"][3:]), gp(k)) for k, v in pins.items() if v["net"].startswith("ROW"))
        self.assertEqual(tuple(g for _, g in rows), board.ROW_PINS)
        addr = PINOUT["matrix"]["columns"]["driver"]["address"]
        self.assertEqual(tuple(gp(addr["A%d" % i]) for i in range(4)), board.COL_ADDR_PINS)
        self.assertEqual(PINOUT["matrix"]["columns"]["count"], board.COLS)
        self.assertEqual(PINOUT["matrix"]["settle_us"], board.SETTLE_US)
        self.assertGreaterEqual(board.COL_IDLE, 7)
        self.assertEqual(board.ROW_MASK, sum(1 << g for g in board.ROW_PINS))

    def test_single_pins(self):
        self.assertEqual(gp(PINOUT["heartbeat"]["gpio"]), board.HEARTBEAT_PIN)
        i2c = PINOUT["i2c"]
        self.assertEqual(gp(i2c["sda"]), board.SDA_PIN)
        self.assertEqual(gp(i2c["scl"]), board.SCL_PIN)
        self.assertEqual("I2C%d" % board.I2C_ID, i2c["bus"])
        self.assertEqual(gp(i2c["mcp_reset"]["gpio"]), board.MCP_RESET_PIN)
        self.assertEqual(gp(i2c["mcp_int"]["gpio"]), board.MCP_INT_PIN)
        self.assertEqual(gp(PINOUT["shock"]["gpio"]), board.SHOCK_PIN)
        leds = sorted((x["channel"], gp(x["gpio"])) for x in PINOUT["led_outputs"])
        self.assertEqual(tuple(g for _, g in leds), board.LED_PINS)

    def test_mcp_addresses(self):
        m = PINOUT["mcp23017"]
        self.assertEqual(int(m["MCP1_U3"]["address"], 16), board.MCP_BUTTONS)
        self.assertEqual(int(m["MCP2_U4"]["address"], 16), board.MCP_TOYS)
        self.assertEqual(int(m["MCP3_U5"]["address"], 16), board.MCP_LIGHTS)

    def test_buttons_toys_lights(self):
        btn = PINOUT["buttons"]
        self.assertEqual(len(btn), 15)
        for i, b in enumerate(btn[:14]):
            self.assertEqual(b["id"], "BTN%d" % (i + 1))
            self.assertEqual(b["source"]["chip"], "MCP1_U3")
            self.assertEqual(bit(b["source"]["pin"]), board.BUTTON_BITS[i])
        self.assertEqual(gp(btn[14]["source"]["pin"]), board.BTN15_PIN)
        for i, t in enumerate(PINOUT["toys"]):
            self.assertEqual((t["chip"], bit(t["pin"])), ("MCP2_U4", board.TOY_BITS[i]))
        for i, t in enumerate(PINOUT["lights"]):
            self.assertEqual((t["chip"], bit(t["pin"])), ("MCP3_U5", board.LIGHT_BITS[i]))

    def test_iodir_never_gpa7_gpb7_input(self):
        for addr, iodir in board.IODIR.items():
            self.assertFalse(iodir & (1 << 7), hex(addr))
            self.assertFalse(iodir & (1 << 15), hex(addr))
        # Seules les broches des boutons sont des entrées.
        self.assertEqual(board.IODIR[board.MCP_BUTTONS], sum(1 << b for b in board.BUTTON_BITS))
        self.assertEqual(board.IODIR[board.MCP_TOYS], 0)
        self.assertEqual(board.IODIR[board.MCP_LIGHTS], 0)


class ProtoCopy(unittest.TestCase):
    def test_proto_is_a_copy_of_shared(self):
        a = (_fw_paths.ROOT / "shared" / "pico_proto.py").read_bytes()
        b = (_fw_paths.FW / "proto.py").read_bytes()
        self.assertEqual(a, b, "firmware/pico_target/proto.py doit être une copie de shared/pico_proto.py")


if __name__ == "__main__":
    unittest.main()
