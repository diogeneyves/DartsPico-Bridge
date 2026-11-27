# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Firmware de la carte Pico : fichier des paramètres (config.py)."""

import json
import os
import tempfile
import unittest

import _fw_paths  # noqa: F401
import config
import proto as P


class Config(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "params.json")

    def tearDown(self):
        self.dir.cleanup()

    def write(self, text):
        with open(self.path, "w") as f:
            f.write(text)

    def test_missing_file_gives_defaults_without_error(self):
        self.assertEqual(config.load(self.path), (P.default_params(), None))

    def test_round_trip(self):
        p = P.default_params()
        p["watchdog_ms"] = 2500
        p["shock_mode"] = "adc"
        config.save(p, self.path)
        self.assertEqual(config.load(self.path), (p, None))
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_partial_file_completed_by_defaults(self):
        self.write(json.dumps({"release_ms": 30}))
        params, err = config.load(self.path)
        self.assertIsNone(err)
        self.assertEqual(params["release_ms"], 30)
        self.assertEqual(params["hold_max_ms"], 400)

    def test_invalid_files_give_defaults_and_cfg_text(self):
        for text in ("{", "[]", '{"nope": 1}', '{"release_ms": 5000}', '{"release_ms": "20"}',
                     '{"release_ms": true}', '{"shock_mode": "ADC"}', '{"release_ms": 1.5}'):
            self.write(text)
            params, err = config.load(self.path)
            self.assertEqual(params, P.default_params(), text)
            self.assertTrue(err, text)
            P.msg_error(P.E_CFG, err)                     # texte valable dans un message E


if __name__ == "__main__":
    unittest.main()
