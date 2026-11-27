# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Remplaçant de rpi_ws281x pour la carte Pico (docs/PI_INTERFACE.md § 5.4).

Mêmes noms que ceux qu'utilise addons/CStrip.py : PixelStrip, Color,
WS2811_STRIP_RGB, WS2811_STRIP_GRB. show() publie l'image RVB **sans**
luminosité sur raspydarts/hw/led/<canal> ; hwd l'envoie au Pico (LEDF).
La luminosité du jeu part à part, par `LEDB <canal> <valeur>` sur
raspydarts/hw/cmd (à begin() et à chaque setBrightness()) ; hwd la plafonne
et le Pico l'applique une seule fois (spécification § 3.8, § 5.3, v0.3.14).
Canal : variable DARTSPICO_BRIDGE_LED_CHANNEL (0, 1 ou 2), sinon pin si 0 à 2.
"""

import os

WS2811_STRIP_RGB = 0x00100800
WS2811_STRIP_RBG = 0x00100008
WS2811_STRIP_GRB = 0x00081000
WS2811_STRIP_GBR = 0x00080010
WS2811_STRIP_BRG = 0x00001008
WS2811_STRIP_BGR = 0x00000810
_ORDERS = {WS2811_STRIP_RGB: "RGB", WS2811_STRIP_RBG: "RBG", WS2811_STRIP_GRB: "GRB",
           WS2811_STRIP_GBR: "GBR", WS2811_STRIP_BRG: "BRG", WS2811_STRIP_BGR: "BGR"}
MAX_LEDS = 300

_transport = None


def set_transport(fn):
    """fn(sujet, contenu) ; pour les tests, ou pour un autre bus."""
    global _transport
    _transport = fn


def _publish(topic, payload):
    global _transport
    if _transport is None:
        import paho.mqtt.client as mqtt
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                             client_id="raspydarts-leds-%d" % os.getpid())
        client.connect_async(os.environ.get("RASPYDARTS_MQTT_HOST", "localhost"), 1883,
                             keepalive=5)
        client.loop_start()
        _transport = lambda t, p: client.publish(t, p, qos=0)  # noqa: E731
    _transport(topic, payload)


def Color(red, green, blue, white=0):
    return (white << 24) | (red << 16) | (green << 8) | blue


class PixelStrip:
    def __init__(self, num, pin, freq_hz=800000, dma=10, invert=False, brightness=255,
                 channel=0, strip_type=None, gamma=None):
        env = os.environ.get("DARTSPICO_BRIDGE_LED_CHANNEL")
        led_channel = int(env) if env is not None else int(pin)
        if led_channel not in (0, 1, 2):
            raise ValueError("canal LED inconnu : définir DARTSPICO_BRIDGE_LED_CHANNEL (0, 1 ou 2)")
        if not 0 <= num <= MAX_LEDS:
            raise ValueError("%d LED : 300 au plus par canal" % num)
        self.led_channel = led_channel
        self._pixels = [0] * num
        self._brightness = int(brightness) & 0xFF
        self._order = _ORDERS.get(strip_type, "GRB")
        base = os.environ.get("RASPYDARTS_TOPIC_BASE", "raspydarts")
        self._led_topic = "%s/hw/led/%d" % (base, led_channel)
        self._cmd_topic = "%s/hw/cmd" % base

    def begin(self):
        _publish(self._cmd_topic, "SET led%d_order %s" % (self.led_channel, self._order))
        self._send_brightness()

    def _send_brightness(self):
        _publish(self._cmd_topic, "LEDB %d %d" % (self.led_channel, self._brightness))

    def numPixels(self):
        return len(self._pixels)

    def setPixelColor(self, n, color):
        if 0 <= n < len(self._pixels):
            self._pixels[n] = int(color) & 0xFFFFFFFF

    def getPixelColor(self, n):
        return self._pixels[n]

    def setBrightness(self, brightness):
        self._brightness = int(brightness) & 0xFF
        self._send_brightness()

    def getBrightness(self):
        return self._brightness

    def frame(self):
        """Image RVB envoyée, sans luminosité (appliquée par le Pico, LEDB)."""
        out = bytearray()
        for c in self._pixels:
            out += bytes(((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF))
        return bytes(out)

    def show(self):
        _publish(self._led_topic, self.frame())


Adafruit_NeoPixel = PixelStrip
