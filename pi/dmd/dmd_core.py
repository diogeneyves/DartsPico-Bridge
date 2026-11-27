# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Cœur du service de rendu du DMD (spécification § 9.9, § 6.6, sous-phase SR-a).

Python pur, bibliothèque standard seulement : ni Pillow, ni MQTT, ni lecture
de fichiers, ni time.sleep. Le cœur reçoit les messages du jeu (sujets
raspydarts/dmd et raspy2dmd), tient la machine d'états (accueil, affichage,
Attract Mode) et rend des commandes de rendu abstraites, que les
sous-phases SR-b et SR-c traduiront en images 128 × 32.

Branchement :
    core = DmdCore(clock=..., attract_delay_s=15, gifs=[...], time_synced=...,
                   gif_source=...,      # gif_source() : liste relue à chaque nouveau sac
                   clock_every_s=120)   # heure toutes les 2 min (50e complément)
    core.start()                  -> [Welcome()]
    core.message(sujet, contenu)  -> commandes (liste, éventuellement vide)
    core.tick()                   -> commandes (minuteries)
    core.media_done(jeton)        -> commandes (fin d'un GIF de l'Attract Mode, ou d'un
                                     média de score : son texte fixe, 48e complément, ou le
                                     dernier texte mis en attente, 49e complément)
    special_move(fléchettes)      coup spécial d'une volée (49e complément)
    core.set_gifs(noms)           liste des GIF de l'Attract Mode
    core.exclude(nom)             GIF corrompu, retiré du tirage pour la vie du service

L'horloge rend des microsecondes (entier), comme celle de hwd.
"""

import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Optional, Tuple

log = logging.getLogger("dmd")

# Sujets MQTT écoutés (§ 9.9) : cdmd.send et boutons de test du menu Afficheur
TOPIC_DMD = "raspydarts/dmd"
TOPIC_RASPY2DMD = "raspy2dmd"
TOPICS = (TOPIC_DMD, TOPIC_RASPY2DMD)

DEFAULT_TEXT_COLOR = (0, 0, 255)        # bleu 0,0,255 ([TextRenderer] de l'auteur, § 9.9)
ATTRACT_DELAY_S = 15                    # attract_delay_s par défaut (§ 9.9)
ATTRACT_FOLDER = "Gifs/600 Gratos"      # GIF de l'Attract Mode (42e complément)
SCORES_FOLDER = "Scores"                # Scores/<code de la fléchette> (§ 9.9)
SPECIALS_FOLDER = "SpecialsMoves"       # coups spéciaux (49e complément)
CLOCK_EVERY_S = 120                     # l'heure toutes les 2 min (attract_clock_every_s) ...
CLOCK_DURATION_S = 10                   # ... pendant 10 s (50e complément)
LOG_PAYLOAD_MAX = 80

# Préfixes de score affichés comme score| (§ 6.6) : score, score1 à score12
SCORE_PREFIX = re.compile(r"^score(?:[1-9]|1[0-2])?$")
# Codes de fléchette qui ont un dossier dans Scores (S1…S20, D1…D20, T1…T20, SB, DB)
DART_FOLDER = re.compile(r"^(?:[SDT](?:[1-9]|1\d|20)|SB|DB)$")
NOT_THROWN = "X"
# Ignorés et journalisés (§ 6.6, § 9.9) : préfixes connus, sans erreur ; fonds des styles
# fond1 à fond12 (fond3 à fond12 : RaspyDarts 4.6.5, cdmdscore.NUMBER, 52e complément)
KNOWN_IGNORED = (tuple(f"fond{n}" for n in range(1, 13))
                 + ("time", "shutdwn", "team501", "zap501", "zapped", "bonus421", "demo", "pico"))

# États
WELCOME = "welcome"
DISPLAY = "display"                     # texte, score ou mire affichés
ATTRACT_GIF = "attract_gif"
ATTRACT_CLOCK = "attract_clock"


# --- Messages analysés ----------------------------------------------------------------------

@dataclass(frozen=True)
class TextMessage:
    kind: str                           # msg, msgmove, msgmovebcl, msgcolor
    text: str
    color: Optional[Tuple[int, int, int]] = None
    background: Optional[Tuple[int, int, int]] = None
    scroll: Optional[str] = None        # sens du défilement tel qu'envoyé (left, flip…) ; None : fixe
    passes: Optional[int] = None        # nombre de passages (msgmovebcl) ; None : non précisé
    tempo: Optional[float] = None


@dataclass(frozen=True)
class ScoreMessage:
    prefix: str                         # score, score1 … score12
    darts: Tuple[str, str, str]
    color: Optional[Tuple[int, int, int]] = None
    gif: bool = True                    # O (ou absent) : True ; N : False

    @property
    def last_dart(self):
        """Fléchette concernée : la dernière lancée, dernier champ différent de X [PROPOSÉ]."""
        for dart in reversed(self.darts):
            if dart != NOT_THROWN:
                return dart
        return None

    @property
    def text(self):
        return " - ".join(self.darts)


@dataclass(frozen=True)
class AttractMessage:                   # demo|gif
    pass


@dataclass(frozen=True)
class PatternMessage:                   # pico|test_rgb
    pass


@dataclass(frozen=True)
class Ignored:
    reason: str


@dataclass(frozen=True)
class Invalid:
    reason: str


# --- Commandes de rendu ---------------------------------------------------------------------

@dataclass(frozen=True)
class Welcome:
    """Écran d'accueil : le texte RASPYDARTS."""
    text: str = "RASPYDARTS"


@dataclass(frozen=True)
class ShowText:
    text: str
    color: Tuple[int, int, int] = DEFAULT_TEXT_COLOR
    background: Optional[Tuple[int, int, int]] = None
    scroll: Optional[str] = None
    passes: Optional[int] = None
    tempo: Optional[float] = None


@dataclass(frozen=True)
class ShowMedia:
    """Un média : name donné (Attract Mode) ou None (un fichier tiré au hasard dans folder,
    choisi par le rendu). fallback : ce qui s'affiche si le dossier est vide ou absent, et
    le texte qui suit le média (score : le média, puis ce texte fixe, 48e complément).
    token : rappelé par media_done() à la fin du média."""
    folder: str
    name: Optional[str] = None
    fallback: Optional[ShowText] = None
    token: int = 0


@dataclass(frozen=True)
class ShowClock:
    duration_s: int = CLOCK_DURATION_S


@dataclass(frozen=True)
class ShowPattern:
    """Mire RGB dessinée par le service (pico|test_rgb)."""
    pass


# --- Analyse ---------------------------------------------------------------------------------

def _color(field):
    """R;G;B -> (r, g, b) ; champ vide -> None ; ValueError si invalide."""
    if field == "":
        return None
    parts = field.split(";")
    if len(parts) != 3:
        raise ValueError(f"bad colour {field!r}")
    values = tuple(int(p) for p in parts)
    if any(not 0 <= v <= 255 for v in values):
        raise ValueError(f"bad colour {field!r}")
    return values


def _int(field, name, minimum):
    if field == "":
        return None
    value = int(field)
    if value < minimum:
        raise ValueError(f"bad {name} {field!r}")
    return value


def _tempo(field):
    if field == "":
        return None
    value = float(field)
    if not value >= 0 or value == float("inf"):
        raise ValueError(f"bad tempo {field!r}")
    return value


def _text(prefix, fields):
    # cdmd.send_text (4.6.5) : msg|texte[|tempo], msgmove|texte|sens[|tempo],
    # msgmovebcl|texte|sens|passages[|tempo], msgcolor|texte|R;G;B|R;G;B|sens|passages
    if not fields:
        raise ValueError("no text")
    text = fields[0]
    rest = fields[1:]
    if prefix == "msg":
        if len(rest) > 1:
            raise ValueError("too many fields")
        return TextMessage(prefix, text, tempo=_tempo(rest[0]) if rest else None)
    if prefix == "msgmove":
        if not 1 <= len(rest) <= 2 or rest[0] == "":
            raise ValueError("no direction" if not rest or rest[0] == "" else "too many fields")
        return TextMessage(prefix, text, scroll=rest[0], tempo=_tempo(rest[1]) if len(rest) > 1 else None)
    if prefix == "msgmovebcl":
        if not 2 <= len(rest) <= 3 or rest[0] == "":
            raise ValueError("no direction or passes" if len(rest) < 2 or rest[0] == "" else "too many fields")
        passes = _int(rest[1], "passes", 1)
        if passes is None:
            raise ValueError("no passes")
        return TextMessage(prefix, text, scroll=rest[0], passes=passes,
                           tempo=_tempo(rest[2]) if len(rest) > 2 else None)
    # msgcolor
    if len(rest) != 4:
        raise ValueError("msgcolor needs 5 fields")
    return TextMessage(prefix, text, color=_color(rest[0]), background=_color(rest[1]),
                       scroll=rest[2] or None, passes=_int(rest[3], "passes", 1))


def _score(prefix, fields):
    # cdmd.score_text : score|<f1> - <f2> - <f3>||<R;G;B>|<O/N> ; formes anciennes acceptées
    # (score|X - X - X, forme 4.6.3 sans <O/N>), § 6.6
    if not fields or len(fields) > 4:
        raise ValueError("bad field count")
    darts = tuple(d.strip() for d in fields[0].split(" - "))
    if len(darts) != 3 or any(d == "" for d in darts):
        raise ValueError(f"bad darts {fields[0]!r}")
    if len(fields) > 1 and fields[1] != "":
        raise ValueError("second field not empty")
    color = _color(fields[2]) if len(fields) > 2 else None
    gif = fields[3] if len(fields) > 3 else "O"
    if gif not in ("O", "N"):
        raise ValueError(f"bad GIF flag {gif!r}")
    return ScoreMessage(prefix, darts, color, gif == "O")


def parse(topic, payload):
    """Un message MQTT -> TextMessage, ScoreMessage, AttractMessage, PatternMessage,
    Ignored ou Invalid. Les deux sujets suivent la même grammaire (§ 6.6, § 9.9)."""
    if topic not in TOPICS:
        return Ignored(f"topic {topic}")
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = bytes(payload).decode("utf-8")
        except UnicodeDecodeError:
            return Invalid("not UTF-8")
    payload = payload.rstrip("\r\n")
    if payload == "":
        return Invalid("empty")
    prefix, sep, rest = payload.partition("|")
    fields = rest.split("|") if sep else []
    try:
        if prefix in ("msg", "msgmove", "msgmovebcl", "msgcolor"):
            return _text(prefix, fields)
        if SCORE_PREFIX.match(prefix):
            return _score(prefix, fields)
    except ValueError as error:
        return Invalid(str(error))
    if payload == "demo|gif":
        return AttractMessage()
    if payload == "pico|test_rgb":
        return PatternMessage()
    if prefix in KNOWN_IGNORED:
        return Ignored("not rendered")
    return Ignored("unknown")


def message_type(topic, payload):
    """Type d'un message pour le journal (§ 9.9, 45e complément) : le texte avant le premier |,
    ou tout le message ; autre sujet : ce sujet ; vide ; pas en UTF-8."""
    if topic not in TOPICS:
        return f"topic {topic}"
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = bytes(payload).decode("utf-8")
        except UnicodeDecodeError:
            return "not UTF-8"
    payload = payload.rstrip("\r\n")
    if payload == "":
        return "empty"
    return payload.partition("|")[0]


def _shown(payload):
    if isinstance(payload, (bytes, bytearray)):
        payload = bytes(payload).decode("utf-8", "replace")
    return payload[:LOG_PAYLOAD_MAX]


# --- Coups spéciaux (49e complément) ----------------------------------------------------------

@dataclass(frozen=True)
class SpecialMove:
    rule: int                           # ligne de la table du 49e complément (ordre de priorité)
    name: str                           # nom du coup dans le jeu (cvideos.py, ctrophies.py)
    folder: Optional[str]               # dossier de SpecialsMoves ; None : règle sans dossier
    file: Optional[str] = None          # fichier précis (DEVIL) ; None : tiré au hasard


MISS = "MISS"
DEVIL_FILE = "devil 2.gif"
DEVIL_TRIPLE_FILE = "devil (triple) 2.gif"
# Ligne 16 : totaux exacts des trophées (include/ctrophies.py:538-546) -> (nom, dossier)
TROPHY_TOTALS = {
    22: ("SWAN_LAKE", "DINKY_DOO"),
    33: ("FEATHERS", "FEATHERS"),
    45: ("BAG_O_NUTS", "BAG_O_NUT"),
    66: ("ROUTE_66", "ROUTE_66"),
    69: ("HAPPY_MEAL", "HAPPY_MEAL"),
    77: ("SUNSET_STRIP", "SUNSET_STRIP"),
    88: ("2_FAT_LADIES", "GARDEN_GATE_FAT_LADIES"),
    95: ("BABY_TON", "BABY_TON"),
}
# Dossiers de SpecialsMoves qu'une règle peut déclencher (les autres ne sont pas chargés)
SPECIAL_FOLDERS = frozenset((
    "MAXIMUM_TON_80", "DEVIL", "BREAKFAST", "CHAMPAGNE_BREAKFAST", "ROUND_OF_TERMS",
    "BUCKET_OF_NAIL", "NOT_OLD", "BLACK_HAT_THREE_IN_THE_BLACK", "HAT_TRICK", "THREE_IN_A_BED",
    "LOW_TON", "HIGH_TON") + tuple(folder for _, folder in TROPHY_TOTALS.values()))


def dart_points(dart):
    """Points d'une fléchette (en majuscules) : S × 1, D × 2, T × 3, SB 25, DB 50, MISS 0 ;
    None si la valeur est inconnue."""
    if dart == MISS:
        return 0
    if dart == "SB":
        return 25
    if dart == "DB":
        return 50
    if DART_FOLDER.match(dart):
        return int(dart[1:]) * {"S": 1, "D": 2, "T": 3}[dart[0]]
    return None


def special_move(darts):
    """Coup spécial d'une volée de trois fléchettes (49e complément) : la première règle
    vraie de la table, dans l'ordre de special_move (include/cvideos.py:308-398 de la 4.6.5),
    puis les totaux des trophées. None : aucune règle, fléchette non lancée (X) ou valeur
    inconnue (le score se rend alors comme un score ordinaire)."""
    if len(darts) != 3:
        return None
    darts = tuple(d.upper() for d in darts)
    points = [dart_points(d) for d in darts]
    if any(p is None for p in points):              # X ou valeur inconnue : pas de coup spécial
        return None
    total = sum(points)
    same = darts[0] == darts[1] == darts[2]
    bag = sorted(darts)
    if same and darts[0] == "T20":
        return SpecialMove(1, "MAXIMUM_TON_80", "MAXIMUM_TON_80")
    if same and darts[0] == "S6":
        return SpecialMove(2, "DEVIL", "DEVIL", DEVIL_FILE)
    if same and darts[0] == "T6":
        return SpecialMove(3, "DEVIL_TRIPLE", "DEVIL", DEVIL_TRIPLE_FILE)
    if bag == sorted(("S1", "S5", "S20")):
        return SpecialMove(4, "BREAKFAST", "BREAKFAST")
    if bag == sorted(("T1", "T5", "T20")):
        return SpecialMove(5, "CHAMPAGNE_BREAKFAST", "CHAMPAGNE_BREAKFAST")
    if all(d.startswith("T") for d in darts):
        return SpecialMove(6, "ROUND_OF_TERMS", "ROUND_OF_TERMS")
    if same and darts[0] == "S1":
        return SpecialMove(7, "BUCKET_OF_NAIL", "BUCKET_OF_NAIL")
    if bag == sorted(("S5", "S12", "S20")):
        return SpecialMove(8, "NOT_OLD", "NOT_OLD")
    if same and darts[0] == "DB":
        return SpecialMove(9, "BLACK_HAT_THREE_IN_THE_BLACK", "BLACK_HAT_THREE_IN_THE_BLACK")
    if same and darts[0] == "SB":
        return SpecialMove(10, "RED_HAT", None)
    if all(d in ("SB", "DB") for d in darts):
        return SpecialMove(11, "HAT_TRICK", "HAT_TRICK")
    if same and darts[0] != MISS:                   # trois MISS exclus (Q11)
        return SpecialMove(12, "THREE_IN_A_BED", "THREE_IN_A_BED")
    if 100 <= total <= 150:                         # Q7
        return SpecialMove(13, "LOW_TON", "LOW_TON")
    if 151 <= total <= 179:
        return SpecialMove(14, "HIGH_TON", "HIGH_TON")
    if darts[2] in ("T17", "T18", "T19", "T20"):
        return SpecialMove(15, "BAIL_OUT", None)
    if total in TROPHY_TOTALS:
        name, folder = TROPHY_TOTALS[total]
        return SpecialMove(16, name, folder)
    return None


# --- Machine d'états -------------------------------------------------------------------------

def _monotonic_us():
    return time.monotonic_ns() // 1000


class DmdCore:
    def __init__(self, clock=None, attract_delay_s=ATTRACT_DELAY_S, gifs=(), time_synced=None, rng=None,
                 gif_source=None, special_moves=True, media_available=None, clock_every_s=CLOCK_EVERY_S):
        if not attract_delay_s > 0:
            raise ValueError("attract_delay_s must be > 0")
        if not clock_every_s > 0:
            raise ValueError("clock_every_s must be > 0")
        self.clock = clock or _monotonic_us
        self.attract_delay_us = int(attract_delay_s * 1000000)
        # Heure toutes les clock_every_s (50e complément) : échéance comptée depuis l'entrée en
        # Attract Mode, puis depuis la fin de chaque affichage de l'heure
        self.clock_every_us = int(clock_every_s * 1000000)
        self._clock_due_us = None
        self.time_synced = time_synced or (lambda: False)
        self.rng = rng or random.Random()
        self.state = WELCOME
        self._last_us = self.clock()
        self._clock_until_us = None
        self._token = 0
        self._wait_token = None         # jeton du GIF de l'Attract Mode en cours
        self._after_media = None        # (jeton, ShowText) : texte qui suit le média de score
        self._pending = None            # dernier texte reçu pendant le média de score (49e complément)
        self.special_moves = special_moves  # --no-special-moves : False (49e complément)
        # media_available(dossier, fichier ou None) : vrai si le rendu a ce média (cache de
        # Scores et SpecialsMoves) ; None : toujours vrai
        self.media_available = media_available or (lambda folder, name: True)
        self._played = 0                # GIF joués depuis l'entrée dans l'Attract Mode
        self._gifs = []
        self._bag = []
        self._last_gif = None
        self._logged = set()            # types déjà journalisés (ignorés ou invalides)
        self._gif_source = gif_source   # gif_source() -> noms, relue à chaque nouveau sac (48e complément)
        self._excluded = set()          # fichiers corrompus, exclus pour la vie du service
        self.set_gifs(gifs)

    # Entrées

    def start(self):
        self.state = WELCOME
        self._last_us = self.clock()
        return [Welcome()]

    def set_gifs(self, names):
        """Liste des GIF de l'Attract Mode (noms dans ATTRACT_FOLDER) ; le sac repart à zéro."""
        self._gifs = sorted(set(names) - self._excluded)
        self._bag = []

    def exclude(self, name):
        """GIF corrompu (48e complément) : retiré du sac en cours et de tous les suivants,
        même quand la liste est relue."""
        if name in self._excluded:
            return
        self._excluded.add(name)
        self._gifs = [g for g in self._gifs if g != name]
        self._bag = [g for g in self._bag if g != name]

    def message(self, topic, payload):
        item = parse(topic, payload)
        if isinstance(item, (Invalid, Ignored)):
            # Une ligne la première fois pour chaque type, puis plus rien (45e complément) ;
            # ni l'image, ni le délai, ni l'Attract Mode ne changent
            kind = message_type(topic, payload)
            if kind not in self._logged:
                self._logged.add(kind)
                if isinstance(item, Invalid):
                    log.warning("DMD : invalid message on %s (%s) : %r, this type no more logged",
                                topic, item.reason, _shown(payload))
                else:
                    log.info("DMD : message ignored on %s (%s) : %r, this type no more logged",
                             topic, item.reason, _shown(payload))
            return []
        if isinstance(item, AttractMessage):
            return self._enter_attract()
        self._last_us = self.clock()        # attract_delay_s : depuis le dernier message reçu
        if isinstance(item, TextMessage):
            text = ShowText(item.text, item.color or DEFAULT_TEXT_COLOR, item.background,
                            item.scroll, item.passes, item.tempo)
            if self.state == DISPLAY and self._after_media is not None:
                # Média d'un score en cours : le texte attend la fin du média, le dernier
                # reçu remplace les précédents et le texte du score (49e complément)
                self._pending = text
                return []
        self.state = DISPLAY
        self._wait_token = None
        self._after_media = None        # score, mire : le média et le texte en attente sont oubliés
        self._pending = None
        self._clock_until_us = None
        if isinstance(item, PatternMessage):
            return [ShowPattern()]
        if isinstance(item, ScoreMessage):
            return self._score(item)
        return [text]

    def _score(self, item):
        """Score : le coup spécial s'il y en a un (49e complément), sinon le média de
        Scores/<dernière fléchette> (48e complément), puis le texte ; sinon le texte seul."""
        text = ShowText(item.text, item.color or DEFAULT_TEXT_COLOR)
        if not item.gif:
            return [text]
        media = None
        move = special_move(item.darts) if self.special_moves else None
        if move is not None and move.folder is not None:
            folder = f"{SPECIALS_FOLDER}/{move.folder}"
            if self.media_available(folder, move.file):
                media = (folder, move.file)
        # Règle vraie sans dossier ou dossier vide : score ordinaire, sans essayer les règles
        # suivantes (49e complément)
        dart = item.last_dart
        if media is None and dart is not None and DART_FOLDER.match(dart):
            folder = f"{SCORES_FOLDER}/{dart}"
            if self.media_available(folder, None):
                media = (folder, None)
        if media is None:
            return [text]
        token = self._next_token()
        self._after_media = (token, text)
        return [ShowMedia(media[0], media[1], text, token)]

    def tick(self):
        now = self.clock()
        # Écran d'accueil compris : Attract Mode après attract_delay_s sans message (45e complément)
        if self.state in (WELCOME, DISPLAY) and now - self._last_us >= self.attract_delay_us:
            return self._enter_attract()
        if self.state == ATTRACT_CLOCK and now - self._clock_until_us >= 0:
            self._clock_until_us = None
            self._clock_due_us = now + self.clock_every_us     # depuis la fin de l'heure
            return self._next_gif()
        return []

    def media_done(self, token):
        """Fin d'un média (rendu) : en Attract Mode, le suivant ; après un média de score,
        le dernier texte reçu pendant le média s'il y en a un, sinon le texte du score, fixe
        (48e et 49e compléments), sans relancer le délai de l'Attract Mode ; sinon rien."""
        if self.state == DISPLAY and self._after_media is not None and token == self._after_media[0]:
            text = self._pending or self._after_media[1]
            self._after_media = None
            self._pending = None
            return [text]
        if self.state != ATTRACT_GIF or token != self._wait_token:
            return []
        self._played += 1
        # L'heure attend la fin du GIF en cours (50e complément)
        now = self.clock()
        if self._clock_due_us is not None and now - self._clock_due_us >= 0:
            if self.time_synced():
                self.state = ATTRACT_CLOCK
                self._wait_token = None
                self._clock_until_us = now + CLOCK_DURATION_S * 1000000
                return [ShowClock()]
            # Heure non synchronisée à l'échéance : pas d'heure, nouvel essai
            # clock_every_s après la fin de ce GIF (50e complément)
            self._clock_due_us = now + self.clock_every_us
        return self._next_gif()

    # Interne

    def _next_token(self):
        self._token += 1
        return self._token

    def _enter_attract(self):
        self._after_media = None
        self._pending = None
        self._played = 0
        self._clock_until_us = None
        self._clock_due_us = self.clock() + self.clock_every_us    # depuis l'entrée
        return self._next_gif()

    def _refill(self):
        """Nouveau sac : liste relue par gif_source s'il y en a une (48e complément), puis
        mélangée ; pas deux fois de suite le même au changement de sac."""
        if self._gif_source is not None:
            try:
                names = self._gif_source()
            except Exception as error:      # le service ne s'arrête jamais pour un média
                if "gif_source" not in self._logged:
                    self._logged.add("gif_source")
                    log.warning("DMD : Attract Mode GIF list unreadable (%s), no more logged", error)
                names = ()
            self._gifs = sorted(set(names) - self._excluded)
        self._bag = list(self._gifs)
        self.rng.shuffle(self._bag)
        if len(self._bag) > 1 and self._bag[-1] == self._last_gif:
            self._bag[0], self._bag[-1] = self._bag[-1], self._bag[0]

    def _draw(self):
        """Tirage « en sac » : tous les GIF passent une fois, dans un ordre au hasard, avant
        qu'un revienne."""
        self._last_gif = self._bag.pop()
        return self._last_gif

    def _next_gif(self):
        if not self._bag:
            self._refill()
        if not self._bag:
            # Attract Mode sans aucun GIF : écran d'accueil (§ 9.9) ; nouvel essai après
            # attract_delay_s, pour prendre des GIF fournis entre-temps
            self.state = WELCOME
            self._last_us = self.clock()
            self._wait_token = None
            return [Welcome()]
        self.state = ATTRACT_GIF
        self._wait_token = self._next_token()
        return [ShowMedia(ATTRACT_FOLDER, self._draw(), None, self._wait_token)]
