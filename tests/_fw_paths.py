# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Rend importables le firmware de la carte Pico et les modules MicroPython simulés."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FW = ROOT / "firmware" / "pico_target"
for p in (str(Path(__file__).resolve().parent / "mpy_fakes"), str(FW)):
    if p not in sys.path:
        sys.path.insert(0, p)
