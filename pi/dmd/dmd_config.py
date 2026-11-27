# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Configuration du service de rendu du DMD (spécification § 9.9, 50e complément).

Fichier TOML, /etc/raspydarts/dmd.toml sur le Pi, lu par l'option --config ;
exemple : pi/dmd/dmd.example.toml. Toutes les clés sont facultatives ; une clé
inconnue ou une valeur hors plage refuse tout le fichier (ValueError). Les options
de la ligne de commande l'emportent sur le fichier (dmd_service.parse_args).
"""

import tomllib
from dataclasses import dataclass, fields

# dmd_media_dir (41e complément) ; ~ développé par dmd_service.parse_args pour l'utilisateur
# qui lance le service (complément 77)
DEFAULT_MEDIA_DIR = "~/dmd-medias"

# Plages des entiers (50e complément) : (minimum, maximum)
RANGES = {
    "dmd_special_moves": (0, 1),            # 1 : coups spéciaux, 0 : coupés (49e complément)
    "attract_delay_s": (1, 3600),           # Attract Mode après ce délai sans message
    "attract_gif_max_s": (5, 600),          # GIF de l'Attract Mode coupé à cette durée (5 s : minimum d'un GIF)
    "attract_clock_every_s": (10, 3600),    # heure toutes les ... secondes, si NTP synchronisé
    "web_port": (0, 65535),                 # DMD virtuel sur 127.0.0.1 ; 0 : coupé
    "hdmi_output": (0, 1),                  # écran HDMI dédié sur HDMI-A-2 ; 0 : coupé (complément 72)
    "mqtt_port": (1, 65535),
}


@dataclass
class DmdConfig:
    dmd_media_dir: str = DEFAULT_MEDIA_DIR
    dmd_special_moves: int = 1
    attract_delay_s: int = 15
    attract_gif_max_s: int = 60
    attract_clock_every_s: int = 120
    web_port: int = 8081
    hdmi_output: int = 0
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883

    def validate(self):
        """ValueError si une valeur a un mauvais type ou est hors plage."""
        for name in ("dmd_media_dir", "mqtt_host"):
            if not isinstance(getattr(self, name), str):
                raise ValueError("%s : texte attendu" % name)
        if not self.mqtt_host:
            raise ValueError("mqtt_host vide")
        for name, (low, high) in RANGES.items():
            value = getattr(self, name)
            # bool est un int en Python : true/false refusés, sauf pour dmd_special_moves
            if isinstance(value, bool) and name != "dmd_special_moves":
                raise ValueError("%s : entier attendu" % name)
            if not isinstance(value, int):
                raise ValueError("%s : entier attendu" % name)
            if not low <= value <= high:
                raise ValueError("%s hors plage (%d à %d)" % (name, low, high))
        return self


KEYS = tuple(f.name for f in fields(DmdConfig))


def load(path):
    """Lit un fichier TOML ; les clés absentes gardent leur valeur par défaut. OSError si le
    fichier ne s'ouvre pas, ValueError s'il est invalide (TOML, clé inconnue, valeur)."""
    with open(path, "rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as error:
            raise ValueError("TOML invalide dans %s : %s" % (path, error)) from None
    unknown = set(data) - set(KEYS)
    if unknown:
        raise ValueError("clés inconnues dans %s : %s" % (path, ", ".join(sorted(unknown))))
    return DmdConfig(**data).validate()
