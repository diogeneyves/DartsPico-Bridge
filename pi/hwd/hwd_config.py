# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Configuration de hwd (fichier TOML, D8 de docs/PI_INTERFACE.md)."""

import tomllib
from dataclasses import dataclass, field

import pico_proto as P

# Nombre de LED par canal (spécification § 3.5) : 0 = LED intégrées à la cible (J31),
# 1 et 2 = rubans accessoires (J32, J33), à déclarer dans [pico_params] s'ils sont branchés.
DEFAULT_LED_COUNTS = {"led0_count": 204, "led1_count": 0, "led2_count": 0}


@dataclass
class HwdConfig:
    pico_device: str = "/dev/cible"
    dmd_device: str = "/dev/dmd"          # Interstate 75 W ; vide : DMD désactivé (32e complément)
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    topic_base: str = "raspydarts"
    keepalive_s: int = 5                  # R6 : testament publié en moins de 8 s
    miss_window_ms: int = 50              # D3 : fenêtre symétrique choc / fléchette
    miss_grace_ms: int = 20               # marge de transport avant de publier MISS
    led_max_fps: int = 30                 # R5 ; 30 : capacité mesurée du Pico (M3b-3, § 9.5)
    ack_timeout_ms: int = 200
    handshake_timeout_ms: int = 1000
    handshake_flush_ms: int = 50          # § 9.5 : lignes ignorées après « \n », avant WHO
    pico_silence_ms: int = 3000
    reconnect_ms: int = 1000
    # DMD (§ 6.4, § 9.5, 30e et 32e compléments).
    dmd_full_period_ms: int = 1000        # FRAME périodique
    dmd_stale_ms: int = 2000              # sans image du rendu : plus d'émission
    dmd_silence_ms: int = 3000            # sans trame de la carte : nouveau HELLO_REQ
    # Paramètres envoyés au Pico par SET à chaque connexion (jamais SAVE).
    pico_params: dict = field(default_factory=dict)
    # Plafond de LEDB par canal (puissance, jamais un multiplicateur) : canal 0 à 128
    # (≈ 6 A max pour 204 LED en blanc, § 3.8) ; hwd envoie min(valeur du jeu, plafond).
    led_brightness: list = field(default_factory=lambda: [128, 255, 255])

    def params(self):
        """Paramètres complets du Pico : défauts du § 5.3 et du § 3.5, puis ceux du fichier."""
        d = P.default_params()
        d.update(DEFAULT_LED_COUNTS)
        d.update(self.pico_params)
        for key in P.BENCH_PARAMS:              # réservés au banc : jamais envoyés (§ 5.3)
            d.pop(key, None)
        return d

    def validate(self):
        """ValueError si une valeur est hors plage (vérifiée par pico_proto)."""
        for key, value in self.pico_params.items():
            if key in P.BENCH_PARAMS:
                raise ValueError("paramètre %s réservé au banc : hwd ne l'envoie jamais (§ 5.3)" % key)
            try:
                P.parse_param_value(key, P.format_param_value(value))
            except P.ProtocolError as e:
                raise ValueError("paramètre %s = %r refusé (%s)" % (key, value, e.code)) from None
        if not 1 <= self.miss_window_ms <= 1000:
            raise ValueError("miss_window_ms hors plage (1 à 1000)")
        if not 1 <= self.led_max_fps <= 200:
            raise ValueError("led_max_fps hors plage (1 à 200)")
        if not 1 <= self.handshake_flush_ms <= 1000:
            raise ValueError("handshake_flush_ms hors plage (1 à 1000)")
        if not 200 <= self.dmd_full_period_ms <= 2500:
            raise ValueError("dmd_full_period_ms hors plage (200 à 2500)")
        if not 1500 <= self.dmd_stale_ms <= 10000:
            raise ValueError("dmd_stale_ms hors plage (1500 à 10000)")
        if not 1500 <= self.dmd_silence_ms <= 10000:
            raise ValueError("dmd_silence_ms hors plage (1500 à 10000)")
        if not 1 <= self.keepalive_s <= 60:
            raise ValueError("keepalive_s hors plage (1 à 60)")
        if len(self.led_brightness) != P.LED_CHANNELS or not all(
                0 <= b <= 255 for b in self.led_brightness):
            raise ValueError("led_brightness : 3 valeurs de 0 à 255")
        return self


def load(path):
    """Lit un fichier TOML ; les clés absentes gardent leur valeur par défaut."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    known = set(HwdConfig.__dataclass_fields__)
    unknown = set(data) - known
    if unknown:
        raise ValueError("clés inconnues dans %s : %s" % (path, ", ".join(sorted(unknown))))
    return HwdConfig(**data).validate()
