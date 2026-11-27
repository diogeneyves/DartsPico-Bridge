# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Cœur 0 (§ 7.1) : protocole avec le Pi, paramètres, chiens de garde.

Le cœur 0 est le **seul** à écrire sur la sortie standard (canal de données,
§ 5.1). Chaque tour de la boucle principale (main.py) appelle feed() avec les
octets reçus, puis step().

- Protocole : grammaire de proto.py (copie de shared/pico_proto.py), même
  comportement que tools/fake_pico.py.
- Paramètres : SET s'applique tout de suite ; ceux du cœur 1 lui sont
  republiés (xcore.Shared) ; `bench_row_pullup` règle les tirages des lignes.
- **Chien de garde de liaison** (§ 5.4) : sans octet du Pi pendant
  `watchdog_ms`, sorties coupées et `E WDG timeout` ; `E WDG resumed` à la
  première ligne complète suivante.
- **Chien de garde matériel** (§ 5.4) : machine.WDT de 4 000 ms, démarré
  seulement par `SET hw_watchdog 1` (jamais au démarrage seul), nourri
  seulement si le compteur de vie du cœur 1 a avancé depuis le tour précédent.
- **Mode banc** (bench.py, `run(bench=True)`, § 3.1.2, § 5.1) : Ctrl-C laissé
  actif, `bench_row_pullup` 1 et `hw_watchdog` 0 au démarrage (non
  sauvegardés), ligne de journal `# HB <niveau> <tour_max_µs>` à chaque
  bascule du voyant.

run() est le point d'entrée sur le Pico : broches, cœur 1, boucle du cœur 0.
"""

import proto as P
import board
import outputs as outputs_mod
import xcore

FW_VERSION = (0, 2, 0)
HW_WDT_MS = 4000
OVERFLOW_TEXT = "%d events lost"


class NullHw:
    """Matériel absent (tests) : chaque méthode est remplacée par main.py."""

    def set_row_pullups(self, on):
        pass

    def start_wdt(self, timeout_ms):
        return None

    def save_params(self, params):
        pass

    def reset(self):
        pass

    def read_heartbeat(self):
        return -1


HB_LOG = "# HB %d %d"


class App:
    def __init__(self, clock, shared, queue, write, params, uid, hw=None,
                 outputs=None, leds=None, cfg_error=None):
        self.clock = clock
        self.shared = shared
        self.queue = queue
        self.write = write
        self.params = params
        self.uid = uid
        self.hw = hw if hw is not None else NullHw()
        self.outputs = outputs
        self.leds = leds
        self.cfg_error = cfg_error
        self.buttons = None                  # buttons.Buttons
        self.mcp = None                      # mcp23017.McpBus
        self.heartbeat = None                # heartbeat.Heartbeat (GP16)
        self.cal = 0
        self.splitter = P.LineSplitter()
        self.last_rx = clock.ticks_ms()
        self.wdg_tripped = False
        self.wdt = None
        self.life_seen = shared.life
        self.life_moved = True
        self._out = []
        self.rebooting = False
        self.bench = False                   # journal # HB (mode banc seulement)
        self.tour_max = 0                    # µs, depuis la dernière ligne # HB

    # ------------------------------------------------------------ sortie

    def emit(self, line):
        self._out.append(line)

    def flush(self):
        if self._out:
            data = "\n".join(self._out) + "\n"
            self._out = []
            self.write(data.encode())

    # ------------------------------------------------------------ démarrage

    def boot(self):
        """Sorties à zéro, paramètres appliqués, puis HELLO, CAPS (et E CFG)."""
        if self.outputs is not None:
            self.outputs.all_off()
        if self.leds is not None:
            self.leds.all_off()
        self.apply_all()
        self.emit(P.msg_hello(FW_VERSION, self.uid))
        self.emit(P.msg_caps())
        if self.cfg_error:
            self.emit(P.msg_error(P.E_CFG, self.cfg_error))
        if self.mcp is not None:
            self.mcp.boot()                  # E MCP … absent après HELLO et CAPS
        self.flush()

    def apply_all(self):
        self.hw.set_row_pullups(self.params["bench_row_pullup"] == 1)
        if self.buttons is not None:
            self.buttons.debounce_ms = self.params["btn_debounce_ms"]
        if self.leds is not None:
            for ch in range(P.LED_CHANNELS):
                self.leds.configure(ch, self.params["led%d_count" % ch],
                                    self.params["led%d_order" % ch])
            self.apply_led_power()
        self.shared.set_params(self.params, self.cal)

    def apply_led_power(self):
        """Budget de courant des LED (§ 5.3, 54e complément)."""
        self.leds.set_power(self.params["led_max_ma"],
                            [self.params["led%d_5v" % ch] for ch in range(P.LED_CHANNELS)])

    # ------------------------------------------------------------ réception

    def feed(self, data):
        if not data:
            return
        self.last_rx = self.clock.ticks_ms()
        for item in self.splitter.feed(data):
            if self.rebooting:
                break                            # machine.reset() ne revient pas (sauf en test)
            if self.wdg_tripped:
                self.wdg_tripped = False
                self.emit(P.msg_error(P.E_WDG, "resumed"))
            if isinstance(item, P.ProtocolError):
                self.emit(P.msg_nack(None, item.code))
            else:
                self.handle_line(item)
        self.flush()

    def handle_line(self, line):
        """Traite une ligne. Une commande refusée n'a aucun effet (§ 5.4)."""
        tag = None
        try:
            tag, name, args = P.parse_command(line)
            after = self.execute(name, args)
        except P.ProtocolError as e:
            # Refus à l'exécution (LEDF de mauvaise longueur) : garder le préfixe (§ 5.1).
            self.emit(P.msg_nack(e.tag if e.tag is not None else tag, e.code))
            return
        if tag is not None:
            self.emit(P.msg_ack(tag))
        if after is not None:
            self.flush()
            after()

    def execute(self, name, a):
        """Exécute une commande valide ; renvoie une action à faire après la réponse."""
        if name == "PING":
            self.emit(P.msg_pong(self.clock.ticks_us()))
        elif name == "WHO":
            self.emit(P.msg_hello(FW_VERSION, self.uid))
            self.emit(P.msg_caps())
        elif name == "GET":
            if a[0] == "led_limited":                 # lecture seule (§ 5.3, 54e complément)
                value = self.leds.limited if self.leds is not None else 0
            else:
                value = self.params[a[0]]
            self.emit(P.msg_value(a[0], P.format_param_value(value)))
        elif name == "SET":
            self.set_param(a[0], a[1])
        elif name == "SAVE":
            self.hw.save_params(self.params)
        elif name == "DEFAULTS":
            for k, v in P.default_params().items():
                self.set_param(k, v, from_set=False)
        elif name in ("TOY", "LGT", "TOYS", "LGTS", "TOYPULSE", "LGTPULSE", "TOYSTOP", "LGTSTOP"):
            self.outputs_command(name, a)
        elif name in ("LEDCFG", "LEDB", "LEDF", "LEDOFF"):
            self.led_command(name, a)
        elif name == "CAL":
            self.cal = a[0]
            self.shared.set_params(self.params, self.cal)
        elif name == "BTNPOLL":
            self.emit(P.msg_buttons_state(self.buttons.mask if self.buttons is not None else 0))
        elif name == "MCPRESET":
            self.mcp_reset()
        elif name == "REBOOT":
            return self.reboot
        else:
            raise P.ProtocolError(P.UNKNOWN)
        return None

    def set_param(self, key, value, from_set=True):
        old = self.params[key]
        self.params[key] = value
        if key == "bench_row_pullup" and value != old:
            self.hw.set_row_pullups(value == 1)
        elif key == "hw_watchdog" and value == 1 and from_set:
            self.start_wdt()
        elif key == "btn_debounce_ms" and self.buttons is not None:
            self.buttons.debounce_ms = value
        elif (key == "led_max_ma" or key.endswith("_5v")) and self.leds is not None:
            self.apply_led_power()
        elif key.startswith("led") and self.leds is not None:
            ch = int(key[3])
            self.leds.configure(ch, self.params["led%d_count" % ch], self.params["led%d_order" % ch])
        self.shared.set_params(self.params, self.cal)

    def start_wdt(self):
        """Une seule fois : machine.WDT ne peut plus être arrêté ensuite."""
        if self.wdt is None:
            self.wdt = self.hw.start_wdt(HW_WDT_MS)

    def outputs_command(self, name, a):
        o = self.outputs
        if o is None:
            return
        kind = 0 if name.startswith("TOY") else 1
        op = name[3:]                                 # "", "S", "PULSE" ou "STOP"
        if op == "":
            o.set(kind, a[0], a[1])
        elif op == "S":
            o.set_mask(kind, a[0])
        elif op == "PULSE":
            o.pulse(kind, a[0], a[1], a[2], a[3])
        else:
            o.stop(kind, a[0])

    def led_command(self, name, a):
        lv = self.leds
        if name == "LEDCFG":
            self.set_param("led%d_count" % a[0], a[1])
        elif lv is None:
            return
        elif name == "LEDB":
            lv.set_brightness(a[0], a[1])
        elif name == "LEDF":
            lv.frame(a[0], a[1])
        else:
            lv.off(a[0])

    def mcp_reset(self):
        """MCPRESET : Toys et voyants à 0, impulsion sur GP28, puces reconfigurées."""
        if self.outputs is not None:
            self.outputs.all_off()
            self.outputs.invalidate()
        if self.mcp is not None:
            self.mcp.boot()

    def mcp_changed(self, addr, present):
        """Puce perdue ou retrouvée (§ 3.2) : voyant, boutons, sorties."""
        self.set_error(("mcp", addr), not present)
        o = self.outputs
        if not present:
            if addr == board.MCP_BUTTONS and self.buttons is not None:
                self.buttons.u3_lost()
            return
        if o is not None:
            if addr == board.MCP_TOYS:
                o.set_mask(outputs_mod.TOY, 0)       # les Toys restent à 0 (D7)
            o.invalidate()                            # voyants : état demandé réécrit

    def reboot(self):
        self.rebooting = True
        self.hw.reset()

    # ------------------------------------------------------------ boucle

    def step(self):
        """Un tour de la boucle principale, hors lecture USB."""
        now = self.clock.ticks_ms()
        life = self.shared.life
        self.life_moved = life != self.life_seen
        self.life_seen = life
        if self.wdt is not None and self.life_moved:
            self.wdt.feed()
        if self.heartbeat is not None:
            level = self.heartbeat.level
            self.heartbeat.step(self.life_moved)
            if self.bench and self.heartbeat.level != level:
                self.emit(HB_LOG % (self.hw.read_heartbeat(), self.tour_max))
                self.tour_max = 0
        if not self.wdg_tripped and self.clock.ticks_diff(now, self.last_rx) >= self.params["watchdog_ms"]:
            self.link_lost()
        self.drain_events()
        if self.mcp is not None:
            self.mcp.step()
        if self.buttons is not None:
            self.buttons.poll()
        if self.outputs is not None:
            self.outputs.update()
        if self.leds is not None:
            self.leds.update()
        self.flush()

    def link_lost(self):
        """Chien de garde de liaison (§ 5.4)."""
        self.wdg_tripped = True
        if self.outputs is not None:
            self.outputs.all_off()
        if self.leds is not None:
            self.leds.all_off()
        self.emit(P.msg_error(P.E_WDG, "timeout"))

    def drain_events(self, limit=16):
        q = self.queue
        for _ in range(limit):
            ev = q.pop()
            if ev is None:
                break
            self.event(ev)
        lost = q.take_dropped()
        if lost:
            self.emit(P.msg_error(P.E_OVERFLOW, OVERFLOW_TEXT % lost))

    def event(self, ev):
        kind, a, b, t, d = ev
        if kind == xcore.EV_DART:
            self.emit(P.msg_dart(a, b, t, d))
        elif kind == xcore.EV_REJECT:
            self.emit(P.msg_reject(a, b, d))
        elif kind == xcore.EV_SHOCK:
            self.emit(P.msg_shock(t, a))
        elif kind == xcore.EV_SHOCK_STUCK:
            self.emit(P.msg_error("SHOCK", "active at start"))
            self.set_error("shock", True)
        elif kind == xcore.EV_SHOCK_CLEAR:
            self.set_error("shock", False)       # aucun message, voyant à 1 Hz
        elif kind == xcore.EV_STUCK:
            self.emit(P.msg_error("STUCK", "%d %d" % (a, b)))
            self.set_error(("stuck", a, b), True)
        elif kind == xcore.EV_STUCK_CLEAR:
            self.set_error(("stuck", a, b), False)

    def set_error(self, source, on):
        if self.heartbeat is not None:
            self.heartbeat.set_error(source, on)


    def ledf_len(self, ch):
        """Octets d'un LEDF du canal après son en-tête (\\n compris) ; 0 = octet par octet.

        Lecture d'un bloc seulement si le chien de garde matériel tourne
        (§ 5.4) : un LEDF coupé en cours de ligne bloque ce readinto, et
        seul machine.WDT en sort (redémarrage). Sinon (banc, hw_watchdog 0
        ou pas encore reçu), 0 : lecture octet par octet, sans blocage.
        """
        if self.wdt is None:
            return 0
        n = self.params["led%d_count" % ch]
        return 6 * n + 1 if n else 0

    def note_tour(self, us):
        """Durée d'un tour de la boucle du cœur 0 (lecture USB comprise)."""
        if us > self.tour_max:
            self.tour_max = us

    def shutdown(self):
        """Fin de la boucle (Ctrl-C au banc, exception) : sorties à zéro, cœur 1 arrêté."""
        self.shared.running = False
        if self.outputs is not None:
            self.outputs.all_off()
            self.outputs.flush(force=True)
        if self.leds is not None:
            self.leds.all_off()
            self.leds.update()


# ---------------------------------------------------------------- sur le Pico

class Clock:
    """Horloge de MicroPython (module time)."""

    def __init__(self, time):
        self.ticks_ms = time.ticks_ms
        self.ticks_us = time.ticks_us
        self.ticks_diff = time.ticks_diff
        self.sleep_us = time.sleep_us
        self.sleep_ms = time.sleep_ms


class MachineHw:
    """Accès au matériel du cœur 0 pour App."""

    def __init__(self, machine, config, scanner, sleep_ms):
        self.machine = machine
        self.config = config
        self.scanner = scanner
        self.sleep_ms = sleep_ms

    def set_row_pullups(self, on):
        self.scanner.set_row_pullups(on)

    def start_wdt(self, timeout_ms):
        return self.machine.WDT(timeout=timeout_ms)

    def save_params(self, params):
        self.config.save(params)

    def reset(self):
        # P14 : le K doit partir avant le redémarrage ; marge pour que l'USB
        # l'envoie avant machine.reset() (au banc : K reçu 7 ms après REBOOT).
        self.sleep_ms(REBOOT_DELAY_MS)
        self.machine.reset()

    def read_heartbeat(self):
        """Niveau réel de GP16, lu dans GPIO_IN du SIO (0 = voyant allumé)."""
        return self.machine.mem32[self.scanner.GPIO_IN] >> board.HEARTBEAT_PIN & 1


I2C_FREQ = 400000
I2C_TIMEOUT_US = 5000                    # délai de 5 ms (§ 3.2)
QUEUE_SIZE = 64
REBOOT_DELAY_MS = 100                    # envoi du K avant machine.reset() (P14)
READ_MAX = 512                           # octets lus au plus par tour


def build(bench, clock, machine, micropython, write):
    """Crée le matériel et l'App ; renvoie (app, cœur 1). GP16 éteint en premier."""
    Pin = machine.Pin
    hb_pin = Pin(board.HEARTBEAT_PIN, Pin.OUT, value=1)     # première instruction utile
    if not bench:
        micropython.kbd_intr(-1)                            # Ctrl-C coupé (§ 5.1)
    import config
    import scanner
    import shock as shock_mod
    from buttons import Buttons
    from core1 import Core1
    from heartbeat import Heartbeat
    from leds import Leds, PioDriver
    from mcp23017 import McpBus, output_writers

    params, cfg_error = config.load()
    if bench:
        params["hw_watchdog"] = 0
        params["bench_row_pullup"] = 1
    scanner.setup_pins(params["bench_row_pullup"] == 1)
    adc, _ = shock_mod.setup_adc()
    shared = xcore.Shared()
    queue = xcore.EventQueue(QUEUE_SIZE)
    c1 = Core1(shared, scanner.Matrix(queue, clock), shock_mod.Shock(queue, clock, adc.read_u16))
    uid = "".join("%02X" % b for b in machine.unique_id())
    app = App(clock, shared, queue, write, params, uid,
              hw=MachineHw(machine, config, scanner, clock.sleep_ms), leds=Leds(PioDriver(clock, Pin)),
              cfg_error=cfg_error)
    app.bench = bench
    reset_pin = Pin(board.MCP_RESET_PIN, Pin.OUT, value=1)
    i2c = machine.I2C(board.I2C_ID, scl=Pin(board.SCL_PIN), sda=Pin(board.SDA_PIN),
                      freq=I2C_FREQ, timeout=I2C_TIMEOUT_US)
    bus = McpBus(i2c, clock, reset_pin, app.emit, app.mcp_changed, sleep_ms=clock.sleep_ms)
    toys, lights = output_writers(bus)
    app.outputs = outputs_mod.Outputs(clock, toys, lights)
    app.mcp = bus
    app.buttons = Buttons(clock, bus, Pin(board.BTN15_PIN, Pin.IN, Pin.PULL_UP),
                          Pin(board.MCP_INT_PIN, Pin.IN, Pin.PULL_UP), app.emit)
    app.heartbeat = Heartbeat(clock, hb_pin)
    return app, c1


def loop(app, read_input, clock):
    """Boucle du cœur 0 ; ne revient que par une exception (Ctrl-C au banc)."""
    ticks_us = clock.ticks_us
    diff = clock.ticks_diff
    while True:
        t0 = ticks_us()
        data = read_input()
        if data:
            app.feed(data)
        app.step()
        app.note_tour(diff(ticks_us(), t0))


HDR_MAX = 14                             # "@65535 LEDF 2 " : plus long en-tête de LEDF


def ledf_header_channel(hdr, n):
    """Canal si hdr[:n] vaut b"LEDF <c> " ou b"@<n> LEDF <c> " (c de 0 à 2), sinon -1."""
    i = 0
    if hdr[0] == 0x40:                       # "@"
        i = 1
        while i < n and 0x30 <= hdr[i] <= 0x39:
            i += 1
        if i == 1 or i >= n or hdr[i] != 0x20:
            return -1
        i += 1
    if n - i != 7 or hdr[i:i + 5] != b"LEDF ":
        return -1
    c = hdr[i + 5] - 0x30
    return c if 0 <= c < P.LED_CHANNELS else -1


def stdin_reader(sys, select, ledf_len=None):
    """Lecture de l'USB, READ_MAX octets au plus par appel, une ligne au plus.

    sys.stdin.buffer.readinto(b) de MicroPython v1.29.0 attend len(b) octets
    (stdio_buffer_read, shared/runtime/sys_stdio_mphal.c) : un octet à la
    fois après ipoll(0), dans des vues préparées d'avance.

    LEDF (M3b-3) : l'en-tête ne porte pas la longueur, mais le Pico la
    connaît : ledf_len(canal) = 6 × led<canal>_count + 1 (\\n compris). Après
    l'en-tête, le reste est lu d'un seul readinto de cette longueur, qui
    bloque jusqu'à son arrivée ; App.ledf_len renvoie 0 (octet par octet,
    sans blocage) tant que le chien de garde matériel n'est pas démarré
    (§ 5.4, M3b-4). Le découpage en lignes reste celui de
    LineSplitter : une ligne de mauvaise longueur est refusée comme avant.
    L'appel rend la main à la fin de chaque ligne, pour qu'un LEDCFG soit
    exécuté avant la lecture du LEDF suivant.
    """
    poll = select.poll()
    poll.register(sys.stdin, select.POLLIN)
    ipoll = poll.ipoll
    readinto = sys.stdin.buffer.readinto
    buf = bytearray(READ_MAX + 6 * P.DEFAULT_LIMITS.max_leds + 1)
    view = memoryview(buf)
    one = [view[i:i + 1] for i in range(READ_MAX)]
    hdr = bytearray(HDR_MAX)
    st = [0]                                 # octets de l'en-tête suivis ; -1 = ligne sans intérêt

    def read_input():
        n = 0
        hl = st[0]
        while n < READ_MAX:
            for _ in ipoll(0):
                break
            else:
                break                        # plus rien en attente
            readinto(one[n])
            c = buf[n]
            n += 1
            if c == 0x0A:
                hl = 0
                break                        # une ligne par appel
            if hl < 0:
                continue
            if hl >= HDR_MAX:
                hl = -1
                continue
            hdr[hl] = c
            hl += 1
            if c == 0x20 and hl >= 7 and ledf_len is not None:
                ch = ledf_header_channel(hdr, hl)
                k = ledf_len(ch) if ch >= 0 else 0
                if k:
                    readinto(view[n:n + k])  # reste de la ligne, d'un bloc
                    n += k
                    hl = 0 if buf[n - 1] == 0x0A else -1
                    break
        st[0] = hl
        return buf[:n] if n else None
    return read_input


def run(bench=False):
    """Point d'entrée sur le Pico (main.py ou bench.py)."""
    import _thread
    import machine
    import micropython
    import select
    import sys
    import time

    P.CHECK_BUILT = False                    # messages vérifiés par les tests sur PC (§ 5.1)
    clock = Clock(time)
    out = sys.stdout.buffer
    app, c1 = build(bench, clock, machine, micropython, out.write)
    app.boot()
    _thread.start_new_thread(c1.run, ())
    try:
        loop(app, stdin_reader(sys, select, app.ledf_len), clock)
    finally:
        app.shutdown()
        time.sleep_ms(20)                    # laisser le cœur 1 sortir de sa boucle
