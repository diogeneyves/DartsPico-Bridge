# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Fausse carte Interstate 75 W : simule l'afficheur côté protocole (§ 6).

Utilisation en mémoire (tests) :
    card = FakeI75W(clock=horloge_en_µs)
    card.boot()                       -> b""
    card.feed(dmd_proto.encode(...))  -> trames de réponse
    card.tick()                       -> TELEMETRY chaque seconde
    STATS_REQ                         -> STATS (champs propres à la carte réelle à 0)
    card.framebuffer                  -> 8 192 octets RGB565 affichés
    card.backbuffer                   -> tampon arrière (double tampon, § 6.4)

Échange des tampons sur un FRAME, ou sur un DELTA dont la base est l'image
affichée, puis recopie de l'image affichée dans le tampon arrière.

Sur un pseudo-terminal : python3 tools/fake_i75w.py
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "shared"))
sys.path.insert(0, _HERE)

import dmd_proto as D  # noqa: E402

FW_VERSION = (0, 1, 0)
LOCAL_ANIMATION_DELAY_US = 3000000
TELEMETRY_PERIOD_US = 1000000

# Types que la carte reçoit ; les autres donnent ERROR « type inconnu ».
_RECEIVED = (D.HELLO_REQ, D.FRAME, D.BRIGHTNESS, D.CLEAR, D.MODE, D.PING, D.DELTA,
             D.STATS_REQ)


class FakeI75W:
    def __init__(self, clock=None, mv=5000, temp_dc=350):
        if clock is None:
            from ptypair import monotonic_us
            clock = monotonic_us
        self.clock = clock
        self.mv = mv
        self.temp_dc = temp_dc
        self.boot()

    def boot(self):
        now = self.clock()
        self.boot_us = now
        self.decoder = D.Decoder()
        self.framebuffer = bytearray(D.PIXELS_BYTES)   # tampon affiché
        self.backbuffer = bytearray(D.PIXELS_BYTES)    # tampon arrière
        self.displayed_no = None           # numéro de l'image du Pi affichée
        self.need_frame = True             # DELTA ignorés jusqu'au prochain FRAME
        self.brightness = 255
        self.forced_local = False          # MODE 1 demandé par le Pi
        self.local_animation = False       # animation de secours affichée
        self.last_frame_no = None          # dernier numéro reçu (FRAME ou DELTA)
        self.last_frame_us = now
        self.frames_shown = 0              # échanges de tampons
        self.deltas_ignored = 0
        self.lost = 0
        self._seen_crc_errors = 0
        self._shown_since_telemetry = 0
        self.frames_rx = 0                 # FRAME reçus (CRC et longueur corrects), MODE 1 compris
        self.deltas_rx = 0                 # DELTA reçus, contenu valide ou non
        self.bytes_rx = 0                  # octets reçus
        self._next_telemetry = now + TELEMETRY_PERIOD_US
        self.errors_sent = []
        return b""

    def mode(self):
        return D.MODE_LOCAL if (self.forced_local or self.local_animation) else D.MODE_PI

    # ------------------------------------------------------------ temps

    def tick(self):
        now = self.clock()
        out = []
        if (not self.forced_local and not self.local_animation
                and now - self.last_frame_us >= LOCAL_ANIMATION_DELAY_US):
            self.local_animation = True
            self.need_frame = True                    # § 6.3 : seul un FRAME en sort
        if now >= self._next_telemetry:
            fps = self._shown_since_telemetry
            self._shown_since_telemetry = 0
            self._next_telemetry = now + TELEMETRY_PERIOD_US
            out.append(D.encode(D.TELEMETRY, D.telemetry_payload(self.mv, self.temp_dc, fps,
                                                                 self.lost)))
        return b"".join(out)

    # ------------------------------------------------------- réception

    def feed(self, data):
        out = [self.tick()]
        self.bytes_rx += len(data)
        for ftype, payload in self.decoder.feed(data):
            out.append(self._handle(ftype, payload))
        crc = self.decoder.crc_errors
        self.lost += crc - self._seen_crc_errors      # trames rejetées pour CRC (B5)
        self._seen_crc_errors = crc
        return b"".join(out)

    def _error(self, code, text):
        self.errors_sent.append(code)
        return D.encode(D.ERROR, D.error_payload(code, text))

    def _handle(self, ftype, p):
        if ftype not in _RECEIVED:
            return self._error(D.ERR_UNKNOWN_TYPE, "type 0x%02X" % ftype)
        try:
            D.check_length(ftype, len(p))
            if ftype == D.HELLO_REQ:
                return D.encode(D.HELLO, D.hello_payload(D.WIDTH, D.HEIGHT, FW_VERSION, self.mv))
            if ftype == D.PING:
                return D.encode(D.PONG, p)
            if ftype == D.BRIGHTNESS:
                self.brightness = D.parse_u8(ftype, p)
                return b""
            if ftype == D.CLEAR:
                # Les deux tampons ; pas une image, numérotation inchangée (§ 6.4).
                self.framebuffer[:] = bytes(D.PIXELS_BYTES)
                self.backbuffer[:] = bytes(D.PIXELS_BYTES)
                self.need_frame = True
                return b""
            if ftype == D.MODE:
                m = D.parse_u8(ftype, p, 1)
                self.forced_local = m == D.MODE_LOCAL
                self.local_animation = self.forced_local
                self.need_frame = True                # DELTA ignorés jusqu'au FRAME (§ 6.3)
                self.last_frame_us = self.clock()
                return b""
            if ftype == D.STATS_REQ:
                return D.encode(D.STATS, self._stats())
            if ftype == D.FRAME:
                self.frames_rx += 1
                self._frame(p)
                return b""
            if ftype == D.DELTA:
                self.deltas_rx += 1
                return self._delta(p)
        except D.PayloadError as e:
            return self._error(e.code, "type 0x%02X" % ftype)
        return self._error(D.ERR_INTERNAL, "non traité")

    def _stats(self):
        """STATS (§ 6.2) ; replaced, loop_max_us, refresh_hz et flags : 0 (carte réelle seulement)."""
        dec = self.decoder
        return D.stats_payload(
            uptime_ms=(self.clock() - self.boot_us) // 1000, frames_rx=self.frames_rx,
            deltas_rx=self.deltas_rx, bytes_rx=self.bytes_rx, skipped=dec.skipped,
            crc_errors=dec.crc_errors, length_errors=dec.length_errors,
            deltas_ignored=self.deltas_ignored, swaps=self.frames_shown)

    def _count_gap(self, number):
        """Trous de numérotation depuis le dernier numéro reçu (B5)."""
        if self.last_frame_no is not None:
            diff = (number - self.last_frame_no) & 0xFFFF
            if 1 < diff < 0x8000:
                self.lost += diff - 1
        self.last_frame_no = number

    def _swap(self, number):
        """Échange des tampons, puis recopie de l'image affichée (§ 6.4)."""
        self.framebuffer, self.backbuffer = self.backbuffer, self.framebuffer
        self.backbuffer[:] = self.framebuffer
        self.displayed_no = number
        self.frames_shown += 1
        self._shown_since_telemetry += 1

    def _frame(self, p):
        number, pixels = D.parse_frame(p)
        if self.forced_local:
            self.last_frame_no = number               # ignorée, pas perdue (B6)
            return
        self._count_gap(number)
        self.local_animation = False                  # retour automatique au mode 0
        self.need_frame = False
        self.last_frame_us = self.clock()
        self.backbuffer[:] = pixels
        self._swap(number)

    def _delta(self, p):
        number, base, _mask = D.delta_header(p)
        if self.forced_local:
            self.last_frame_no = number               # ignoré, pas perdu (B6)
            return b""
        self.last_frame_us = self.clock()             # CRC et longueur corrects (§ 6.3)
        self._count_gap(number)
        if self.need_frame or self.local_animation or base != self.displayed_no:
            self.need_frame = True                    # sans ERROR (§ 6.4)
            self.lost += 1
            self.deltas_ignored += 1
            return b""
        try:
            _n, _b, tiles = D.parse_delta(p)
        except D.PayloadError as e:
            self.backbuffer[:] = self.framebuffer
            self.need_frame = True
            self.lost += 1
            self.deltas_ignored += 1
            return self._error(e.code, e.detail)
        for i, tile in tiles:
            D.put_tile(self.backbuffer, i, tile)
        self._swap(number)
        return b""


def main():
    import time
    from ptypair import PtyServer, stop_on_sigterm

    stop_on_sigterm()
    srv = PtyServer(FakeI75W()).start()
    sys.stderr.write("# fausse carte 75 W sur %s (Ctrl-C pour arrêter)\n" % srv.path)
    sys.stderr.flush()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()


if __name__ == "__main__":
    main()
