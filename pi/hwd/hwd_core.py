# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Cœur de hwd : toute la logique, sans entrée-sortie (spécification § 9.5).

Le démon (hwd_daemon.py) branche ce cœur sur le port série et sur MQTT :
    core.serial_opened() / serial_data(octets) / serial_closed()
    core.bus_message(sujet, contenu)
    core.tick()                      au moins toutes les 10 ms
    core.take_serial()  -> une ligne à écrire au Pico (octets) ou None
    core.dmd                         liaison avec l'Interstate 75 W (hwd_dmd.py), même schéma
    core.take_publishes() -> [(sujet, contenu, retenu)]
L'horloge (en µs) est injectée : les tests contrôlent le temps.
"""

import json
import logging
from collections import deque

import hwbus
import pico_proto as P
from hwd_dmd import DmdLink

log = logging.getLogger("hwd")

ABSENT = "absent"
HANDSHAKE = "handshake"
CONNECTED = "connected"


class HwdCore:
    def __init__(self, cfg, clock):
        self.cfg = cfg
        self.clock = clock
        self.topics = hwbus.topics(cfg.topic_base)
        self.state = ABSENT
        self.fw = None
        self.uid = None
        self._publishes = []
        self.dmd = DmdLink(cfg, clock, self._dmd_event, self._publish_status)
        self._splitter = P.LineSplitter()
        self._hi = deque()                 # commandes, PING, initialisation
        self._led_seq = deque()            # LEDCFG puis LEDF d'une image en cours
        self._pending = {}                 # tag -> (ligne, échéance)
        self._tag = 0
        self._caps_wanted = False
        # Vidage de la ligne à l'ouverture du port (§ 9.5, 28e complément).
        self._flushing = False
        self._flushed_lines = 0
        self._raw_out = None               # « \n » sans préfixe @n, envoyé une fois
        # État voulu, connu de hwd (restauré à la connexion : D7).
        self.params = cfg.params()
        self.toys = [0] * P.TOYS
        self.lights = [0] * P.LIGHTS
        # Luminosité (§ 3.8, v0.3.14) : led_brightness est un plafond de puissance ;
        # le Pico reçoit LEDB = min(valeur du jeu, plafond), le plafond sans valeur du jeu.
        self.game_brightness = [None] * P.LED_CHANNELS
        self.cal = 0
        self._restore = {}                 # ("T"|"L", n) -> (état d'avant, échéance) : D9
        # LED (R5).
        self.led_latest = [None] * P.LED_CHANNELS
        self.led_dirty = [False] * P.LED_CHANNELS
        self.led_last_sent = [None] * P.LED_CHANNELS
        self.led_count_on_pico = [None] * P.LED_CHANNELS
        self._led_next = 0
        # Fusion choc / fléchette (D3).
        self._recent_darts = deque()       # (t_µs du Pico, heure locale)
        self._pending_shocks = []          # (t_µs du Pico, échéance locale)
        # Temps.
        now = clock()
        self._last_rx = now
        self._next_ping = now
        self._handshake_deadline = now
        self.counters = {"darts": 0, "miss": 0, "shocks_absorbed": 0, "led_frames": 0,
                         "led_frames_dropped": 0, "nacks": 0, "timeouts": 0}
        self._publish_status()

    # ------------------------------------------------------------ sorties

    def take_publishes(self):
        out, self._publishes = self._publishes, []
        return out

    def _publish(self, topic, payload, retain=False):
        self._publishes.append((topic, payload, retain))

    def _event(self, line):
        self._publish(self.topics["events"], line)

    def _publish_status(self):
        status = {"pico": self.state, "fw": self.fw, "uid": self.uid}
        dmd = getattr(self, "dmd", None)
        if dmd is not None:                # 32e complément : absent si dmd_device est vide
            status["dmd"] = dmd.state
            status["dmd_fw"] = dmd.fw
        self._publish(self.topics["status"], json.dumps(status, sort_keys=True), True)

    def _dmd_event(self, code, text):
        self._event(hwbus.ev_error(code, text))

    def _set_state(self, state):
        if state != self.state:
            log.info("# Pico : %s -> %s", self.state, state)
            self.state = state
            self._publish_status()

    def _send(self, line):
        self._hi.append(line)

    def take_serial(self):
        """Prochaine ligne à écrire au Pico, étiquetée @n (sauf la ligne vide du vidage), ou None."""
        if self.state == ABSENT:
            return None
        if self._raw_out is not None:
            out, self._raw_out = self._raw_out, None
            return out
        if self._flushing:
            return None
        if self._hi:
            line = self._hi.popleft()
        elif self.state == CONNECTED and (self._led_seq or self._schedule_led()):
            line = self._led_seq.popleft()
        else:
            return None
        tag = self._tag
        self._tag = (self._tag + 1) & 0xFFFF
        self._pending[tag] = (line, self.clock() + self.cfg.ack_timeout_ms * 1000)
        return P.encode_line("@%d %s" % (tag, line))

    # ---------------------------------------------------------- port série

    def serial_opened(self):
        self._splitter.reset()
        self._hi.clear()
        self._led_seq.clear()
        self._pending.clear()
        self.led_count_on_pico = [None] * P.LED_CHANNELS
        now = self.clock()
        self._last_rx = now
        self._set_state(HANDSHAKE)
        self._start_flush(now)

    def serial_closed(self):
        self._hi.clear()
        self._led_seq.clear()
        self._pending.clear()
        self._pending_shocks = []
        self._recent_darts.clear()
        self.toys = [0] * P.TOYS                  # un Pico rebranché démarre à 0
        self._restore.clear()
        self.fw = None
        self.uid = None
        self._flushing = False
        self._raw_out = None
        self._set_state(ABSENT)

    def _start_flush(self, now):
        """Vidage (§ 9.5) : « \n » sans préfixe, lignes ignorées pendant handshake_flush_ms.

        Termine en une ligne à part les octets laissés dans le Pico (mpremote), qui
        sinon se colleraient au premier WHO et le feraient perdre. Seulement à
        l'ouverture du port, jamais au retour à WHO après un silence.
        """
        self._hi.clear()
        self._led_seq.clear()
        self._caps_wanted = False
        self._flushing = True
        self._flushed_lines = 0
        self._raw_out = b"\n"
        self._handshake_deadline = now + self.cfg.handshake_flush_ms * 1000

    def _who(self, now):
        if self._flushing:
            self._flushing = False
            log.debug("# Pico : %d line(s) flushed before WHO", self._flushed_lines)
        self._hi.clear()
        self._led_seq.clear()
        self._caps_wanted = False
        self._send("WHO")
        self._handshake_deadline = now + self.cfg.handshake_timeout_ms * 1000

    def serial_data(self, data):
        if self.state == ABSENT or not data:
            return
        self._last_rx = self.clock()
        for item in self._splitter.feed(data):
            if self._flushing:
                self._flushed_lines += 1           # ignorée, sans événement publié
                continue
            if isinstance(item, P.ProtocolError):
                log.warning("# Pico : line rejected (%s)", item.code)
                continue
            try:
                msg = P.parse_event(item)
            except P.ProtocolError as e:
                log.warning("# Pico : invalid line (%s) : %r", e.code, item[:80])
                continue
            self._on_pico(msg)

    def _on_pico(self, msg):
        kind = msg[0]
        now = self.clock()
        if kind == "HELLO":
            self.fw = "%d.%d.%d" % msg[3]
            self.uid = msg[4]
            if msg[1] != P.PROTO_VERSION:
                self._event(hwbus.ev_error("PROTO", "version %d" % msg[1]))
                self._caps_wanted = False
                return
            if self.state == CONNECTED:
                log.info("# Pico : unrequested HELLO, the Pico restarted")
                self.toys = [0] * P.TOYS
                self._restore.clear()
                self._set_state(HANDSHAKE)
            self._caps_wanted = True
        elif kind == "CAPS":
            if self._caps_wanted:
                self._caps_wanted = False
                caps = msg[1]
                if any(caps.get(k) != v for k, v in P.CAPS):
                    self._event(hwbus.ev_error("CAPS", "Unexpected board"))
                    return
                self._initialise()
                self._set_state(CONNECTED)
        elif kind == "D":
            self._dart(msg[1], msg[2], msg[3], msg[4], now)
        elif kind == "X":
            self._event(hwbus.ev_reject(msg[1], msg[2], msg[3]))
        elif kind == "B":
            self._event(hwbus.ev_button(msg[1], msg[2], msg[3]))
        elif kind == "BS":
            self._event(hwbus.ev_buttons_state(msg[1]))
        elif kind == "S":
            self._shock(msg[1], msg[2], now)
        elif kind == "E":
            self._event(hwbus.ev_error(msg[1], msg[2]))
            if msg[1] == P.E_WDG and msg[2] == "timeout":
                # Le Pico a tout coupé : les Toys ne seront pas rallumés (D7).
                self.toys = [0] * P.TOYS
                self._restore.clear()
            elif msg[1] == P.E_WDG and msg[2] == "resumed" and self.state == CONNECTED:
                self._initialise()
        elif kind == "K":
            self._pending.pop(msg[1], None)
        elif kind == "N":
            line = self._pending.pop(msg[1], ("?", 0))[0] if msg[1] is not None else "?"
            self.counters["nacks"] += 1
            self._event(hwbus.ev_error("NACK", "%s %s" % (msg[2], line[:60])))
        elif kind == "#":
            log.info("# Pico : %s", msg[1])

    def brightness(self, ch):
        """LEDB envoyé au Pico : la valeur du jeu, plafonnée (jamais un multiplicateur)."""
        cap = self.cfg.led_brightness[ch]
        game = self.game_brightness[ch]
        return cap if game is None else min(game, cap)

    def _initialise(self):
        """Remise en état après HELLO + CAPS ou E WDG resumed (D7, D8)."""
        for key, value in sorted(self.params.items()):
            if key.endswith("_count"):
                continue                           # vient des images (LEDCFG)
            self._send("SET %s %s" % (key, P.format_param_value(value)))
        for ch in range(P.LED_CHANNELS):
            self._send("LEDB %d %d" % (ch, self.brightness(ch)))
            # Nombre de LED : celui de la dernière image reçue, sinon celui de la
            # configuration (led<n>_count, 204 sur le canal 0 par défaut, § 3.5).
            latest = self.led_latest[ch]
            count = len(latest) // 3 if latest is not None else self.params["led%d_count" % ch]
            self._send("LEDCFG %d %d" % (ch, count))
            self.led_count_on_pico[ch] = count
            if latest is not None:
                self.led_dirty[ch] = True          # dernière image renvoyée
        self._send("LGTS %X" % _mask(self.lights))  # voyants : oui ; Toys : non (D7)
        if self.cal:
            self._send("CAL 1")
        self._send("BTNPOLL")                      # le jeu resynchronise ses boutons
        self._next_ping = self.clock()

    # --------------------------------------------------------- fusion (D3)

    def _window_us(self):
        return self.cfg.miss_window_ms * 1000

    def _dart(self, row, col, t, dur, now):
        self.counters["darts"] += 1
        self._event(hwbus.ev_dart(row, col, t, dur))
        w = self._window_us()
        kept = []
        for ts, deadline in self._pending_shocks:
            if abs(P.ticks_diff(t, ts)) <= w:
                self.counters["shocks_absorbed"] += 1
            else:
                kept.append((ts, deadline))
        self._pending_shocks = kept
        self._recent_darts.append((t, now))

    def _shock(self, t, peak, now):
        self._event(hwbus.ev_shock(t, peak))
        w = self._window_us()
        for td, _ in self._recent_darts:
            if abs(P.ticks_diff(td, t)) <= w:
                self.counters["shocks_absorbed"] += 1
                return
        self._pending_shocks.append((t, now + w + self.cfg.miss_grace_ms * 1000))

    def _fusion_tick(self, now):
        keep = []
        for ts, deadline in self._pending_shocks:
            if now >= deadline:
                self.counters["miss"] += 1
                self._event(hwbus.ev_miss(ts))
            else:
                keep.append((ts, deadline))
        self._pending_shocks = keep
        horizon = 2 * self._window_us() + self.cfg.miss_grace_ms * 1000
        while self._recent_darts and now - self._recent_darts[0][1] > horizon:
            self._recent_darts.popleft()

    # ------------------------------------------------------------- temps

    def tick(self):
        now = self.clock()
        self.dmd.tick()
        self._fusion_tick(now)
        if self.state == ABSENT:
            return
        if self.state == CONNECTED and now - self._last_rx >= self.cfg.pico_silence_ms * 1000:
            self._event(hwbus.ev_error("SILENCE", "No news from the Pico"))
            self._last_rx = now
            self._set_state(HANDSHAKE)
            self._pending.clear()
            self._who(now)
            return
        if self.state == HANDSHAKE:
            if now >= self._handshake_deadline:
                self._who(now)
            return
        if now >= self._next_ping:
            self._send("PING")
            self._next_ping = now + self.params["watchdog_ms"] * 1000 // 3
        for tag in [t for t, (_, d) in self._pending.items() if now >= d]:
            line = self._pending.pop(tag)[0]
            self.counters["timeouts"] += 1
            self._event(hwbus.ev_error("TIMEOUT", line[:60]))
        for key in [k for k, (_, d) in self._restore.items() if now >= d]:
            prev = self._restore.pop(key)[0]
            if prev:
                self._send("%s %d 1" % ("TOY" if key[0] == "T" else "LGT", key[1]))

    # --------------------------------------------------------------- bus

    def bus_message(self, topic, payload):
        led = self.topics["led"]
        if topic == self.topics["dmd_frame"]:
            self.dmd.image(payload)
        elif topic.startswith(led):
            self._led_frame(topic[len(led):], payload)
        elif topic == self.topics["cmd"]:
            try:
                text = payload.decode() if isinstance(payload, (bytes, bytearray)) else payload
                name, args = hwbus.parse_command(text.strip())
            except (UnicodeError, hwbus.BusError) as e:
                self._event(hwbus.ev_error("CMD", str(e)[:100]))
                return
            self._command(name, args)

    def _command(self, name, a):
        online = self.state == CONNECTED
        now = self.clock()
        if name == hwbus.OFF:
            self.toys = [0] * P.TOYS
            self.lights = [0] * P.LIGHTS
            self._restore.clear()
            self.led_latest = [None] * P.LED_CHANNELS
            self.led_dirty = [False] * P.LED_CHANNELS
            self._led_seq.clear()
            if online:
                self._send("TOYS 0")
                self._send("LGTS 0")
                for ch in range(P.LED_CHANNELS):
                    self._send("LEDOFF %d" % ch)
            return
        if name in ("TOY", "LGT", "TOYSTOP", "LGTSTOP", "TOYPULSE", "LGTPULSE"):
            kind = "T" if name.startswith("TOY") else "L"
            arr = self.toys if kind == "T" else self.lights
            n = a[0]
            self._restore.pop((kind, n), None)
            if name in ("TOY", "LGT"):
                arr[n - 1] = a[1]
                line = "%s %d %d" % (name, n, a[1])
            elif name.endswith("STOP"):
                arr[n - 1] = 0
                line = "%s %d" % (name, n)
            else:
                on, off, rep = a[1], a[2], a[3]
                if rep:
                    # D9 : l'état d'avant revient à la fin prévue ; arr garde cet état.
                    self._restore[(kind, n)] = (arr[n - 1], now + rep * (on + off) * 1000)
                else:
                    arr[n - 1] = 0                 # continu : rien à restaurer
                line = "%s %d %d %d %d" % (name, n, on, off, rep)
            if online:
                self._send(line)
            elif kind == "T":
                self.toys[n - 1] = 0               # Toys non mémorisés hors connexion (D7)
                self._restore.pop((kind, n), None)
            return
        if name in ("TOYS", "LGTS"):
            kind = "T" if name == "TOYS" else "L"
            arr = self.toys if kind == "T" else self.lights
            for k in [k for k in self._restore if k[0] == kind]:
                del self._restore[k]
            for i in range(len(arr)):
                arr[i] = (a[0] >> i) & 1 if (online or kind == "L") else 0
            if online:
                self._send("%s %X" % (name, a[0]))
            return
        if name == "LEDB":
            self.game_brightness[a[0]] = a[1]
            if online:
                self._send("LEDB %d %d" % (a[0], self.brightness(a[0])))
            return
        if name == "LEDOFF":
            self.led_latest[a[0]] = None
            self.led_dirty[a[0]] = False
        elif name == "CAL":
            self.cal = a[0]
        elif name == "SET":
            self.params[a[0]] = a[1]
        line = " ".join([name] + [P.format_param_value(v) for v in a])
        if online:
            self._send(line)

    # ---------------------------------------------------------- LED (R5)

    def _led_frame(self, suffix, payload):
        try:
            ch = P.parse_dec(suffix, 0, P.LED_CHANNELS - 1)
        except P.ProtocolError:
            self._event(hwbus.ev_error("LED", "Unknown channel %r" % suffix[:8]))
            return
        payload = bytes(payload)
        if len(payload) % 3 or len(payload) > 3 * P.DEFAULT_LIMITS.max_leds:
            self.counters["led_frames_dropped"] += 1
            self._event(hwbus.ev_error("LED", "Image of %d bytes rejected" % len(payload)))
            return
        if self.led_dirty[ch]:
            self.counters["led_frames_dropped"] += 1     # remplacée avant envoi
        self.led_latest[ch] = payload
        self.led_dirty[ch] = True

    def _schedule_led(self):
        """Prépare l'envoi d'une image en attente si le débit le permet."""
        now = self.clock()
        period = 1000000 // self.cfg.led_max_fps
        for i in range(P.LED_CHANNELS):
            ch = (self._led_next + i) % P.LED_CHANNELS
            if not self.led_dirty[ch]:
                continue
            last = self.led_last_sent[ch]
            if last is not None and now - last < period:
                continue
            frame = self.led_latest[ch]
            self.led_dirty[ch] = False
            self.led_last_sent[ch] = now
            self._led_next = ch + 1
            count = len(frame) // 3
            if self.led_count_on_pico[ch] != count:
                self._led_seq.append("LEDCFG %d %d" % (ch, count))
                self.led_count_on_pico[ch] = count
            if count:
                self._led_seq.append(P.command("LEDF", ch, frame))
            self.counters["led_frames"] += 1
            return bool(self._led_seq)
        return False


def _mask(bits):
    m = 0
    for i, b in enumerate(bits):
        if b:
            m |= 1 << i
    return m
