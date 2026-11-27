# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Mise en page de docs/SPECIFICATION.md (spécification, complément 87).

Aucune ligne ne dépasse 6 000 octets : le journal porte une ligne par complément.
"""

import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SPEC = os.path.join(ROOT, "docs", "SPECIFICATION.md")
MAX_LINE_BYTES = 6000


def long_lines(data, limit=MAX_LINE_BYTES):
    """Numéros (à partir de 1) et tailles en octets des lignes de plus de `limit` octets."""
    return [(no, len(line)) for no, line in enumerate(data.split(b"\n"), 1) if len(line) > limit]


class TestSpecLayout(unittest.TestCase):
    def test_no_line_over_limit(self):
        with open(SPEC, "rb") as f:
            hits = long_lines(f.read())
        self.assertEqual(hits, [], "docs/SPECIFICATION.md : lignes de plus de %d octets "
                         "(numéro, octets) : %s" % (MAX_LINE_BYTES, hits))

    def test_witness(self):
        # La limite est comprise : 6 000 octets passent, 6 001 non ; les octets UTF-8 comptent.
        self.assertEqual(long_lines(b"a" * 6000 + b"\n" + b"b" * 6001), [(2, 6001)])
        self.assertEqual(long_lines("é".encode() * 3001), [(1, 6002)])


if __name__ == "__main__":
    unittest.main()
