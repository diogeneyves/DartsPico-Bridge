# firmware/pico_target

Firmware MicroPython de la carte Pico : balayage de la cible, boutons, Toys, voyants, LED, détecteur de choc (spécification § 7).
Jalon **M3**. Fichiers prévus : `main.py`, `board.py`, `scanner.py`, `mcp23017.py`, `leds.py`, `shock.py`, `proto.py`, `config.py`.

## Banc

`app.run()` est le point d'entrée (broches, cœur 1, boucle du cœur 0). `main.py` (§ 5.1) : issue GP18 sur place, fenêtre Ctrl-C de 2 s à distance, puis `app.run()` ; **copié sur le Pico seulement avec l'accord du propriétaire**. Au banc : copier tous les modules **sauf** `bench.py` et `main.py` (jamais de `boot.py`), puis `mpremote run --no-follow bench.py`. Mode banc (spécification § 5.1) : Ctrl-C actif, `bench_row_pullup` 1, `hw_watchdog` 0 au démarrage, journal `# HB <niveau> <tour_max_µs>` ; le script de banc envoie `SET hw_watchdog 1` juste après `HELLO`.
