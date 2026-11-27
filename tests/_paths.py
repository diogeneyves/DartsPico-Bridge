# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Rend importables shared/, tools/, pi/hwd/ et pi/dmd/ depuis les tests (modules à plat, comme sur les cartes)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for sub in ("shared", "tools", "pi/hwd", "pi/dmd"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)
