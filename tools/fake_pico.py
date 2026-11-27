# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Faux Pico : simule la carte Pico côté protocole (spécification § 5).

Ce n'est pas le firmware : la logique matérielle (balayage, MCP23017, PIO)
est remplacée par des méthodes d'injection d'événements. Le comportement
protocolaire, lui, suit la spécification, et sert de référence aux tests du
démon matériel.

Utilisation en mémoire (tests) :
    pico = FakePico(clock=horloge_en_µs)
    pico.boot()                 -> octets HELLO + CAPS
    pico.feed(b"@1 PING\\n")     -> b"PONG ...\\nK 1\\n"
    pico.dart(3, 4, 2000)       -> b"D 3 4 ... 2000\\n"
    FakePico(closed_at_start=((3, 4),)).boot()  -> HELLO, CAPS, E STUCK 3 4
    pico.release(3, 4)          -> case rouverte (fin du 5 Hz), aucun octet
    pico.tick()                 -> chien de garde, stroboscopes

Sur un pseudo-terminal (essais à la main) :
    python3 tools/fake_pico.py [--closed <ligne>,<col> ...]
puis, sur l'entrée standard : d <ligne> <col> [durée_µs] ; b <n> <0|1> ;
s [crête] ; o <ligne> <col> (ouvre une case fermée au démarrage) ; q.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "shared"))
sys.path.insert(0, _HERE)

import pico_proto as P  # noqa: E402

FW_VERSION = (0, 1, 0)
FAKE_UID = "E6614103E7452D2F"

# Voyant de vie sur GP16 (spécification § 3.1.2) : LED vers +3V3, allumée au
# niveau bas. Demi-périodes : 1 Hz en fonctionnement normal, 5 Hz sur erreur.
HEARTBEAT_HALF_MS = {"normal": 500, "error": 100}


class FakePico:
    def __init__(self, clock=None, uid=FAKE_UID, limits=P.DEFAULT_LIMITS, missing_mcp=(),
                 closed_at_start=()):
        if clock is None:
            from ptypair import monotonic_us
            clock = monotonic_us
        self.clock = clock
        self.uid = uid
        self.limits = limits
        self.missing_mcp = tuple(missing_mcp)
        # Cases physiquement fermées (segment coincé, nappe en court-circuit) :
        # vues dès le premier balayage, donc E STUCK au démarrage (§ 7.2 point 3).
        self.closed = set(tuple(rc) for rc in closed_at_start)
        self.stuck = set()                           # cases signalées E STUCK au démarrage
        self.gp16 = 1                                # avant le firmware : éteinte
        self.booted = False
        self._hb_last = None
        self.saved = P.default_params(limits)       # « fichier » de paramètres
        self.save_count = 0
        self.reboots = 0
        self._splitter = P.LineSplitter(limits)
        self._reset_state()

    # ------------------------------------------------------------ état

    def _reset_state(self):
        self.params = dict(self.saved)
        self.toys = [0] * P.TOYS
        self.lights = [0] * P.LIGHTS
        self.pulses = {}                             # ("T"|"L", n) -> [on, off, rep, début]
        self.led_brightness = [255] * P.LED_CHANNELS
        self.led_rgb = [b""] * P.LED_CHANNELS
        for ch in range(P.LED_CHANNELS):
            self._resize_leds(ch)
        self.cal = 0
        self.buttons = 0
        self.wdg_tripped = False
        self._last_shock = None
        self._splitter.reset()
        self.last_rx = self.clock()

    def _resize_leds(self, ch):
        self.led_rgb[ch] = bytes(3 * self.params["led%d_count" % ch])

    def outputs_off(self):
        """État de repos : Toys, voyants à 0, stroboscopes arrêtés, LED noires."""
        self.toys = [0] * P.TOYS
        self.lights = [0] * P.LIGHTS
        self.pulses = {}
        for ch in range(P.LED_CHANNELS):
            self._resize_leds(ch)

    def all_outputs_zero(self):
        return (not any(self.toys) and not any(self.lights) and not self.pulses
                and not any(any(b) for b in self.led_rgb))

    def led_wire(self, ch):
        """Octets tels qu'envoyés aux WS2812 : ordre led<n>_order, luminosité appliquée."""
        order = self.params["led%d_order" % ch]
        idx = ["RGB".index(c) for c in order]
        k = self.led_brightness[ch] + 1            # comme rpi_ws281x : (c × (b + 1)) >> 8
        rgb = self.led_rgb[ch]
        out = bytearray()
        for i in range(0, len(rgb), 3):
            for j in idx:
                out.append(rgb[i + j] * k >> 8)
        return bytes(out)

    # -------------------------------------------------------- émission

    def _now(self):
        return self.clock()

    def _t(self):
        return self._now() & P.TICKS_MASK

    @staticmethod
    def _out(lines):
        return b"".join(P.encode_line(s) for s in lines)

    def boot(self):
        """Démarrage : sorties à zéro, puis HELLO et CAPS (et erreurs MCP simulées)."""
        self._reset_state()
        self.booted = True
        self.gp16 = 1                                # éteinte dès le début de main.py
        self._hb_last = self._now()
        lines = [P.msg_hello(FW_VERSION, self.uid), P.msg_caps()]
        for addr in self.missing_mcp:
            lines.append(P.msg_error(P.E_MCP, "%s absent" % addr))
        self.stuck = set(self.closed)
        for row, col in sorted(self.stuck):
            lines.append(P.msg_error("STUCK", "%d %d" % (row, col)))
        return self._out(lines)

    # ------------------------------------------------- voyant de vie

    @property
    def heartbeat_on(self):
        """Voyant de vie allumé (GP16 au niveau bas)."""
        return self.gp16 == 0

    def heartbeat_mode(self):
        """« normal » (1 Hz) ou « error » (5 Hz : MCP23017 absent, case fermée au démarrage)."""
        return "error" if self.missing_mcp or self.stuck else "normal"

    def _update_heartbeat(self, now):
        # Un tour de la boucle principale du cœur 0 : au plus un basculement,
        # référence repartant de l'instant courant (pas de rattrapage).
        if not self.booted:
            return
        if (now - self._hb_last) // 1000 >= HEARTBEAT_HALF_MS[self.heartbeat_mode()]:
            self.gp16 ^= 1
            self._hb_last = now

    # ------------------------------------------------------- temps

    def _update_pulses(self, now):
        for key in list(self.pulses):
            on, off, rep, start = self.pulses[key]
            elapsed = (now - start) // 1000
            period = on + off
            state = 1 if elapsed % period < on else 0
            if rep and elapsed // period >= rep:
                del self.pulses[key]
                state = 0
            arr = self.toys if key[0] == "T" else self.lights
            arr[key[1] - 1] = state

    def tick(self):
        now = self._now()
        lines = []
        if not self.wdg_tripped and now - self.last_rx >= self.params["watchdog_ms"] * 1000:
            self.outputs_off()
            self.wdg_tripped = True
            lines.append(P.msg_error(P.E_WDG, "timeout"))
        self._update_pulses(now)
        self._update_heartbeat(now)
        return self._out(lines)

    # ------------------------------------------------------- réception

    def feed(self, data):
        out = [self.tick()]                         # un délai écoulé avant ces octets compte
        if data:
            self.last_rx = self._now()
        for item in self._splitter.feed(data):
            lines = []
            if self.wdg_tripped:
                self.wdg_tripped = False
                lines.append(P.msg_error(P.E_WDG, "resumed"))
            if isinstance(item, P.ProtocolError):
                lines.append(P.msg_nack(None, item.code))
            else:
                lines += self.handle_line(item)
            out.append(self._out(lines))
        return b"".join(out)

    def handle_line(self, line):
        """Traite une ligne ; renvoie les lignes de réponse. Aucun effet si refusée."""
        tag = None
        try:
            tag, name, args = P.parse_command(line, self.limits)
            data, after = self._execute(name, args)
        except P.ProtocolError as e:
            # Refus à l'exécution (LEDF de mauvaise longueur) : garder le préfixe (§ 5.1).
            return [P.msg_nack(e.tag if e.tag is not None else tag, e.code)]
        lines = list(data)
        if tag is not None:
            lines.append(P.msg_ack(tag))
        if after:
            lines += after()
        return lines

    def _execute(self, name, a):
        """Renvoie (lignes de données, action à faire après la réponse ou None)."""
        if name == "PING":
            return [P.msg_pong(self._t())], None
        if name == "WHO":
            return [P.msg_hello(FW_VERSION, self.uid), P.msg_caps()], None
        if name == "GET":
            if a[0] in P.READ_ONLY_KEYS:              # le faux Pico ne bride pas : 0
                return [P.msg_value(a[0], "0")], None
            return [P.msg_value(a[0], P.format_param_value(self.params[a[0]]))], None
        if name == "SET":
            self._set_param(a[0], a[1])
            return [], None
        if name == "SAVE":
            self.saved = dict(self.params)
            self.save_count += 1
            return [], None
        if name == "DEFAULTS":
            for k, v in P.default_params(self.limits).items():
                self._set_param(k, v)
            return [], None
        if name in ("TOY", "LGT"):
            kind = "T" if name == "TOY" else "L"
            self.pulses.pop((kind, a[0]), None)
            (self.toys if kind == "T" else self.lights)[a[0] - 1] = a[1]
            return [], None
        if name in ("TOYS", "LGTS"):
            kind = "T" if name == "TOYS" else "L"
            arr = self.toys if kind == "T" else self.lights
            for k in [k for k in self.pulses if k[0] == kind]:
                del self.pulses[k]
            for i in range(len(arr)):
                arr[i] = (a[0] >> i) & 1
            return [], None
        if name in ("TOYPULSE", "LGTPULSE"):
            kind = "T" if name == "TOYPULSE" else "L"
            self.pulses[(kind, a[0])] = [a[1], a[2], a[3], self._now()]
            self._update_pulses(self._now())
            return [], None
        if name in ("TOYSTOP", "LGTSTOP"):
            kind = "T" if name == "TOYSTOP" else "L"
            self.pulses.pop((kind, a[0]), None)
            (self.toys if kind == "T" else self.lights)[a[0] - 1] = 0
            return [], None
        if name == "LEDCFG":
            self._set_param("led%d_count" % a[0], a[1])
            return [], None
        if name == "LEDB":
            self.led_brightness[a[0]] = a[1]
            return [], None
        if name == "LEDF":
            ch, rgb = a
            if len(rgb) != 3 * self.params["led%d_count" % ch]:
                raise P.ProtocolError(P.ARGS)
            self.led_rgb[ch] = bytes(rgb)
            return [], None
        if name == "LEDOFF":
            self._resize_leds(a[0])
            return [], None
        if name == "CAL":
            self.cal = a[0]
            return [], None
        if name == "BTNPOLL":
            return [P.msg_buttons_state(self.buttons)], None
        if name == "MCPRESET":
            self.toys = [0] * P.TOYS
            self.lights = [0] * P.LIGHTS
            self.pulses = {}
            return [], None
        if name == "REBOOT":
            def reboot():
                self.reboots += 1
                return [s.decode() for s in self.boot().splitlines()]
            return [], reboot
        raise P.ProtocolError(P.UNKNOWN)

    def _set_param(self, key, value):
        old = self.params.get(key)
        self.params[key] = value
        if key.endswith("_count") and value != old:
            self._resize_leds(int(key[3]))

    # ------------------------------------------- injection d'événements

    def dart(self, row, col, dur_us):
        """Contact de durée dur_us sur (ligne, colonne), filtré comme le firmware (§ 7.2).

        D part au contact validé ; son champ durée vaut ici dur_us (diagnostic
        seulement). Au-delà de hold_max_ms : D puis E STUCK <ligne> <colonne>.
        """
        if (row, col) in self.stuck:
            return b""                                # case encore fermée : bloquée, rien
        p = self.params
        if self.cal:
            line = P.msg_dart(row, col, self._t(), dur_us)
        elif dur_us < p["min_contact_us"]:
            if not p["reject_report"]:
                return b""
            line = P.msg_reject(row, col, dur_us)
        elif dur_us > p["hold_max_ms"] * 1000:
            return self._out([P.msg_dart(row, col, self._t(), dur_us),
                              P.msg_error("STUCK", "%d %d" % (row, col))])
        else:
            line = P.msg_dart(row, col, self._t(), dur_us)
        return self._out([line])

    def release(self, row, col):
        """Ouvre une case fermée au démarrage : libération après release_ms, ni D ni X.

        Le voyant de vie revient à 1 Hz ; le contact suivant sur la case est normal.
        """
        self.closed.discard((row, col))
        self.stuck.discard((row, col))
        return b""

    def button(self, n, pressed):
        bit = 1 << (n - 1)
        state = bool(self.buttons & bit)
        if state == bool(pressed):
            return b""
        self.buttons ^= bit
        return self._out([P.msg_button(n, pressed, self._t())])

    def shock(self, value=None):
        """Valeur ADC (0 à 4 095) lue sur GP26 ; par défaut, pleine échelle active.

        § 7.4 : la lecture passe toujours par l'ADC et le seuil s'applique dans
        les deux modes, dans le sens de `shock_polarity` ; seul le contenu de
        `S` change (crête 1 en `digital`, valeur mesurée en `adc`).
        """
        now = self._now()
        p = self.params
        low = p["shock_polarity"] == "low"
        if value is None:
            value = 0 if low else 4095
        if (value > p["shock_threshold"]) if low else (value < p["shock_threshold"]):
            return b""                                # en deçà du seuil : pas un choc
        if self._last_shock is not None and now - self._last_shock < p["shock_holdoff_ms"] * 1000:
            return b""
        self._last_shock = now
        peak = 1 if p["shock_mode"] == "digital" else value
        return self._out([P.msg_shock(now, peak)])


def main():
    import select
    from ptypair import PtyServer, stop_on_sigterm

    import argparse

    ap = argparse.ArgumentParser(description="Faux Pico sur un pseudo-terminal.")
    ap.add_argument("--closed", action="append", default=[], metavar="LIGNE,COL",
                    help="case fermée au démarrage (E STUCK, répétable)")
    args = ap.parse_args()
    closed = [tuple(int(x) for x in rc.split(",")) for rc in args.closed]
    stop_on_sigterm()
    pico = FakePico(closed_at_start=closed)
    srv = PtyServer(pico).start()
    sys.stderr.write("# faux Pico sur %s (d l c [µs] | b n 0|1 | s [crête] | o l c | q)\n" % srv.path)
    sys.stderr.flush()
    try:
        while True:
            r, _, _ = select.select([sys.stdin], [], [], 0.5)
            if not r:
                continue
            words = sys.stdin.readline().split()
            if not words or words[0] == "q":
                break
            try:
                v = [int(w) for w in words[1:]]
                if words[0] == "d":
                    srv.call(pico.dart, v[0], v[1], v[2] if len(v) > 2 else 2000)
                elif words[0] == "b":
                    srv.call(pico.button, v[0], v[1])
                elif words[0] == "s":
                    srv.call(pico.shock, v[0] if v else None)
                elif words[0] == "o":
                    srv.call(pico.release, v[0], v[1])
            except (ValueError, IndexError) as e:
                sys.stderr.write("# commande refusée : %s\n" % e)
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()


if __name__ == "__main__":
    main()
