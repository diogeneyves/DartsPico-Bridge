# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Fichier des paramètres (`SAVE`, § 5.3) : lecture au démarrage, écriture.

Format : un objet JSON, clé -> valeur. Un fichier absent donne les valeurs
par défaut sans erreur. Un fichier illisible, ou une seule clé ou valeur
refusée, donne les valeurs par défaut et le texte d'une erreur `E CFG`.
"""

import json
import os

import proto as P

PARAMS_FILE = "params.json"


def load(path=PARAMS_FILE):
    """Renvoie (paramètres, texte d'erreur ou None)."""
    params = P.default_params()
    try:
        f = open(path)
    except OSError:
        return params, None                          # premier démarrage
    try:
        try:
            data = json.loads(f.read())
        finally:
            f.close()
        if not isinstance(data, dict):
            raise ValueError
        loaded = {}
        for key in data:
            value = data[key]
            if key not in params or isinstance(value, bool):
                raise ValueError
            if not isinstance(value, type(params[key])):
                raise ValueError                     # nombre attendu, texte reçu, ou l'inverse
            loaded[key] = P.parse_param_value(key, P.format_param_value(value))
    except (ValueError, P.ProtocolError):
        return params, "params file invalid, defaults loaded"
    params.update(loaded)
    return params, None


def save(params, path=PARAMS_FILE):
    """Écrit d'abord un fichier temporaire, puis le renomme (pas de fichier à moitié écrit)."""
    tmp = path + ".tmp"
    f = open(tmp, "w")
    try:
        f.write(json.dumps(params))
    finally:
        f.close()
    os.rename(tmp, path)
