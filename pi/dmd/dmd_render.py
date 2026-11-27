# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Rendu du DMD avec Pillow (spécification § 9.9, 46e et 48e compléments,
sous-phases SR-b et SR-c).

Traduit les commandes du cœur (dmd_core) en scènes 128 × 32 ; chaque scène
rend des images complètes RGB565 petit-boutistes de 8 192 octets.

    renderer = Renderer(media_dir)
    scene = renderer.scene(commande)
    scene.next_frame(now_us) -> 8 192 octets (avance d'un pas si la scène est animée ;
                                un média montre l'image visible à l'instant now_us)
    scene.animated           vrai tant que l'image change à chaque pas
    scene.token              (médias seulement) jeton rendu à core.media_done()
    scene.is_done(now_us)    (médias seulement) vrai quand le média est fini
    scene.close()            libère le fichier du média (sans effet sinon)

Horloge (ShowClock, 48e complément) : « HH:MM » en Blackwood.ttf, bleu sur noir,
redessinée quand la minute change ; sa durée (10 s) est tenue par le cœur.

Pillow 11.1.0, comme sur le Pi (§ 9.9). Ce module est le seul du service qui
importe Pillow : le cœur et la publication n'en dépendent pas.
"""

import logging
import os
import random
import struct
import time

from PIL import Image, ImageDraw, ImageFont

import dmd_core as C
import dmd_gif as G
import dmd_proto as D

log = logging.getLogger("dmd")

WIDTH = D.WIDTH
HEIGHT = D.HEIGHT
FONT_FILE = os.path.join("Fonts", "Impact.ttf")
CLOCK_FONT_FILE = os.path.join("Fonts", "Blackwood.ttf")   # police de l'horloge (48e complément)
CLOCK_FORMAT = "%H:%M"                  # heure locale du fuseau du Pi
MAX_SIZE = 30                           # taille 30 au plus (§ 9.9)
MIN_SIZE = 6                            # plus petite taille essayée [PROPOSÉ]
SCROLL_STEP = 2                         # pixels par image [PROPOSÉ]
DIRECTIONS = ("left", "right", "up", "down")
BLACK = (0, 0, 0)
WELCOME_TEXT = "RASPYDARTS"
PPM_STILL_US = 2 * 1000000              # un PPM s'affiche 2 s (48e complément)
MEDIA_EXTENSIONS = (".gif", ".ppm")     # médias de Scores (casse ignorée)

_PACK = struct.Struct("<%dH" % (WIDTH * HEIGHT))
_BLACK_FRAME = bytes(D.PIXELS_BYTES)


def to_rgb565(image):
    """Image Pillow 128 × 32 -> 8 192 octets RGB565 petit-boutistes, par dmd_proto.rgb565."""
    if image.size != (WIDTH, HEIGHT):
        raise ValueError("image de %dx%d au lieu de %dx%d" % (image.size + (WIDTH, HEIGHT)))
    data = image.convert("RGB").tobytes()
    cache = {}
    values = []
    append = values.append
    for i in range(0, len(data), 3):
        key = data[i:i + 3]
        v = cache.get(key)
        if v is None:
            v = cache[key] = D.rgb565(key[0], key[1], key[2])
        append(v)
    return _PACK.pack(*values)


# --- Scènes ------------------------------------------------------------------------------------

class StaticScene:
    """Une image fixe."""
    animated = False

    def __init__(self, frame):
        self._frame = frame

    def next_frame(self, now_us=None):
        return self._frame

    def close(self):
        pass


class ScrollScene:
    """Texte qui défile : passes passages, puis écran noir fixe (46e complément)."""

    def __init__(self, strip, direction, passes, background):
        self.strip = strip                  # image du texte seul, fond compris
        self.direction = direction
        self.passes = passes
        self.background = background
        self.animated = True
        self._pass = 0
        self._pos = 0
        w, h = strip.size
        span = (WIDTH + w) if direction in ("left", "right") else (HEIGHT + h)
        self._steps = -(-span // SCROLL_STEP) + 1   # du bord d'entrée à la sortie complète

    def _offset(self):
        w, h = self.strip.size
        d = self._pos * SCROLL_STEP
        if self.direction == "left":
            return (WIDTH - d, (HEIGHT - h) // 2)
        if self.direction == "right":
            return (d - w, (HEIGHT - h) // 2)
        if self.direction == "up":
            return ((WIDTH - w) // 2, HEIGHT - d)
        return ((WIDTH - w) // 2, d - h)    # down

    def close(self):
        pass

    def next_frame(self, now_us=None):
        if not self.animated:
            return _black(self.background)
        image = Image.new("RGB", (WIDTH, HEIGHT), self.background)
        image.paste(self.strip, self._offset())
        self._pos += 1
        if self._pos >= self._steps:
            self._pos = 0
            self._pass += 1
            if self._pass >= self.passes:
                self.animated = False
        return to_rgb565(image)


class ClockScene:
    """L'heure « HH:MM », image fixe, redessinée quand la minute change (48e complément).
    wall() rend l'heure en secondes depuis l'époque (time.time), convertie dans le fuseau
    local."""
    animated = False

    def __init__(self, draw, wall=time.time):
        self.draw = draw                    # draw(texte) -> 8 192 octets
        self.wall = wall
        self._text = None
        self._frame = None

    def next_frame(self, now_us=None):
        text = time.strftime(CLOCK_FORMAT, time.localtime(self.wall()))
        if text != self._text:
            self._frame = self.draw(text)
            self._text = text
        return self._frame

    def close(self):
        pass


class FailedMediaScene:
    """Média manquant ou corrompu : le texte de repli (sinon l'écran d'accueil), et la fin
    du média tout de suite, pour que le cœur passe au suivant (48e complément)."""

    def __init__(self, base, token):
        self.base = base
        self.token = token

    @property
    def animated(self):
        return self.base.animated

    def is_done(self, now_us):
        return True

    def next_frame(self, now_us=None):
        return self.base.next_frame(now_us)

    def close(self):
        self.base.close()


def _compose(image):
    """Une image du média -> 8 192 octets : réduite au voisin le plus proche si elle dépasse
    128 × 32, centrée sur fond noir, sans étirement ni recadrage (48e complément)."""
    image = image.convert("RGBA")           # transparence : le fond noir
    size = G.fit_size(*image.size)
    if size != image.size:
        image = image.resize(size, Image.NEAREST)
    canvas = Image.new("RGB", (WIDTH, HEIGHT), BLACK)
    canvas.paste(image, G.offset(size), image)
    return to_rgb565(canvas)


class GifSource:
    """Images d'un GIF, décodées au fur et à mesure (une à la fois, 48e complément) ;
    délais lus par dmd_gif (GCE), sans Pillow."""

    def __init__(self, path):
        self.info = G.read_gif(path)
        self.delays_us = self.info.delays_us
        self._image = Image.open(path)
        self._index = None
        self._frame = None
        try:
            self.frame(0)                   # première image décodée tout de suite
        except BaseException:
            self.close()
            raise

    def frame(self, index):
        if index != self._index:
            self._image.seek(index)         # Pillow décode les images sautées (disposition)
            self._frame = _compose(self._image)
            self._index = index
        return self._frame

    def close(self):
        if self._image is not None:
            self._image.close()
            self._image = None


class StillSource:
    """Une image fixe (PPM), décodée à l'ouverture."""

    def __init__(self, path, duration_us):
        with Image.open(path) as image:
            image.load()
            self._frame = _compose(image)
        self.delays_us = (duration_us,)

    def frame(self, index):
        return self._frame

    def close(self):
        pass


class PreloadedMedia:
    """Un média de Scores décodé d'avance : toutes ses images RGB565 et leurs délais."""

    def __init__(self, path, frames, delays_us):
        self.path = path
        self.frames = tuple(frames)
        self.delays_us = tuple(delays_us)

    def frame(self, index):
        return self.frames[index]

    def close(self):
        pass


class MediaScene:
    """Un GIF ou un PPM qui se joue en temps réel : à chaque pas, l'image qui doit être
    visible à cet instant (les images plus courtes qu'un pas sont sautées), selon la
    durée réelle du média (48e complément). Le passage commence à la première image publiée."""

    def __init__(self, source, timeline, token, on_error):
        self.source = source
        self.timeline = timeline
        self.token = token
        self.on_error = on_error            # on_error(exception) : image indécodable en cours de route
        self.animated = True
        self._start = None
        self._frame = None
        self._failed = False

    def is_done(self, now_us):
        if self._failed:
            return True
        if self._start is None:
            return False
        return self.timeline.done(now_us - self._start)

    def next_frame(self, now_us=None):
        if now_us is None:
            now_us = time.monotonic_ns() // 1000
        if self._start is None:
            self._start = now_us
        elapsed = now_us - self._start
        if not self._failed:
            if self.timeline.done(elapsed):
                self.animated = False       # dernière image gardée jusqu'au remplacement
            try:
                self._frame = self.source.frame(self.timeline.index_at(elapsed))
            except Exception as error:      # le service ne s'arrête jamais pour un média
                self._failed = True
                self.animated = False
                self.on_error(error)
        return self._frame if self._frame is not None else _BLACK_FRAME

    def close(self):
        self.source.close()


def _black(background):
    if background == BLACK:
        return _BLACK_FRAME
    return to_rgb565(Image.new("RGB", (WIDTH, HEIGHT), background))


# --- Rendu -------------------------------------------------------------------------------------

class Renderer:
    def __init__(self, media_dir, rng=None, wall=time.time, special_moves=True,
                 attract_max_s=G.ATTRACT_MAX_US // 1000000, should_stop=None):
        self.media_dir = media_dir
        # Durée maximale d'un GIF de l'Attract Mode (attract_gif_max_s, 50e complément)
        self.attract_max_us = int(attract_max_s * 1000000)
        # should_stop() : vrai si l'arrêt du service est demandé ; vérifié entre deux fichiers
        # pendant le décodage du démarrage (50e complément)
        self.should_stop = should_stop or (lambda: False)
        self.rng = rng or random.Random()
        self.wall = wall                    # heure de l'horloge (secondes depuis l'époque)
        self.scores = {}                    # code de la fléchette -> [PreloadedMedia]
        self.specials = {}                  # dossier de SpecialsMoves -> [PreloadedMedia]
        self._font_path = None
        self._fonts = {}
        self._logged = set()
        self.bad_media = set()              # fichiers corrompus (exclus du tirage par le service)
        self._font_path = self._check_font(FONT_FILE)
        self._clock_font_path = self._check_font(CLOCK_FONT_FILE)
        self.load_scores()
        if special_moves and not self.should_stop():    # --no-special-moves : rien n'est chargé
            self.load_specials()

    def _check_font(self, name):
        """Chemin de <media_dir>/<name> s'il s'ouvre, sinon None (police intégrée de Pillow),
        journalisé une fois."""
        path = os.path.join(self.media_dir, name) if self.media_dir else None
        if path and os.path.isfile(path):
            try:
                ImageFont.truetype(path, MAX_SIZE)
                return path
            except OSError as error:
                log.warning("DMD : font %s unreadable (%s), Pillow built-in font used", path, error)
        else:
            log.warning("DMD : font %s missing, Pillow built-in font used", path)
        return None

    @property
    def builtin_font(self):
        return self._font_path is None

    @property
    def builtin_clock_font(self):
        return self._clock_font_path is None

    def font(self, size, path=None):
        """Police à la taille size : path (Impact.ttf si None) ou la police intégrée."""
        path = path if path is not None else self._font_path
        f = self._fonts.get((path, size))
        if f is None:
            if path:
                f = ImageFont.truetype(path, size)
            else:
                f = ImageFont.load_default(size=size)
            self._fonts[(path, size)] = f
        return f

    def fit(self, text, max_w, max_h, path=None):
        """Plus grande taille de MAX_SIZE à MIN_SIZE où le texte tient ; MIN_SIZE sinon."""
        for size in range(MAX_SIZE, MIN_SIZE - 1, -1):
            f = self.font(size, path)
            left, top, right, bottom = f.getbbox(text)
            if (max_w is None or right - left <= max_w) and bottom - top <= max_h:
                return f
        return self.font(MIN_SIZE, path)

    def _strip(self, text, font, color, background):
        """Image du texte seul, à la taille de son encre (au moins 1 × 1)."""
        left, top, right, bottom = font.getbbox(text)
        w, h = max(1, right - left), max(1, bottom - top)
        image = Image.new("RGB", (w, h), background)
        ImageDraw.Draw(image).text((-left, -top), text, font=font, fill=color)
        return image

    # Scènes

    def _fixed_image(self, text, color, background, path=None):
        image = Image.new("RGB", (WIDTH, HEIGHT), background)
        if text.strip():
            font = self.fit(text, WIDTH, HEIGHT, path)
            strip = self._strip(text, font, color, background)
            image.paste(strip, ((WIDTH - strip.width) // 2, (HEIGHT - strip.height) // 2))
        return to_rgb565(image)

    def fixed_text(self, text, color=C.DEFAULT_TEXT_COLOR, background=None):
        return StaticScene(self._fixed_image(text, color, background or BLACK))

    def clock_frame(self, text):
        """Image de l'heure : Blackwood.ttf (ou police intégrée), bleu sur noir, taille et
        centrage du texte fixe (48e complément)."""
        return self._fixed_image(text, C.DEFAULT_TEXT_COLOR, BLACK, self._clock_font_path or "")

    def clock(self):
        return ClockScene(self.clock_frame, self.wall)

    def scrolling_text(self, text, direction, passes, color=C.DEFAULT_TEXT_COLOR, background=None):
        background = background or BLACK
        direction = (direction or "left").lower()
        if direction not in DIRECTIONS:
            key = ("direction", direction)
            if key not in self._logged:
                self._logged.add(key)
                log.info("DMD : unknown scroll direction %r, left used, this value no more logged",
                         direction[:C.LOG_PAYLOAD_MAX])
            direction = "left"
        horizontal = direction in ("left", "right")
        font = self.fit(text, None if horizontal else WIDTH, HEIGHT)
        strip = self._strip(text, font, color, background)
        return ScrollScene(strip, direction, max(1, passes or 1), background)

    def pattern(self):
        """Mire RGB (46e complément) : bandes rouge, vert, bleu, blanc ; dégradé de gris."""
        image = Image.new("RGB", (WIDTH, HEIGHT), BLACK)
        draw = ImageDraw.Draw(image)
        band = WIDTH // 4
        for i, color in enumerate(((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255))):
            draw.rectangle((i * band, 0, (i + 1) * band - 1, HEIGHT // 2 - 1), fill=color)
        for x in range(WIDTH):
            g = x * 255 // (WIDTH - 1)
            draw.line((x, HEIGHT // 2, x, HEIGHT - 1), fill=(g, g, g))
        return StaticScene(to_rgb565(image))

    def welcome(self):
        return self.fixed_text(WELCOME_TEXT)

    def _log_once(self, key, message, *args):
        if key not in self._logged:
            self._logged.add(key)
            log.warning(message, *args)

    def _corrupt(self, path, error):
        self.bad_media.add(path)
        self._log_once(("corrupt", path), "DMD : media %s unreadable (%s), skipped, no more logged",
                       path, str(error)[:C.LOG_PAYLOAD_MAX])

    def open_source(self, path, still_us=PPM_STILL_US):
        """GifSource ou StillSource selon l'extension (casse ignorée) ; exception si le
        fichier est corrompu (Pillow ne l'ouvre pas ou ne le décode pas, aucune image)."""
        ext = os.path.splitext(path)[1].lower()
        if ext == ".gif":
            return GifSource(path)
        if ext == ".ppm":
            return StillSource(path, still_us)
        raise ValueError("extension %r non prise en charge" % ext)

    def _preload(self, path):
        source = self.open_source(path)
        try:
            frames = [source.frame(i) for i in range(len(source.delays_us))]
        finally:
            source.close()
        return PreloadedMedia(path, frames, source.delays_us)

    def load_scores(self):
        """Lit et décode d'avance <media_dir>/Scores/<code de la fléchette>/*.gif|*.ppm
        (48e complément), une fois, au démarrage du service. Dossier absent ou illisible :
        journalisé une fois, texte seul ; fichier corrompu : journalisé une fois, exclu du tirage."""
        self.scores = self._preload_tree(C.SCORES_FOLDER, C.DART_FOLDER.match, "scores",
                                         "text only for scores")

    def load_specials(self):
        """Comme load_scores, pour <media_dir>/SpecialsMoves/<dossier> (49e complément) : seuls
        les dossiers qu'une règle de la table peut déclencher sont décodés (17,6 Mo mesurés pour
        les GIF de l'auteur, sous la limite de 50 Mo). Dossier absent : score ordinaire."""
        self.specials = self._preload_tree(C.SPECIALS_FOLDER, C.SPECIAL_FOLDERS.__contains__,
                                           "special moves", "ordinary scores instead")

    def _preload_tree(self, top, wanted, what, fallback):
        result = {}
        if not self.media_dir:
            return result
        root = os.path.join(self.media_dir, top)
        try:
            folders = sorted(os.listdir(root))
        except OSError as error:
            self._log_once(("scores", root), "DMD : %s folder %s unreadable (%s), %s",
                           what, root, error, fallback)
            return result
        loaded = 0
        frames = 0
        for folder in folders:
            path = os.path.join(root, folder)
            if not wanted(folder) or not os.path.isdir(path):
                continue
            try:
                names = sorted(os.listdir(path))
            except OSError as error:
                self._log_once(("scores", path), "DMD : %s folder %s unreadable (%s), %s",
                               what, path, error, fallback)
                continue
            medias = []
            for name in names:
                file = os.path.join(path, name)
                if (name.startswith(".") or os.path.splitext(name)[1].lower() not in MEDIA_EXTENSIONS
                        or not os.path.isfile(file)):
                    continue
                if self.should_stop():          # arrêt demandé : décodage abandonné (50e complément)
                    log.info("DMD : decoding of %s interrupted by the stop request", root)
                    return result
                try:
                    medias.append(self._preload(file))
                except Exception as error:      # le service ne s'arrête jamais pour un média
                    self._corrupt(file, error)
            if medias:
                result[folder] = medias
                loaded += len(medias)
                frames += sum(len(m.frames) for m in medias)
        log.info("DMD : %d %s media loaded from %s (%d folders, %d frames, %.1f MB)", loaded, what,
                 root, len(result), frames, frames * D.PIXELS_BYTES / 1e6)
        return result

    def _cached(self, folder, name):
        """Médias du cache pour un dossier « Scores/<code> » ou « SpecialsMoves/<dossier> »,
        réduits au fichier name s'il est donné ; liste vide sinon."""
        top, _, sub = folder.partition("/")
        if top == C.SCORES_FOLDER:
            choices = self.scores.get(sub) or []
        elif top == C.SPECIALS_FOLDER:
            choices = self.specials.get(sub) or []
        else:
            return []
        if name is not None:
            choices = [m for m in choices if os.path.basename(m.path) == name]
        return choices

    def has_media(self, folder, name=None):
        """media_available du cœur (49e complément) : vrai si le cache a ce média."""
        return bool(self._cached(folder, name))

    def score_media(self, command):
        """ShowMedia d'un score : un média tiré au hasard dans le cache de
        Scores/<code de la fléchette> ou de SpecialsMoves/<dossier> (fichier précis s'il est
        nommé : DEVIL), joué une fois (PPM : 2 s) ; aucun : le texte seul, sans jeton
        (48e et 49e compléments)."""
        choices = self._cached(command.folder, command.name)
        if not choices:
            if command.fallback is not None:
                return self.scene(command.fallback)
            return self.welcome()
        media = self.rng.choice(choices)
        return MediaScene(media, G.Timeline(media.delays_us), command.token,
                          lambda error: self._corrupt(media.path, error))

    def media(self, command):
        """ShowMedia avec un nom de fichier -> MediaScene ; FailedMediaScene si le fichier
        manque ou est corrompu (journalisé une fois par fichier, 48e complément)."""
        path = os.path.join(self.media_dir or "", command.folder, command.name)
        if command.folder == C.ATTRACT_FOLDER:
            min_us, max_us = G.ATTRACT_MIN_US, self.attract_max_us  # 5 s au moins, 60 s au plus
        else:
            min_us, max_us = 0, None                                # un passage
        if path in self.bad_media:
            source = None
        elif not os.path.isfile(path):
            self._log_once(("missing", path), "DMD : media %s missing, no more logged", path)
            source = None
        else:
            try:
                source = self.open_source(path)
            except Exception as error:
                self._corrupt(path, error)
                source = None
        if source is None:
            base = self.scene(command.fallback) if command.fallback is not None else self.welcome()
            return FailedMediaScene(base, command.token)
        return MediaScene(source, G.Timeline(source.delays_us, min_us, max_us), command.token,
                          lambda error: self._corrupt(path, error))

    def scene(self, command):
        """Commande du cœur -> scène."""
        if isinstance(command, C.Welcome):
            return self.fixed_text(command.text)
        if isinstance(command, C.ShowText):
            if command.scroll:
                return self.scrolling_text(command.text, command.scroll, command.passes,
                                           command.color, command.background)
            return self.fixed_text(command.text, command.color, command.background)
        if isinstance(command, C.ShowPattern):
            return self.pattern()
        if isinstance(command, C.ShowMedia):
            if command.folder.partition("/")[0] in (C.SCORES_FOLDER, C.SPECIALS_FOLDER):
                return self.score_media(command)    # cache (score, coup spécial)
            return self.media(command)
        if isinstance(command, C.ShowClock):
            return self.clock()
        raise TypeError("commande inconnue : %r" % (command,))
