#!/usr/bin/env python3
# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Banc de l'Interstate 75 W : émetteur côté Pi de trames FRAME complètes (§ 6.2, § 11.3).

Envoie des images 128 × 32 RGB565 non compressées (8 203 octets par trame, pire cas du
§ 6.3) d'abord à 15 images/s, puis aussi vite que possible, et affiche le débit obtenu côté
Pi, la cadence annoncée par la carte (TELEMETRY) et ses compteurs (STATS, lus après HELLO
puis après chaque phase : 40e complément). C'est l'essai du § 11.3 avec le firmware C++ ;
avec le récepteur MicroPython (rx_bench.py), qui ne répond pas à STATS_REQ, l'essai se fait
sur TELEMETRY seule (« STATS absent »).

    python3 tx_bench.py /dev/ttyACM1 [--rate 15] [--duration 10] [--no-max]
    python3 tx_bench.py /dev/ttyACM1 --check      HELLO et STATS seulement (i75w.sh check)

Critères (§ 11.3) : écarts de skipped, crc_errors et length_errors nuls pendant chaque phase,
frames_rx égal aux images envoyées. Essai non valable si le bit 0 de flags (chien de garde)
passe à 1 ou si uptime_ms recule. Une réponse trop courte pour son type (STATS, TELEMETRY,
HELLO, ERROR) est comptée invalide, sans arrêter la lecture.

Codes de sortie de --check : 0 bon, 2 pas de HELLO, 3 protocole ou taille inattendus.

Python 3 seul (termios, threads), aucune dépendance. dmd_proto.py doit être dans le même
dossier ou dans ../../shared.
"""

import argparse
import os
import select
import struct
import sys
import termios
import threading
import time
import tty

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(os.path.dirname(os.path.dirname(_HERE)), "shared")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import dmd_proto as D  # noqa: E402

FRAME_BYTES = D.HEADER_LEN + D.MAX_PAYLOAD + D.CRC_LEN      # 8 203
STATS_REQ_BYTES = D.HEADER_LEN + D.CRC_LEN                    # 9
PATTERNS = 32
TARGET_FPS = 15
STATS_TIMEOUT = 1.0
U32 = 1 << 32
# Compteurs u32 de STATS comparés entre deux lectures (écarts modulo 2³²).
COUNTERS = ("frames_rx", "deltas_rx", "bytes_rx", "skipped", "crc_errors", "length_errors",
            "deltas_ignored", "swaps", "replaced")


# ------------------------------------------------------------------ images

def test_pixels(k):
    """Image de test k : dégradé, barre verticale mobile en x = 4k (mod 128).

    Pixel (0, 0) = 0x55AA (octets AA 55 : fausse synchronisation) et pixel (1, 0) = 0x0303
    (octets 03 : Ctrl-C), pour vérifier la resynchronisation et que Ctrl-C est bien coupé.
    """
    out = bytearray(D.PIXELS_BYTES)
    bar = (4 * k) % D.WIDTH
    for y in range(D.HEIGHT):
        for x in range(D.WIDTH):
            if abs(x - bar) < 2:
                c = D.rgb565(255, 255, 255)
            else:
                c = D.rgb565(x * 2, y * 8, (k * 8) & 0xFF)
            i = 2 * (y * D.WIDTH + x)
            out[i] = c & 0xFF
            out[i + 1] = c >> 8
    out[0:4] = b"\xAA\x55\x03\x03"
    return bytes(out)


class FrameSource:
    """Trames FRAME numérotées (u16, qui boucle), sur PATTERNS images précalculées."""

    def __init__(self, number=0):
        self.number = number & 0xFFFF
        self._pixels = [test_pixels(k) for k in range(PATTERNS)]

    def next(self):
        frame = D.encode(D.FRAME, D.frame_payload(self.number,
                                                  self._pixels[self.number % PATTERNS]))
        self.number = (self.number + 1) & 0xFFFF
        return frame


# ------------------------------------------------------------------ cadence

class Pacer:
    """Cadence fixe (rate images/s) ou maximale (rate None).

    delay(now) : secondes à attendre avant l'image suivante. Une image partie plus d'une
    période en retard est comptée dans late et la grille repart de maintenant.
    """

    def __init__(self, rate, now):
        self.period = 1.0 / rate if rate else 0.0
        self.next_at = now
        self.late = 0

    def delay(self, now):
        if not self.period:
            return 0.0
        if now - self.next_at > self.period:
            self.late += 1
            self.next_at = now
        wait = max(0.0, self.next_at - now)
        self.next_at += self.period
        return wait


# ------------------------------------------------------------------ port

def open_port(path):
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
    if os.isatty(fd):
        tty.setraw(fd, termios.TCSANOW)
        attrs = termios.tcgetattr(fd)
        attrs[3] &= ~termios.ECHO
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIOFLUSH)
    return fd


def write_all(fd, data):
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]


class Listener(threading.Thread):
    """Lit les réponses de la carte : HELLO, TELEMETRY et STATS (avec l'heure d'arrivée), ERROR.

    Une réponse dont les données ne conviennent pas à son type (trop courte, par exemple :
    Decoder ne rejette que les longueurs au-delà du maximum) est comptée dans invalid, par
    nom de type, et la lecture continue.
    """

    def __init__(self, fd, clock=time.monotonic):
        threading.Thread.__init__(self, daemon=True)
        self.fd = fd
        self.clock = clock
        self.decoder = D.Decoder()
        self.lock = threading.Lock()
        self.hello = None
        self.telemetry = []           # (instant, mV, température × 10, fps, perdues)
        self.stats = []               # (instant, dictionnaire des champs de STATS)
        self.errors = []
        self.invalid = {}             # nom du type -> réponses invalides
        self.others = 0
        self._halt = threading.Event()

    def run(self):
        while not self._halt.is_set():
            r, _, _ = select.select([self.fd], [], [], 0.2)
            if not r:
                continue
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                return
            if not data:
                return
            self.handle(data)

    def handle(self, data):
        now = self.clock()
        for ftype, p in self.decoder.feed(data):
            with self.lock:
                try:
                    if ftype == D.HELLO:
                        self.hello = D.parse_hello(p)
                    elif ftype == D.TELEMETRY:
                        self.telemetry.append((now,) + tuple(D.parse_telemetry(p)))
                    elif ftype == D.STATS:
                        self.stats.append((now, D.parse_stats(p)))
                    elif ftype == D.ERROR:
                        self.errors.append(D.parse_error(p))
                    else:
                        self.others += 1
                except (D.PayloadError, ValueError, struct.error):
                    name = _NAMES.get(ftype, "0x%02X" % ftype)
                    self.invalid[name] = self.invalid.get(name, 0) + 1

    def stop(self):
        self._halt.set()


_NAMES = {D.HELLO: "HELLO", D.TELEMETRY: "TELEMETRY", D.STATS: "STATS", D.ERROR: "ERROR"}


def request_stats(fd, listener, timeout=STATS_TIMEOUT, write=write_all, sleep=time.sleep,
                  clock=time.monotonic):
    """Envoie STATS_REQ et attend la réponse : dictionnaire des champs, ou None (absente)."""
    with listener.lock:
        count = len(listener.stats)
    write(fd, D.encode(D.STATS_REQ))
    deadline = clock() + timeout
    while clock() < deadline:
        with listener.lock:
            if len(listener.stats) > count:
                return listener.stats[-1][1]
        sleep(0.02)
    return None


# ------------------------------------------------------------------ STATS

def stats_delta(before, after):
    """Écarts des compteurs u32 entre deux STATS (modulo 2³²)."""
    return dict((k, (after[k] - before[k]) % U32) for k in COUNTERS)


def first_stats_notes(st):
    """Avertissements sur la première STATS de l'essai (40e complément)."""
    notes = []
    if st["flags"] & D.STATS_FLAG_WATCHDOG:
        notes.append("Attention : démarrage précédent par le chien de garde (bit 0 de flags)")
    if st["flags"] & ~D.STATS_FLAG_WATCHDOG & 0xFF:
        notes.append("Attention : bits inconnus dans flags (0x%02X)" % st["flags"])
    return notes


def stats_validity(prev, cur):
    """(non valable, avertissements) entre deux STATS successives de l'essai.

    Non valable : uptime_ms recule (la carte a redémarré), ou le bit 0 de flags passe à 1
    (redémarrage par le chien de garde pendant l'essai).
    """
    invalid = []
    notes = []
    if cur["uptime_ms"] < prev["uptime_ms"]:
        invalid.append("uptime_ms recule (%d -> %d ms) : la carte a redémarré"
                       % (prev["uptime_ms"], cur["uptime_ms"]))
    if cur["flags"] & D.STATS_FLAG_WATCHDOG and not prev["flags"] & D.STATS_FLAG_WATCHDOG:
        invalid.append("bit 0 de flags passé à 1 : redémarrage par le chien de garde")
    unknown = cur["flags"] & ~D.STATS_FLAG_WATCHDOG & 0xFF
    if unknown and unknown != prev["flags"] & ~D.STATS_FLAG_WATCHDOG & 0xFF:
        notes.append("Attention : bits inconnus dans flags (0x%02X)" % cur["flags"])
    return invalid, notes


def phase_stats(phase, before, after):
    """(lignes, bon) : compteurs de la carte pendant une phase (§ 11.3).

    Bon : aucun octet sauté, aucun CRC faux, aucune longueur refusée, et frames_rx égal au
    nombre d'images envoyées. bytes_rx est comparé aux octets envoyés (images et STATS_REQ
    de fin de phase) : un écart est seulement signalé.
    """
    d = stats_delta(before, after)
    sent_bytes = phase["frames"] * FRAME_BYTES + STATS_REQ_BYTES
    lines = ["  STATS : %d FRAME reçus sur %d envoyés, %d octets reçus sur %d envoyés"
             % (d["frames_rx"], phase["frames"], d["bytes_rx"], sent_bytes),
             "  STATS : %d octets sautés, %d CRC faux, %d longueurs refusées, %d échanges, "
             "%d remplacées" % (d["skipped"], d["crc_errors"], d["length_errors"], d["swaps"],
                                d["replaced"]),
             "  STATS : plus long tour du cœur 0 %d µs, rafraîchissement %d cycles/s"
             % (after["loop_max_us"], after["refresh_hz"])]
    good = (d["skipped"] == 0 and d["crc_errors"] == 0 and d["length_errors"] == 0
            and d["frames_rx"] == phase["frames"])
    if d["bytes_rx"] != sent_bytes:
        lines.append("  Attention : octets reçus différents des octets envoyés")
    if not good:
        lines.append("  STATS : critère du § 11.3 non tenu (octets sautés, CRC faux, "
                     "longueurs refusées ou images manquantes)")
    return lines, good


def describe_stats(st):
    return ("STATS : uptime %.1f s, %d FRAME, %d octets reçus, %d octets sautés, %d CRC faux, "
            "%d longueurs refusées, flags 0x%02X"
            % (st["uptime_ms"] / 1000.0, st["frames_rx"], st["bytes_rx"], st["skipped"],
               st["crc_errors"], st["length_errors"], st["flags"]))


# ------------------------------------------------------------------ phases

def card_side(telemetry, start, end):
    """Cadence de la carte pendant [start, end] : (moyenne, maximum, perdues) ou None.

    On garde les télémétries arrivées entre start + 1,2 s et end + 0,2 s : chacune compte les
    images de la seconde qui la précède, entièrement dans la phase.
    """
    inside = [t for t in telemetry if start + 1.2 <= t[0] <= end + 0.2]
    if not inside:
        return None
    fps = [t[3] for t in inside]
    before = [t for t in telemetry if t[0] < start + 1.2]
    lost0 = before[-1][4] if before else inside[0][4]
    return (sum(fps) / len(fps), max(fps), inside[-1][4] - lost0)


def run_phase(fd, source, rate, duration, clock=time.monotonic, sleep=time.sleep,
              write=write_all):
    start = clock()
    pacer = Pacer(rate, start)
    frames = 0
    while clock() - start < duration:
        wait = pacer.delay(clock())
        if wait:
            sleep(wait)
        write(fd, source.next())
        frames += 1
    seconds = clock() - start
    return {"rate": rate, "frames": frames, "seconds": seconds, "start": start,
            "end": start + seconds, "fps": frames / seconds if seconds else 0.0,
            "kbps": frames * FRAME_BYTES / 1000.0 / seconds if seconds else 0.0,
            "late": pacer.late}


def describe(phase, card):
    label = "%d images/s visées" % phase["rate"] if phase["rate"] else "au maximum"
    lines = ["Phase %s : %d images en %.1f s -> %.1f images/s, %.1f ko/s envoyés"
             % (label, phase["frames"], phase["seconds"], phase["fps"], phase["kbps"])]
    if phase["rate"] and phase["late"]:
        lines.append("  retards de l'émetteur : %d" % phase["late"])
    if card is None:
        lines.append("  carte : aucune télémétrie reçue pendant la phase")
    else:
        lines.append("  carte : %.1f images/s en moyenne, %d au mieux, %d perdues"
                     % card)
    return lines


def verdict(card_max_phase, stats_ok=None, valid=True):
    """Résultat de l'essai.

    stats_ok : None sans STATS (récepteur MicroPython, résultat sur TELEMETRY seule), sinon
    True si toutes les phases tiennent le critère des compteurs. valid : False si la carte a
    redémarré pendant l'essai.
    """
    if not valid:
        return "Résultat : essai non valable (la carte a redémarré pendant l'essai)."
    if card_max_phase is None:
        return "Résultat : indéterminé (pas de télémétrie de la carte)."
    if stats_ok is None:
        if card_max_phase[0] >= TARGET_FPS:
            return ("Résultat : >= %d images/s en pire cas (TELEMETRY seule, STATS absent)."
                    % TARGET_FPS)
        return "Résultat : < %d images/s (TELEMETRY seule, STATS absent)." % TARGET_FPS
    if card_max_phase[0] >= TARGET_FPS and stats_ok:
        return ("Résultat : bon, >= %d images/s en pire cas, sans octet sauté ni CRC faux "
                "(§ 11.3)." % TARGET_FPS)
    if card_max_phase[0] >= TARGET_FPS:
        return ("Résultat : >= %d images/s, mais compteurs hors critère (octets sautés, CRC "
                "faux, images manquantes ou STATS manquant)." % TARGET_FPS)
    return "Résultat : < %d images/s en pire cas : objectif du § 11.3 non atteint." % TARGET_FPS


def wait_hello(fd, listener, timeout=2.0):
    write_all(fd, D.encode(D.HELLO_REQ))
    deadline = time.monotonic() + timeout
    while listener.hello is None and time.monotonic() < deadline:
        time.sleep(0.05)
    return listener.hello


def describe_hello(hello):
    proto, w, h, fw, mv = hello
    return ("HELLO : protocole %d, %d × %d, version %d.%d.%d, 5 V = %d mV"
            % ((proto, w, h) + tuple(fw) + (mv,)))


def run_check(fd, listener, say):
    """HELLO puis STATS (i75w.sh check). Refus (3) si protocole ≠ 1 ou taille ≠ 128 × 32."""
    hello = wait_hello(fd, listener)
    if hello is None:
        say("Pas de HELLO de la carte en 2 s.")
        return 2
    say(describe_hello(hello))
    proto, w, h, _, _ = hello
    if proto != D.PROTO_VERSION or (w, h) != (D.WIDTH, D.HEIGHT):
        say("Refus : protocole %d et taille %d × %d attendus." % (D.PROTO_VERSION, D.WIDTH,
                                                                  D.HEIGHT))
        return 3
    st = request_stats(fd, listener)
    if st is None:
        say("STATS absent.")
    else:
        say(describe_stats(st))
        for note in first_stats_notes(st):
            say(note)
    return 0


def main(argv=None, out=sys.stdout):
    ap = argparse.ArgumentParser(description="Émetteur du banc de l'Interstate 75 W")
    ap.add_argument("port", help="port série de la carte, par exemple /dev/ttyACM1")
    ap.add_argument("--rate", type=float, default=TARGET_FPS, help="cadence de la phase 1")
    ap.add_argument("--duration", type=float, default=10.0, help="durée de chaque phase (s)")
    ap.add_argument("--no-max", action="store_true", help="sauter la phase au maximum")
    ap.add_argument("--settle", type=float, default=1.5,
                    help="attente après chaque phase, pour la dernière télémétrie (s)")
    ap.add_argument("--check", action="store_true",
                    help="HELLO et STATS seulement, sans images (i75w.sh check)")
    args = ap.parse_args(argv)

    def say(text):
        out.write(text + "\n")
        out.flush()

    fd = open_port(args.port)
    listener = Listener(fd)
    listener.start()
    try:
        if args.check:
            return run_check(fd, listener, say)
        if wait_hello(fd, listener) is None:
            say("Pas de HELLO de la carte en 2 s : firmware lancé ?")
        else:
            say(describe_hello(listener.hello))
        prev = request_stats(fd, listener)
        if prev is None:
            say("STATS absent : résultat sur TELEMETRY seule.")
        else:
            say(describe_stats(prev))
            for note in first_stats_notes(prev):
                say(note)
        stats_ok = None if prev is None else True
        valid = True
        source = FrameSource()
        phases = []
        rates = [args.rate] if args.no_max else [args.rate, None]
        for rate in rates:
            phase = run_phase(fd, source, rate, args.duration)
            time.sleep(args.settle)
            with listener.lock:
                card = card_side(list(listener.telemetry), phase["start"], phase["end"])
            phases.append((phase, card))
            for line in describe(phase, card):
                say(line)
            if prev is not None:
                cur = request_stats(fd, listener)
                if cur is None:
                    say("  STATS absent après la phase")
                    stats_ok = False
                    continue
                invalid, notes = stats_validity(prev, cur)
                for line in invalid:
                    say("  Essai non valable : " + line)
                for line in notes:
                    say("  " + line)
                if invalid:
                    valid = False
                else:
                    lines, good = phase_stats(phase, prev, cur)
                    for line in lines:
                        say(line)
                    stats_ok = stats_ok and good
                prev = cur
        with listener.lock:
            if listener.telemetry:
                t = listener.telemetry[-1]
                say("Dernière télémétrie : 5 V = %d mV, %.1f °C, %d images perdues au total"
                    % (t[1], t[2] / 10.0, t[4]))
            if listener.errors:
                say("ERROR reçues : %r" % (listener.errors,))
            for name in sorted(listener.invalid):
                say("%s invalide : %d" % (name, listener.invalid[name]))
        say("Décodeur côté Pi : %d CRC faux, %d octets sautés"
            % (listener.decoder.crc_errors, listener.decoder.skipped))
        if not args.no_max:
            say(verdict(phases[-1][1], stats_ok, valid))
        elif not valid:
            say(verdict(None, stats_ok, valid))
        return 0
    finally:
        listener.stop()
        listener.join(1.0)              # avant de fermer le port qu'il surveille
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
