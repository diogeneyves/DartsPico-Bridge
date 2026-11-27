# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt). Code destiné à RaspyDarts (GPL v3).
"""Couche « carte Pico » de RaspyDarts (docs/PI_INTERFACE.md § 5.3).

Remplace, en mode Pico, les deux sources matérielles de Craspberry :
- PicoExtender : même interface que Gpio_extender (include/cgpio_extender.py) ;
- PicoTargetSource : objet placé dans Craspberry.target_reader ; les fléchettes
  arrivent dans la file Craspberry.target_darts sous la forme de RaspyDarts 4.6.5,
  un couple (code R<ligne>C<colonne>, instant), l'instant en time.monotonic() pris
  à la réception (spécification, complément 19 ; t_µs du Pico non utilisé).
PicoLink est le client MQTT unique vers hwd (durée de maintien de 5 s,
testament OFF). Ce module n'importe ni pygame ni RPi.GPIO.

Fichiers à installer avec lui dans include/ : hwbus.py (pi/hwd/) et
pico_proto.py (shared/).
"""

import json
import os
import sys
import threading
import time

try:
    import hwbus
except ImportError:                       # installé dans include/ de RaspyDarts
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import hwbus

import pico_proto as P

# Correspondance par défaut (spécification § 3.3 et § 3.4), préréglage « carte Pico ».
_BUTTON_FUNCTIONS = ("UP", "DOWN", "LEFT", "RIGHT", "PLUS", "MINUS", "VALIDATE", "CANCEL",
                     "NEXTPLAYER", "BACK", "GAMEBUTTON", "VOLUME_UP", "VOLUME_DOWN",
                     "VOLUME_MUTE", "CPTPLAYER")
_LIGHT_FUNCTIONS = ("NEXTPLAYER", "BACK", "NAVIGATE", "VALIDATE", "LASER", "FLASH",
                    "PLAYERS", "CELEBRATION", "CELEBRATION2", "LIGHT")
MISS_VALUE = "MISS"                       # valeur de PIN_MISSDART : MISS décidé par hwd (D3)

DEFAULTS = {}
for _i, _f in enumerate(_BUTTON_FUNCTIONS):
    DEFAULTS["PIN_" + _f] = "BTN%d" % (_i + 1)
DEFAULTS["PIN_MISSDART"] = MISS_VALUE
for _f in ("PLAY", "NEXT", "PREVIOUS", "VOLUME_UP", "VOLUME_DOWN", "STOP"):
    DEFAULTS["PIN_MUSIC_" + _f] = ""      # pas de broche sur la carte (pavés USB possibles)
for _i, _f in enumerate(_LIGHT_FUNCTIONS):
    DEFAULTS["LIGHT_" + _f] = "V%d" % (_i + 1)
for _i in range(16):
    DEFAULTS["TOY%d" % (_i + 1)] = "T%d" % (_i + 1) if _i < P.TOYS else ""

# Lecture des boutons, comme include/cgpio_extender.py de RaspyDarts 4.6.3 : un appui compte
# une fois (passage de relâché à appuyé) ; un nouvel appui moins de DEBOUNCE secondes après le
# relâchement du même bouton est un rebond, ignoré ; seules les flèches maintenues se répètent
# (après REPEAT_DELAY, puis toutes les REPEAT_EVERY) ; un bouton maintenu plus de STUCK secondes
# est banni jusqu'à son relâchement.
DEBOUNCE = 0.05
REPEAT_DELAY = 0.5
REPEAT_EVERY = 0.15
REPEATED = ("PIN_LEFT", "PIN_RIGHT", "PIN_UP", "PIN_DOWN")
STUCK = 10
# Mode capture (include/ccapture.py, RaspyDarts 4.6.4) : Valider maintenu HOLD_END secondes
# donne PIN_CAPTURE_END ; relâché avant, c'est un appui ordinaire, rendu au relâchement.
HOLD_END = 2.0

_SHARED = None


def shared(logs, **kwargs):
    """Le lien unique du jeu : Craspberry peut être créé deux fois (raspydarts.py:267-274)."""
    global _SHARED
    if _SHARED is None:
        _SHARED = PicoLink(logs, **kwargs)
    return _SHARED


def _reset_shared():
    """Pour les tests."""
    global _SHARED
    _SHARED = None


def _paho():
    import paho.mqtt.client as mqtt
    return mqtt


class PicoLink:
    """Client MQTT vers hwd : état des boutons, verrou, file des fléchettes, état du Pico."""

    def __init__(self, logs, host="localhost", port=1883, topic_base=hwbus.TOPIC_BASE,
                 mqtt_module=None, client_id=None):
        self.logs = logs
        self.topics = hwbus.topics(topic_base)
        self._lock = threading.Lock()
        self.pressed = 0                   # masque des boutons appuyés (B, BS)
        self.latched = 0                   # appuis pas encore vus par le jeu (R3)
        self.misses = 0                    # MISS pas encore vus par le jeu
        self.connected = False             # Pico présent, d'après raspydarts/hw/status
        self.status = {}
        self._dart_queues = []
        # Heure d'une fléchette : time.monotonic() à la réception, l'horloge de
        # Craspberry.dart_time (remplaçable par une horloge simulée dans les tests)
        self.clock = time.monotonic
        mqtt = mqtt_module or _paho()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=client_id or "raspydarts-game-%d" % os.getpid())
        # Testament (R6) : si le jeu disparaît, hwd éteint tout.
        self.client.will_set(self.topics["cmd"], hwbus.OFF, qos=1, retain=False)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(1, 5)
        self.client.connect_async(host, port, keepalive=hwbus.KEEPALIVE_S)
        self.client.loop_start()

    # ------------------------------------------------------------ réception

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        client.subscribe([(self.topics["events"], 0), (self.topics["status"], 0)])

    def _on_message(self, client, userdata, message):
        try:
            payload = message.payload.decode()
        except UnicodeError:
            return
        if message.topic == self.topics["status"]:
            self._on_status(payload)
        elif message.topic == self.topics["events"]:
            self.on_event(payload)

    def _on_status(self, payload):
        try:
            status = json.loads(payload)
        except ValueError:
            return
        with self._lock:
            self.status = status
            self.connected = status.get("pico") == "connected"
            if not self.connected:
                self.pressed = 0           # plus de Pico : plus de bouton appuyé

    def on_event(self, line):
        """Une ligne de raspydarts/hw/events (appelée par le fil MQTT)."""
        try:
            ev = hwbus.parse_event(line.strip())
        except hwbus.BusError as error:
            self.logs.warning("Pico : %s" % error)
            return
        kind = ev[0]
        with self._lock:
            if kind == "D":
                # (code, instant) comme ctarget_reader dans RaspyDarts 4.6.5 (take_dart
                # dépaquette ce couple) ; le t_µs de l'événement est l'horloge du Pico
                received = self.clock()
                for q in self._dart_queues:
                    q.put((ev[1], received))
            elif kind == "B":
                bit = 1 << (ev[1] - 1)
                if ev[2]:
                    self.pressed |= bit
                    self.latched |= bit
                else:
                    self.pressed &= ~bit
            elif kind == "BS":
                self.pressed = ev[1]
            elif kind == "MISS":
                self.misses += 1
            elif kind == "E":
                self.logs.warning("Pico : error %s %s" % (ev[1], ev[2]))

    def add_dart_queue(self, q):
        with self._lock:
            self._dart_queues.append(q)

    def remove_dart_queue(self, q):
        with self._lock:
            if q in self._dart_queues:
                self._dart_queues.remove(q)

    def take_buttons(self):
        """(masque appuyé ou verrouillé, MISS en attente) ; le verrou est vidé (R2, R3)."""
        with self._lock:
            mask = self.pressed | self.latched
            self.latched = 0
            miss = self.misses > 0
            if miss:
                self.misses -= 1
            return mask, miss

    # ------------------------------------------------------------- émission

    def send(self, line):
        self.client.publish(self.topics["cmd"], line, qos=0)

    def all_off(self):
        self.send(hwbus.OFF)

    def close(self):
        """Fin propre : tout éteint, puis déconnexion (le testament n'est pas publié)."""
        self.all_off()
        self.client.disconnect()
        self.client.loop_stop()


class PicoOutput:
    """Sortie du Pico (Toy ou voyant), à la place de la broche d'un MCP23017 dans
    toys_list[nom][1] : le jeu lit pin.value (cscreen.button_led_try, RaspyDarts 4.6.4).

    value : 1 si light_toys l'a allumée (le Pi ne relit pas les sorties du Pico), sinon 0 ;
    écrire value envoie la commande, comme light_toys.
    """

    def __init__(self, extender, name, kind, number):
        self.extender = extender
        self.name = name
        self.kind = kind                   # "TOY" ou "LGT"
        self.number = number

    @property
    def value(self):
        return 1 if self.extender._lit.get(self.name) else 0

    @value.setter
    def value(self, state):
        self.extender.light_toys([self.name], bool(state))

    def __repr__(self):
        return "PicoOutput(%s %s %d)" % (self.name, self.kind, self.number)


class PicoExtender:
    """Même interface que Gpio_extender (cgpio_extender.py), pour la carte Pico.

    Valeurs de [Raspberry] : BTN1..BTN15, T1..T8, V1..V12, MISS (PIN_MISSDART).
    Aucune puce n'est accessible depuis le Pi : chips est vide, donc le monnayeur
    (ccoins.py) n'a rien à lire (D4 : pas de monnayeur en mode Pico).
    """

    def __init__(self, logs, link, buttons=None):
        self.logs = logs
        self.link = link
        self.chips = []
        self.mcp2 = None
        self.all_io = (["BTN%d" % (i + 1) for i in range(P.BUTTONS)]
                       + ["T%d" % (i + 1) for i in range(P.TOYS)]
                       + ["V%d" % (i + 1) for i in range(P.LIGHTS)] + [MISS_VALUE])
        self.buttons_list = {}
        self.toys_list = {}
        self.pin_switcher = {}
        self.banned_button = None
        self.clock = time.monotonic        # remplaçable par une horloge simulée (tests)
        self.sleep = time.sleep            # remplaçable dans les tests (blink_toy)
        # Mode capture : mis à True ou False par ccapture.py ; held_fired : fin déjà rendue
        self.hold_validate = False
        self.held_fired = False
        self._lit = {}                     # sorties allumées par light_toys (blink_toy)
        self._held_button = None           # bouton appuyé à la dernière lecture
        self._held_since = 0
        self._held_returned = 0
        self._released_at = {}
        if buttons:
            self.set_io(buttons)

    @property
    def mcp1(self):
        """Non None quand le Pico est là : le jeu lit alors les boutons (craspberry.py:758)."""
        return self if self.link.connected else None

    @staticmethod
    def decode(value):
        """BTN3 -> ('BTN', 3) ; T2 -> ('TOY', 2) ; V12 -> ('LGT', 12) ; MISS ; sinon (None, None)."""
        value = str(value).upper()
        if value == MISS_VALUE:
            return MISS_VALUE, 0
        for prefix, kind, count in (("BTN", "BTN", P.BUTTONS), ("T", "TOY", P.TOYS),
                                    ("V", "LGT", P.LIGHTS)):
            rest = value[len(prefix):]
            if value.startswith(prefix) and rest.isdigit() and rest[0] != "0":
                n = int(rest)
                if 1 <= n <= count:
                    return kind, n
        return None, None

    def set_io(self, buttons):
        self.buttons_list = {}
        self.toys_list = {}
        self.pin_switcher = {}
        for name, raw in buttons.items():
            value = str(raw).upper()
            kind, n = self.decode(value)
            if kind is None:
                continue                                   # '' ou TOY9..TOY16 : ignorés
            if name.startswith("PIN_") and kind in ("BTN", MISS_VALUE):
                self.buttons_list[name] = (value, None, n)
                self.pin_switcher[(kind, n)] = name
            elif (name.startswith("TOY") and kind == "TOY") or (name.startswith("LIGHT_") and kind == "LGT"):
                self.toys_list[name] = (value, PicoOutput(self, name, kind, n), n)
        return True

    def get_defaults(self):
        return DEFAULTS

    def set_defaults(self):
        self.set_io(DEFAULTS)

    def read_entries(self):
        """Comme Gpio_extender.read_entries (RaspyDarts 4.6.3) : le bouton qui vient d'être
        appuyé, une flèche maintenue (répétée), sinon None. Lu sur le masque et le verrou (R3) :
        un appui plus court qu'un tour de boucle du jeu est vu une fois.
        """
        mask, miss = self.link.take_buttons()
        pressed = None
        for (kind, n), button in self.pin_switcher.items():
            if miss if kind == MISS_VALUE else mask & (1 << (n - 1)):
                pressed = button

        now = self.clock()
        if pressed != self._held_button:
            # Relâché, ou un autre bouton : un nouvel appui (sauf rebond d'un relâchement récent)
            released = self._held_button
            if self._held_button is not None:
                self._released_at[self._held_button] = now
            if self.banned_button is not None and pressed != self.banned_button:
                self.logs.info("Button %s released : no longer banned" % self.banned_button)
                self.banned_button = None
            self._held_button = pressed
            self._held_since = now
            # Mode capture : Valider relâché avant HOLD_END, un appui ordinaire (rendu maintenant)
            short_validate = (released == "PIN_VALIDATE" and self.hold_validate
                              and not self.held_fired)
            self.held_fired = False
            if pressed is None or now - self._released_at.get(pressed, 0) < DEBOUNCE:
                return "PIN_VALIDATE" if short_validate else None
            if pressed == "PIN_VALIDATE" and self.hold_validate:
                # Maintenu ou non : on le saura au relâchement (ou après HOLD_END)
                return "PIN_VALIDATE" if short_validate else None
            self._held_returned = now
            return pressed

        if pressed is None or pressed == self.banned_button:
            return None
        if (pressed == "PIN_VALIDATE" and self.hold_validate and not self.held_fired
                and now - self._held_since >= HOLD_END):
            self.held_fired = True
            return "PIN_CAPTURE_END"
        if now - self._held_since > STUCK:
            self.banned_button = pressed
            self.logs.warning("Button %s is banned ! (held more than %s s)" % (pressed, STUCK))
            return None
        if (pressed in REPEATED and now - self._held_since >= REPEAT_DELAY
                and now - self._held_returned >= REPEAT_EVERY):
            self._held_returned = now
            return pressed
        return None

    def test_entries(self):
        """Valeurs appuyées (['BTN1', 'BTN9']), pour le menu de test des boutons."""
        mask, miss = self.link.take_buttons()
        out = []
        for button, (value, _, n) in self.buttons_list.items():
            if (miss if value == MISS_VALUE else mask & (1 << (n - 1))):
                out.append(value)
        return out

    def light_toys(self, toys, light=True):
        for toy in toys:
            element = self.toys_list.get(toy) if isinstance(toy, str) else None
            if element is not None:                        # TOY9..TOY16 : rien n'est envoyé
                self.link.send("%s %d %d" % (element[1].kind, element[2], 1 if light else 0))
                self._lit[toy] = bool(light)

    def blink_toy(self, toy, cycles, on_ms, off_ms):
        """Comme Gpio_extender.blink_toy (RaspyDarts 4.6.4) : une LED de bouton clignote à
        partir de son état du moment. Allumée : éteinte off_ms puis rallumée on_ms (la
        dernière fois, elle reste allumée) ; éteinte : allumée on_ms puis éteinte off_ms.
        Dans un fil, rendu (ou None). L'état est celui demandé par light_toys (le Pi ne
        relit pas les sorties du Pico) ; chaque état est envoyé, rien n'est inversé.
        """
        element = self.toys_list.get(toy) if isinstance(toy, str) else None
        if element is None or cycles <= 0:
            return None
        kind, n = element[1].kind, element[2]
        lit = self._lit.get(toy, False)

        def blink():
            for cycle in range(cycles):
                self.link.send("%s %d %d" % (kind, n, 0 if lit else 1))
                self.sleep((off_ms if lit else on_ms) / 1000)
                self.link.send("%s %d %d" % (kind, n, 1 if lit else 0))
                if cycle + 1 < cycles:
                    self.sleep((on_ms if lit else off_ms) / 1000)

        thread = threading.Thread(target=blink, daemon=True)
        thread.start()
        return thread

    def strobe_toys(self, toys, iterations=3, delay_on=10, delay_off=100):
        """TOYPULSE / LGTPULSE au Pico ; renvoie des fils témoins de même durée (R4).

        Le jeu attend ces fils (raspydarts.py:2252-2254) ; hwd remet l'état d'avant
        à la fin (D9).
        """
        threads = []
        if iterations <= 0:
            return threads
        on = min(max(int(delay_on), 1), P.PULSE_MS_MAX)
        off = min(max(int(delay_off), 1), P.PULSE_MS_MAX)
        iterations = min(int(iterations), P.PULSE_REPEAT_MAX)
        # Durée des fils d'origine : iterations allumages, iterations - 1 extinctions.
        duration = (iterations * delay_on + (iterations - 1) * delay_off) / 1000
        for toy in toys:
            element = self.toys_list.get(toy) if isinstance(toy, str) else None
            if element is None:
                continue
            self.link.send("%sPULSE %d %d %d %d" % (element[1].kind, element[2], on, off, iterations))
            threads.append(threading.Thread(target=time.sleep, args=(duration,), daemon=True))
        for thread in threads:
            thread.start()
        return threads

    def all_off(self):
        """Pour Craspberry.gpio_flush en mode Pico."""
        self.link.all_off()
        self._lit.clear()


class NoCoins:
    """Monnayeur absent (D4) : remplace ccoins.Coins en mode Pico, toujours en mode « off ».

    Même interface que ccoins.Coins (ccoins.py:73-367) et mêmes réponses que
    celui-ci en mode off : parties gratuites, aucun fichier lu ni écrit, aucun fil.
    """

    def __init__(self):
        self.settings = {"mode": "off", "mcp": "", "password": ""}
        self.state = {"credits": 0, "seconds": 0, "coins": 0}
        self.changed = False
        self.reader = None

    def start(self):
        pass

    def stop(self):
        pass

    def active(self):
        return False

    def price(self, game, game_type):
        return 0

    def cost(self, game, game_type, players):
        return 0

    def can_pay(self, cost):
        return True

    def pay(self, cost):
        pass

    def spend(self, seconds):
        return 0

    def tick(self, in_game):
        pass

    def time_up(self):
        return False

    def add(self, credits=0, seconds=0):
        pass

    def reset_count(self):
        pass

    def left(self):
        return None

    def save_settings(self, settings):
        pass                                   # le mode reste « off »

    @staticmethod
    def hash_password(password):
        return ""

    def check_password(self, password):
        return False


class PicoTargetSource:
    """Remplace le processus ctarget_reader.py dans Craspberry.target_reader.

    Les fléchettes arrivent dans la file donnée (Craspberry.target_darts), en
    couples (code R<l>C<c>, instant time.monotonic() de la réception).
    Les méthodes terminate, wait, kill et poll imitent subprocess.Popen, pour
    Craspberry.stop_target_reader.
    """

    def __init__(self, link, dart_queue):
        self.link = link
        self.queue = dart_queue
        self.stopped = False
        link.add_dart_queue(dart_queue)

    def poll(self):
        return 0 if self.stopped else None

    def terminate(self):
        """Plus aucune fléchette dans la file (assistant de câblage, fin du jeu)."""
        self.stopped = True
        self.link.remove_dart_queue(self.queue)

    kill = terminate

    def wait(self, timeout=None):
        return 0


# ------------------------------------------------------------------ serveurs LED
# Lancement de addons/StripLeds_Server.py et addons/TargetLeds_Server.py en mode carte Pico
# (patch 0005 de raspydarts.py, docs/PI_INTERFACE.md § 5.4). PIN_TARGETLED et PIN_STRIPLED
# sont alors des canaux de la carte (cible 0, ruban 1, spécification § 9.2). Les serveurs
# tournent sans sudo, pico_shims/ en tête de PYTHONPATH : addons/CStrip.py charge le
# remplaçant de rpi_ws281x, qui lit le canal dans DARTSPICO_BRIDGE_LED_CHANNEL. Les deux
# serveurs et CStrip.py restent inchangés : StripLeds_Server.py n'accepte que les broches
# 10, 12, 18 ou 21, d'où une broche fictive.

LED_CHANNEL_VALUES = ("0", "1", "2")
LED_SERVER_PIN = "18"
LED_ORDERS = ("rgb", "grb")               # options -type des serveurs


def led_channel(value):
    """Canal LED de la carte (0 à 2) donné par PIN_TARGETLED ou PIN_STRIPLED, sinon None."""
    text = str(value).strip()
    return int(text) if text in LED_CHANNEL_VALUES else None


def _count(value):
    try:
        return int(str(value).strip())
    except ValueError:
        return 0


def strip_leds_wanted(config_leds):
    """Ruban sur un canal de la carte, avec au moins une LED (le serveur refuse 0)."""
    return (led_channel(config_leds.get("PIN_STRIPLED")) is not None
            and _count(config_leds.get("NBR_STRIPLED")) > 0)


def target_leds_wanted(config_leds):
    """LED de la cible sur un canal de la carte (leur nombre vient de [LEDTarget])."""
    return led_channel(config_leds.get("PIN_TARGETLED")) is not None


def _type_option(order):
    order = str(order).strip().lower()
    return ["-type=%s" % order] if order in LED_ORDERS else []


def led_server_env(root_dir, channel, broker, environ=None):
    """Environnement d'un serveur LED : remplaçant de rpi_ws281x, canal, broker MQTT."""
    env = dict(os.environ if environ is None else environ)
    shims = os.path.join(root_dir, "pico_shims")
    env["PYTHONPATH"] = shims + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["DARTSPICO_BRIDGE_LED_CHANNEL"] = str(channel)
    env["RASPYDARTS_MQTT_HOST"] = str(broker or "localhost")
    return env


def strip_server(root_dir, broker, topic, config_leds, environ=None):
    """(commande, environnement) de StripLeds_Server.py, comme raspydarts.py, sans sudo."""
    cmd = (["python3", "addons/StripLeds_Server.py", "-host=%s" % broker]
           + _type_option(config_leds.get("TYP_STRIPLED"))
           + ["9", "%s" % topic, LED_SERVER_PIN, "%s" % config_leds["NBR_STRIPLED"],
              "%s" % config_leds["BRI_STRIPLED"]])
    channel = led_channel(config_leds["PIN_STRIPLED"])
    return cmd, led_server_env(root_dir, channel, broker, environ)


def target_server(root_dir, broker, topic, config_leds, config_target_leds, bgcolors,
                  bgbrightness, environ=None):
    """(commande, environnement) de TargetLeds_Server.py, comme raspydarts.py, sans sudo."""
    cmd = (["python3", "%s/addons/TargetLeds_Server.py" % root_dir, "-host=%s" % broker]
           + _type_option(config_leds.get("TYP_TARGETLED"))
           + ["-bgcolors=%s" % bgcolors, "-bgbrightness=%s" % bgbrightness, "10",
              "%s" % topic, "%s" % config_target_leds, LED_SERVER_PIN,
              "%s" % config_leds["BRI_TARGETLED"]])
    channel = led_channel(config_leds["PIN_TARGETLED"])
    return cmd, led_server_env(root_dir, channel, broker, environ)
