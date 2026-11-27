#!/usr/bin/env python3
# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Enregistreur des messages du jeu pour le DMD (spécification § 9.9, 47e complément).

Lancé à la main sur le Pi ; écrit le journal du mode rejeu du service de rendu :

    <délai en secondes depuis le message précédent> <sujet> <contenu>

Premier délai 0, une ligne vidée sur disque par message. Contenu non UTF-8
décodé avec remplacement, retours à la ligne remplacés par une espace (chacun
journalisé une fois). paho-mqtt 2, déjà sur le Pi par le jeu.

    python3 tools/dmd_record.py -o partie.txt        (Ctrl-C pour arrêter)
"""

import argparse
import logging
import sys
import threading
import time

log = logging.getLogger("dmd_record")

# Mêmes sujets que dmd_core.TOPICS (vérifié par les tests)
TOPICS = ("raspydarts/dmd", "raspy2dmd")


class Recorder:
    def __init__(self, out, clock=None):
        self.out = out
        self.clock = clock or time.monotonic       # secondes
        self.count = 0
        self._last = None
        self._warned = set()
        self._lock = threading.Lock()

    def _warn_once(self, key, text):
        if key not in self._warned:
            self._warned.add(key)
            log.warning(text)

    def record(self, topic, payload):
        if isinstance(payload, (bytes, bytearray)):
            try:
                text = bytes(payload).decode("utf-8")
            except UnicodeDecodeError:
                text = bytes(payload).decode("utf-8", "replace")
                self._warn_once("utf8", "# contenu non UTF-8, décodé avec remplacement")
        else:
            text = str(payload)
        if "\n" in text or "\r" in text:
            text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
            self._warn_once("newline", "# retour à la ligne dans un contenu, remplacé par une espace")
        with self._lock:
            now = self.clock()
            delay = 0.0 if self._last is None else max(0.0, now - self._last)
            self._last = now
            self.out.write("%.3f %s %s\n" % (delay, topic, text))
            self.out.flush()
            self.count += 1


def connect(recorder, host="127.0.0.1", port=1883, mqtt_module=None):
    if mqtt_module is None:
        import paho.mqtt.client as mqtt_module     # paho-mqtt 2

    def on_connect(client, userdata, flags, reason_code, properties):
        client.subscribe([(topic, 0) for topic in TOPICS])
        log.info("# MQTT connecté, enregistrement de %s", ", ".join(TOPICS))

    def on_message(client, userdata, message):
        recorder.record(message.topic, message.payload)

    client = mqtt_module.Client(mqtt_module.CallbackAPIVersion.VERSION2, client_id="raspydarts-dmd-record")
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(1, 5)
    client.connect_async(host, port, keepalive=5)
    client.loop_start()
    return client


def main(argv=None):
    parser = argparse.ArgumentParser(description="Enregistre les messages du jeu pour le rejeu du DMD")
    parser.add_argument("-o", "--output", help="fichier du journal (défaut : sortie standard)")
    parser.add_argument("--mqtt-host", default="127.0.0.1")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    out = open(args.output, "a", encoding="utf-8") if args.output else sys.stdout
    recorder = Recorder(out)
    client = connect(recorder, args.mqtt_host, args.mqtt_port)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()
        client.loop_stop()
        log.info("# %d messages enregistrés", recorder.count)
        if out is not sys.stdout:
            out.close()


if __name__ == "__main__":
    main()
