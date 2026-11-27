# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Démon hwd : branche le cœur (hwd_core.py) sur les ports série du Pico et du DMD, et sur MQTT.

    python3 pi/hwd/hwd_daemon.py --config /etc/raspydarts/hwd.toml [--pico /dev/cible] [--dmd /dev/dmd]

Un seul fil fait tout le travail (port série, horloge, cœur) ; le fil réseau
de paho ne fait que déposer les messages reçus dans une file, et réveille la
boucle principale (tube surveillé par select() avec le port série).
"""

import fcntl
import json
import logging
import os
import queue
import select
import sys
import termios
import threading
import time
import tty

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(os.path.dirname(os.path.dirname(_HERE)), "shared")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hwbus  # noqa: E402
import hwd_config  # noqa: E402
from hwd_core import HwdCore  # noqa: E402

log = logging.getLogger("hwd")


def monotonic_us():
    return time.monotonic_ns() // 1000


# ------------------------------------------------------------ port série

class SerialLink:
    """Port série USB (ou pseudo-terminal) en mode brut, ouvert en exclusivité."""

    def __init__(self, path):
        self.path = path
        self.fd = None

    def open(self):
        fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            # hwd seul propriétaire du port : verrou (respecté par pyserial, minicom…)
            # et mode exclusif du terminal (ignoré par macOS sur un pseudo-terminal).
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.ioctl(fd, termios.TIOCEXCL)
            tty.setraw(fd, termios.TCSANOW)
            attrs = termios.tcgetattr(fd)
            attrs[3] &= ~termios.ECHO
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            termios.tcflush(fd, termios.TCIOFLUSH)
        except OSError:
            os.close(fd)
            raise
        self.fd = fd

    def close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def read(self):
        """Octets disponibles ; b"" s'il n'y a rien ; EOFError si le port a disparu."""
        try:
            data = os.read(self.fd, 4096)
        except BlockingIOError:
            return b""
        except OSError as e:
            raise EOFError(e.strerror) from None
        if not data:
            raise EOFError("End of file")
        return data

    def write(self, data, timeout=0.5):
        view = memoryview(data)
        end = time.monotonic() + timeout
        while view:
            try:
                n = os.write(self.fd, view)
                view = view[n:]
            except BlockingIOError:
                left = end - time.monotonic()
                if left <= 0:
                    raise EOFError("Port blocked on write")
                select.select([], [self.fd], [], left)
            except OSError as e:
                raise EOFError(e.strerror) from None


# ------------------------------------------------------------------ bus

class Waker:
    """Réveil de la boucle principale : un tube que select() surveille avec le port série.

    Le fil réseau écrit un octet à chaque message reçu ; la boucle vide le tube avant de lire
    la file. Sans lui, une commande du jeu attendait la fin du select() (5 ms, mesuré sur le Pi).
    """

    def __init__(self):
        self.read_fd, self._write_fd = os.pipe()
        os.set_blocking(self.read_fd, False)
        os.set_blocking(self._write_fd, False)

    def notify(self):
        try:
            os.write(self._write_fd, b"\0")
        except (BlockingIOError, OSError):       # tube plein (déjà réveillée) ou fermé
            pass

    def drain(self):
        try:
            while os.read(self.read_fd, 4096):
                pass
        except (BlockingIOError, OSError):
            pass

    def close(self):
        for fd in (self.read_fd, self._write_fd):
            try:
                os.close(fd)
            except OSError:
                pass
        self.read_fd = self._write_fd = -1


class _QueueBus:
    """File des messages reçus, avec réveil de la boucle principale."""

    def __init__(self):
        self._in = queue.Queue()
        self._waker = Waker()

    def _received(self, topic, payload):
        self._in.put((topic, payload))
        self._waker.notify()

    def wakeup_fd(self):
        return self._waker.read_fd

    def poll(self):
        self._waker.drain()                       # avant la lecture : aucun message manqué
        out = []
        while True:
            try:
                out.append(self._in.get_nowait())
            except queue.Empty:
                return out


class MemoryBus(_QueueBus):
    """Bus en mémoire pour les tests : publish() garde tout, inject() simule un message reçu."""

    def __init__(self):
        super().__init__()
        self.published = []

    def publish(self, topic, payload, retain=False):
        self.published.append((topic, payload, retain))

    def inject(self, topic, payload):
        self._received(topic, payload)

    def close(self):
        self._waker.close()


def _paho():
    try:
        import paho.mqtt.client as mqtt    # importé seulement quand le démon tourne vraiment
    except ImportError:
        raise SystemExit("hwd : paho-mqtt 2 est requis (pip install 'paho-mqtt>=2')") from None
    if not hasattr(mqtt, "CallbackAPIVersion"):
        raise SystemExit("hwd : paho-mqtt 2 est requis (version 1.x installée)")
    return mqtt


class PahoBus(_QueueBus):
    """Client MQTT (paho-mqtt 2) : durée de maintien et testament de R6."""

    def __init__(self, cfg, mqtt_module=None):
        super().__init__()
        self.cfg = cfg
        self.topics = hwbus.topics(cfg.topic_base)
        mqtt = mqtt_module or _paho()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id="raspydarts-hwd-%d" % os.getpid())
        # Testament : si hwd disparaît, le jeu apprend que la cible est absente.
        self.client.will_set(self.topics["status"], json.dumps({"pico": "absent"}),
                             qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(1, 5)
        self.client.connect_async(cfg.mqtt_host, cfg.mqtt_port, keepalive=cfg.keepalive_s)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        subs = [(self.topics["cmd"], 0), (self.topics["led"] + "+", 0)]
        if self.cfg.dmd_device:            # DMD désactivé : pas d'abonnement aux images
            subs.append((self.topics["dmd_frame"], 0))
        client.subscribe(subs)
        log.info("# MQTT : connected to %s:%d", self.cfg.mqtt_host, self.cfg.mqtt_port)

    def _on_message(self, client, userdata, message):
        self._received(message.topic, message.payload)

    def publish(self, topic, payload, retain=False):
        self.client.publish(topic, payload, qos=1 if retain else 0, retain=retain)

    def close(self):
        self.client.publish(self.topics["status"], json.dumps({"pico": "absent"}),
                            qos=1, retain=True)
        self.client.disconnect()
        self.client.loop_stop()
        self._waker.close()


# ---------------------------------------------------------------- démon

class _Port:
    """Un port série et la partie du cœur qui le sert (Pico ou DMD)."""

    def __init__(self, link, side):
        self.link = link
        self.side = side                   # serial_opened / serial_data / serial_closed / take_serial
        self.next_open = 0


class Daemon:
    def __init__(self, cfg, bus, clock=monotonic_us, link=None, dmd_link=None):
        self.cfg = cfg
        self.bus = bus
        self.clock = clock
        self.core = HwdCore(cfg, clock)
        self.link = link or SerialLink(cfg.pico_device)
        self.ports = [_Port(self.link, self.core)]
        # DMD : dmd_device vide = désactivé (32e complément).
        if dmd_link is None and cfg.dmd_device:
            dmd_link = SerialLink(cfg.dmd_device)
        self.dmd_link = dmd_link
        if dmd_link is not None:
            self.ports.append(_Port(dmd_link, self.core.dmd))
        self._flush_publishes()

    @property
    def connected_port(self):
        return self.link.fd is not None

    def _flush_publishes(self):
        for topic, payload, retain in self.core.take_publishes():
            self.bus.publish(topic, payload, retain)

    def _try_open(self, port):
        now = self.clock()
        if now < port.next_open:
            return
        port.next_open = now + self.cfg.reconnect_ms * 1000
        try:
            port.link.open()
        except OSError as e:
            log.debug("# Port %s : unavailable (%s)", port.link.path, e.strerror)
            return
        log.info("# Port %s : opened", port.link.path)
        port.side.serial_opened()

    def _lost(self, port, why):
        log.warning("# Port %s : lost (%s)", port.link.path, why)
        port.link.close()
        port.side.serial_closed()
        port.next_open = self.clock() + self.cfg.reconnect_ms * 1000

    def run_once(self, timeout=0.005):
        for port in self.ports:
            if port.link.fd is None:
                self._try_open(port)
        # Réveillée par un port série ou par un message du bus (commande du jeu, image LED ou DMD)
        fds = [self.bus.wakeup_fd()] + [p.link.fd for p in self.ports if p.link.fd is not None]
        r, _, _ = select.select(fds, [], [], timeout)
        for port in self.ports:
            if port.link.fd is not None and port.link.fd in r:
                try:
                    port.side.serial_data(port.link.read())
                except EOFError as e:
                    self._lost(port, e)
        for topic, payload in self.bus.poll():
            self.core.bus_message(topic, payload)
        self.core.tick()
        for port in self.ports:
            while port.link.fd is not None:
                out = port.side.take_serial()
                if out is None:
                    break
                try:
                    port.link.write(out)
                except EOFError as e:
                    self._lost(port, e)
        self._flush_publishes()

    def run(self, stop):
        while not stop.is_set():
            self.run_once()

    def close(self):
        for port in self.ports:
            port.link.close()
            port.side.serial_closed()
        self._flush_publishes()
        self.bus.close()


def main(argv=None):
    import argparse
    import signal

    parser = argparse.ArgumentParser(description="Démon matériel de RaspyDarts-Pico")
    parser.add_argument("--config", help="fichier TOML (défaut : valeurs par défaut)")
    parser.add_argument("--pico", help="port du Pico (remplace pico_device)")
    parser.add_argument("--dmd", help="port de l'Interstate 75 W (remplace dmd_device ; \"\" : désactivé)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = hwd_config.load(args.config) if args.config else hwd_config.HwdConfig().validate()
    if args.pico:
        cfg.pico_device = args.pico
    if args.dmd is not None:
        cfg.dmd_device = args.dmd
    stop = threading.Event()
    causes = []                         # signal reçu, journalisé à l'arrêt (50e complément)

    def request(name):
        causes.append(name)
        stop.set()
    signal.signal(signal.SIGTERM, lambda *_: request("SIGTERM"))
    signal.signal(signal.SIGINT, lambda *_: request("SIGINT"))
    daemon = Daemon(cfg, PahoBus(cfg))
    cause = None
    try:
        daemon.run(stop)
    except KeyboardInterrupt:
        cause = "KeyboardInterrupt"
    except BaseException as error:
        cause = "error %s" % type(error).__name__
        raise
    finally:
        daemon.close()
        log.info("# Stop : %s", cause or (causes[0] if causes else "stop requested"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
