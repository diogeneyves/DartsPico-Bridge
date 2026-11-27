# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Protocole binaire Pi <-> Interstate 75 W (spécification § 6).

Code côté Pi (CPython). Depuis la v0.3.5, la carte est en C++ (§ 8) et
n'exécute pas ce module : il sert de référence au firmware et produit ses
vecteurs de test. Il garde les règles de shared/ (pas de f-string, pas
d'annotation, aucune entrée-sortie), vérifiées par test_shared_subset.

Trame : AA 55 | type u8 | longueur u16 | données | CRC-32 u32.
Entiers petit-boutistes ; CRC-32 « ZIP » (binascii.crc32, valeur initiale 0)
calculé sur type + longueur + données.
"""

import binascii
import struct

PROTO_VERSION = 1
WIDTH = 128
HEIGHT = 32
PIXELS_BYTES = WIDTH * HEIGHT * 2          # RGB565
SYNC = b"\xAA\x55"
HEADER_LEN = 5                              # sync + type + longueur
CRC_LEN = 4
MAX_PAYLOAD = 2 + PIXELS_BYTES              # 8 194 (FRAME)
PING_MAX = 64
ERROR_TEXT_MAX = 128
TARGET_FPS = 15                             # cadence cible (§ 6.3, v0.3.4)

HELLO_REQ = 0x01
FRAME = 0x02
BRIGHTNESS = 0x03
CLEAR = 0x04
MODE = 0x05
PING = 0x06
DELTA = 0x07
STATS_REQ = 0x08                            # compteurs de la carte (§ 6.2, 36e complément)
HELLO = 0x81
PONG = 0x86
STATS = 0x88
TELEMETRY = 0x90
ERROR = 0xE0
REPLY = 0x80

MODE_PI = 0
MODE_LOCAL = 1

# Codes ERROR (§ 6.2).
ERR_UNKNOWN_TYPE = 0x01
ERR_LENGTH = 0x02
ERR_RANGE = 0x03
ERR_DELTA = 0x04                            # contenu de DELTA invalide (§ 6.4.1)
ERR_INTERNAL = 0x10

# DELTA (§ 6.4.1) : 64 tuiles de 8 × 8, tuile i = ty × 16 + tx.
TILE = 8
TILES_X = WIDTH // TILE                     # 16
TILES_Y = HEIGHT // TILE                    # 4
TILE_COUNT = TILES_X * TILES_Y              # 64
TILE_PIXELS = TILE * TILE                   # 64
TILE_BYTES = TILE_PIXELS * 2                # 128
DELTA_HEADER = 12                           # numéro, base, masque u64
TILE_RAW = 0
TILE_RLE = 1

# STATS (§ 6.2) : 11 × u32, u16, u8, petit-boutistes, 47 octets.
STATS_FIELDS = (
    "uptime_ms", "frames_rx", "deltas_rx", "bytes_rx", "skipped", "crc_errors",
    "length_errors", "deltas_ignored", "swaps", "replaced", "loop_max_us",
    "refresh_hz", "flags",
)
_STATS_FORMAT = "<11IHB"
STATS_LEN = struct.calcsize(_STATS_FORMAT)  # 47
STATS_FLAG_WATCHDOG = 0x01                  # dernier démarrage causé par le chien de garde

# Longueur maximale (et exacte quand min == max) par type connu.
_LENGTHS = {
    HELLO_REQ: (0, 0),
    HELLO: (10, 10),
    FRAME: (MAX_PAYLOAD, MAX_PAYLOAD),
    BRIGHTNESS: (1, 1),
    CLEAR: (0, 0),
    MODE: (1, 1),
    PING: (0, PING_MAX),
    DELTA: (DELTA_HEADER, MAX_PAYLOAD),
    STATS_REQ: (0, 0),
    PONG: (0, PING_MAX),
    STATS: (STATS_LEN, STATS_LEN),
    TELEMETRY: (9, 9),
    ERROR: (1, 1 + ERROR_TEXT_MAX),
}


class PayloadError(Exception):
    """Données d'une trame invalides pour son type ; code : un des ERR_*."""

    def __init__(self, code, detail=""):
        Exception.__init__(self, code)
        self.code = code
        self.detail = detail


def crc32(data, crc=0):
    return binascii.crc32(data, crc) & 0xFFFFFFFF


def max_length(ftype):
    """Longueur de données maximale acceptée par le récepteur pour ce type."""
    r = _LENGTHS.get(ftype)
    return MAX_PAYLOAD if r is None else r[1]


def check_length(ftype, n):
    """Lève PayloadError si n n'est pas une longueur valide pour ce type connu."""
    r = _LENGTHS.get(ftype)
    if r is None:
        raise PayloadError(ERR_UNKNOWN_TYPE)
    if n < r[0] or n > r[1]:
        raise PayloadError(ERR_LENGTH)


def encode(ftype, payload=b""):
    """Construit une trame complète (bytes). ValueError si type ou longueur invalide."""
    if ftype < 0 or ftype > 255:
        raise ValueError("type hors plage")
    n = len(payload)
    if n > max_length(ftype):
        raise ValueError("données trop longues pour le type 0x%02X" % ftype)
    head = struct.pack("<BH", ftype, n)
    crc = crc32(payload, crc32(head))
    return SYNC + head + bytes(payload) + struct.pack("<I", crc)


# ------------------------------------------------------- contenu par type

def hello_payload(width, height, fw_version, mv):
    return struct.pack("<BHHBBBH", PROTO_VERSION, width, height,
                       fw_version[0], fw_version[1], fw_version[2], mv)


def parse_hello(p):
    """-> (proto, largeur, hauteur, (x, y, z), mV)."""
    check_length(HELLO, len(p))
    v = struct.unpack("<BHHBBBH", p)
    return (v[0], v[1], v[2], (v[3], v[4], v[5]), v[6])


def frame_payload(number, pixels):
    """number : u16 (boucle) ; pixels : 8 192 octets RGB565 petit-boutiste."""
    if len(pixels) != PIXELS_BYTES:
        raise ValueError("image de %d octets au lieu de %d" % (len(pixels), PIXELS_BYTES))
    return struct.pack("<H", number & 0xFFFF) + bytes(pixels)


def parse_frame(p):
    """-> (numéro, memoryview des pixels), sans copie."""
    check_length(FRAME, len(p))
    return (p[0] | (p[1] << 8), memoryview(p)[2:])


# ------------------------------------------------------------ DELTA (§ 6.4.1)

def tile_bytes(image, i):
    """Les 128 octets de la tuile i d'une image de 8 192 octets, ligne par ligne."""
    row = WIDTH * 2
    start = (i // TILES_X) * TILE * row + (i % TILES_X) * TILE * 2
    out = bytearray(TILE_BYTES)
    for y in range(TILE):
        a = start + y * row
        out[y * TILE * 2:(y + 1) * TILE * 2] = image[a:a + TILE * 2]
    return bytes(out)


def put_tile(image, i, tile):
    """Écrit les 128 octets d'une tuile dans une image (bytearray de 8 192 octets)."""
    row = WIDTH * 2
    start = (i // TILES_X) * TILE * row + (i % TILES_X) * TILE * 2
    for y in range(TILE):
        a = start + y * row
        image[a:a + TILE * 2] = tile[y * TILE * 2:(y + 1) * TILE * 2]


def rle_tile(tile):
    """Plages (compte u8, couleur u16) d'une tuile de 128 octets."""
    out = bytearray()
    k = 0
    while k < TILE_PIXELS:
        c0 = tile[2 * k]
        c1 = tile[2 * k + 1]
        n = 1
        while (k + n < TILE_PIXELS and tile[2 * (k + n)] == c0
               and tile[2 * (k + n) + 1] == c1):
            n += 1
        out.append(n)
        out.append(c0)
        out.append(c1)
        k += n
    return bytes(out)


def encode_tile(tile):
    """Mode puis données, le plus court ; RLE seulement s'il fait moins de 128 octets."""
    r = rle_tile(tile)
    if len(r) < TILE_BYTES:
        return bytes([TILE_RLE]) + r
    return bytes([TILE_RAW]) + bytes(tile)


def delta_payload(number, base, old, new):
    """Données d'un DELTA de l'image old (numéro base) vers new (numéro number).

    Renvoie None si le DELTA atteindrait 8 194 octets ou plus : l'appelant
    envoie alors un FRAME (§ 6.4.1, règles d'émission).
    """
    if len(old) != PIXELS_BYTES or len(new) != PIXELS_BYTES:
        raise ValueError("images de %d octets attendues" % PIXELS_BYTES)
    mask = 0
    parts = []
    size = DELTA_HEADER
    for i in range(TILE_COUNT):
        t = tile_bytes(new, i)
        if t != tile_bytes(old, i):
            mask |= 1 << i
            e = encode_tile(t)
            size += len(e)
            if size >= MAX_PAYLOAD:
                return None
            parts.append(e)
    return struct.pack("<HHQ", number & 0xFFFF, base & 0xFFFF, mask) + b"".join(parts)


def delta_header(p):
    """-> (numéro, base, masque) ; la longueur doit être valide pour DELTA."""
    check_length(DELTA, len(p))
    return struct.unpack("<HHQ", bytes(p[:DELTA_HEADER]))


def parse_delta(p):
    """-> (numéro, base, [(i, 128 octets), ...]).

    PayloadError(ERR_LENGTH) sous 12 octets ou au-delà de 8 194 ;
    PayloadError(ERR_DELTA) si le contenu ne suit pas le § 6.4.1 (mode
    inconnu, compte nul, somme différente de 64, données trop courtes ou en trop).
    """
    number, base, mask = delta_header(p)
    tiles = []
    pos = DELTA_HEADER
    n = len(p)
    for i in range(TILE_COUNT):
        if not (mask >> i) & 1:
            continue
        if pos >= n:
            raise PayloadError(ERR_DELTA, "tuile %d absente" % i)
        mode = p[pos]
        pos += 1
        if mode == TILE_RAW:
            if pos + TILE_BYTES > n:
                raise PayloadError(ERR_DELTA, "tuile %d tronquée" % i)
            tiles.append((i, bytes(p[pos:pos + TILE_BYTES])))
            pos += TILE_BYTES
        elif mode == TILE_RLE:
            t = bytearray(TILE_BYTES)
            k = 0
            while k < TILE_PIXELS:
                if pos + 3 > n:
                    raise PayloadError(ERR_DELTA, "tuile %d tronquée" % i)
                count = p[pos]
                if count == 0 or k + count > TILE_PIXELS:
                    raise PayloadError(ERR_DELTA, "tuile %d : compte %d" % (i, count))
                c0 = p[pos + 1]
                c1 = p[pos + 2]
                pos += 3
                for j in range(k, k + count):
                    t[2 * j] = c0
                    t[2 * j + 1] = c1
                k += count
            tiles.append((i, bytes(t)))
        else:
            raise PayloadError(ERR_DELTA, "tuile %d : mode %d" % (i, mode))
    if pos != n:
        raise PayloadError(ERR_DELTA, "%d octets en trop" % (n - pos))
    return (number, base, tiles)


def parse_u8(ftype, p, hi=255):
    check_length(ftype, len(p))
    if p[0] > hi:
        raise PayloadError(ERR_RANGE)
    return p[0]


def telemetry_payload(mv, temp_dc, fps, lost):
    """mv : tension en mV ; temp_dc : °C × 10 (signé) ; fps : u8 ; lost : u32."""
    return struct.pack("<HhBI", mv, temp_dc, min(fps, 255), lost & 0xFFFFFFFF)


def parse_telemetry(p):
    """-> (mV, température °C × 10, images par seconde, images perdues)."""
    check_length(TELEMETRY, len(p))
    return struct.unpack("<HhBI", p)


def stats_payload(**fields):
    """Données de STATS ; champs de STATS_FIELDS, absents = 0. Les u32 bouclent."""
    unknown = set(fields) - set(STATS_FIELDS)
    if unknown:
        raise ValueError("champs STATS inconnus : %s" % ", ".join(sorted(unknown)))
    v = [fields.get(name, 0) for name in STATS_FIELDS]
    v[:11] = [x & 0xFFFFFFFF for x in v[:11]]
    v[11] = min(v[11], 0xFFFF)
    v[12] &= 0xFF
    return struct.pack(_STATS_FORMAT, *v)


def parse_stats(p):
    """-> dictionnaire des champs de STATS_FIELDS."""
    check_length(STATS, len(p))
    return dict(zip(STATS_FIELDS, struct.unpack(_STATS_FORMAT, p)))


def error_payload(code, text=""):
    t = text.encode()[:ERROR_TEXT_MAX]
    return bytes([code & 0xFF]) + t


def parse_error(p):
    """-> (code, texte)."""
    check_length(ERROR, len(p))
    try:
        text = bytes(p[1:]).decode()
    except UnicodeError:
        text = "?"
    return (p[0], text)


def rgb565(r, g, b):
    """Couleur 8 bits par composante -> valeur RGB565."""
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


# --------------------------------------------------------------- récepteur

class Decoder:
    """Récepteur de trames avec resynchronisation (§ 6.1, B2).

    feed(data) renvoie la liste des trames reçues, sous forme de couples
    (type, données en bytes). Compteurs : crc_errors, length_errors (longueur
    annoncée au-delà du maximum du type), skipped (octets ignorés hors trame).
    Sur une fausse en-tête, la recherche reprend à l'octet qui suit son 0xAA.
    Une fausse en-tête qui annonce une longueur plausible retient les trames
    suivantes jusqu'à ce que cette longueur soit reçue et que son CRC échoue
    (au plus 8 203 octets, soit une image) ; elles sont alors toutes rendues.
    """

    def __init__(self):
        self._buf = bytearray()
        self.crc_errors = 0
        self.length_errors = 0
        self.skipped = 0
        self.frames = 0

    def reset(self):
        """Oublie une trame partielle (par exemple après une reconnexion)."""
        self.skipped += len(self._buf)
        self._buf = bytearray()

    def feed(self, data):
        buf = self._buf
        buf.extend(data)
        out = []
        pos = 0
        n = len(buf)
        while True:
            i = buf.find(SYNC, pos)
            if i < 0:
                # Garder un 0xAA final : il peut commencer la synchronisation.
                keep = 1 if n > pos and buf[n - 1] == 0xAA else 0
                self.skipped += n - pos - keep
                pos = n - keep
                break
            self.skipped += i - pos
            pos = i
            if n - i < HEADER_LEN:
                break
            ftype = buf[i + 2]
            length = buf[i + 3] | (buf[i + 4] << 8)
            if length > max_length(ftype):
                self.length_errors += 1
                pos = i + 1
                self.skipped += 1
                continue
            end = i + HEADER_LEN + length + CRC_LEN
            if end > n:
                break
            body_end = i + HEADER_LEN + length
            got = (buf[body_end] | (buf[body_end + 1] << 8) |
                   (buf[body_end + 2] << 16) | (buf[body_end + 3] << 24))
            if crc32(memoryview(buf)[i + 2:body_end]) != got:
                self.crc_errors += 1
                pos = i + 1
                self.skipped += 1
                continue
            out.append((ftype, bytes(buf[i + HEADER_LEN:body_end])))
            self.frames += 1
            pos = end
        self._buf = buf[pos:]
        return out
