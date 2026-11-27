# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Protocole texte Pi <-> Pico (spécification § 5).

Code partagé : s'exécute sous CPython (Pi) et sous MicroPython (Pico).
Règles : pas de f-string, pas d'annotation de type, pas de typing, enum,
dataclasses, abc ni re ; aucune entrée-sortie. Les entiers sont analysés
par les fonctions de ce module (int() diffère entre CPython et MicroPython).

Conventions :
- une ligne est une chaîne str sans terminaison ; LineSplitter découpe un
  flux d'octets en lignes, encode_line() ajoute la terminaison ;
- les fonctions parse_* renvoient des tuples dont le premier élément est le
  mot-clé du message, et lèvent ProtocolError en cas de refus ;
- les fonctions de construction lèvent ValueError si un argument est faux
  (erreur de programmation de l'appelant) : elles valident leur résultat en
  le relisant avec l'analyseur, pour que les deux ne divergent jamais.
"""

import binascii

PROTO_VERSION = 1
BOARD_NAME = "darts-pico"

# t_µs : time.ticks_us() de MicroPython, sur 30 bits (§ 5.2).
TICKS_PERIOD = 1 << 30
TICKS_MASK = TICKS_PERIOD - 1

ROWS = 12          # matrice de la cible : 12 lignes (spécification v0.3.6)
COLS = 7
BUTTONS = 15
TOYS = 8
LIGHTS = 12
LED_CHANNELS = 3
# Budget de courant des LED sur +5V_LEDT (F2), en mA : maximum de led_max_ma,
# jamais dépassé (§ 3.7, § 5.3, 54e complément).
LED_MAX_MA = 10000
SHOCK_MAX = 4095
TAG_MAX = 65535
PULSE_MS_MAX = 60000
PULSE_REPEAT_MAX = 65535

CAPS = (("rows", ROWS), ("cols", COLS), ("buttons", BUTTONS), ("toys", TOYS),
        ("lights", LIGHTS), ("leds", LED_CHANNELS), ("shock", 1))

# Codes de refus N (§ 5.1).
SYNTAX = "SYNTAX"
UNKNOWN = "UNKNOWN"
ARGS = "ARGS"
RANGE = "RANGE"
KEY = "KEY"
TOOLONG = "TOOLONG"
NACK_CODES = (SYNTAX, UNKNOWN, ARGS, RANGE, KEY, TOOLONG)

# Codes d'erreur E connus (§ 5.2) ; d'autres sont acceptés à la lecture.
E_MCP = "MCP"
E_WDG = "WDG"
E_CFG = "CFG"
E_OVERFLOW = "OVERFLOW"

LED_ORDERS = ("RGB", "RBG", "GRB", "GBR", "BRG", "BGR")


class Limits:
    """Limites paramétrables (§ 5.1, P7) : longueur de ligne, LED par canal."""

    def __init__(self, max_line=2048, max_leds=300):
        if max_line < 64 or max_leds < 1:
            raise ValueError("limites trop petites")
        self.max_line = max_line
        self.max_leds = max_leds


DEFAULT_LIMITS = Limits()


class ProtocolError(Exception):
    """Message refusé. code : un des NACK_CODES ; tag : n du préfixe ou None."""

    def __init__(self, code, tag=None, detail=""):
        super().__init__(code)          # MicroPython v1.29 : seul super() marche ici
        self.code = code
        self.tag = tag
        self.detail = detail


# ---------------------------------------------------------------- outils

def ticks_diff(a, b):
    """Écart a - b entre deux t_µs, modulo 2^30, dans [-2^29, 2^29[."""
    d = (a - b) & TICKS_MASK
    if d >= TICKS_PERIOD >> 1:
        d -= TICKS_PERIOD
    return d


_DEC = "0123456789"
_HEX = "0123456789ABCDEFabcdef"


def parse_dec(s, lo, hi, code=RANGE):
    """Entier décimal strict dans [lo, hi]. Format faux : ARGS ; hors plage : code."""
    if not s or len(s) > 10:
        raise ProtocolError(ARGS, None, s)
    for ch in s:
        if ch not in _DEC:
            raise ProtocolError(ARGS, None, s)
    v = 0
    for ch in s:
        v = v * 10 + (ord(ch) - 48)
    if v < lo or v > hi:
        raise ProtocolError(code, None, s)
    return v


def parse_hex(s, nbits):
    """Masque hexadécimal strict (sans 0x, toute largeur) sur nbits bits."""
    if not s or len(s) > 8:
        raise ProtocolError(ARGS, None, s)
    v = 0
    for ch in s:
        i = _HEX.find(ch)
        if i < 0:
            raise ProtocolError(ARGS, None, s)
        if i > 15:
            i -= 6
        v = (v << 4) | i
    if v >> nbits:
        raise ProtocolError(RANGE, None, s)
    return v


def parse_bool(s):
    if s == "0":
        return 0
    if s == "1":
        return 1
    raise ProtocolError(ARGS, None, s)


def _is_word(s, extra=""):
    """Mot fait de majuscules, chiffres, _ et des caractères de extra."""
    if not s:
        return False
    for ch in s:
        if not ("A" <= ch <= "Z" or "0" <= ch <= "9" or ch == "_" or ch in extra):
            return False
    return True


def split_strict(line, limits=DEFAULT_LIMITS):
    """Découpe une ligne selon la syntaxe stricte (§ 5.1) ; lève SYNTAX ou TOOLONG."""
    if len(line) > limits.max_line:
        raise ProtocolError(TOOLONG)
    if not line:
        raise ProtocolError(SYNTAX)
    # min() et max() sur les octets bouclent en C : un LEDF de 1 200 caractères
    # coûtait environ 30 ms sous MicroPython avec une boucle par caractère.
    # Un caractère non ASCII donne des octets >= 0x80, donc refusés aussi.
    b = line.encode()
    if min(b) < 0x20 or max(b) > 0x7E:
        raise ProtocolError(SYNTAX)
    if line[0] == " " or line[-1] == " " or "  " in line:
        raise ProtocolError(SYNTAX)
    return line.split(" ")


def encode_line(line):
    """Ligne str -> octets terminés par \\n."""
    return line.encode() + b"\n"


class LineSplitter:
    """Découpe un flux d'octets en lignes (§ 5.1).

    feed(data) (data : bytes ou bytearray) renvoie une liste d'éléments :
    une str par ligne complète (\\r final retiré), ou une ProtocolError
    (TOOLONG : ligne trop longue ; SYNTAX : octets non décodables), à
    refuser par N - <code>. Une ligne trop longue n'est jamais conservée en
    mémoire au-delà de la limite.
    """

    def __init__(self, limits=DEFAULT_LIMITS):
        self.limits = limits
        self._buf = bytearray()
        self._overflow = False

    def reset(self):
        self._buf = bytearray()
        self._overflow = False

    def pending(self):
        """Nombre d'octets reçus d'une ligne pas encore terminée."""
        return len(self._buf)

    def feed(self, data):
        out = []
        start = 0
        n = len(data)
        while start < n:
            i = data.find(b"\n", start)
            end = n if i < 0 else i
            if not self._overflow:
                self._buf.extend(data[start:end])
                # \r final toléré : il ne compte pas dans la limite.
                limit = self.limits.max_line + 1
                if len(self._buf) > limit:
                    self._overflow = True
                    self._buf = bytearray()
            if i < 0:
                break
            if self._overflow:
                out.append(ProtocolError(TOOLONG))
            else:
                raw = self._buf
                if raw and raw[-1] == 0x0D:
                    raw = raw[:-1]
                if len(raw) > self.limits.max_line:
                    out.append(ProtocolError(TOOLONG))
                else:
                    try:
                        out.append(bytes(raw).decode())
                    except UnicodeError:
                        out.append(ProtocolError(SYNTAX))
            self._buf = bytearray()
            self._overflow = False
            start = i + 1
        return out


# ------------------------------------------------- Pico -> Pi (événements)

def _check_ticks(s):
    return parse_dec(s, 0, TICKS_MASK)


def _parse_caps(fields):
    caps = {}
    for f in fields:
        k, sep, v = f.partition("=")
        if not sep or not k or k in caps:
            raise ProtocolError(ARGS, None, f)
        for ch in k:
            if not ("a" <= ch <= "z"):
                raise ProtocolError(ARGS, None, f)
        caps[k] = parse_dec(v, 0, 1000000)
    for k, _ in CAPS:
        if k not in caps:
            raise ProtocolError(ARGS, None, k)
    return caps


def _parse_version(s):
    parts = s.split(".")
    if len(parts) != 3:
        raise ProtocolError(ARGS, None, s)
    return (parse_dec(parts[0], 0, 999), parse_dec(parts[1], 0, 999),
            parse_dec(parts[2], 0, 999))


def parse_event(line, limits=DEFAULT_LIMITS):
    """Analyse une ligne émise par le Pico ; renvoie un tuple.

    ('HELLO', proto, board, (x, y, z), uid) ; ('CAPS', dict) ;
    ('D', row, col, t, dur) ; ('X', row, col, dur) ; ('B', n, state, t) ;
    ('BS', mask) ; ('S', t, peak) ; ('E', code, text) ; ('PONG', t) ;
    ('K', n) ; ('N', n ou None, code) ; ('V', key, value) ; ('#', text).
    """
    if line.startswith("#"):
        return ("#", line[1:].strip())
    f = split_strict(line, limits)
    kw = f[0]
    a = f[1:]
    na = len(a)
    if kw == "D":
        if na != 4:
            raise ProtocolError(ARGS)
        return ("D", parse_dec(a[0], 0, ROWS - 1), parse_dec(a[1], 0, COLS - 1),
                _check_ticks(a[2]), _check_ticks(a[3]))
    if kw == "B":
        if na != 3:
            raise ProtocolError(ARGS)
        return ("B", parse_dec(a[0], 1, BUTTONS), parse_bool(a[1]), _check_ticks(a[2]))
    if kw == "S":
        if na != 2:
            raise ProtocolError(ARGS)
        return ("S", _check_ticks(a[0]), parse_dec(a[1], 0, SHOCK_MAX))
    if kw == "X":
        if na != 3:
            raise ProtocolError(ARGS)
        return ("X", parse_dec(a[0], 0, ROWS - 1), parse_dec(a[1], 0, COLS - 1),
                _check_ticks(a[2]))
    if kw == "BS":
        if na != 1 or len(a[0]) != 4:
            raise ProtocolError(ARGS)
        return ("BS", parse_hex(a[0], BUTTONS))
    if kw == "K":
        if na != 1:
            raise ProtocolError(ARGS)
        return ("K", parse_dec(a[0], 0, TAG_MAX))
    if kw == "N":
        if na != 2:
            raise ProtocolError(ARGS)
        tag = None if a[0] == "-" else parse_dec(a[0], 0, TAG_MAX)
        if not _is_word(a[1]):
            raise ProtocolError(ARGS)
        return ("N", tag, a[1])
    if kw == "PONG":
        if na != 1:
            raise ProtocolError(ARGS)
        return ("PONG", _check_ticks(a[0]))
    if kw == "V":
        if na != 2:
            raise ProtocolError(ARGS)
        return ("V", a[0], a[1])
    if kw == "E":
        if na < 1 or not _is_word(a[0]):
            raise ProtocolError(ARGS)
        return ("E", a[0], " ".join(a[1:]))
    if kw == "HELLO":
        if na != 4 or a[1] != BOARD_NAME:
            raise ProtocolError(ARGS)
        uid = a[3]
        if len(uid) > 32:
            raise ProtocolError(ARGS)
        for ch in uid:
            if ch not in "0123456789ABCDEF":
                raise ProtocolError(ARGS)
        return ("HELLO", parse_dec(a[0], 0, 255), a[1], _parse_version(a[2]), uid)
    if kw == "CAPS":
        return ("CAPS", _parse_caps(a))
    raise ProtocolError(UNKNOWN)


# Revérification de chaque message construit par parse_event. Gardée sur le Pi
# et dans les tests sur PC ; le firmware la coupe (app.run) : environ 0,5 ms
# par message sur le Pico (mesuré au banc, M3b-2).
CHECK_BUILT = True


def _built(line):
    if not CHECK_BUILT:
        return line
    try:
        parse_event(line)
    except ProtocolError as e:
        raise ValueError("message invalide : %s (%s)" % (line, e.code))
    return line


def _t(t):
    """Ramène un compteur sur 30 bits."""
    return t & TICKS_MASK


def msg_hello(fw_version, uid):
    """fw_version : tuple (x, y, z) ; uid : identifiant hexadécimal majuscule."""
    return _built("HELLO %d %s %d.%d.%d %s" % (PROTO_VERSION, BOARD_NAME,
                                               fw_version[0], fw_version[1],
                                               fw_version[2], uid))


def msg_caps():
    return _built("CAPS " + " ".join(["%s=%d" % kv for kv in CAPS]))


def msg_dart(row, col, t, dur):
    return _built("D %d %d %d %d" % (row, col, _t(t), min(dur, TICKS_MASK)))


def msg_reject(row, col, dur):
    return _built("X %d %d %d" % (row, col, min(dur, TICKS_MASK)))


def msg_button(n, pressed, t):
    return _built("B %d %d %d" % (n, 1 if pressed else 0, _t(t)))


def msg_buttons_state(mask):
    return _built("BS %04X" % mask)


def msg_shock(t, peak):
    return _built("S %d %d" % (_t(t), peak))


def msg_error(code, text):
    return _built(("E %s %s" % (code, text)) if text else ("E %s" % code))


def msg_pong(t):
    return _built("PONG %d" % _t(t))


def msg_ack(tag):
    return _built("K %d" % tag)


def msg_nack(tag, code):
    return _built("N %s %s" % ("-" if tag is None else "%d" % tag, code))


def msg_value(key, value):
    return _built("V %s %s" % (key, value))


# ------------------------------------------------- Pi -> Pico (commandes)

# Paramètres (§ 5.3) : clé -> (défaut, plage) ; plage = (min, max) ou tuple de mots.
def param_specs(limits=DEFAULT_LIMITS):
    specs = {
        "min_contact_us": (300, (0, 100000)),
        "release_ms": (20, (0, 1000)),
        "hold_max_ms": (400, (1, 10000)),
        "btn_debounce_ms": (10, (0, 1000)),
        "shock_mode": ("digital", ("digital", "adc")),
        "shock_polarity": ("high", ("high", "low")),
        "shock_threshold": (2048, (0, SHOCK_MAX)),
        "shock_holdoff_ms": (50, (0, 10000)),
        "watchdog_ms": (3000, (2000, 60000)),
        "reject_report": (0, (0, 1)),
        "hw_watchdog": (1, (0, 1)),          # chien de garde matériel (§ 5.4, v0.3.14)
        "bench_row_pullup": (0, (0, 1)),     # réservé au banc (§ 3.1.1, v0.3.14)
        "led_max_ma": (LED_MAX_MA, (0, LED_MAX_MA)),   # bridage du courant (54e complément)
    }
    for ch in range(LED_CHANNELS):
        specs["led%d_order" % ch] = ("GRB", LED_ORDERS)
        specs["led%d_count" % ch] = (0, (0, limits.max_leds))
        specs["led%d_5v" % ch] = (1, (0, 1))           # compté dans le budget de +5V_LEDT
    return specs


_SPECS = param_specs()

# Paramètres réservés au banc (§ 5.3) : hwd ne les envoie jamais.
BENCH_PARAMS = ("bench_row_pullup",)

# Valeurs lisibles par GET seulement (§ 5.3) : ni SET, ni fichier, ni DEFAULTS.
READ_ONLY_KEYS = ("led_limited",)


def default_params(limits=DEFAULT_LIMITS):
    d = {}
    for k, v in param_specs(limits).items():
        d[k] = v[0]
    return d


def parse_param_value(key, value, limits=DEFAULT_LIMITS):
    """Valeur typée d'un paramètre ; lève KEY, ARGS ou RANGE."""
    specs = _SPECS if limits is DEFAULT_LIMITS else param_specs(limits)
    if key not in specs:
        raise ProtocolError(KEY)
    rng = specs[key][1]
    if isinstance(rng[0], str):
        if value not in rng:
            raise ProtocolError(RANGE)
        return value
    return parse_dec(value, rng[0], rng[1])


def format_param_value(value):
    return value if isinstance(value, str) else "%d" % value


def _n_args(a, n):
    if len(a) != n:
        raise ProtocolError(ARGS)


def _pulse(a, count):
    _n_args(a, 4)
    return (parse_dec(a[0], 1, count), parse_dec(a[1], 1, PULSE_MS_MAX),
            parse_dec(a[2], 1, PULSE_MS_MAX), parse_dec(a[3], 0, PULSE_REPEAT_MAX))


def _parse_args(name, a, limits):
    """Arguments typés d'une commande (sans le préfixe)."""
    if name in ("PING", "WHO", "SAVE", "DEFAULTS", "BTNPOLL", "MCPRESET", "REBOOT"):
        _n_args(a, 0)
        return ()
    if name == "GET":
        _n_args(a, 1)
        specs = _SPECS if limits is DEFAULT_LIMITS else param_specs(limits)
        if a[0] not in specs and a[0] not in READ_ONLY_KEYS:
            raise ProtocolError(KEY)
        return (a[0],)
    if name == "SET":
        _n_args(a, 2)
        return (a[0], parse_param_value(a[0], a[1], limits))
    if name == "TOY" or name == "LGT":
        _n_args(a, 2)
        return (parse_dec(a[0], 1, TOYS if name == "TOY" else LIGHTS), parse_bool(a[1]))
    if name == "TOYS":
        _n_args(a, 1)
        return (parse_hex(a[0], TOYS),)
    if name == "LGTS":
        _n_args(a, 1)
        return (parse_hex(a[0], LIGHTS),)
    if name == "TOYPULSE":
        return _pulse(a, TOYS)
    if name == "LGTPULSE":
        return _pulse(a, LIGHTS)
    if name == "TOYSTOP" or name == "LGTSTOP":
        _n_args(a, 1)
        return (parse_dec(a[0], 1, TOYS if name == "TOYSTOP" else LIGHTS),)
    if name == "LEDCFG":
        _n_args(a, 2)
        return (parse_dec(a[0], 0, LED_CHANNELS - 1), parse_dec(a[1], 0, limits.max_leds))
    if name == "LEDB":
        _n_args(a, 2)
        return (parse_dec(a[0], 0, LED_CHANNELS - 1), parse_dec(a[1], 0, 255))
    if name == "LEDOFF":
        _n_args(a, 1)
        return (parse_dec(a[0], 0, LED_CHANNELS - 1),)
    if name == "LEDF":
        _n_args(a, 2)
        ch = parse_dec(a[0], 0, LED_CHANNELS - 1)
        h = a[1]
        if len(h) % 6:
            raise ProtocolError(ARGS)
        if len(h) > 6 * limits.max_leds:
            raise ProtocolError(RANGE)
        # unhexlify vérifie les chiffres en C (ValueError, binascii.Error sous
        # CPython) ; les blancs qu'accepte celui de MicroPython ne peuvent pas
        # arriver ici (champ sans espace, caractères de contrôle refusés plus haut).
        try:
            return (ch, binascii.unhexlify(h))
        except ValueError:
            raise ProtocolError(ARGS)
    if name == "CAL":
        _n_args(a, 1)
        return (parse_bool(a[0]),)
    raise ProtocolError(UNKNOWN)


def parse_command(line, limits=DEFAULT_LIMITS):
    """Analyse une commande du Pi ; renvoie (tag, nom, arguments typés).

    tag vaut None sans préfixe. ProtocolError porte le tag s'il a pu être lu,
    pour répondre N <n> <code> ou N - <code>.
    """
    f = split_strict(line, limits)
    tag = None
    if f[0].startswith("@"):
        try:
            tag = parse_dec(f[0][1:], 0, TAG_MAX)
        except ProtocolError:
            raise ProtocolError(SYNTAX)
        f = f[1:]
        if not f:
            raise ProtocolError(SYNTAX, tag)
    name = f[0]
    try:
        if not _is_word(name):
            raise ProtocolError(UNKNOWN)
        return (tag, name, _parse_args(name, f[1:], limits))
    except ProtocolError as e:
        raise ProtocolError(e.code, tag, e.detail)


def command(name, *args, **kw):
    """Construit une commande ; kw : tag=n (préfixe @n), limits=Limits.

    Les arguments sont des entiers, des str ou, pour LEDF, des bytes (RVB).
    Les masques TOYS, LGTS sont donnés en entier.
    """
    tag = kw.get("tag")
    limits = kw.get("limits", DEFAULT_LIMITS)
    parts = [] if tag is None else ["@%d" % tag]
    parts.append(name)
    for i, v in enumerate(args):
        if name in ("TOYS", "LGTS") and i == 0:
            parts.append("%X" % v)
        elif name == "LEDF" and i == 1:
            parts.append(binascii.hexlify(v).decode().upper())
        elif isinstance(v, str):
            parts.append(v)
        else:
            parts.append("%d" % v)
    line = " ".join(parts)
    try:
        parse_command(line, limits)
    except ProtocolError as e:
        raise ValueError("commande invalide : %s (%s)" % (line[:60], e.code))
    return line
