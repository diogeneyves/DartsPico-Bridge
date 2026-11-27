# pi/raspydarts_layer

Nouveaux fichiers destinés à RaspyDarts, pour le mode « carte Pico » (jalon M2, phase 2b).
Ce sont des fichiers **nouveaux** : les modifications des fichiers existants de RaspyDarts viendront sous forme de patchs dans `pi/patches/` (phase suivante, voir `docs/PI_INTERFACE.md` § 6). Licence : GPL v3.

| Fichier | Rôle | Destination dans RaspyDarts |
|---|---|---|
| `include/cpico.py` | `PicoLink` (client MQTT vers `hwd`, durée de maintien 5 s, testament `OFF`) ; `PicoExtender` (même interface que `Gpio_extender`) ; `PicoTargetSource` (remplace `ctarget_reader.py` dans `Craspberry.target_reader`) | `include/cpico.py` |
| `shims/rpi_ws281x.py` | Remplaçant de la bibliothèque `rpi_ws281x` : `show()` publie l'image vers `hwd` | Dossier à part (voir ci-dessous) |

## Installation (à faire par le futur script d'installation)

- Copier dans `include/` de RaspyDarts, **à côté de `cpico.py`** :
  - `pi/hwd/hwbus.py` (protocole entre le jeu et `hwd`) ;
  - `shared/pico_proto.py` (protocole du Pico).

  Sans eux, `cpico` ne s'importe pas. Le test `SharedAndInstall.test_import_when_installed_in_include` vérifie cette disposition.
- Dépendance : `paho-mqtt>=2`, déjà demandée par RaspyDarts (`requirements.txt:29`).

## `shims/` : seulement pour les serveurs LED, jamais pour le jeu

- Le dossier `shims/` est placé **en tête du chemin Python des serveurs LED seulement** (`addons/TargetLeds_Server.py`, `addons/StripLeds_Server.py`), par exemple avec `PYTHONPATH=…/shims`. Ainsi, `import rpi_ws281x` dans `addons/CStrip.py` charge le remplaçant, et `CStrip.py` reste inchangé.
- **Le jeu (`raspydarts.py`) ne doit jamais avoir `shims/` dans son chemin.** Il n'utilise pas `rpi_ws281x`, et `cpico` ne l'importe pas. Le test `test_shim_import.py` vérifie ces deux points.
- Chaque serveur LED reçoit son canal par la variable d'environnement **`DARTSPICO_BRIDGE_LED_CHANNEL`** : `0` pour la cible, `1` pour la bande, `2` pour la sortie de secours. Sans cette variable, l'argument `pin` est pris comme canal s'il vaut 0, 1 ou 2 ; sinon, l'ouverture est refusée.
- Variables facultatives : `RASPYDARTS_MQTT_HOST` (défaut `localhost`) et `RASPYDARTS_TOPIC_BASE` (défaut `raspydarts`).
- La luminosité (`setBrightness`) n'est plus appliquée par le remplaçant (spécification v0.3.14, § 5.3) : il l'envoie par `LEDB <canal> <valeur>` sur `raspydarts/hw/cmd` (à `begin()` et à chaque changement) ; `hwd` la plafonne (`led_brightness`) et le Pico l'applique une seule fois, avec l'arrondi de `rpi_ws281x` : `(c × (b + 1)) >> 8`. L'ordre des couleurs (`strip_type`) est transmis à `hwd` par `SET led<n>_order` lors de `begin()`. Les images partent toujours en RVB.

## Points à connaître

- **Monnayeur** : aucun en mode Pico (D4). `PicoExtender.chips` est vide, donc `ccoins.start_mcp` ne trouve aucune puce. Le patch du jeu devra en plus désactiver `include/ccoins.py` en mode Pico.
- **`read_entries`** reproduit exactement la logique de `Gpio_extender.read_entries` de RaspyDarts 4.6.4 (un appui = une action, rebonds de moins de 50 ms ignorés, seules les flèches maintenues se répètent, bannissement d'un bouton maintenu plus de 10 s ; en mode capture, Valider rendu au relâchement, ou `PIN_CAPTURE_END` s'il est maintenu 2 s) : c'est vérifié par un test différentiel contre le vrai code de RaspyDarts, sur une horloge simulée. Il ajoute un **verrou** : un appui plus court qu'un tour de boucle du jeu est rendu au moins une fois (R3).
- **`blink_toy`** (LED des boutons, 4.6.4) : mêmes états et mêmes attentes que l'original (test différentiel), envoyés au Pico (`LGT`/`TOY`) depuis un fil ; l'état de départ est celui demandé par `light_toys`, oublié par `all_off`.
- **`PIN_MISSDART`** prend la valeur `MISS` : c'est `hwd` qui décide qu'une fléchette est manquée (D3). Le jeu reçoit ce `MISS` une fois, comme un appui.
- **`strobe_toys`** renvoie des fils témoins de la même durée que les anciens fils, pour que les `join()` du jeu attendent comme avant. `hwd` remet l'état d'avant à la fin (D9).
