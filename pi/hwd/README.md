# pi/hwd

Démon matériel `hwd` : seul propriétaire des ports du Pico (`/dev/cible`) et du DMD (`/dev/dmd`), relais MQTT local vers le jeu et les serveurs LED (spécification § 9.5, détail dans `docs/PI_INTERFACE.md`).
Jalon **M2, phase 2a** ; port de l'Interstate 75 W (`/dev/dmd`) ajouté au jalon **M4-d** (spécification § 9.5, 32e complément).

| Fichier | Rôle |
|---|---|
| `hwbus.py` | Protocole jeu ↔ `hwd` : sujets, codes `R<ligne>C<colonne>`, lignes d'événements, commandes autorisées |
| `hwd_config.py` | Configuration (TOML), validée par `shared/pico_proto.py` |
| `hwd_core.py` | Toute la logique, sans entrée-sortie : présentation, remise en état, `MISS`, LED, stroboscopes, accusés de réception |
| `hwd_dmd.py` | DMD, sans entrée-sortie : présentation (`HELLO_REQ`, `MODE 0`), dernière image du rendu, 15 images/s au plus, tuiles (`DELTA`), `FRAME` périodique, arrêt après `dmd_stale_ms` |
| `hwd_daemon.py` | Ports série du Pico et du DMD (mode brut, exclusifs), client MQTT (paho-mqtt 2, durée de maintien 5 s, testament), boucle principale |
| `hwd.example.toml` | Exemple de configuration, avec les valeurs par défaut |

Lancement (sur le Pi, ou sur un PC avec le faux Pico) :

```bash
python3 pi/hwd/hwd_daemon.py --config pi/hwd/hwd.example.toml --pico /dev/ttys003 --dmd /dev/ttys004
```

Dépendances : Python 3.11 ou plus récent (`tomllib`), `paho-mqtt>=2` et un broker MQTT local. Les tests n'ont besoin ni de paho ni de broker.
