# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Banc jetable de l'Interstate 75 W : récepteur de trames complètes (§ 6.1, § 6.2).

MicroPython de Pimoroni (i75w_rp2350) ; ce n'est pas le firmware de M4 (C++, § 8), et ce
n'est pas l'essai du § 11.3 (programme C++ minimal) : il en donne une première mesure,
sous MicroPython. À copier en main.py, avec dmd_proto.py, bench_config.py et bench_fast.py.

Reçoit sur le port USB (console de MicroPython, Ctrl-C coupé) les trames de
shared/dmd_proto.py : FRAME (affichée), HELLO_REQ (-> HELLO), PING (-> PONG) ; les autres
types connus sont lus et ignorés. Envoie TELEMETRY chaque seconde : tension 5 V (GPIO28,
diviseur par 2, § 4), température, images affichées dans la dernière seconde, images
perdues (trous de numérotation + CRC faux, B5). Aucun texte sur le port USB.
Sans trame pendant 1 s, la dalle affiche les compteurs.

Issue de secours : bouton A maintenu au démarrage -> le récepteur ne part pas ; Ctrl-C reste
possible pendant les 2 premières secondes.

La classe FrameReader n'utilise que struct et dmd_proto : elle est testée sur PC.
"""

import struct

import dmd_proto as D

BENCH_FW_VERSION = (0, 0, 0)        # 0.0.0 : banc, pas un firmware
TELEMETRY_MS = 1000
IDLE_MS = 1000


class FrameReader:
    """Lit une trame à la fois ; resynchronisation du § 6.1 (B2).

    readinto(memoryview) -> nombre d'octets lus (0 : fin), bloquant. Une fausse en-tête
    (longueur au-delà du maximum du type, ou CRC faux) fait reprendre la recherche à l'octet
    qui suit son 0xAA : les octets déjà lus sont relus depuis la file `_pending`.
    """

    def __init__(self, readinto):
        self._readinto = readinto
        self._buf = bytearray(D.HEADER_LEN + D.MAX_PAYLOAD + D.CRC_LEN)
        self._mv = memoryview(self._buf)
        self._pending = b""
        self.frames = 0
        self.crc_errors = 0
        self.length_errors = 0
        self.skipped = 0

    def _fill(self, start, end):
        mv = self._mv[start:end]
        want = end - start
        p = self._pending
        got = 0
        if p:
            k = min(len(p), want)
            mv[0:k] = p[0:k]
            self._pending = p[k:]
            got = k
        while got < want:
            n = self._readinto(mv[got:])
            if not n:
                raise EOFError("fin du flux")
            got += n

    def _retry_after_sync(self, end):
        """Relire tout ce qui suit le 0xAA de la fausse en-tête (octets 1 à end)."""
        self.skipped += 1
        self._pending = bytes(self._buf[1:end]) + self._pending

    def read_frame(self):
        """-> (type, memoryview des données). Valable jusqu'à l'appel suivant."""
        buf = self._buf
        while True:
            self._fill(0, 1)
            if buf[0] != 0xAA:
                self.skipped += 1
                continue
            self._fill(1, 2)
            if buf[1] != 0x55:
                self._retry_after_sync(2)
                continue
            self._fill(2, D.HEADER_LEN)
            ftype = buf[2]
            length = buf[3] | (buf[4] << 8)
            if length > D.max_length(ftype):
                self.length_errors += 1
                self._retry_after_sync(D.HEADER_LEN)
                continue
            body_end = D.HEADER_LEN + length
            end = body_end + D.CRC_LEN
            self._fill(D.HEADER_LEN, end)
            got = struct.unpack_from("<I", buf, body_end)[0]
            if D.crc32(self._mv[2:body_end]) != got:
                self.crc_errors += 1
                self._retry_after_sync(end)
                continue
            self.frames += 1
            return ftype, self._mv[D.HEADER_LEN:body_end]


class Stats:
    """Compteurs du banc. now et diff : horloge en ms et écart (time.ticks_diff sur la carte)."""

    def __init__(self, now, diff=None):
        self.diff = diff or (lambda a, b: a - b)
        self.shown = 0
        self.gaps = 0
        self.last_no = None
        self.last_frame_ms = now
        self.window = 0
        self.window_start = now
        self.last_fps = 0
        self.best_fps = 0

    def on_frame(self, number, now):
        if self.last_no is not None:
            d = (number - self.last_no) & 0xFFFF
            if 1 < d < 0x8000:
                self.gaps += d - 1
        self.last_no = number
        self.last_frame_ms = now
        self.shown += 1
        self.window += 1

    def telemetry_due(self, now):
        """Vrai une fois par TELEMETRY_MS ; met à jour last_fps (images de la dernière fenêtre)."""
        if self.diff(now, self.window_start) < TELEMETRY_MS:
            return False
        self.last_fps = self.window
        if self.window > self.best_fps:
            self.best_fps = self.window
        self.window = 0
        self.window_start = now
        return True

    def idle(self, now):
        return self.diff(now, self.last_frame_ms) >= IDLE_MS


def swap16_py(dst, src, n):
    """Copie n octets en permutant chaque paire (RGB565 petit-boutiste -> tampon PicoGraphics)."""
    for i in range(0, n, 2):
        dst[i] = src[i + 1]
        dst[i + 1] = src[i]


def readinto_from_read(read):
    """Repli si le flux n'a pas readinto : read(n) puis copie."""
    def readinto(mv):
        data = read(len(mv))
        if not data:
            return 0
        mv[0:len(data)] = data
        return len(data)
    return readinto


def telemetry_frame(stats, reader, mv, temp_dc):
    lost = stats.gaps + reader.crc_errors
    return D.encode(D.TELEMETRY, D.telemetry_payload(mv, temp_dc, min(stats.last_fps, 255),
                                                     lost & 0xFFFFFFFF))


def stats_lines(stats, reader, swap_name):
    return ("RX banc  %s" % swap_name,
            "img %d  max %d/s" % (stats.shown, stats.best_fps),
            "CRC %d  trous %d" % (reader.crc_errors, stats.gaps),
            "long %d  saut %d" % (reader.length_errors, reader.skipped))


# --------------------------------------------------------------- carte seulement

def main():
    import select
    import sys
    import time

    import machine
    import micropython

    import bench_config as C

    if machine.Pin(C.ESCAPE_PIN, machine.Pin.IN, machine.Pin.PULL_UP).value() == 0:
        return                                      # bouton A : console libre
    time.sleep_ms(C.START_DELAY_MS)                 # Ctrl-C encore possible

    import hub75
    from picographics import DISPLAY_INTERSTATE75_128X32, PEN_RGB565, PicoGraphics

    g = PicoGraphics(display=DISPLAY_INTERSTATE75_128X32, pen_type=PEN_RGB565)
    panel = hub75.Hub75(C.WIDTH, C.HEIGHT, **C.hub75_options(hub75))
    panel.start()
    fb = memoryview(g)
    try:
        from bench_fast import swap16
        swap_name = "viper"
    except Exception:                               # viper absent : Python pur
        swap16 = swap16_py
        swap_name = "python"

    adc_5v = machine.ADC(28)
    adc_temp = machine.ADC(getattr(machine.ADC, "CORE_TEMP", 4))

    def mv_5v():
        return adc_5v.read_u16() * 3300 * 2 // 65535

    def temp_dc():
        v = adc_temp.read_u16() * 3.3 / 65535
        return int((27 - (v - 0.706) / 0.001721) * 10)

    def show_lines(lines):
        g.set_pen(g.create_pen(0, 0, 0))
        g.clear()
        g.set_pen(g.create_pen(255, 255, 0))
        for i, text in enumerate(lines):
            g.text(text, 0, i * 8, -1, 1)
        panel.update(g)

    g.set_font("bitmap6")
    now = time.ticks_ms()
    stats = Stats(now, time.ticks_diff)
    stdin = sys.stdin.buffer
    out = sys.stdout.buffer
    reader = FrameReader(getattr(stdin, "readinto", None) or readinto_from_read(stdin.read))
    show_lines(stats_lines(stats, reader, swap_name))
    stats_shown = True

    micropython.kbd_intr(-1)                        # 0x03 dans les pixels : pas de Ctrl-C
    poll = select.poll()
    poll.register(sys.stdin, select.POLLIN)
    while True:
        now = time.ticks_ms()
        if stats.telemetry_due(now):
            out.write(telemetry_frame(stats, reader, mv_5v(), temp_dc()))
        if not poll.poll(50):
            if stats.idle(now) and not stats_shown:
                show_lines(stats_lines(stats, reader, swap_name))
                stats_shown = True
            continue
        ftype, p = reader.read_frame()
        if ftype == D.FRAME and len(p) == D.MAX_PAYLOAD:
            stats.on_frame(p[0] | (p[1] << 8), time.ticks_ms())
            swap16(fb, p[2:], D.PIXELS_BYTES)
            panel.update(g)
            stats_shown = False
        elif ftype == D.HELLO_REQ:
            out.write(D.encode(D.HELLO, D.hello_payload(C.WIDTH, C.HEIGHT, BENCH_FW_VERSION,
                                                        mv_5v())))
        elif ftype == D.PING:
            out.write(D.encode(D.PONG, bytes(p)))


if __name__ == "__main__":
    main()
