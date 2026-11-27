# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Faux include.cplatform (RaspyDarts 4.6.5) pour les bancs d'essai.

Le vrai module lit /proc/device-tree/model et /dev/gpiochip0 : sur la machine de test
(macOS, CI Linux), il dirait « PC » et sans GPIO. Par défaut, le faux décrit un Pi 4 avec
ses GPIO, la machine visée ; gpio=False ou un modèle sans « Raspberry Pi » imite un PC.
"""

import types

PI4 = "Raspberry Pi 4 Model B Rev 1.5"


def make(model=PI4, gpio=True):
    module = types.ModuleType("include.cplatform")
    raspberry = "Raspberry Pi" in model
    module.MODEL_FILE = "/proc/device-tree/model"
    module.model = lambda: model if raspberry else ""
    module.is_raspberry = lambda: raspberry
    module.has_gpio = lambda: raspberry and gpio
    module.system_name = lambda: "Raspbian GNU/Linux 13 (trixie)" if raspberry else "Ubuntu 24.04.1 LTS"
    module.system_arch = lambda: "armhf" if raspberry else "amd64"
    module.description = lambda: model if raspberry else "PC x86_64 (Ubuntu 24.04.1 LTS)"
    return module


def install(package, modules, model=PI4, gpio=True):
    """Place le faux dans le paquet include donné et dans le dictionnaire modules
    (sys.modules en général). Renvoie le faux."""
    module = make(model, gpio)
    modules["include.cplatform"] = module
    package.cplatform = module
    return module
