# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Délais des GIF, mise à l'échelle et minutage des médias du DMD
(spécification § 9.9, 48e complément, sous-phase SR-c).

Bibliothèque standard seulement : testable partout, sans Pillow. Le décodage
des images reste à Pillow (dmd_render) ; ce module lit les délais dans les
blocs de contrôle graphique (GCE), calcule la taille d'affichage et dit
quelle image doit être visible à un instant donné.

    info = read_gif(chemin)                 -> GifInfo (délais en 1/100 s, taille)
    fit_size(largeur, hauteur)              -> (largeur, hauteur) affichées
    t = Timeline(délais_us, min_us, max_us)
    t.total_us, t.index_at(écoulé_us)
"""

import bisect
from dataclasses import dataclass
from typing import Optional, Tuple

WIDTH = 128
HEIGHT = 32
DEFAULT_DELAY_CS = 10           # délai 0, 1 ou absent : 10/100 s (convention des navigateurs)
CS_US = 10000                   # 1/100 s en microsecondes

# Durée d'un GIF de l'Attract Mode (48e complément) : un passage, coupé à 60 s
# (attract_gif_max_s, 50e complément ; 30 s jusque-là), rejoué tant que le total fait
# moins de 5 s
ATTRACT_MIN_US = 5 * 1000000
ATTRACT_MAX_US = 60 * 1000000


@dataclass(frozen=True)
class GifInfo:
    width: int                  # écran logique
    height: int
    raw_delays: Tuple[int, ...]  # délais lus (1/100 s), 0 pour une image sans GCE
    netscape_loops: Optional[int] = None   # lu pour information, ignoré (48e complément)

    @property
    def delays(self):
        """Délais retenus (1/100 s) : 0 ou 1 -> 10, les autres tels quels."""
        return tuple(normalize_delay(d) for d in self.raw_delays)

    @property
    def delays_us(self):
        return tuple(d * CS_US for d in self.delays)


def normalize_delay(delay_cs):
    return DEFAULT_DELAY_CS if delay_cs <= 1 else delay_cs


class _Reader:
    def __init__(self, f):
        self.f = f

    def read(self, n):
        data = self.f.read(n)
        if len(data) != n:
            raise EOFError("GIF tronqué")
        return data

    def sub_blocks(self):
        """Lit une suite de sous-blocs jusqu'au bloc de taille 0 ; rend leur contenu."""
        chunks = []
        while True:
            size = self.read(1)[0]
            if size == 0:
                return b"".join(chunks)
            chunks.append(self.read(size))

    def skip_sub_blocks(self):
        while True:
            size = self.read(1)[0]
            if size == 0:
                return
            self.read(size)


def read_gif_file(f):
    """Lit un GIF depuis un fichier ouvert en binaire. ValueError s'il n'est pas lisible
    ou s'il n'a aucune image ; un fichier tronqué rend les images complètes lues."""
    r = _Reader(f)
    try:
        header = r.read(13)
    except EOFError:
        raise ValueError("pas un GIF (trop court)")
    if header[:6] not in (b"GIF87a", b"GIF89a"):
        raise ValueError("pas un GIF (signature %r)" % header[:6])
    width = header[6] | header[7] << 8
    height = header[8] | header[9] << 8
    flags = header[10]
    delays = []
    loops = None
    pending = None                  # délai du GCE en attente de son image
    try:
        if flags & 0x80:
            r.read(3 << ((flags & 7) + 1))          # table de couleurs globale
        while True:
            intro = r.read(1)[0]
            if intro == 0x3B:                       # fin du fichier
                break
            if intro == 0x21:                       # extension
                label = r.read(1)[0]
                if label == 0xF9:                   # contrôle graphique
                    data = r.sub_blocks()
                    if len(data) < 4:
                        raise ValueError("bloc GCE trop court")
                    pending = data[1] | data[2] << 8
                elif label == 0xFF:                 # application (NETSCAPE2.0 : boucles)
                    ident = r.sub_blocks()
                    if ident[:11] == b"NETSCAPE2.0" and len(ident) >= 14 and ident[11] == 1:
                        loops = ident[12] | ident[13] << 8
                else:
                    r.skip_sub_blocks()
                continue
            if intro == 0x2C:                       # image
                desc = r.read(9)
                if desc[8] & 0x80:
                    r.read(3 << ((desc[8] & 7) + 1))    # table de couleurs locale
                r.read(1)                           # taille minimale des codes LZW
                r.skip_sub_blocks()
                delays.append(pending or 0)         # image sans GCE : délai 0
                pending = None
                continue
            raise ValueError("bloc inconnu 0x%02X" % intro)
    except EOFError:
        pass                                        # tronqué : images complètes gardées
    if not delays:
        raise ValueError("aucune image")
    return GifInfo(width, height, tuple(delays), loops)


def read_gif(path):
    """Lit un GIF sur disque (voir read_gif_file). OSError si le fichier ne s'ouvre pas."""
    with open(path, "rb") as f:
        return read_gif_file(f)


def fit_size(width, height):
    """Taille affichée (48e complément) : inchangée si l'image tient dans 128 × 32 ;
    sinon réduite proportionnellement, facteur min(128 / largeur, 32 / hauteur)."""
    if width <= 0 or height <= 0:
        raise ValueError("taille %dx%d" % (width, height))
    if width <= WIDTH and height <= HEIGHT:
        return width, height
    if WIDTH * height <= HEIGHT * width:             # la largeur limite
        return WIDTH, min(HEIGHT, max(1, (height * WIDTH + width // 2) // width))
    return min(WIDTH, max(1, (width * HEIGHT + height // 2) // height)), HEIGHT


def offset(size):
    """Position du coin haut gauche pour centrer une image de cette taille."""
    return (WIDTH - size[0]) // 2, (HEIGHT - size[1]) // 2


class Timeline:
    """Minutage d'un média : délais de ses images (µs), passages répétés tant que le total
    fait moins de min_us, total coupé à max_us (None : pas de limite)."""

    def __init__(self, delays_us, min_us=0, max_us=None):
        if not delays_us or any(d <= 0 for d in delays_us):
            raise ValueError("délais invalides")
        self.delays_us = tuple(delays_us)
        self.starts = []
        t = 0
        for d in self.delays_us:
            self.starts.append(t)
            t += d
        self.pass_us = t
        passes = max(1, -(-min_us // t)) if min_us > 0 else 1
        total = passes * t
        if max_us is not None and total > max_us:
            total = max_us
        self.passes = passes
        self.total_us = total

    def done(self, elapsed_us):
        return elapsed_us >= self.total_us

    def position(self, elapsed_us):
        """(passage, index de l'image visible) à cet instant (dernière image après la fin)."""
        if elapsed_us >= self.total_us:
            elapsed_us = self.total_us - 1
        elapsed_us = max(0, elapsed_us)
        n, pos = divmod(elapsed_us, self.pass_us)
        return n, bisect.bisect_right(self.starts, pos) - 1

    def index_at(self, elapsed_us):
        return self.position(elapsed_us)[1]
