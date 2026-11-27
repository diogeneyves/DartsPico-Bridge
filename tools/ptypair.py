# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Expose un simulateur sur un pseudo-terminal (macOS et Linux).

Le simulateur garde le côté maître ; le côté esclave (/dev/ttys00N sur
macOS, /dev/pts/N sur Linux) s'ouvre comme un port série USB. Les deux côtés
sont en mode brut : pas d'écho, pas de conversion \\n -> \\r\\n, pas
d'interprétation de 0x03, 0x11 ou 0x13.

Un simulateur fournit feed(octets) -> octets et tick() -> octets.
"""

import errno
import os
import select
import termios
import threading
import time
import tty

OUT_BUFFER_MAX = 1 << 16    # au-delà, les octets non lus sont jetés (aucun lecteur)


def set_raw(fd):
    tty.setraw(fd, termios.TCSANOW)
    attrs = termios.tcgetattr(fd)
    attrs[3] &= ~termios.ECHO
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def open_pty():
    """Crée une paire ; renvoie (fd maître, fd esclave gardé ouvert, chemin esclave)."""
    master, slave = os.openpty()
    set_raw(slave)
    os.set_blocking(master, False)
    return master, slave, os.ttyname(slave)


def open_client(path):
    """Ouvre le côté esclave comme le ferait le démon (lecture/écriture, mode brut)."""
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
    set_raw(fd)
    return fd


class PtyServer:
    """Fait tourner un simulateur sur un pty, dans un fil d'exécution."""

    def __init__(self, sim, tick_s=0.005):
        self.sim = sim
        self.tick_s = tick_s
        self.master, self._slave, self.path = open_pty()
        self._out = bytearray()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None

    def start(self):
        self._push(self.sim.boot())
        self._thread = threading.Thread(target=self._run, name="pty-sim", daemon=True)
        self._thread.start()
        return self

    def call(self, fn, *args):
        """Appelle une méthode du simulateur depuis un autre fil (injection d'événements)."""
        with self._lock:
            self._push(fn(*args))

    def _push(self, data):
        if data:
            self._out.extend(data)
            if len(self._out) > OUT_BUFFER_MAX:
                del self._out[:len(self._out) - OUT_BUFFER_MAX]

    def _run(self):
        while not self._stop.is_set():
            w = [self.master] if self._out else []
            try:
                r, w, _ = select.select([self.master], w, [], self.tick_s)
            except (OSError, ValueError, TypeError):      # pty fermé pendant l'attente
                break
            with self._lock:
                if r:
                    try:
                        data = os.read(self.master, 4096)
                    except OSError as e:
                        if e.errno not in (errno.EAGAIN, errno.EIO):
                            raise
                        data = b""
                    if data:
                        self._push(self.sim.feed(data))
                self._push(self.sim.tick())
                if self._out:
                    try:
                        n = os.write(self.master, bytes(self._out))
                        del self._out[:n]
                    except OSError as e:
                        if e.errno not in (errno.EAGAIN, errno.EIO):
                            raise

    def stop(self):
        """Arrête et ferme le pty : le lecteur voit une fin de fichier ou EIO (débranchement)."""
        self._stop.set()
        if self._thread:
            self._thread.join(2)
        # Une seule fois : un second close() fermerait des numéros déjà réutilisés
        # par un autre pseudo-terminal.
        fds, self.master, self._slave = (self.master, self._slave), None, None
        for fd in fds:
            if fd is None:
                continue
            try:
                os.close(fd)
            except OSError:
                pass

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def monotonic_us():
    return time.monotonic_ns() // 1000


def stop_on_sigterm():
    """SIGTERM (kill, systemd) arrête le simulateur comme Ctrl-C, en fermant le pty."""
    import signal

    def handler(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handler)
