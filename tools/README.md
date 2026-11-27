# tools

Outils : règles udev, flash, calibration de la cible, **faux Pico** et **fausse carte 75 W** (simulateurs utilisés par les tests).

## Banc jetable de l'Interstate 75 W

`bench_i75w/` : premier flash du firmware C++ de l'Interstate 75 W et essai de débit USB du § 11.3 (`i75w.sh`, `tx_bench.py` avec `STATS`) ; banc MicroPython du 23 novembre 2026 en annexe (procédure : `bench_i75w/PROCEDURE.md`).

## Simulateurs (jalon M1)

| Fichier | Rôle |
|---|---|
| `fake_pico.py` | Faux Pico : protocole texte du § 5 (réponses, paramètres, sorties, stroboscopes, chien de garde, filtres des contacts) |
| `fake_i75w.py` | Fausse carte Interstate 75 W : protocole binaire du § 6 (trames, images perdues, télémétrie, animation locale) |
| `ptypair.py` | Expose un simulateur sur un pseudo-terminal en mode brut |

Ce ne sont pas des firmwares : ils simulent le **comportement protocolaire** des cartes, pas le matériel.
Les deux s'utilisent en mémoire dans les tests, avec une horloge simulée, ou sur un pseudo-terminal pour des essais à la main :

```bash
python3 tools/fake_pico.py
```

Le chemin du port s'affiche (par exemple `/dev/ttys003`). Sur l'entrée standard :
`d <ligne> <colonne> [durée_µs]` (fléchette), `b <n> <0|1>` (bouton), `s [valeur ADC]` (choc ; seuil et polarité appliqués dans les deux modes, défaut : pleine échelle active), `q` (quitter).

```bash
python3 tools/fake_i75w.py
```

Arrêt par Ctrl-C ou SIGTERM.

**Limites** : un pseudo-terminal ne simule ni les noms `/dev/ttyACM*`, ni les règles udev, ni la ré-énumération USB, ni les débits et latences réels de l'USB.

## Enregistreur des messages du DMD

`dmd_record.py` (spécification § 9.9, 47e complément) : lancé à la main sur le Pi, abonné à
`raspydarts/dmd` et `raspy2dmd` (paho-mqtt 2, installé par le jeu), il écrit le journal du mode
rejeu du service de rendu (`<délai en s> <sujet> <contenu>`, une ligne par message) :

```bash
python3 tools/dmd_record.py -o partie.txt
```

Sans `-o`, sur la sortie standard ; avec `-o`, ajout en fin de fichier. Ctrl-C pour arrêter.

## Médias du DMD

`sync_medias.sh` (spécification § 12, 41e à 43e compléments ; procédure : `docs/PROCEDURE.md`,
« Médias du DMD ») : copie les médias du DMD du Mac vers `~/dmd-medias` (dossier personnel de
l'utilisateur distant) sur le Pi, par rsync et `ssh -4`. `--source` obligatoire, sans défaut
(complément 77). Essai à blanc par défaut, copie réelle seulement avec `--apply`, jamais de
suppression sur le Pi. Lancé depuis le Mac par l'auteur seul :

```bash
tools/sync_medias.sh --source "/chemin/des/medias"
```

## Services hwd et rendu du DMD

`install_services.sh` (spécification § 9.9, 50e complément ; procédure : `docs/PROCEDURE.md`,
§ 2) : installe le code dans `/opt/raspydarts-pico/`, les unités `raspydarts-hwd.service` et
`raspydarts-dmd.service` (sans les activer) et, s'ils manquent, `/etc/raspydarts/hwd.toml` et
`dmd.toml`. Essai à blanc par défaut, sans aucune connexion ; installation par `ssh -4` et `sudo`
seulement avec `--apply`. Options obligatoires (complément 77) : `--user` (utilisateur des unités ;
groupe principal lu sur le Pi), `--pico-serial` (minuscules) et `--i75w-serial` (majuscules), qui
remplissent les marqueurs des unités et de la règle udev. Lancé depuis le Mac par l'auteur seul :

```bash
tools/install_services.sh --user <utilisateur> --pico-serial <SERIE_PICO> --i75w-serial <SERIE_I75W>
```

## GIF de test du DMD

`make_test_gifs.py` (spécification § 9.9, 48e complément ; Pillow) : fabrique dans le dossier
donné, créé s'il manque, six GIF (128 × 32, 117 × 32 et 128 × 30, avec et sans extension
NETSCAPE, trois images de délais 0, 2 et 10 centièmes de seconde) et un PPM P6 de 117 × 32.
Lancé par les tests dans un dossier temporaire ; il refuse d'écrire dans le dépôt :

```bash
python3 tools/make_test_gifs.py /tmp/gifs-dmd
```

## Fork RaspyDarts-Pico-game

`build_fork.sh` (spécification, compléments 78 à 80 ; `docs/UPSTREAM.md`) : construit la branche
`main` du fork dans un dossier inexistant ou vide, hors du dépôt : clone de l'amont (`--upstream`,
défaut `https://github.com/rmdelcelier/RaspyDarts`), `BASE` en tête détachée, puis
`apply_patches.sh` complet (10 patchs et 11e commit des fichiers nouveaux et du bandeau du `README.md`). Ne pousse pas : affiche
la commande de push, que l'auteur lance lui-même. `check_fork.sh FORK RÉFÉRENCE` contrôle qu'un fork
est `BASE` + 11 commits aux arbres, messages et auteurs égaux à ceux d'un fork fraîchement construit
(pas de SHA reproductibles).

```bash
tools/build_fork.sh /chemin/hors/du/depot/fork
tools/check_fork.sh /chemin/du/clone/du/fork /chemin/hors/du/depot/fork
```

## Export du dépôt RaspyDarts-Pico

`export_repo.sh` (spécification, complément 81) : prépare, dans un dossier inexistant ou vide hors
du dépôt (prévu : `~/RaspyDarts-Pico-export`), le commit initial unique de
`https://github.com/diogeneyves/RaspyDarts-Pico` : arbre `HEAD` du projet sans `CLAUDE.md`, workflow
transformé pour la racine, `.gitignore` fusionné, `git init -b main` et un seul commit. Lit seulement
le dépôt source ; ne pousse pas (affiche les commandes). `--run-tests` lance la suite dans l'export.

```bash
tools/export_repo.sh --run-tests ~/RaspyDarts-Pico-export
```
