# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Sorties LED WS2812, 3 canaux (§ 3.5, § 5.3 P10 et P11), sur le cœur 0.

Logique : nombre de LED, luminosité, image RVB reçue par LEDF, ordre des
couleurs `led<n>_order`. L'envoi aux LED passe par `show(canal, octets)` du
pilote donné à la construction (PioDriver) ; il reçoit les octets dans
l'ordre du fil, luminosité appliquée. LED noires au départ (§ 5.4).

PioDriver (§ 7.1) : une machine PIO par canal (PIO0, machines 0 à 2), jamais
le module `neopixel` ; les mots partent par DMA vers la FIFO de la machine,
sans bloquer le cœur 0. Une nouvelle image n'est lancée que si le transfert
précédent est fini et que le temps de verrouillage (300 µs à l'état bas) est
écoulé ; sinon show() renvoie False et le canal reste à envoyer.

Bridage du courant (§ 3.7, § 5.3, 54e complément) : F2 ne tient qu'environ
12 A en continu (10 A à 75 °C). Courant estimé sur +5V_LEDT, canaux dont
led<n>_5v vaut 1 : LED_IDLE_MA par LED, plus LED_MA_PER_COLOR par couleur à
255 (octet émis × 20 / 255). Si la somme exacte des octets émis tient dans le
budget led_max_ma, rien ne change ; sinon le facteur k = b + 1 de chaque canal
compté devient k × budget // U (U = Σc × k / 256 arrondi vers le haut), ce
qui garantit Σ((c × k') >> 8) ≤ Σc × k' / 256 ≤ budget. Entiers < 2^30.
"""

from array import array

import micropython
import proto as P
import board

try:
    import rp2
except ImportError:                          # CPython sans les modules simulés
    rp2 = None

LED_FREQ = 8000000                           # 10 cycles PIO par bit à 800 kHz
BIT_US = 30                                  # 24 bits × 1,25 µs par LED
LATCH_US = 300                               # repos bas entre deux images (WS2812B : ≥ 280 µs)
DREQ_PIO0_TX0 = 0                            # pico-sdk, rp2350 dreq.h : PIO0 TX0..TX2 = 0..2
LED_MA_PER_COLOR = 20                        # mA par couleur à 255 (à recaler au banc, § 12)
LED_IDLE_MA = 1                              # mA au repos par LED (idem)
COUNTER_MASK = 0x3FFFFFFF                    # compteur led_limited modulo 2^30 (petit entier)


try:
    ptr8                                     # noqa: F821 (viper : conversion du compilateur)
except NameError:                            # CPython (tests) : indexation directe
    def ptr8(b):
        return b
    ptr32 = ptr8


# Boucles en viper (§ 7.1) : en Python, wire() et l'empaquetage de show()
# coûtaient 3,9 ms et 7,5 ms pour 204 LED (mesuré au banc, M3b-2).
@micropython.viper
def _wire_into(src, dst, n: int, k: int, i0: int, i1: int, i2: int):
    s = ptr8(src)
    o = ptr8(dst)
    i = 0
    while i < n:
        o[i] = (s[i + i0] * k) >> 8
        o[i + 1] = (s[i + i1] * k) >> 8
        o[i + 2] = (s[i + i2] * k) >> 8
        i += 3


@micropython.viper
def _sums_into(src, n: int, k: int, out):
    """out[0] = Σc (octets bruts), out[1] = Σ((c × k) >> 8) (octets émis)."""
    s = ptr8(src)
    o = ptr32(out)
    raw = 0
    emit = 0
    i = 0
    while i < n:
        c = int(s[i])
        raw += c
        emit += (c * k) >> 8
        i += 1
    o[0] = raw
    o[1] = emit


@micropython.viper
def _pack_words(src, dst, n: int):
    s = ptr8(src)
    w = ptr32(dst)
    i = 0
    j = 0
    while i < n:
        w[i] = (s[j] << 24) | (s[j + 1] << 16) | (s[j + 2] << 8)
        i += 1
        j += 3


class Leds:
    def __init__(self, driver=None):
        self.driver = driver
        self.count = [0] * P.LED_CHANNELS
        self.order = ["GRB"] * P.LED_CHANNELS
        self.brightness = [255] * P.LED_CHANNELS
        self.rgb = [bytearray(0) for _ in range(P.LED_CHANNELS)]
        self.dirty = [True] * P.LED_CHANNELS
        # Bridage du courant (54e complément).
        self.max_ma = P.LED_MAX_MA
        self.on_5v = [1] * P.LED_CHANNELS
        self.raw = [0] * P.LED_CHANNELS          # Σc de l'image reçue
        self.emit = [0] * P.LED_CHANNELS         # Σ((c × (b + 1)) >> 8) : octets émis sans bridage
        self.k_eff = [256] * P.LED_CHANNELS      # facteur appliqué : b + 1, ou moins si bridé
        self.limited = 0                         # trames envoyées réduites (GET led_limited)
        self._sums = array("i", [0, 0])
        self._stale = False

    def _measure(self, ch):
        rgb = self.rgb[ch]
        _sums_into(rgb, len(rgb), self.brightness[ch] + 1, self._sums)
        self.raw[ch] = self._sums[0]
        self.emit[ch] = self._sums[1]
        self._stale = True

    def set_power(self, max_ma, on_5v):
        """Budget (jamais au-dessus de LED_MAX_MA) et canaux comptés sur +5V_LEDT."""
        self.max_ma = max(0, min(max_ma, P.LED_MAX_MA))
        self.on_5v = list(on_5v)
        self._stale = True

    def _refresh(self):
        """Recalcule les facteurs ; un canal dont le facteur change est à renvoyer."""
        if not self._stale:
            return
        self._stale = False
        total = 0
        upper = 0
        n = 0
        for ch in range(P.LED_CHANNELS):
            if self.on_5v[ch]:
                total += self.emit[ch]
                upper += self.raw[ch] * (self.brightness[ch] + 1)
                n += self.count[ch]
        budget = (self.max_ma - n * LED_IDLE_MA) * 255 // LED_MA_PER_COLOR
        if budget < 0:
            budget = 0
        limit = total > budget                   # total ≥ 1, donc upper ≥ 1
        upper = (upper + 255) >> 8
        for ch in range(P.LED_CHANNELS):
            k = self.brightness[ch] + 1
            if limit and self.on_5v[ch]:
                k = k * budget // upper
            if k != self.k_eff[ch]:
                self.k_eff[ch] = k
                self.dirty[ch] = True

    def configure(self, ch, count, order):
        if count != self.count[ch]:
            self.count[ch] = count
            self.rgb[ch] = bytearray(3 * count)
            self.dirty[ch] = True
            self.raw[ch] = 0
            self.emit[ch] = 0
            self._stale = True
        if order != self.order[ch]:
            self.order[ch] = order
            self.dirty[ch] = True

    def set_brightness(self, ch, level):
        if level != self.brightness[ch]:
            self.brightness[ch] = level
            self.dirty[ch] = True
            self._measure(ch)

    def frame(self, ch, rgb):
        """Image complète ; refusée (ARGS) si la longueur ne vaut pas 3 × nombre de LED."""
        if len(rgb) != 3 * self.count[ch]:
            raise P.ProtocolError(P.ARGS)
        self.rgb[ch][:] = rgb
        self.dirty[ch] = True
        self._measure(ch)

    def off(self, ch):
        self.rgb[ch] = bytearray(3 * self.count[ch])
        self.dirty[ch] = True
        self.raw[ch] = 0
        self.emit[ch] = 0
        self._stale = True

    def all_off(self):
        for ch in range(P.LED_CHANNELS):
            self.off(ch)

    def is_black(self):
        for ch in range(P.LED_CHANNELS):
            if any(self.rgb[ch]):
                return False
        return True

    def wire(self, ch):
        """Octets dans l'ordre du fil, luminosité LEDB appliquée une seule fois.

        Même arrondi que ws2811_render() de rpi_ws281x (RaspyDarts 4.6.4) :
        (c × (b + 1)) >> 8, table gamma identité ; b = 255 laisse c inchangé.
        Au-delà du budget de courant, k = b + 1 est réduit (bridage).
        """
        self._refresh()
        order = self.order[ch]
        i0 = "RGB".index(order[0])
        i1 = "RGB".index(order[1])
        i2 = "RGB".index(order[2])
        k = self.k_eff[ch]                   # b + 1 (rpi_ws281x), ou moins si bridé
        rgb = self.rgb[ch]
        out = bytearray(len(rgb))
        _wire_into(rgb, out, len(rgb), k, i0, i1, i2)
        return out

    def update(self):
        """Envoie les canaux modifiés."""
        self._refresh()
        for ch in range(P.LED_CHANNELS):
            if self.dirty[ch]:
                if self.driver is None or self.driver.show(ch, self.wire(ch)) is not False:
                    self.dirty[ch] = False
                    if self.raw[ch] and self.k_eff[ch] <= self.brightness[ch]:
                        self.limited = (self.limited + 1) & COUNTER_MASK


if rp2 is not None:
    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW, out_shiftdir=rp2.PIO.SHIFT_LEFT,
                 autopull=True, pull_thresh=24)
    def ws2812():
        # Un bit = 10 cycles : 1 = haut 7 cycles ; 0 = haut 2 cycles.
        wrap_target()                                    # noqa: F821
        label("bitloop")                                 # noqa: F821
        out(x, 1).side(0)[2]                             # noqa: F821
        jmp(not_x, "do_zero").side(1)[1]                 # noqa: F821
        jmp("bitloop").side(1)[4]                        # noqa: F821
        label("do_zero")                                 # noqa: F821
        nop().side(0)[4]                                 # noqa: F821
        wrap()                                           # noqa: F821


class PioDriver:
    def __init__(self, clock, Pin, pins=board.LED_PINS, max_leds=300):
        self.clock = clock
        self.sms = []
        self.dmas = []
        self.ctrls = []
        self.words = []
        self.sent_at = []                   # début du dernier envoi (µs)
        self.dur = []                       # durée de l'envoi + verrouillage (µs), 0 = libre
        for ch in range(len(pins)):
            pin = Pin(pins[ch], Pin.OUT, value=0)            # niveau bas avant la PIO
            sm = rp2.StateMachine(ch, ws2812, freq=LED_FREQ, sideset_base=pin)
            sm.active(1)
            dma = rp2.DMA()
            self.ctrls.append(dma.pack_ctrl(size=2, inc_read=True, inc_write=False,
                                            treq_sel=DREQ_PIO0_TX0 + ch))
            self.sms.append(sm)
            self.dmas.append(dma)
            self.words.append(array("I", [0] * max_leds))
            self.sent_at.append(0)
            self.dur.append(0)

    def busy(self, ch):
        if self.dmas[ch].active():
            return True
        if self.dur[ch]:
            d = self.clock.ticks_diff(self.clock.ticks_us(), self.sent_at[ch])
            if 0 <= d < self.dur[ch]:
                return True                  # trame en cours ou verrouillage
            self.dur[ch] = 0                 # (un écart négatif = repli de ticks : libre)
        return False

    def show(self, ch, data):
        if self.busy(ch):
            return False
        n = len(data) // 3
        if n == 0:
            return True
        w = self.words[ch]
        _pack_words(data, w, n)
        self.dmas[ch].config(read=w, write=self.sms[ch], count=n, ctrl=self.ctrls[ch], trigger=True)
        self.sent_at[ch] = self.clock.ticks_us()
        self.dur[ch] = n * BIT_US + LATCH_US
        return True
