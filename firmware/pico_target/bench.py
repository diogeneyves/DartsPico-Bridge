# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Lanceur de banc (§ 5.1) : `mpremote run bench.py`, jamais copié sur le Pico.

Ctrl-C actif, bench_row_pullup 1, hw_watchdog 0 au démarrage, journal # HB.
Le script de banc envoie `SET hw_watchdog 1` juste après HELLO (§ 5.4).
"""

import app

app.run(bench=True)
