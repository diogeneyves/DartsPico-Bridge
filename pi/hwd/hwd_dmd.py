# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""DMD dans hwd : liaison avec l'Interstate 75 W, sans entrée-sortie (§ 6, § 9.5).

Le service de rendu publie des images complètes (8 192 octets RGB565) sur
raspydarts/hw/dmd/frame ; ce module garde la dernière, en envoie au plus
15 par seconde sur /dev/dmd, calcule les tuiles modifiées (DELTA, § 6.4.1)
par rapport à la dernière image envoyée, et envoie un FRAME toutes les
dmd_full_period_ms. Sans image du rendu pendant dmd_stale_ms, il cesse
d'émettre (animation locale de la carte, § 6.3) et reprend par un FRAME.

Présentation (32e complément) : HELLO_REQ, HELLO attendu sous
handshake_timeout_ms, puis MODE 0 et un FRAME. Sans trame valide de la
carte pendant dmd_silence_ms : retour à HELLO_REQ. hwd n'envoie jamais
CLEAR, MODE 1 ni BRIGHTNESS.

Trames invalides de la carte (51e complément) : réponse trop courte, type
inattendu, CRC ou longueur refusés par le Decoder. Journal seulement, au
plus une ligne par minute et par clé ; rien sur MQTT.

Branchement (par HwdCore et le démon) :
    dmd.serial_opened() / serial_data(octets) / serial_closed()
    dmd.image(contenu)        image reçue du rendu
    dmd.tick()
    dmd.take_serial()  -> une trame à écrire (octets) ou None
"""

import logging
from collections import deque

import dmd_proto as D

log = logging.getLogger("hwd")

ABSENT = "absent"
HANDSHAKE = "handshake"
CONNECTED = "connected"

IMAGE_PERIOD_US = 1000000 // D.TARGET_FPS       # au plus 15 images par seconde (§ 9.5)
REJECT_EVENT_PERIOD_US = 60 * 1000000           # refus d'image : un événement par minute au plus
INVALID_LOG_PERIOD_US = 60 * 1000000            # trame invalide : une ligne par minute et par clé (51e complément)

# Réponses de la carte que hwd sait lire, et celles qu'il ne demande jamais (ni PING ni STATS_REQ).
REPLY_NAMES = {D.HELLO: "HELLO", D.ERROR: "ERROR", D.TELEMETRY: "TELEMETRY"}
UNEXPECTED_NAMES = {D.PONG: "PONG", D.STATS: "STATS"}


class _LimitedLog:
    """Une clé du journal des trames invalides : au plus une ligne par période, terminée
    par le nombre de cas depuis la ligne précédente (cas en cours compris)."""

    def __init__(self):
        self.pending = 0
        self.text = ""
        self.next_us = None

    def add(self, now, count, text):
        self.pending += count
        self.text = text                   # dernier cas : c'est lui que la ligne décrit
        self.flush(now)

    def flush(self, now):
        if self.pending and (self.next_us is None or now >= self.next_us):
            log.warning("# DMD : %s, %d since last log", self.text, self.pending)
            self.pending = 0
            self.next_us = now + INVALID_LOG_PERIOD_US


class DmdLink:
    def __init__(self, cfg, clock, event, state_changed):
        """event(code, texte) publie « E code texte » ; state_changed() republie le statut."""
        self.cfg = cfg
        self.clock = clock
        self._event = event
        self._state_changed = state_changed
        self.state = ABSENT
        self.fw = None
        self._decoder = D.Decoder()
        self._out = deque()                # trames de contrôle (HELLO_REQ, MODE)
        now = clock()
        self._last_rx = now
        self._handshake_deadline = now
        # Images.
        self.latest = None                 # dernière image du rendu (8 192 octets)
        self._latest_us = None
        self._dirty = False                # image reçue depuis le dernier envoi
        self.sent_image = None             # dernière image envoyée sur le port
        self._sent_no = 0                  # son numéro (u16, commun FRAME et DELTA)
        self._need_frame = True            # prochain envoi : FRAME (§ 6.4.1)
        self._next_slot_us = None         # créneau du prochain envoi d'image (15 par seconde)
        self._last_full_us = None
        self._stale = True
        self._next_reject_event = None
        self.counters = {"frames": 0, "deltas": 0, "images_rejected": 0,
                         "board_errors": 0, "telemetry": 0,
                         "invalid_replies": {name: 0 for name in REPLY_NAMES.values()},
                         "unexpected_replies": {"PONG": 0, "STATS": 0, "unknown": 0}}
        self.telemetry = None              # (mV, °C × 10, images/s, perdues)
        # Journal des trames invalides (51e complément) : une clé par cas ; erreurs du
        # Decoder déjà écrites (ses compteurs repartent de 0 à chaque ouverture du port).
        self._invalid_logs = {}
        self._seen_crc_errors = 0
        self._seen_length_errors = 0

    # ------------------------------------------------------------ état

    def _set_state(self, state):
        if state != self.state:
            log.info("# DMD : %s -> %s", self.state, state)
            self.state = state
            self._state_changed()

    def _hello_req(self, now):
        self._out.clear()
        self._out.append(D.encode(D.HELLO_REQ))
        self._handshake_deadline = now + self.cfg.handshake_timeout_ms * 1000

    # ------------------------------------------------------------ port

    def serial_opened(self):
        self._decoder = D.Decoder()        # pas de vidage de la ligne (32e complément)
        self._seen_crc_errors = 0
        self._seen_length_errors = 0
        now = self.clock()
        self._last_rx = now
        self._need_frame = True
        self._set_state(HANDSHAKE)
        self._hello_req(now)

    def serial_closed(self):
        self._out.clear()
        self.fw = None
        self._need_frame = True
        self._set_state(ABSENT)

    def serial_data(self, data):
        if self.state == ABSENT or not data:
            return
        frames = self._decoder.feed(data)
        self._decoder_errors()
        for ftype, payload in frames:
            self._last_rx = self.clock()
            self._on_frame(ftype, payload)

    # ------------------------------------------------------------ trames invalides

    def _log_invalid(self, key, count, text):
        entry = self._invalid_logs.get(key)
        if entry is None:
            entry = self._invalid_logs[key] = _LimitedLog()
        entry.add(self.clock(), count, text)

    def _decoder_errors(self):
        """Hausse des compteurs CRC et longueur du Decoder depuis le dernier appel."""
        dec = self._decoder
        crc = dec.crc_errors - self._seen_crc_errors
        length = dec.length_errors - self._seen_length_errors
        self._seen_crc_errors = dec.crc_errors
        self._seen_length_errors = dec.length_errors
        if crc:
            self._log_invalid("crc", crc, "bad frame CRC")
        if length:
            self._log_invalid("length", length, "bad frame length")

    def _invalid_reply(self, ftype, p):
        """Réponse plus courte que la longueur fixe de son type (§ 6.2, 40e complément)."""
        name = REPLY_NAMES[ftype]
        self.counters["invalid_replies"][name] += 1
        self._log_invalid(name, 1, "invalid %s (%d bytes)" % (name, len(p)))

    def _unexpected_reply(self, ftype, p):
        name = UNEXPECTED_NAMES.get(ftype)
        key = name or "unknown"
        self.counters["unexpected_replies"][key] += 1
        shown = name or "type 0x%02X" % ftype
        self._log_invalid(key, 1, "unexpected %s (%d bytes)" % (shown, len(p)))

    def _on_frame(self, ftype, p):
        if ftype == D.HELLO:
            try:
                proto, width, height, fw, _mv = D.parse_hello(p)
            except D.PayloadError:
                self._invalid_reply(ftype, p)
                return
            self.fw = "%d.%d.%d" % fw
            if proto != D.PROTO_VERSION or (width, height) != (D.WIDTH, D.HEIGHT):
                self._event("DMD", "Unexpected board : protocol %d, %dx%d" % (proto, width, height))
                if self.state == CONNECTED:
                    self._set_state(HANDSHAKE)
                self._state_changed()          # dmd_fw
                return
            # HELLO attendu, ou non demandé (la carte a redémarré) : MODE 0 puis FRAME.
            self._out.clear()
            self._out.append(D.encode(D.MODE, bytes([D.MODE_PI])))
            self._need_frame = True
            if self.state == CONNECTED:
                log.info("# DMD : HELLO received, board restarted")
                self._state_changed()
            self._set_state(CONNECTED)
        elif ftype == D.ERROR:
            try:
                code, text = D.parse_error(p)
            except D.PayloadError:
                self._invalid_reply(ftype, p)
                return
            self.counters["board_errors"] += 1
            log.warning("# DMD : board error 0x%02X : %s", code, text)
            self._event("DMD", "Error 0x%02X : %s" % (code, text[:80]))
            self._need_frame = True
        elif ftype == D.TELEMETRY:
            try:
                self.telemetry = D.parse_telemetry(p)
            except D.PayloadError:
                self._invalid_reply(ftype, p)
                return
            self.counters["telemetry"] += 1
            log.debug("# DMD : telemetry %d mV, %d dC, %d fps, %d lost", *self.telemetry)
        else:
            self._unexpected_reply(ftype, p)

    # ------------------------------------------------------------ rendu

    def image(self, payload):
        """Image complète publiée par le service de rendu."""
        payload = bytes(payload)
        now = self.clock()
        if len(payload) != D.PIXELS_BYTES:
            self.counters["images_rejected"] += 1
            if self._next_reject_event is None or now >= self._next_reject_event:
                self._next_reject_event = now + REJECT_EVENT_PERIOD_US
                self._event("DMD", "Image of %d bytes rejected" % len(payload))
            return
        self.latest = payload
        self._latest_us = now
        self._dirty = True                 # même identique : DELTA à masque nul (32e complément)

    # ------------------------------------------------------------ temps

    def tick(self):
        now = self.clock()
        for entry in self._invalid_logs.values():  # cas restés en attente, minute écoulée
            entry.flush(now)
        if self.state == ABSENT:
            return
        if self.state == CONNECTED and now - self._last_rx >= self.cfg.dmd_silence_ms * 1000:
            log.warning("# DMD : no frame from the board for %d ms", self.cfg.dmd_silence_ms)
            self._last_rx = now
            self._need_frame = True
            self._set_state(HANDSHAKE)
            self._hello_req(now)
            return
        if self.state == HANDSHAKE and now >= self._handshake_deadline:
            self._hello_req(now)

    def _is_stale(self, now):
        stale = (self._latest_us is None
                 or now - self._latest_us >= self.cfg.dmd_stale_ms * 1000)
        if stale and not self._stale:
            log.info("# DMD : no image from the renderer for %d ms, sending stopped",
                     self.cfg.dmd_stale_ms)
        if stale:
            self._need_frame = True        # reprise par un FRAME (§ 9.5)
        self._stale = stale
        return stale

    def take_serial(self):
        """Prochaine trame à écrire à la carte, ou None."""
        if self.state == ABSENT:
            return None
        if self._out:
            return self._out.popleft()
        if self.state != CONNECTED:
            return None
        now = self.clock()
        if self._is_stale(now):
            return None
        if self._next_slot_us is not None and now < self._next_slot_us:
            return None
        full_due = (self._last_full_us is None
                    or now - self._last_full_us >= self.cfg.dmd_full_period_ms * 1000)
        if not (self._dirty or full_due or self._need_frame):
            return None
        return self._send_image(now, full_due)

    def _send_image(self, now, full_due):
        number = (self._sent_no + 1) & 0xFFFF
        new = self.latest
        payload = None
        if not (full_due or self._need_frame) and self.sent_image is not None:
            payload = D.delta_payload(number, self._sent_no, self.sent_image, new)
        if payload is None:                # FRAME demandé, ou DELTA de 8 194 octets ou plus
            frame = D.encode(D.FRAME, D.frame_payload(number, new))
            self._last_full_us = now
            self._need_frame = False
            self.counters["frames"] += 1
        else:
            frame = D.encode(D.DELTA, payload)
            self.counters["deltas"] += 1
        self._sent_no = number
        self.sent_image = new
        self._dirty = False
        # Créneaux fixes : la moyenne reste à 15 par seconde au plus, sans perdre d'image
        # quand le rendu, lui aussi à 15 par seconde, arrive un peu avant le créneau.
        slot = self._next_slot_us
        if slot is None or now - slot >= IMAGE_PERIOD_US:
            slot = now
        self._next_slot_us = slot + IMAGE_PERIOD_US
        return frame
