# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Lanceur du firmware au démarrage du Pico (spécification § 5.1).

N'importe aucun module du firmware avant app.run(). Dans cet ordre :
0. Voyant de vie : GP16 en sortie au niveau haut (éteint, § 3.1.2). Au
   démarrage, son tirage interne vers le bas ferait luire D58 pendant la
   fenêtre de 2 s. C'est la seule sortie touchée avant app.run().
1. Issue de secours sur place : GP18 (BTN15) lu avec le tirage interne vers
   le haut (aucun tirage externe sur le réseau BTN15). À la masse : ligne
   « # », puis arrêt sans lancer le firmware (console et mpremote libres).
2. Issue de secours à distance : ligne « # », puis 2 s avec Ctrl-C actif.
3. app.run() (Ctrl-C coupé par kbd_intr(-1), § 5.1).

Copié sur le Pico seulement avec l'accord du propriétaire ; au banc, le
firmware se lance par `mpremote run bench.py`.
"""

import machine
import micropython
import sys
import time

HEARTBEAT_PIN = 16      # GP16 = board.HEARTBEAT_PIN (pinout.json, réseau LED_VIE)
BTN15_PIN = 18          # GP18 = board.BTN15_PIN (pinout.json), board n'est pas importé
SETTLE_MS = 1           # établissement du tirage interne avant la lecture
WINDOW_MS = 2000        # fenêtre Ctrl-C, issue de secours à distance


def start(machine=machine, micropython=micropython, time=time, write=sys.stdout.write, run=None):
    """Lance le firmware ; renvoie False sans le lancer si GP18 est à la masse."""
    machine.Pin(HEARTBEAT_PIN, machine.Pin.OUT, value=1)   # première instruction utile
    pin = machine.Pin(BTN15_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
    time.sleep_ms(SETTLE_MS)
    if pin.value() == 0:
        write("# main: GP18 grounded, firmware not started\n")
        return False
    write("# main: Ctrl-C enabled for 2 s\n")
    micropython.kbd_intr(3)                 # Ctrl-C actif (déjà le cas au démarrage)
    time.sleep_ms(WINDOW_MS)                # KeyboardInterrupt : retour à la console
    if run is None:
        import app
        run = app.run
    run()
    return True


if __name__ == "__main__":
    start()
