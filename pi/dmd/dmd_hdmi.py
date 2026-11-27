# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Écran HDMI dédié du DMD : cœur en Python pur (spécification § 9.9, « Écran HDMI
dédié », complément 72).

Alternative au DMD physique : les images publiées par le service s'affichent en plein
écran sur la sortie HDMI-A-2 du Pi 4 (2e port micro-HDMI). Clé hdmi_output de dmd.toml
(0 par défaut), jamais en rejeu.

Côté service (dmd_service.run) :

    out = HdmiOutput()
    out.start()             processus dmd_hdmi_screen.py, SDL_VIDEODRIVER=wayland
    out.frame(octets)       depuis publish() ; n'attend jamais
    out.close()

Les images passent par l'entrée standard du processus, un tube non bloquant
(FrameSender) : une image commencée est finie aux appels suivants, et parmi les images
pas encore commencées seule la dernière est gardée. Le processus (FrameReader) n'affiche
que la dernière image complète reçue.

Côté écran (dmd_hdmi_screen.py, couche pygame) : Connector relit le branchement toutes
les 5 s, choose_display() choisit l'écran par son nom, fit() et band() donnent l'échelle
entière, la place et le rectangle de la bande (seul rectangle mis à jour), Changes écarte
une image identique à la précédente. L'image RGB565 est copiée telle quelle dans une
surface pygame 16 bits (MASKS, DEPTH), sans conversion pixel par pixel en Python
(complément 73) ; pixel_channels() décrit, en Python pur, ce que ces masques lisent.
Rendu (complément 74) : choose_path() essaie d'abord le rendu accéléré (texture 128 × 32
agrandie par le GPU), sinon la surface 16 bits ; FrameDrawer ne met à jour et ne présente
qu'après un changement d'image. Ce module n'importe pas pygame.
"""

import logging
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_SHARED = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "shared")
for _p in (_HERE, _SHARED):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import dmd_proto as D  # noqa: E402

log = logging.getLogger("dmd")

OUTPUT_NAME = "HDMI-A-2"                    # 2e port micro-HDMI du Pi 4
STATUS_PATH = "/sys/class/drm/card1-HDMI-A-2/status"
POLL_US = 5 * 1000000                       # branchement relu toutes les 5 s
RESTART_US = 30 * 1000000                   # processus relancé au plus toutes les 30 s
CLOSE_TIMEOUT_S = 2                         # attente de la fin du processus à la fermeture
SCREEN_SCRIPT = os.path.join(_HERE, "dmd_hdmi_screen.py")


def monotonic_us():
    return time.monotonic_ns() // 1000


# Surface pygame 16 bits : masques RGB565 (rouge, vert, bleu, alpha) ; les octets du
# protocole y sont copiés tels quels, donc l'ordre natif doit être celui du protocole :
# u16 petit-boutiste (§ 6.2, shared/dmd_proto.py:149 ; firmware lib/card.cpp:272).
MASKS = (0xF800, 0x07E0, 0x001F, 0)
DEPTH = 16
BYTE_ORDER = "little"


def native_order_ok(byteorder=None):
    """Vrai si l'ordre natif des octets (sys.byteorder) est celui du protocole."""
    return (sys.byteorder if byteorder is None else byteorder) == BYTE_ORDER


def _shift(mask):
    return (mask & -mask).bit_length() - 1


def pixel_channels(frame, index):
    """Composantes brutes (r5, g6, b5) du pixel n° index, lues comme la surface 16 bits les
    lit : u16 dans l'ordre BYTE_ORDER, puis MASKS. Sert aux tests ; jamais appelé par
    image."""
    if len(frame) != D.PIXELS_BYTES:
        raise ValueError("image de %d octets au lieu de %d" % (len(frame), D.PIXELS_BYTES))
    v = int.from_bytes(frame[2 * index:2 * index + 2], BYTE_ORDER)
    return tuple((v & m) >> _shift(m) for m in MASKS[:3])


class Changes:
    """Image identique à la précédente : rien n'est redessiné (le service répète une image
    fixe toutes les secondes). reset() après une ouverture de l'écran."""

    def __init__(self):
        self.last = None

    def is_new(self, frame):
        if frame == self.last:
            return False
        self.last = bytes(frame)
        return True

    def reset(self):
        self.last = None


def fit(width, height):
    """(échelle, x, y) : plus grande échelle entière qui tient, image centrée. None si
    l'écran fait moins de 128 x 32 pixels."""
    scale = min(width // D.WIDTH, height // D.HEIGHT)
    if scale < 1:
        return None
    return scale, (width - D.WIDTH * scale) // 2, (height - D.HEIGHT * scale) // 2


PATH_ACCELERATED = "accelerated"           # texture agrandie par le GPU (complément 74)
PATH_SURFACE = "surface"                   # surface 16 bits agrandie en C (complément 73)


def choose_path(make_accelerated, make_surface, once):
    """(chemin, rendu) : le rendu accéléré s'il se construit, sinon la surface 16 bits ;
    l'échec du rendu accéléré est journalisé une fois (once). Une erreur du repli remonte."""
    try:
        return PATH_ACCELERATED, make_accelerated()
    except Exception as error:      # noqa: BLE001 (pygame._sdl2 absent, pilote refusé, etc.)
        once("accelerated", "DMD HDMI : accelerated renderer unavailable (%s: %s), surface path",
             type(error).__name__, error)
    return PATH_SURFACE, make_surface()


WINDOWPOS_UNDEFINED_DISPLAY = 0x1FFF0000    # SDL_WINDOWPOS_UNDEFINED_DISPLAY(0) (complément 75)


def display_position(index):
    """Position SDL d'une fenêtre placée sur l'écran index :
    (SDL_WINDOWPOS_UNDEFINED_DISPLAY(index),) * 2."""
    value = WINDOWPOS_UNDEFINED_DISPLAY | index
    return value, value


def open_accelerated(index, make_window, make_renderer, make_texture):
    """Chemin accéléré sans set_mode (complément 75) : fenêtre sur l'écran index, vérifiée
    par son display_index, puis rendu et texture. En cas d'échec après la création de la
    fenêtre, elle est fermée (window.destroy()) et l'erreur remonte (repli par choose_path)."""
    window = make_window(display_position(index))
    try:
        if window.display_index != index:
            raise RuntimeError("window on screen %d instead of %d" % (window.display_index, index))
        renderer = make_renderer(window)
        texture = make_texture(renderer)
    except BaseException:
        window.destroy()
        raise
    return window, renderer, texture


def open_message(index, size, scale, path):
    """Texte de la ligne de journal de l'ouverture, avec le chemin de rendu choisi."""
    return "full screen on %s (screen %d, %dx%d, x%d, %s)" % (
        OUTPUT_NAME, index, size[0], size[1], scale, path)


class FrameDrawer:
    """Dessin d'une image : backend.upload(image) puis backend.present(), seulement si
    l'image diffère de la dernière dessinée. Une image dont l'envoi échoue n'est pas
    marquée comme dessinée. reset() après une ouverture de l'écran."""

    def __init__(self, backend):
        self.backend = backend
        self.changes = Changes()

    def show(self, frame):
        if frame == self.changes.last:
            return False
        self.backend.upload(frame)
        self.backend.present()
        self.changes.is_new(frame)
        return True

    def reset(self):
        self.changes.reset()


def band(width, height):
    """(x, y, largeur, hauteur) de la bande agrandie, seul rectangle mis à jour ; None si
    l'écran est trop petit."""
    place = fit(width, height)
    if place is None:
        return None
    scale, x, y = place
    return x, y, D.WIDTH * scale, D.HEIGHT * scale


def _is_output(name):
    if not name:
        return False
    return name == OUTPUT_NAME or name.endswith("(%s)" % OUTPUT_NAME)


def choose_display(names):
    """Numéro de l'écran SDL dont le nom est HDMI-A-2 (nom exact, ou description qui finit
    par « (HDMI-A-2) »). None si aucun écran ne convient ou si plusieurs conviennent :
    jamais d'autre écran."""
    found = [i for i, name in enumerate(names) if _is_output(name)]
    return found[0] if len(found) == 1 else None


def read_status(path=STATUS_PATH):
    """Vrai si la sortie est branchée (« connected ») ; toute autre valeur ou lecture
    impossible : débranchée."""
    try:
        with open(path, encoding="ascii", errors="replace") as f:
            return f.read().strip() == "connected"
    except OSError:
        return False


class Connector:
    """Branchement de HDMI-A-2, relu au plus toutes les POLL_US µs.

    check() : None entre deux lectures ; sinon l'état lu (vrai : branché). Le premier
    appel lit tout de suite. Chaque changement est journalisé une fois."""

    def __init__(self, read=read_status, clock=monotonic_us, period_us=POLL_US):
        self.read = read
        self.clock = clock
        self.period_us = period_us
        self.plugged = False
        self._last = None

    def check(self):
        now = self.clock()
        if self._last is not None and now - self._last < self.period_us:
            return None
        self._last = now
        plugged = bool(self.read())
        if plugged != self.plugged:
            log.info("DMD HDMI : %s %s", OUTPUT_NAME, "connected" if plugged else "disconnected")
        self.plugged = plugged
        return plugged


class OnceLog:
    """Une ligne de journal par cause, pour la vie du processus."""

    def __init__(self, logger=log):
        self.logger = logger
        self.seen = set()

    def __call__(self, cause, fmt, *args):
        if cause in self.seen:
            return False
        self.seen.add(cause)
        self.logger.warning(fmt, *args)
        return True


class FrameSender:
    """Envoi des images dans un tube non bloquant ; n'attend jamais.

    Une image commencée est finie aux appels suivants (le flux reste aligné sur 8 192
    octets) ; parmi les images pas encore commencées, seule la dernière est gardée
    (dropped compte les autres). BrokenPipeError si le lecteur a fermé le tube."""

    def __init__(self, fd):
        self.fd = fd
        os.set_blocking(fd, False)
        self._pending = None                # reste de l'image en cours d'écriture
        self._latest = None                 # dernière image pas encore commencée
        self.dropped = 0

    def send(self, frame):
        if len(frame) != D.PIXELS_BYTES:
            raise ValueError("image de %d octets au lieu de %d" % (len(frame), D.PIXELS_BYTES))
        if self._latest is not None:
            self.dropped += 1
        self._latest = bytes(frame)
        self.flush()

    def flush(self):
        while True:
            if not self._pending:
                if self._latest is None:
                    return
                self._pending = memoryview(self._latest)
                self._latest = None
            try:
                n = os.write(self.fd, self._pending)
            except BlockingIOError:
                return
            self._pending = self._pending[n:]


class FrameReader:
    """Lecture non bloquante du tube : read() rend la dernière image complète arrivée
    (None s'il n'y en a pas) ; EOFError à la fin du tube."""

    CHUNK = 65536

    def __init__(self, fd):
        self.fd = fd
        os.set_blocking(fd, False)
        self._buf = bytearray()
        self._eof = False

    def read(self):
        while not self._eof:
            try:
                data = os.read(self.fd, self.CHUNK)
            except BlockingIOError:
                break
            if not data:
                self._eof = True
                break
            self._buf += data
        count = len(self._buf) // D.PIXELS_BYTES
        if count:
            end = count * D.PIXELS_BYTES
            frame = bytes(self._buf[end - D.PIXELS_BYTES:end])
            del self._buf[:end]
            return frame
        if self._eof:
            raise EOFError("fin du tube des images")
        return None


def screen_command():
    return [sys.executable, SCREEN_SCRIPT]


def screen_env(environ=None, uid=None):
    """Environnement du processus d'écran : SDL_VIDEODRIVER=wayland pour lui seul,
    PYGAME_HIDE_SUPPORT_PROMPT=1 ;
    XDG_RUNTIME_DIR et WAYLAND_DISPLAY donnés s'ils manquent (l'unité systemd n'en a
    pas). L'environnement reçu n'est pas modifié."""
    env = dict(os.environ if environ is None else environ)
    env["SDL_VIDEODRIVER"] = "wayland"
    env["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"     # pas de bandeau de pygame dans le journal
    env.setdefault("XDG_RUNTIME_DIR", "/run/user/%d" % (os.getuid() if uid is None else uid))
    env.setdefault("WAYLAND_DISPLAY", "wayland-0")
    return env


def spawn_screen():
    return subprocess.Popen(screen_command(), stdin=subprocess.PIPE, env=screen_env())


class HdmiOutput:
    """Sortie HDMI du service : processus d'écran et envoi des images.

    Processus arrêté ou impossible à lancer : journalisé une fois, relancé au plus toutes
    les RESTART_US µs ; le service ne s'arrête jamais pour l'écran."""

    def __init__(self, spawn=spawn_screen, clock=monotonic_us, restart_us=RESTART_US):
        self.spawn = spawn
        self.clock = clock
        self.restart_us = restart_us
        self.proc = None
        self.sender = None
        self.started_at = None
        self.closed = False
        self.once = OnceLog()

    def start(self):
        self.started_at = self.clock()
        try:
            self.proc = self.spawn()
        except OSError as error:
            self.proc = None
            self.once("spawn", "DMD : HDMI screen process not started (%s)", error)
            return
        self.sender = FrameSender(self.proc.stdin.fileno())
        log.info("DMD : HDMI screen process started")

    def _alive(self):
        if self.proc is not None and self.proc.poll() is None and self.sender is not None:
            return True
        if self.proc is not None:
            code = self.proc.poll()
            self.once("stopped", "DMD : HDMI screen process stopped (code %s)", code)
            self._drop()
        if self.clock() - self.started_at >= self.restart_us:
            self.start()
            return self.proc is not None
        return False

    def _drop(self):
        if self.proc is not None:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
        self.proc = None
        self.sender = None

    def frame(self, data):
        if len(data) != D.PIXELS_BYTES:
            raise ValueError("image de %d octets au lieu de %d" % (len(data), D.PIXELS_BYTES))
        if self.closed or self.started_at is None or not self._alive():
            return
        try:
            self.sender.send(data)
        except BrokenPipeError:
            self.once("stopped", "DMD : HDMI screen process stopped (pipe closed)")
            self._drop()

    def close(self):
        if self.closed:
            return
        self.closed = True
        proc = self.proc
        self._drop()
        if proc is None:
            return
        try:
            proc.wait(timeout=CLOSE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
