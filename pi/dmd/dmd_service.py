# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Publication des images du DMD (spécification § 9.9, 46e et 48e compléments,
sous-phases SR-b et SR-c).

Relie le cœur (dmd_core), le rendu (dmd_render, Pillow) et MQTT :

    service = DmdService(core, renderer, publish, clock)
    service.start()
    service.message(sujet, contenu)
    service.step()   -> microsecondes avant le prochain pas utile

Au plus une image toutes les 1 000 000 // TARGET_FPS µs (même pas que hwd) ;
une animation publie une image à chaque pas, une image fixe dès qu'elle change
puis toutes les secondes. La fin d'un média (GIF, PPM) est relayée au cœur
par core.media_done(jeton) (48e complément) ; les textes reçus pendant le média d'un
score attendent sa fin (49e complément, dans le cœur). Les images publiées sont aussi copiées vers le DMD
virtuel (dmd_web, 47e complément) et, si hdmi_output = 1, vers l'écran HDMI dédié
(dmd_hdmi, complément 72, jamais en rejeu) ; --replay rejoue un journal sans MQTT
(dmd_replay). Ce module n'importe pas Pillow : le rendu est passé
en paramètre (main() l'importe).

Sur le Pi (50e complément) : unité systemd raspydarts-dmd.service, code dans
/opt/dartspico-bridge (structure du dépôt), configuration /etc/raspydarts/dmd.toml :

    python3 pi/dmd/dmd_service.py --config /etc/raspydarts/dmd.toml

SIGTERM et SIGINT posent un événement d'arrêt, lu par la boucle et entre deux
fichiers du décodage du démarrage ; l'arrêt est journalisé avec sa cause.
"""

import argparse
import logging
import os
import queue
import subprocess
import sys
import threading
import time

# shared/ (dmd_proto) à côté de pi/, comme hwd_daemon.py (50e complément)
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(os.path.dirname(os.path.dirname(_HERE)), "shared")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import dmd_config as K  # noqa: E402
import dmd_core as C  # noqa: E402
import dmd_proto as D  # noqa: E402

log = logging.getLogger("dmd")

TOPIC_FRAME = "raspydarts/hw/dmd/frame"     # hwbus.topics()["dmd_frame"] (§ 9.5)
FRAME_PERIOD_US = 1000000 // D.TARGET_FPS   # = IMAGE_PERIOD_US de hwd_dmd
STATIC_REPEAT_US = 1000000                  # image fixe répétée (sous dmd_stale_ms)
DEFAULT_MEDIA_DIR = K.DEFAULT_MEDIA_DIR     # dmd_media_dir (41e complément)
WEB_PORT = 8081                             # DMD virtuel (§ 9.9, 47e complément)
REPLAY_WEB_PORT = 8082                      # rejeu : 8081 reste au vrai service
NTP_COMMAND = ("timedatectl", "show", "-p", "NTPSynchronized", "--value")
NTP_TIMEOUT_S = 2                           # délai maximum de timedatectl (48e complément)
NTP_PERIOD_US = 60 * 1000000                # relu au plus toutes les 60 s
G_MAX_S = K.DmdConfig.attract_gif_max_s    # attract_gif_max_s par défaut (60 s, 50e complément)


def _monotonic_us():
    return time.monotonic_ns() // 1000


class DmdService:
    def __init__(self, core, renderer, publish, clock=None):
        self.core = core
        self.renderer = renderer
        self.publish = publish              # publish(octets) : une image vers TOPIC_FRAME
        self.clock = clock or _monotonic_us
        self.scene = None
        self._changed = False
        self._last_pub_us = None
        self._reported = None               # scène dont la fin a déjà été signalée
        self._excluded = set()              # fichiers corrompus déjà passés au cœur
        self.published = 0

    def start(self):
        self._apply(self.core.start())

    def message(self, topic, payload):
        self._apply(self.core.message(topic, payload))

    def _apply(self, commands):
        for command in commands:
            old = self.scene
            self.scene = self.renderer.scene(command)
            self._changed = True
            if old is not None and old is not self.scene:
                close = getattr(old, "close", None)
                if close is not None:
                    close()

    def _exclude_bad(self):
        """GIF corrompus de l'Attract Mode (renderer.bad_media) : retirés du tirage du cœur
        pour la vie du service (48e complément) ; le rendu les a déjà journalisés une fois."""
        bad = getattr(self.renderer, "bad_media", None)
        if not bad or len(bad) == len(self._excluded):
            return
        folder = os.path.normpath(os.path.join(getattr(self.renderer, "media_dir", None) or "",
                                               C.ATTRACT_FOLDER))
        for path in list(bad):
            if path in self._excluded:
                continue
            self._excluded.add(path)
            if os.path.normpath(os.path.dirname(path)) == folder:
                self.core.exclude(os.path.basename(path))

    def _media_end(self, now):
        """Fin du média affiché : signalée une fois au cœur, qui passe au suivant (48e complément)."""
        scene = self.scene
        token = getattr(scene, "token", None)
        if token is None or scene is self._reported or not scene.is_done(now):
            return
        self._reported = scene
        self._apply(self.core.media_done(token))

    def _due_us(self, now):
        """Instant du prochain envoi, ou None sans scène ; now si rien n'a encore été publié
        (l'heure n'est lue qu'une fois par pas : une seconde lecture, toujours plus tardive
        avec une vraie horloge, empêchait toute publication)."""
        if self.scene is None:
            return None
        if self._last_pub_us is None:
            return now
        if self._changed or self.scene.animated:
            return self._last_pub_us + FRAME_PERIOD_US
        return self._last_pub_us + STATIC_REPEAT_US

    def step(self):
        """Minuteries du cœur, fin de média, puis une image si son heure est venue."""
        self._apply(self.core.tick())
        now = self.clock()
        self._exclude_bad()
        self._media_end(now)
        due = self._due_us(now)
        if due is not None and now >= due:
            frame = self.scene.next_frame(now)
            if len(frame) != D.PIXELS_BYTES:
                raise ValueError("image de %d octets" % len(frame))
            self.publish(frame)
            self.published += 1
            self._changed = False
            self._last_pub_us = now
            due = self._due_us(now)
        return FRAME_PERIOD_US if due is None else max(0, due - now)


# --- Attract Mode et heure ----------------------------------------------------------------

class AttractGifs:
    """Liste des GIF de <media_dir>/Gifs/600 Gratos, relue par le cœur à chaque nouveau sac
    (48e complément) : extension .gif (casse ignorée), fichiers cachés exclus. Dossier absent
    ou illisible : liste vide (écran d'accueil), journalisé une fois."""

    def __init__(self, media_dir):
        self.folder = os.path.join(media_dir or "", C.ATTRACT_FOLDER)
        self._logged = False

    def __call__(self):
        try:
            names = os.listdir(self.folder)
        except OSError as error:
            if not self._logged:
                self._logged = True
                log.warning("DMD : Attract Mode folder %s unreadable (%s), welcome screen instead, "
                            "no more logged", self.folder, error)
            return []
        return sorted(n for n in names
                      if not n.startswith(".") and n.lower().endswith(".gif")
                      and os.path.isfile(os.path.join(self.folder, n)))


def read_ntp_synchronized(timeout_s=NTP_TIMEOUT_S):
    """timedatectl show -p NTPSynchronized --value : vrai seulement pour « yes » avec un code
    nul ; commande absente, délai dépassé ou autre réponse : faux (48e complément)."""
    try:
        result = subprocess.run(NTP_COMMAND, capture_output=True, text=True, timeout=timeout_s,
                                check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "yes"


class NtpStatus:
    """time_synced() du cœur, sans jamais bloquer la boucle des images : la lecture se fait
    dans un fil à part, au plus une fois toutes les 60 s ; entre deux lectures, et tant que
    la première n'est pas finie, la dernière valeur connue sert (faux au départ)."""

    def __init__(self, reader=read_ntp_synchronized, clock=None, period_us=NTP_PERIOD_US,
                 spawn=None):
        self.reader = reader
        self.clock = clock or _monotonic_us
        self.period_us = period_us
        self.spawn = spawn or self._thread
        self.value = False
        self._lock = threading.Lock()
        self._running = False
        self._last_us = None
        self.reads = 0

    @staticmethod
    def _thread(target):
        threading.Thread(target=target, name="dmd-ntp", daemon=True).start()

    def refresh(self):
        """Lance une lecture si aucune n'est en cours et si la dernière date d'au moins 60 s."""
        now = self.clock()
        with self._lock:
            if self._running or (self._last_us is not None and now - self._last_us < self.period_us):
                return
            self._running = True
            self._last_us = now
        self.spawn(self._read)

    def _read(self):
        try:
            value = bool(self.reader())
        except Exception:                   # échec : non synchronisé
            value = False
        with self._lock:
            changed = self.reads == 0 or value != self.value
            self.value = value
            self.reads += 1
            self._running = False
        if changed:                         # première lecture comprise (50e complément)
            log.info("DMD : NTP synchronized : %s", "yes" if value else "no")

    def __call__(self):
        self.refresh()
        return self.value


# --- MQTT ------------------------------------------------------------------------------------

def _paho():
    import paho.mqtt.client as mqtt       # paho-mqtt 2, comme le jeu et hwd
    return mqtt


class MqttLink:
    """Abonnement aux deux sujets du jeu (§ 9.9) et publication des images."""

    def __init__(self, host="127.0.0.1", port=1883, mqtt_module=None, client_id="raspydarts-dmd"):
        mqtt = mqtt_module or _paho()
        self.inbox = queue.SimpleQueue()    # (sujet, contenu), rempli par le fil de paho
        self.connected = False
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(1, 5)
        self.client.connect_async(host, port, keepalive=5)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        client.subscribe([(topic, 0) for topic in C.TOPICS])
        self.connected = True
        log.info("DMD : MQTT connected")

    def _on_message(self, client, userdata, message):
        self.inbox.put((message.topic, message.payload))

    def publish_frame(self, frame):
        # Rien n'est publié tant que MQTT n'est pas connecté (46e complément)
        if self.connected:
            self.client.publish(TOPIC_FRAME, frame, qos=0, retain=False)

    def drain(self, service):
        while True:
            try:
                topic, payload = self.inbox.get_nowait()
            except queue.Empty:
                return
            service.message(topic, payload)

    def close(self):
        self.client.disconnect()
        self.client.loop_stop()


class _Inputs:
    """Messages du jeu (MQTT ou rejeu) vers le service et vers le DMD virtuel."""

    def __init__(self, service, web=None):
        self.service = service
        self.web = web

    def message(self, topic, payload):
        if self.web is not None:
            self.web.message(topic, payload)
        self.service.message(topic, payload)


class ShutdownRequest:
    """Événement d'arrêt (50e complément) : posé par SIGTERM ou SIGINT (install()), lu par la
    boucle de run() et entre deux fichiers du décodage du démarrage. Appelable : vrai si
    l'arrêt est demandé ; cause : nom du signal (le premier reçu)."""

    def __init__(self):
        self.event = threading.Event()
        self.cause = None

    def request(self, cause):
        if self.cause is None:
            self.cause = cause
        self.event.set()

    def __call__(self):
        return self.event.is_set()

    def install(self, signal_module=None):
        if signal_module is None:
            import signal as signal_module
        for name in ("SIGTERM", "SIGINT"):
            signal_module.signal(getattr(signal_module, name),
                                 lambda signum, frame, name=name: self.request(name))


def parse_args(argv=None):
    """Options, puis fichier de configuration (--config) : une option donnée l'emporte sur
    le fichier (50e complément). Fichier illisible ou invalide : arrêt avec un message."""
    parser = argparse.ArgumentParser(description="Service de rendu du DMD (§ 9.9)")
    parser.add_argument("--config", metavar="FICHIER",
                        help="configuration TOML (sur le Pi : /etc/raspydarts/dmd.toml)")
    parser.add_argument("--media-dir", default=None,
                        help="dossier des médias (remplace dmd_media_dir ; défaut %s)" % DEFAULT_MEDIA_DIR)
    parser.add_argument("--mqtt-host", default=None, help="remplace mqtt_host (défaut 127.0.0.1)")
    parser.add_argument("--mqtt-port", type=int, default=None, help="remplace mqtt_port (défaut 1883)")
    parser.add_argument("--web-port", type=int, default=None,
                        help="port du DMD virtuel sur 127.0.0.1 (remplace web_port ; défaut %d, "
                        "%d en rejeu ; 0 = coupé)" % (WEB_PORT, REPLAY_WEB_PORT))
    parser.add_argument("--replay", metavar="JOURNAL",
                        help="rejoue un journal de messages, sans MQTT (47e complément)")
    parser.add_argument("--loop", action="store_true", help="rejeu en boucle")
    parser.add_argument("--speed", type=float, default=1.0, help="rejeu N fois plus vite (N > 0)")
    parser.add_argument("--no-special-moves", action="store_true",
                        help="coupe les coups spéciaux (remplace dmd_special_moves = 1)")
    args = parser.parse_args(argv)
    if not args.speed > 0:
        parser.error("--speed doit être > 0")
    if (args.loop or args.speed != 1.0) and not args.replay:
        parser.error("--loop et --speed demandent --replay")
    try:
        cfg = K.load(args.config) if args.config else K.DmdConfig()
    except (OSError, ValueError) as error:
        parser.error("configuration refusée : %s" % error)
    if args.media_dir is None:
        args.media_dir = cfg.dmd_media_dir
    # ~ développé pour l'utilisateur qui lance le service (HOME, fixé par systemd d'après User=,
    # complément 77), que la valeur vienne du défaut, du fichier ou de l'option
    args.media_dir = os.path.expanduser(args.media_dir)
    if args.mqtt_host is None:
        args.mqtt_host = cfg.mqtt_host
    if args.mqtt_port is None:
        args.mqtt_port = cfg.mqtt_port
    if args.web_port is None:
        # En rejeu, 8082 : web_port reste au vrai service (47e complément)
        args.web_port = REPLAY_WEB_PORT if args.replay else cfg.web_port
    args.special_moves = bool(cfg.dmd_special_moves) and not args.no_special_moves
    args.no_special_moves = not args.special_moves
    args.attract_delay_s = cfg.attract_delay_s
    args.attract_gif_max_s = cfg.attract_gif_max_s
    args.attract_clock_every_s = cfg.attract_clock_every_s
    args.hdmi_output = cfg.hdmi_output      # sans option en ligne de commande (complément 72)
    if not 0 <= args.web_port <= 65535:
        parser.error("--web-port hors plage")
    if not 1 <= args.mqtt_port <= 65535:
        parser.error("--mqtt-port hors plage")
    return args


def run(args, renderer, link_factory=None, web_factory=None, clock=None, sleep=time.sleep, stop=None,
        time_synced=None, hdmi_factory=None):
    """Boucle du service. En rejeu, link_factory (MQTT) et hdmi_factory (écran HDMI) ne sont
    jamais appelés. time_synced : NtpStatus par défaut (timedatectl dans un fil à part,
    première lecture au démarrage)."""
    import dmd_web
    web = None
    if args.web_port:
        web = (web_factory or dmd_web.WebView)(args.web_port)
        web.start()
    hdmi = None
    if getattr(args, "hdmi_output", 0):
        if args.replay:
            log.info("DMD : hdmi_output ignored in replay")
        else:
            import dmd_hdmi
            hdmi = (hdmi_factory or dmd_hdmi.HdmiOutput)()
            hdmi.start()
            log.info("DMD : HDMI screen output on (%s)", dmd_hdmi.OUTPUT_NAME)
    link = None
    replay = None
    if args.replay:
        import dmd_replay
        replay = dmd_replay.Replayer(dmd_replay.load(args.replay), clock=clock,
                                     speed=args.speed, loop=args.loop)
        log.info("DMD : replay of %s, no MQTT", args.replay)
    else:
        link = (link_factory or MqttLink)(args.mqtt_host, args.mqtt_port)

    def publish(frame):
        if link is not None:
            link.publish_frame(frame)
        if web is not None:
            web.frame(frame)            # copie interne, sans MQTT (47e complément)
        if hdmi is not None:
            hdmi.frame(frame)           # tube non bloquant, seule la dernière compte (complément 72)

    if time_synced is None:
        time_synced = NtpStatus()
        time_synced.refresh()
    special_moves = not getattr(args, "no_special_moves", False)
    attract_delay_s = getattr(args, "attract_delay_s", C.ATTRACT_DELAY_S)
    clock_every_s = getattr(args, "attract_clock_every_s", C.CLOCK_EVERY_S)
    core = C.DmdCore(clock=clock, time_synced=time_synced, gif_source=AttractGifs(args.media_dir),
                     special_moves=special_moves, attract_delay_s=attract_delay_s,
                     clock_every_s=clock_every_s,
                     media_available=getattr(renderer, "has_media", None))
    service = DmdService(core, renderer, publish, clock=clock)
    inputs = _Inputs(service, web)
    log.info("DMD : started (configuration %s, media %s, special moves %s, Attract Mode after %s s, "
             "GIF cut at %s s, clock every %s s, web port %s, %s)",
             getattr(args, "config", None) or "defaults", args.media_dir,
             "on" if special_moves else "off", attract_delay_s,
             getattr(args, "attract_gif_max_s", G_MAX_S), clock_every_s, args.web_port or "off",
             "replay" if args.replay else "MQTT %s:%s" % (args.mqtt_host, args.mqtt_port))
    service.start()
    cause = None
    try:
        while stop is None or not stop():
            if link is not None:
                link.drain(inputs)
            wait_us = service.step()
            if replay is not None:
                for topic, payload in replay.due():
                    inputs.message(topic, payload)
                next_us = replay.wait_us()
                if next_us is not None:
                    wait_us = min(wait_us, next_us)
            sleep(min(wait_us, 10000) / 1e6)
    except KeyboardInterrupt:
        cause = "KeyboardInterrupt"
    except BaseException as error:
        cause = "error %s" % type(error).__name__
        raise
    finally:
        if cause is None:
            # Sortie de la boucle : arrêt demandé (cause du signal s'il y en a une)
            cause = getattr(stop, "cause", None) or "stop requested"
        if link is not None:
            link.close()
        if web is not None:
            web.close()
        if hdmi is not None:
            hdmi.close()
        log.info("DMD : stopped (%s)", cause)
    return service


def load_renderer(factory, args, should_stop=None, clock=time.monotonic):
    """Construit le rendu, qui décode d'avance Scores et SpecialsMoves (environ 8 s sur le Pi) ;
    décodage avant run(), début et fin journalisés avec la durée (50e complément)."""
    special_moves = not getattr(args, "no_special_moves", False)
    log.info("DMD : decoding media of %s (Scores%s)...", args.media_dir,
             ", SpecialsMoves" if special_moves else "")
    start = clock()
    renderer = factory(args.media_dir, special_moves=special_moves,
                       attract_max_s=getattr(args, "attract_gif_max_s", G_MAX_S),
                       should_stop=should_stop)
    log.info("DMD : decoding done in %.1f s", clock() - start)
    return renderer


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    shutdown = ShutdownRequest()
    shutdown.install()                      # avant le décodage : un SIGTERM l'interrompt
    import dmd_render                       # Pillow, seulement ici
    renderer = load_renderer(dmd_render.Renderer, args, should_stop=shutdown)
    if shutdown():
        log.info("DMD : stopped during decoding (%s)", shutdown.cause)
        return 0
    run(args, renderer, stop=shutdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
