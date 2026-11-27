# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Écran HDMI dédié du DMD : couche pygame (spécification § 9.9, « Écran HDMI dédié »,
complément 72).

Processus lancé par dmd_hdmi.HdmiOutput (dmd_service), avec SDL_VIDEODRIVER=wayland
pour lui seul. Il lit les images RGB565 sur son entrée standard et les affiche en plein
écran, pixels carrés nets, échelle entière maximale, centrées sur fond noir, uniquement
sur l'écran SDL nommé HDMI-A-2. L'image RGB565 est copiée telle quelle dans une surface
16 bits, agrandie par pygame.transform.scale (en C) ; seule la bande est mise à jour
(display.update), et une image identique à la précédente n'est pas redessinée
(complément 73). Rendu accéléré d'abord (complément 74) : texture 128 × 32 au format
par défaut du rendu (les rendus accélérés du Pi n'offrent pas le RGB565 : SDL convertit
la surface 16 bits en C), agrandie par le GPU dans la bande ; repli sur la surface
16 bits si le rendu accéléré ne se crée pas. Le chemin accéléré n'appelle pas set_mode
(SDL refuse un rendu sur une fenêtre qui a déjà une surface) : il ouvre sa propre fenêtre
pygame._sdl2.video.Window en plein écran sur le bureau de l'écran choisi (complément 75) ;
en cas d'échec, elle est fermée et le chemin set_mode prend le relais. Branchement relu toutes les 5 s : ouverture au
branchement, fermeture au débranchement. Fin de l'entrée standard : arrêt.

Couche mince, NON TESTÉE hors du Pi (ni le Mac ni la CI n'ont pygame) : toute la logique
est dans dmd_hdmi (Python pur, testé). pygame n'est importé qu'à l'ouverture de l'écran.
Aucune sortie sur stdout ; journal sur stderr (journal de l'unité systemd).
"""

import ctypes
import ctypes.util
import logging
import os
import select
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import dmd_hdmi as H  # noqa: E402

log = logging.getLogger("dmd")

WAIT_S = 0.1                                # attente maximale d'une image


def display_names(count):
    """Noms des écrans par SDL_GetDisplayName de la bibliothèque SDL déjà chargée par
    pygame ; None si elle n'est pas trouvée."""
    for name in ("libSDL2-2.0.so.0", ctypes.util.find_library("SDL2")):
        if not name:
            continue
        try:
            lib = ctypes.CDLL(name)
            get_name = lib.SDL_GetDisplayName
        except (OSError, AttributeError):
            continue
        get_name.restype = ctypes.c_char_p
        get_name.argtypes = [ctypes.c_int]
        names = []
        for i in range(count):
            raw = get_name(i)
            names.append(raw.decode("utf-8", "replace") if raw else None)
        return names
    return None


def write_frame(pygame, source, frame):
    """Copie l'image RGB565 telle quelle dans la surface 16 bits (masques H.MASKS)."""
    buf = source.get_buffer()
    pitch = source.get_pitch()
    row = H.D.WIDTH * 2
    if pitch == row:
        buf.write(bytes(frame), 0)
    else:
        for line in range(H.D.HEIGHT):
            buf.write(bytes(frame[line * row:(line + 1) * row]), line * pitch)
    del buf                                     # libère le verrou de la surface


class AcceleratedBackend:
    """Fenêtre SDL à part (sans set_mode), plein écran sur l'écran index ; texture 128 × 32
    agrandie par le GPU dans la bande (pygame._sdl2.video, complément 75)."""

    def __init__(self, pygame, index, size, rect):
        from pygame._sdl2 import video
        self.pygame = pygame
        self.rect = pygame.Rect(rect)
        self.source = pygame.Surface((H.D.WIDTH, H.D.HEIGHT), 0, H.DEPTH, H.MASKS)
        self.window, self.renderer, self.texture = H.open_accelerated(
            index,
            lambda position: video.Window("RaspyDarts DMD", size, position=position,
                                          fullscreen_desktop=True),
            lambda window: video.Renderer(window, accelerated=1),
            lambda renderer: video.Texture(renderer, (H.D.WIDTH, H.D.HEIGHT), streaming=True))
        try:
            self.renderer.draw_color = (0, 0, 0, 255)
            self.renderer.clear()               # fond noir sur tout l'écran
            self.renderer.present()
        except BaseException:
            self.close()
            raise

    def upload(self, frame):
        write_frame(self.pygame, self.source, frame)
        self.texture.update(self.source)        # conversion RGB565 -> format de la texture par SDL

    def present(self):
        self.renderer.clear()
        self.texture.draw(dstrect=self.rect)    # agrandie par le GPU
        self.renderer.present()

    def close(self):
        window = self.window
        self.texture = self.renderer = self.window = None
        if window is not None:
            window.destroy()


class SurfaceBackend:
    """Repli : surface 16 bits agrandie en C, seule la bande mise à jour (complément 73)."""

    def __init__(self, pygame, index, size, rect):
        self.pygame = pygame
        surface = pygame.display.set_mode(size, pygame.FULLSCREEN, display=index)
        pygame.display.set_caption("RaspyDarts DMD")
        self.surface = surface
        self.rect = pygame.Rect(rect)
        self.source = pygame.Surface((H.D.WIDTH, H.D.HEIGHT), 0, H.DEPTH, H.MASKS)
        self.scaled = pygame.Surface(self.rect.size, 0, H.DEPTH, H.MASKS)
        surface.fill((0, 0, 0))
        pygame.display.flip()                   # une fois : fond noir sur tout l'écran

    def upload(self, frame):
        write_frame(self.pygame, self.source, frame)
        self.pygame.transform.scale(self.source, self.rect.size, self.scaled)
        self.surface.blit(self.scaled, self.rect.topleft)

    def present(self):
        self.pygame.display.update(self.rect)

    def close(self):
        self.surface = None


class Screen:
    def __init__(self, once):
        self.once = once
        self.pygame = None
        self.backend = None
        self.drawer = None
        self.path = None

    @property
    def is_open(self):
        return self.backend is not None

    def open(self):
        """Plein écran sur HDMI-A-2 ; faux (et une ligne de journal par cause) sinon."""
        try:
            import pygame
        except ImportError as error:
            self.once("pygame", "DMD HDMI : pygame missing (%s)", error)
            return False
        self.pygame = pygame
        if not H.native_order_ok():
            self.once("order", "DMD HDMI : native byte order %s, RGB565 needs %s, nothing opened",
                      sys.byteorder, H.BYTE_ORDER)
            return False
        try:
            pygame.display.init()
        except pygame.error as error:
            self.once("init", "DMD HDMI : no display (%s)", error)
            return False
        sizes = pygame.display.get_desktop_sizes()
        names = display_names(len(sizes))
        if names is None:
            self.once("names", "DMD HDMI : screen names unreadable (SDL library not found)")
            pygame.display.quit()
            return False
        index = H.choose_display(names)
        if index is None:
            self.once("name", "DMD HDMI : %s not found among the screens %s, nothing opened",
                      H.OUTPUT_NAME, names)
            pygame.display.quit()
            return False
        size = sizes[index]
        rect = H.band(*size)
        if rect is None:
            self.once("size", "DMD HDMI : screen %s too small (%s)", H.OUTPUT_NAME, size)
            pygame.display.quit()
            return False
        pygame.mouse.set_visible(False)
        try:
            self.path, backend = H.choose_path(
                lambda: AcceleratedBackend(pygame, index, size, rect),
                lambda: SurfaceBackend(pygame, index, size, rect), self.once)
        except pygame.error as error:
            self.once("mode", "DMD HDMI : full screen refused (%s)", error)
            pygame.display.quit()
            return False
        self.backend = backend
        self.drawer = H.FrameDrawer(backend)
        log.info("DMD HDMI : %s", H.open_message(index, size, rect[2] // H.D.WIDTH, self.path))
        return True

    def show(self, frame):
        self.drawer.show(frame)                 # rien si l'image n'a pas changé

    def pump(self):
        self.pygame.event.pump()

    def close(self):
        if self.backend is None:
            return
        backend, self.backend, self.drawer = self.backend, None, None
        backend.close()
        self.pygame.display.quit()
        log.info("DMD HDMI : screen closed")


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(message)s")
    fd = sys.stdin.fileno()
    reader = H.FrameReader(fd)
    connector = H.Connector()
    screen = Screen(H.OnceLog())
    last = None
    try:
        while True:
            select.select([fd], [], [], WAIT_S)
            try:
                frame = reader.read()
            except EOFError:
                break
            plugged = connector.check()
            if plugged is True and not screen.is_open:
                if screen.open() and last is not None:
                    screen.show(last)
            elif plugged is False and screen.is_open:
                screen.close()
            if frame is not None:
                last = frame
                if screen.is_open:
                    screen.show(frame)
            if screen.is_open:
                screen.pump()
    except KeyboardInterrupt:
        pass
    finally:
        screen.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
