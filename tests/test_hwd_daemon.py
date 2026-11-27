# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests du démon hwd : vrai pseudo-terminal avec le faux Pico, bus en mémoire.

Le port est un lien symbolique, comme /dev/cible créé par udev : on le fait
pointer vers un nouveau pseudo-terminal pour simuler un rebranchement.
Le client MQTT est testé avec le faux paho commun (tests/fake_mqtt.py), sans paho.
"""

import json
import os
import select
import tempfile
import threading
import time
import unittest

import _paths  # noqa: F401
import hwbus as H
from fake_pico import FakePico
from hwd_config import HwdConfig
from fake_mqtt import FakeMqttModule, Message
from hwd_daemon import Daemon, MemoryBus, PahoBus, SerialLink
from ptypair import PtyServer

T = H.topics()
TIMEOUT = 5.0


@unittest.skipUnless(hasattr(os, "openpty"), "pas de pseudo-terminal sur ce système")
class DaemonOverPty(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.link_path = os.path.join(self.dir, "cible")
        self.servers = []
        self.plug()
        self.bus = MemoryBus()
        cfg = HwdConfig(pico_device=self.link_path, reconnect_ms=100).validate()
        self.daemon = Daemon(cfg, self.bus)

    def tearDown(self):
        self.daemon.close()
        for srv in self.servers:
            srv.stop()
        if os.path.lexists(self.link_path):
            os.unlink(self.link_path)
        os.rmdir(self.dir)

    def plug(self):
        """Nouveau Pico sur un nouveau pseudo-terminal ; le lien pointe vers lui."""
        self.pico = FakePico()
        srv = PtyServer(self.pico).start()
        self.servers.append(srv)
        if os.path.lexists(self.link_path):
            os.unlink(self.link_path)
        os.symlink(srv.path, self.link_path)
        return srv

    def run_until(self, pred, what):
        end = time.monotonic() + TIMEOUT
        while time.monotonic() < end:
            self.daemon.run_once(0.005)
            if pred():
                return
        self.fail("délai dépassé : %s" % what)

    def status(self):
        st = [json.loads(p) for t, p, r in self.bus.published if t == T["status"]]
        return st[-1]["pico"] if st else None

    def events(self):
        return [p for t, p, _ in self.bus.published if t == T["events"]]

    def test_connect_dart_command_led(self):
        self.run_until(lambda: self.status() == "connected", "connexion")
        self.servers[-1].call(self.pico.dart, 2, 5, 1500)
        self.run_until(lambda: any(e.startswith("D R2C5 ") for e in self.events()), "fléchette")
        self.bus.inject(T["cmd"], b"TOY 3 1")
        self.run_until(lambda: self.pico.toys[2] == 1, "Toy allumé")
        self.bus.inject(T["led"] + "1", bytes(range(30)) * 30)
        self.run_until(lambda: self.pico.led_rgb[1] == bytes(range(30)) * 30, "image LED")

    def test_bytes_left_on_line_do_not_lose_first_who(self):
        # § 9.5, 28e complément : octets laissés par mpremote, sans fin de ligne, dans le
        # Pico. Le vidage (« \n », puis lignes ignorées) fait passer le premier WHO :
        # connexion avant la reprise de WHO (1 000 ms), sans NACK publié.
        self.servers[-1].call(self.pico.feed, b"\x01\x04xyz")
        start = time.monotonic()
        self.run_until(lambda: self.status() == "connected", "connexion")
        self.assertLess(time.monotonic() - start, 0.8)
        self.assertFalse([e for e in self.events() if e.startswith("E NACK")], self.events())

    def test_unplug_and_replug(self):
        self.run_until(lambda: self.status() == "connected", "connexion")
        self.bus.inject(T["cmd"], b"LGTS 3")
        self.bus.inject(T["cmd"], b"TOYS FF")
        self.run_until(lambda: self.pico.lights[:2] == [1, 1] and self.pico.toys[0], "sorties")
        self.servers[-1].stop()                                      # débranchement
        os.unlink(self.link_path)                                    # udev retire le lien
        self.run_until(lambda: self.status() == "absent", "état absent")
        self.assertFalse(self.daemon.connected_port)
        self.plug()                                                  # rebranchement
        self.run_until(lambda: self.status() == "connected", "reconnexion")
        self.run_until(lambda: self.pico.lights[:2] == [1, 1], "voyants restaurés")
        self.assertEqual(self.pico.toys, [0] * 8)

    def test_missing_port_at_start(self):
        self.servers[-1].stop()
        os.unlink(self.link_path)
        for _ in range(20):
            self.daemon.run_once(0.005)
        self.assertEqual(self.status(), "absent")
        self.plug()
        self.run_until(lambda: self.status() == "connected", "connexion tardive")

    def run_once_with_command(self, payload, timeout_s):
        """run_once(timeout_s), la commande arrivant 50 ms après le début ; durée de run_once."""
        timer = threading.Timer(0.05, self.bus.inject, (T["cmd"], payload))
        timer.start()
        start = time.monotonic()
        self.daemon.run_once(timeout_s)
        elapsed = time.monotonic() - start
        timer.join()
        return elapsed

    def test_command_wakes_loop_without_port(self):
        # Sans Pico, la boucle attend sur le réveil du bus (et non time.sleep) : le délai de 2 s
        # est interrompu par la commande.
        self.servers[-1].stop()
        os.unlink(self.link_path)
        self.daemon.run_once(0.005)
        self.assertLess(self.run_once_with_command(b"TOY 1 1", 2.0), 0.5)

    def test_command_forwarded_without_waiting_for_timeout(self):
        # Mesure sur le Pi : chaque commande du jeu attendait la fin du select() sur le port
        # (5 ms). Elle doit réveiller la boucle et partir aussitôt vers le Pico.
        self.run_until(lambda: self.status() == "connected", "connexion")
        for _ in range(20):                                  # présentation terminée, port calme
            self.daemon.run_once(0.005)
        self.assertLess(self.run_once_with_command(b"TOY 4 1", 2.0), 0.5)
        self.run_until(lambda: self.pico.toys[3] == 1, "Toy allumé")

    def test_port_opened_exclusively(self):
        self.run_until(lambda: self.status() == "connected", "connexion")
        other = SerialLink(self.link_path)
        with self.assertRaises(OSError):
            other.open()


class PahoClient(unittest.TestCase):
    def setUp(self):
        FakeMqttModule.Client.instances = []
        self.bus = PahoBus(HwdConfig().validate(), mqtt_module=FakeMqttModule)
        self.client = FakeMqttModule.Client.instances[-1]

    def call(self, name):
        return [c for c in self.client.calls if c[0] == name]

    def test_keepalive_5_s_and_callback_api_2(self):
        # R6 : durée de maintien de 5 s (défaut de paho-mqtt 2.1.0 : 60 s).
        self.assertEqual(self.client.api, "v2")
        (_, host, port, keepalive), = self.call("connect_async")
        self.assertEqual((host, port, keepalive), ("localhost", 1883, 5))
        self.assertLessEqual(1.5 * keepalive, 8)              # testament en moins de 8 s

    def test_last_will_set_before_connect(self):
        names = [c[0] for c in self.client.calls]
        self.assertLess(names.index("will_set"), names.index("connect_async"))
        (_, topic, payload, qos, retain), = self.call("will_set")
        self.assertEqual(topic, "raspydarts/hw/status")
        self.assertEqual(json.loads(payload), {"pico": "absent"})
        self.assertTrue(retain)

    def test_subscriptions_and_messages(self):
        self.bus._on_connect(self.client, None, {}, 0, None)
        (_, topics), = self.call("subscribe")
        self.assertEqual(topics, [("raspydarts/hw/cmd", 0), ("raspydarts/hw/led/+", 0),
                                  ("raspydarts/hw/dmd/frame", 0)])

    def test_no_dmd_subscription_when_disabled(self):
        FakeMqttModule.Client.instances = []
        bus = PahoBus(HwdConfig(dmd_device="").validate(), mqtt_module=FakeMqttModule)
        client = FakeMqttModule.Client.instances[-1]
        bus._on_connect(client, None, {}, 0, None)
        (_, topics), = [c for c in client.calls if c[0] == "subscribe"]
        self.assertEqual(topics, [("raspydarts/hw/cmd", 0), ("raspydarts/hw/led/+", 0)])
        bus.close()
        self.bus._on_message(self.client, None, Message("raspydarts/hw/cmd", b"OFF"))
        self.assertEqual(self.bus.poll(), [("raspydarts/hw/cmd", b"OFF")])
        self.assertEqual(self.bus.poll(), [])

    def test_message_wakes_main_loop(self):
        # Le fil réseau de paho signale chaque message sur un descripteur que la boucle
        # principale surveille avec le port série ; poll() le remet au repos.
        fd = self.bus.wakeup_fd()
        self.assertEqual(select.select([fd], [], [], 0)[0], [])
        for _ in range(3):
            self.bus._on_message(self.client, None, Message("raspydarts/hw/cmd", b"OFF"))
        self.assertEqual(select.select([fd], [], [], 0)[0], [fd])
        self.assertEqual(len(self.bus.poll()), 3)
        self.assertEqual(select.select([fd], [], [], 0)[0], [])
        self.bus.close()

    def test_retained_status_and_clean_close(self):
        self.bus.publish("raspydarts/hw/status", '{"pico": "connected"}', retain=True)
        self.bus.publish("raspydarts/hw/events", "D R1C1 1 1")
        pubs = self.call("publish")
        self.assertEqual(pubs[0][3:], (1, True))
        self.assertEqual(pubs[1][3:], (0, False))
        self.bus.close()
        last = self.call("publish")[-1]
        self.assertEqual(json.loads(last[2]), {"pico": "absent"})
        self.assertIn(("loop_stop",), self.client.calls)

    def test_daemon_uses_keepalive_from_config(self):
        FakeMqttModule.Client.instances = []
        PahoBus(HwdConfig(keepalive_s=3).validate(), mqtt_module=FakeMqttModule)
        client = FakeMqttModule.Client.instances[-1]
        self.assertEqual([c for c in client.calls if c[0] == "connect_async"][0][3], 3)


if __name__ == "__main__":
    unittest.main()
