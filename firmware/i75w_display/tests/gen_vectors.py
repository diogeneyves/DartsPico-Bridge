# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Vecteurs de test de la bibliothèque C++ du DMD (spécification § 8, 33e complément).

Produits par shared/dmd_proto.py et tools/fake_i75w.py, sans horloge :
    vectors/dmd_lib.txt   CRC-32, récepteur (§ 6.1), décodage des DELTA (§ 6.4.1)
    vectors/dmd_card.txt  scénarios de la carte (§ 6.2 à § 6.4), rejoués dans fake_i75w

Utilisation :
    python3 firmware/i75w_display/tests/gen_vectors.py          réécrit les deux fichiers
    python3 firmware/i75w_display/tests/gen_vectors.py --check  code 1 s'ils ne sont pas à jour

Le résultat ne dépend que de ce script et des deux modules : générateur
pseudo-aléatoire maison (xorshift32), aucun appel au module random.
"""

import os
import struct
import sys
import unicodedata

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for _sub in ("shared", "tools"):
    _p = os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import dmd_proto as D  # noqa: E402
from fake_i75w import FakeI75W  # noqa: E402

VECTORS_DIR = os.path.join(_HERE, "vectors")
LIB_FILE = "dmd_lib.txt"
CARD_FILE = "dmd_card.txt"


# ------------------------------------------------------------- utilitaires

class Rng:
    """xorshift32 : même suite sur toutes les versions de Python."""

    def __init__(self, seed):
        self.s = seed & 0xFFFFFFFF or 1

    def u32(self):
        x = self.s
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        self.s = x
        return x

    def below(self, n):
        return self.u32() % n

    def bytes(self, n):
        return bytes(self.u32() & 0xFF for _ in range(n))


def hx(b):
    """Octets en hexadécimal minuscule ; « - » pour une suite vide."""
    return bytes(b).hex() if len(b) else "-"


def ascii_text(s):
    """Commentaires des fichiers en ASCII : accents retirés, guillemets droits."""
    s = s.replace("\u00ab ", '"').replace(" \u00bb", '"').replace("\u00d7", "x").replace("\u00a7 ", "section ")
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def crc_hex(b):
    return "%08x" % D.crc32(bytes(b))


def raw_frame(ftype, payload):
    """Trame avec un CRC juste, sans contrôle de longueur (pour les cas invalides)."""
    head = struct.pack("<BH", ftype, len(payload))
    return D.SYNC + head + bytes(payload) + struct.pack("<I", D.crc32(head + bytes(payload)))


def bad_crc(frame):
    """Même trame, dernier octet du CRC inversé."""
    return frame[:-1] + bytes([frame[-1] ^ 0xFF])


def set_px(img, x, y, color):
    a = (y * D.WIDTH + x) * 2
    img[a] = color & 0xFF
    img[a + 1] = color >> 8


def solid_tile(color):
    return bytes([color & 0xFF, color >> 8]) * D.TILE_PIXELS


def delta_raw(number, base, tiles):
    """DELTA écrit à la main : tiles = [(i, mode, données)], dans l'ordre donné."""
    mask = 0
    body = b""
    for i, mode, data in tiles:
        mask |= 1 << i
        body += bytes([mode]) + bytes(data)
    return struct.pack("<HHQ", number & 0xFFFF, base & 0xFFFF, mask) + body


def frame(number, img):
    return D.encode(D.FRAME, D.frame_payload(number, img))


def delta(number, base, payload_or_old, new=None):
    if new is None:
        return D.encode(D.DELTA, payload_or_old)
    p = D.delta_payload(number, base, payload_or_old, new)
    if p is None:
        raise ValueError("DELTA trop long pour un vecteur")
    return D.encode(D.DELTA, p)


# ------------------------------------------------------------------ images

def make_images():
    rng = Rng(0x75D3)
    imgs = {}
    imgs["black"] = bytes(D.PIXELS_BYTES)
    imgs["noise"] = rng.bytes(D.PIXELS_BYTES)

    # Texte de deux couleurs : plages de longueur 1 à 12 sur les lignes 8 à 23.
    fg = D.rgb565(255, 200, 0)
    bg = D.rgb565(0, 0, 32)
    img = bytearray(D.PIXELS_BYTES)
    for y in range(D.HEIGHT):
        for x in range(D.WIDTH):
            set_px(img, x, y, bg)
    for y in range(8, 24):
        x = 0
        on = False
        while x < D.WIDTH:
            n = 1 + rng.below(12)
            for k in range(x, min(x + n, D.WIDTH)):
                if on:
                    set_px(img, k, y, fg)
            x += n
            on = not on
    imgs["text"] = bytes(img)

    # Même texte, quelques tuiles changées (score).
    img2 = bytearray(imgs["text"])
    for (x0, y0) in ((16, 8), (24, 8), (96, 16), (120, 24)):
        for y in range(y0, y0 + 8):
            for x in range(x0, x0 + 8):
                set_px(img2, x, y, D.rgb565(255, 0, 0) if (x + y) & 1 else fg)
    imgs["text2"] = bytes(img2)

    img = bytearray(D.PIXELS_BYTES)
    for y in range(D.HEIGHT):
        for x in range(D.WIDTH):
            set_px(img, x, y, D.rgb565(x * 2, y * 8, (x + y) * 2))
    imgs["gradient"] = bytes(img)

    # Pixels qui forment partout la synchronisation AA 55.
    imgs["sync"] = b"\xAA\x55" * (D.PIXELS_BYTES // 2)
    return imgs


HEADER_COMMON = """\
# Format commun (spécification § 8, 33e complément) : texte ASCII, fins de ligne LF.
# Une ligne = un enregistrement : mot-clé puis champs séparés par une espace.
# Lignes vides et lignes commençant par « # » : ignorées.
# Octets en hexadécimal minuscule, sans séparateur ; « - » = aucun octet.
# CRC-32 « ZIP » (zlib), 8 chiffres hexadécimaux ; nombres sans 0x : en décimal,
# sauf type, code et masque, en hexadécimal comme indiqué.
"""

LIB_HEADER = HEADER_COMMON + """\
#
# dmd_lib.txt : bibliothèque (CRC-32, récepteur, DELTA). Généré par gen_vectors.py.
#
# crc <octets> <crc32>
#     CRC-32 des octets (valeur initiale 0).
# image <nom> <8 192 octets>
#     Image RGB565 nommée, base des cas « delta ».
# decoder <nom>
#     Nouveau récepteur (§ 6.1), vide. Suivi de :
#   feed <octets>
#       Un appel de réception avec ces octets.
#   frame <type, 2 hex> <longueur> <crc32 des données>
#       Trames rendues par le « feed » qui précède, dans l'ordre.
#   counters <crc_errors> <length_errors> <skipped> <frames> <pending>
#       Fin du cas : compteurs de Decoder (shared/dmd_proto.py), exactement :
#       CRC faux, longueurs au-delà du maximum du type, octets sautés hors trame,
#       trames rendues, octets gardés en attente (dont un 0xAA final).
# delta <nom> <image de base> <données du DELTA>
#     Décodage des données (§ 6.4.1), puis application à une copie de l'image. Suivi de :
#   result ok <numéro> <base> <masque, 16 hex> <crc32 de l'image obtenue>
#   result error <code, 2 hex>       (02 : longueur ; 04 : contenu invalide)
"""

CARD_HEADER = HEADER_COMMON + """\
#
# dmd_card.txt : scénarios de la carte, rejoués dans tools/fake_i75w.py sans horloge
# (ni animation locale après 3 s ni TELEMETRY). Généré par gen_vectors.py.
#
# scenario <nom>
#     Carte neuve, juste démarrée. Suivi de :
#   feed <octets>
#       Octets reçus du Pi en un appel.
#   reply <type, 2 hex> <détail>
#       Trames émises en réponse à ce « feed », dans l'ordre :
#         reply 81 <protocole> <largeur> <hauteur>   HELLO (version et tension non comparées)
#         reply 86 <octets>                          PONG
#         reply e0 <code, 2 hex>                     ERROR (texte non comparé)
#         reply 88 <longueur> <octets 4 à 35>        STATS : longueur (47) et champs frames_rx
#                                                    à swaps ; uptime_ms et champs de la carte
#                                                    (replaced, loop_max_us, refresh_hz, flags)
#                                                    non comparés (38e complément)
#   state <affiché> <arrière> <numéro> <perdues> <échanges> <delta_ignorés> <mode> <attend_frame> <luminosité>
#       État après ce « feed » : CRC-32 du tampon affiché et du tampon arrière,
#       numéro de l'image affichée (« - » : aucune), images perdues (B5),
#       échanges de tampons, DELTA ignorés, mode (0 ou 1), 1 si les DELTA sont
#       ignorés jusqu'au prochain FRAME, luminosité (0 à 255).
"""


# ---------------------------------------------------------- dmd_lib.txt

def _crc_section(out, imgs):
    rng = Rng(0xC0C0)
    out.append("# --- CRC-32")
    cases = [
        b"",
        b"123456789",
        b"The quick brown fox jumps over the lazy dog",
        b"\x00",
        b"\xff",
        b"\x00" * 4,
        b"\xff" * 32,
        struct.pack("<BH", D.CLEAR, 0),
        rng.bytes(1000),
        D.frame_payload(1, imgs["noise"]),
    ]
    for c in cases:
        out.append("crc %s %s" % (hx(c), crc_hex(c)))


def _decoder_case(out, name, chunks):
    dec = D.Decoder()
    out.append("decoder %s" % name)
    for c in chunks:
        out.append("feed %s" % hx(c))
        for ftype, payload in dec.feed(c):
            out.append("frame %02x %d %s" % (ftype, len(payload), crc_hex(payload)))
    out.append("counters %d %d %d %d %d" % (dec.crc_errors, dec.length_errors, dec.skipped,
                                           dec.frames, len(dec._buf)))


def _decoder_section(out, imgs):
    rng = Rng(0xDEC0)
    out.append("# --- récepteur")
    hello_req = D.encode(D.HELLO_REQ)
    ping5 = D.encode(D.PING, b"\x01\x02\xAA\x55\x03")
    every = [
        hello_req,
        frame(1, imgs["noise"]),
        D.encode(D.BRIGHTNESS, b"\x80"),
        D.encode(D.CLEAR),
        D.encode(D.MODE, b"\x00"),
        D.encode(D.PING, bytes(range(64))),
        delta(2, 1, imgs["black"], imgs["text"]),
        D.encode(D.HELLO, D.hello_payload(128, 32, (1, 2, 3), 5000)),
        D.encode(D.PONG, b"ok"),
        D.encode(D.TELEMETRY, D.telemetry_payload(5000, 350, 15, 0)),
        D.encode(D.ERROR, D.error_payload(D.ERR_DELTA, "x")),
    ]
    _decoder_case(out, "single_hello_req", [hello_req])
    _decoder_case(out, "all_types_one_feed", [b"".join(every)])
    _decoder_case(out, "byte_by_byte", [ping5[k:k + 1] for k in range(len(ping5))])
    f = frame(7, imgs["gradient"])
    _decoder_case(out, "split_frame", [f[:1], f[1:4000], f[4000:]])
    _decoder_case(out, "garbage_before", [rng.bytes(37) + hello_req + b"\x00\x55\xAA\x00" + ping5])
    _decoder_case(out, "sync_in_pixels", [frame(3, imgs["sync"]) + ping5])
    _decoder_case(out, "trailing_aa_pending", [b"\x01\x02\xAA"])
    _decoder_case(out, "trailing_aa_completed", [b"\x01\x02" + ping5[:1], ping5[1:]])
    _decoder_case(out, "double_aa", [b"\xAA" + ping5])
    _decoder_case(out, "crc_error_then_frame", [bad_crc(ping5) + hello_req])
    bad_pix = bytearray(frame(4, imgs["text"]))
    bad_pix[100] ^= 0x01
    _decoder_case(out, "crc_error_in_pixels", [bytes(bad_pix) + ping5])
    _decoder_case(out, "length_over_type_max",
                  [b"\xAA\x55\x03\x02\x00" + D.encode(D.BRIGHTNESS, b"\x10")])
    _decoder_case(out, "length_over_8194_unknown_type", [b"\xAA\x55\x42\x03\x20" + hello_req])
    _decoder_case(out, "ping_65", [raw_frame(D.PING, bytes(65)) + hello_req])
    _decoder_case(out, "delta_8194", [raw_frame(D.DELTA, rng.bytes(D.MAX_PAYLOAD)) + hello_req])
    _decoder_case(out, "delta_8195", [raw_frame(D.DELTA, rng.bytes(D.MAX_PAYLOAD + 1)) + hello_req])
    _decoder_case(out, "unknown_type_short", [raw_frame(0x42, b"\x01\x02\x03")])
    # Fausse en-tête de longueur plausible (PING, 40 octets) : les trames qui suivent
    # sont retenues jusqu'à l'échec de son CRC, puis toutes rendues.
    behind = hello_req + D.encode(D.BRIGHTNESS, b"\x20") + ping5 + hello_req + ping5
    _decoder_case(out, "false_header_plausible",
                  [b"\xAA\x55\x06\x28\x00" + behind[:10], behind[10:]])
    _decoder_case(out, "partial_at_end", [ping5 + f[:3000]])
    _decoder_case(out, "empty_feed", [b"", hello_req, b""])


def _delta_case(out, imgs, name, base_name, payload):
    out.append("delta %s %s %s" % (name, base_name, hx(payload)))
    try:
        number, base, mask = D.delta_header(payload)
        _n, _b, tiles = D.parse_delta(payload)
    except D.PayloadError as e:
        out.append("result error %02x" % e.code)
        return
    img = bytearray(imgs[base_name])
    for i, t in tiles:
        D.put_tile(img, i, t)
    out.append("result ok %d %d %016x %s" % (number, base, mask, crc_hex(img)))


def _delta_section(out, imgs):
    rng = Rng(0xDE17)
    out.append("# --- DELTA")
    for n in ("black", "noise", "text", "text2", "gradient"):
        out.append("image %s %s" % (n, hx(imgs[n])))
    red = D.rgb565(255, 0, 0)
    noise_tile = D.tile_bytes(imgs["noise"], 5)
    ok = [
        ("raw_tile_0", "black", delta_raw(1, 0, [(0, 0, noise_tile)])),
        ("raw_tile_63", "black", delta_raw(2, 1, [(63, 0, noise_tile)])),
        ("rle_one_run", "noise", delta_raw(3, 2, [(17, 1, b"\x40" + struct.pack("<H", red))])),
        ("rle_64_runs", "black",
         delta_raw(4, 3, [(30, 1, b"".join(b"\x01" + struct.pack("<H", k * 997 & 0xFFFF)
                                           for k in range(64)))])),
        ("rle_runs_1_63", "text", delta_raw(5, 4, [(1, 1, b"\x01\x00\xf8\x3f\xe0\x07")])),
        ("empty_mask", "text", delta_raw(6, 5, [])),
        ("number_wrap", "black", delta_raw(0, 0xFFFF, [(40, 0, noise_tile)])),
        ("raw_63_tiles", "black",
         delta_raw(8, 7, [(i, 0, D.tile_bytes(imgs["noise"], i)) for i in range(63)])),
        ("rle_64_tiles", "noise",
         delta_raw(9, 8, [(i, 1, b"\x20" + struct.pack("<H", i) + b"\x20" + struct.pack("<H", red))
                          for i in range(64)])),
        ("encoder_black_to_text", "black", D.delta_payload(10, 9, imgs["black"], imgs["text"])),
        ("encoder_text_to_text2", "text", D.delta_payload(11, 10, imgs["text"], imgs["text2"])),
        ("encoder_text_to_noise_tiles", "text",
         D.delta_payload(12, 11, imgs["text"],
                         _with_tiles(imgs["text"], imgs["noise"], (0, 15, 16, 47, 48, 63)))),
    ]
    for name, base_name, p in ok:
        assert p is not None, name
        _delta_case(out, imgs, name, base_name, p)
    good = delta_raw(20, 19, [(2, 0, noise_tile)])
    bad = [
        ("too_short_11", "black", good[:11]),
        ("too_long_8195", "black", rng.bytes(D.MAX_PAYLOAD + 1)),
        ("mode_2", "black", delta_raw(20, 19, [(2, 2, noise_tile)])),
        ("mode_ff", "black", delta_raw(20, 19, [(2, 0xFF, b"")])),
        ("rle_count_zero", "black", delta_raw(20, 19, [(2, 1, b"\x00\x00\x00" + b"\x40\x00\x00")])),
        ("rle_sum_over_64", "black", delta_raw(20, 19, [(2, 1, b"\x3c\x00\x00\x05\x00\x00")])),
        ("rle_count_65", "black", delta_raw(20, 19, [(2, 1, b"\x41\x00\x00")])),
        ("rle_sum_under_64_end", "black", delta_raw(20, 19, [(2, 1, b"\x20\x00\x00")])),
        ("rle_run_truncated", "black", delta_raw(20, 19, [(2, 1, b"\x20\x00\x00\x20\x00")])),
        ("raw_truncated", "black", delta_raw(20, 19, [(2, 0, noise_tile[:100])])),
        ("extra_byte", "black", good + b"\x00"),
        ("extra_byte_empty_mask", "black", delta_raw(20, 19, []) + b"\x00"),
        ("mask_bit_no_data", "black", struct.pack("<HHQ", 20, 19, 1 << 9)),
        ("second_tile_missing", "black", struct.pack("<HHQ", 20, 19, (1 << 2) | (1 << 3))
         + b"\x00" + noise_tile),
    ]
    for name, base_name, p in bad:
        _delta_case(out, imgs, name, base_name, p)


def _with_tiles(img, src, tiles):
    out = bytearray(img)
    for i in tiles:
        D.put_tile(out, i, D.tile_bytes(src, i))
    return bytes(out)


def build_lib():
    imgs = make_images()
    out = ascii_text(LIB_HEADER).rstrip("\n").split("\n")
    _crc_section(out, imgs)
    _decoder_section(out, imgs)
    _delta_section(out, imgs)
    return ascii_text("\n".join(out) + "\n")


# --------------------------------------------------------- dmd_card.txt

class _Scenario:
    def __init__(self, out, name):
        self.out = out
        self.card = FakeI75W(clock=lambda: 0)
        self.rx = D.Decoder()
        out.append("scenario %s" % name)

    def feed(self, data):
        c = self.card
        self.out.append("feed %s" % hx(data))
        for t, p in self.rx.feed(c.feed(data)):
            if t == D.ERROR:
                self.out.append("reply e0 %02x" % D.parse_error(p)[0])
            elif t == D.HELLO:
                proto, w, h, _fw, _mv = D.parse_hello(p)
                self.out.append("reply 81 %d %d %d" % (proto, w, h))
            elif t == D.PONG:
                self.out.append("reply 86 %s" % hx(p))
            elif t == D.STATS:
                self.out.append("reply 88 %d %s" % (len(p), hx(p[4:36])))
            else:
                raise AssertionError("réponse inattendue 0x%02X" % t)
        no = "-" if c.displayed_no is None else "%d" % c.displayed_no
        self.out.append("state %s %s %s %d %d %d %d %d %d" % (
            crc_hex(c.framebuffer), crc_hex(c.backbuffer), no, c.lost, c.frames_shown,
            c.deltas_ignored, c.mode(), 1 if c.need_frame else 0, c.brightness))


def build_card():
    imgs = make_images()
    out = ascii_text(CARD_HEADER).rstrip("\n").split("\n")
    black, text, text2, noise = imgs["black"], imgs["text"], imgs["text2"], imgs["noise"]
    noise_tile = D.tile_bytes(noise, 5)

    s = _Scenario(out, "boot")
    s.feed(b"")

    s = _Scenario(out, "hello_ping")
    s.feed(D.encode(D.HELLO_REQ))
    s.feed(D.encode(D.PING))
    s.feed(D.encode(D.PING, bytes(range(64))))
    s.feed(D.encode(D.HELLO_REQ) + D.encode(D.PING, b"\xAA\x55"))

    s = _Scenario(out, "frame_delta_raw")
    s.feed(frame(1, text))
    s.feed(delta(2, 1, delta_raw(2, 1, [(0, 0, noise_tile), (33, 0, noise_tile)])))

    s = _Scenario(out, "frame_delta_encoder")
    s.feed(frame(10, black))
    s.feed(delta(11, 10, black, text))
    s.feed(delta(12, 11, text, text2))
    s.feed(delta(13, 12, text2, text2))           # masque nul : échange quand même

    s = _Scenario(out, "delta_before_first_frame")
    s.feed(delta(1, 0, black, text))
    s.feed(frame(2, black))
    s.feed(delta(3, 2, black, text))

    s = _Scenario(out, "delta_wrong_base")
    s.feed(frame(1, text))
    s.feed(delta(2, 0, text, text2))
    s.feed(delta(3, 1, text, text2))              # ignoré : on attend un FRAME
    s.feed(frame(4, text))
    s.feed(delta(5, 4, text, text2))

    s = _Scenario(out, "delta_invalid_content")
    invalid = [
        delta_raw(0, 0, [(2, 2, noise_tile)]),
        delta_raw(0, 0, [(2, 1, b"\x00\x00\x00\x40\x00\x00")]),
        delta_raw(0, 0, [(2, 1, b"\x20\x00\x00")]),
        delta_raw(0, 0, [(2, 0, noise_tile[:100])]),
        delta_raw(0, 0, [(2, 0, noise_tile)]) + b"\x00",
    ]
    n = 1
    for p in invalid:
        s.feed(frame(n, text))
        s.feed(delta(0, 0, p[:0] + struct.pack("<HH", n + 1, n) + p[4:]))
        s.feed(delta(n + 2, n, text, text2))      # ignoré jusqu'au FRAME
        n += 3

    s = _Scenario(out, "delta_invalid_wrong_base")
    s.feed(frame(1, text))
    s.feed(delta(0, 0, delta_raw(2, 0, [(2, 2, noise_tile)])))

    s = _Scenario(out, "clear")
    s.feed(frame(1, text))
    s.feed(D.encode(D.CLEAR))
    s.feed(delta(2, 1, text, text2))
    s.feed(frame(3, text2))

    s = _Scenario(out, "mode")
    s.feed(frame(1, text))
    s.feed(D.encode(D.MODE, b"\x01"))
    s.feed(frame(2, text2))
    s.feed(delta(3, 2, text2, text))
    s.feed(D.encode(D.MODE, b"\x00"))
    s.feed(delta(4, 1, text, text2))
    s.feed(frame(5, text2))
    s.feed(D.encode(D.MODE, b"\x02"))

    s = _Scenario(out, "crc_error")
    s.feed(frame(1, text))
    s.feed(bad_crc(frame(2, text2)))
    s.feed(delta(3, 2, text2, text))
    s.feed(frame(4, text))

    s = _Scenario(out, "resync")
    rng = Rng(0x5EED)
    s.feed(rng.bytes(23) + b"\xAA\x55\x02\xFF\xFF" + b"\xAA\x55\x06\x28\x00"
           + frame(1, imgs["sync"]) + D.encode(D.PING, b"\x07") + b"\xAA")
    s.feed(D.encode(D.HELLO_REQ)[1:] + delta(2, 1, imgs["sync"], text))

    s = _Scenario(out, "bad_lengths")
    s.feed(raw_frame(D.FRAME, bytes(100)))
    s.feed(raw_frame(D.DELTA, bytes(5)))
    s.feed(raw_frame(D.MODE, b""))
    s.feed(raw_frame(D.BRIGHTNESS, b""))
    s.feed(raw_frame(D.BRIGHTNESS, b"\x01\x02"))  # rejeté par le récepteur : aucune réponse
    s.feed(raw_frame(D.HELLO_REQ, b"\x00"))
    s.feed(raw_frame(D.PING, bytes(65)))
    s.feed(raw_frame(0x42, b"\x01\x02\x03"))
    s.feed(D.encode(D.HELLO, D.hello_payload(128, 32, (1, 0, 0), 5000)))
    s.feed(b"\xAA\x55\x42\x03\x20" + D.encode(D.HELLO_REQ))

    s = _Scenario(out, "brightness")
    s.feed(D.encode(D.BRIGHTNESS, b"\x00"))
    s.feed(D.encode(D.BRIGHTNESS, b"\x80"))
    s.feed(D.encode(D.BRIGHTNESS, b"\xff"))

    s = _Scenario(out, "numbering")
    s.feed(frame(1, black))
    s.feed(frame(5, text))
    s.feed(frame(0xFFFE, text2))
    s.feed(frame(0xFFFF, text))
    s.feed(delta(1, 0xFFFF, text, text2))
    s.feed(delta(2, 1, text2, text))
    s.feed(frame(4, black))

    s = _Scenario(out, "split_feeds")
    f = frame(1, text)
    s.feed(f[:3])
    s.feed(f[3:5000])
    s.feed(f[5000:] + D.encode(D.PING, b"\x01"))

    s = _Scenario(out, "stats")
    s.feed(D.encode(D.STATS_REQ))
    s.feed(rng.bytes(7) + frame(1, text) + delta(2, 1, text, text2))
    s.feed(delta(5, 3, text2, text) + D.encode(D.STATS_REQ))         # base fausse
    s.feed(b"\xAA\x55\x02\xFF\xFF" + bad_crc(frame(6, text)) + D.encode(D.STATS_REQ))
    s.feed(D.encode(D.MODE, b"\x01") + frame(7, text) + delta(8, 7, text, text2)
           + D.encode(D.STATS_REQ) + D.encode(D.STATS_REQ))
    s.feed(D.encode(D.MODE, b"\x00") + frame(9, black) + D.encode(D.CLEAR)
           + D.encode(D.STATS_REQ))
    s.feed(raw_frame(D.STATS_REQ, b"\x00") + D.encode(D.STATS_REQ))  # longueur refusée
    s.feed(D.encode(D.STATS, bytes(D.STATS_LEN)))                    # mauvais sens : ERROR 01
    return ascii_text("\n".join(out) + "\n")


# ------------------------------------------------------------------ main

def build_all():
    return {LIB_FILE: build_lib(), CARD_FILE: build_card()}


def main(argv):
    check = "--check" in argv
    stale = []
    for name, text in sorted(build_all().items()):
        path = os.path.join(VECTORS_DIR, name)
        data = text.encode("ascii")
        if check:
            try:
                with open(path, "rb") as fh:
                    if fh.read() != data:
                        stale.append(name)
            except OSError:
                stale.append(name)
        else:
            os.makedirs(VECTORS_DIR, exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(data)
            sys.stderr.write("# %s : %d octets\n" % (path, len(data)))
    if stale:
        sys.stderr.write("# vecteurs pas à jour : %s\n" % ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
