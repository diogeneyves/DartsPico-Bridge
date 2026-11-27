# Mise en service du Pi

Procédure de mise en service du Raspberry Pi 4, à suivre par l'auteur. Le banc de l'Interstate 75 W
a sa propre procédure : `tools/bench_i75w/PROCEDURE.md`.

## 1. Médias du DMD

Copie des médias du DMD du Mac vers `~/dmd-medias` sur le Pi, dans le dossier personnel de
l'utilisateur distant (spécification § 9.3 et § 12, 41e à 43e et 77e compléments). Le service de
rendu les lira par sa clé `dmd_media_dir` (défaut `~/dmd-medias`, développé pour l'utilisateur du
service) : la copie doit se faire avec le même utilisateur que celui des unités (`--user`, § 2).

- **Copiés** : `Gifs`, `SpecialsMoves`, `Scores`, `Fonts`, `Raspy2DMD`.
- **Exclus** : fichiers et dossiers cachés (`.config_backups/`, `.cache/`, `.DS_Store`…), `.cfg`,
  `.log`, `.json`, fichiers datés, dossiers vides, et les dossiers `Images`, `Sounds`, `Patterns`, `Meteo`,
  `EDFJoursTempo`, `PerfVisualizer`, `HDMI`.
- **Jamais dans `/Raspy2DMD`** : le jeu réagit à ce dossier (§ 6.6).
- **Rien n'est effacé** sur le Pi (pas de `--delete`) ; `~/.raspydarts` et les thèmes du jeu ne sont
  pas touchés.
- **Droits sur le Pi** : dossiers 755, fichiers 644 (la source du Mac est en 777). Après une copie
  réelle, le script les fixe lui-même par `ssh -4`, seulement sous `dmd-medias`, `~/dmd-medias` ou
  `/home/<utilisateur>/dmd-medias` quand `--dest` nomme l'utilisateur.
- Les médias ne vont **jamais** dans le dépôt (`.gitignore` : `Medias*/`, `dmd-medias/`,
  `.config_backups/`).

Toutes les commandes se lancent **sur le Mac**, depuis le dossier `RaspyDarts-Pico` du dépôt.

### 1.1 Vérifier rsync sur le Pi

Le Pi doit avoir rsync. Version relevée le 24 novembre 2026 : `rsync  version 3.5.0  protocol
version 32` (43e complément). Sur le Mac, rsync est openrsync (protocole 29,
« rsync version 2.6.9 compatible »), rien à installer.

```bash
ssh -4 pi@raspydarts.local 'rsync --version | head -1'
```

Si la commande répond « command not found » : `sudo apt install rsync` sur le Pi.

### 1.2 Essai à blanc

Rien n'est copié. Le script affiche la source, la destination, le nombre et la taille des fichiers du
périmètre (calculés sur le Mac), puis la liste des fichiers à transférer. `--source` est obligatoire
(dossier des médias sur le Mac, sans défaut) ; la destination par défaut est
`pi@raspydarts.local:dmd-medias/`.

```bash
tools/sync_medias.sh --source "/chemin/des/medias"
```

Lire le résumé : ligne `Périmètre` (sur le Mac de l'auteur, le 24 novembre 2026 : 738 fichiers,
163 340 029 octets, soit 155,8 Mio) et liste des fichiers. Aucun fichier caché, `.cfg`, `.log`, `.json` ni dossier
exclu ne doit y figurer.

Autre utilisateur ou autre hôte :

```bash
tools/sync_medias.sh --source "/chemin/des/medias" --dest <utilisateur>@raspydarts.local:dmd-medias/
```

### 1.3 Copie réelle

```bash
tools/sync_medias.sh --source "/chemin/des/medias" --apply
```

Le script fixe ensuite les droits sur le Pi (ligne `Droits fixés sur … : dossiers 755, fichiers 644`).
La commande peut être relancée : seuls les fichiers absents ou modifiés sont transférés.

### 1.4 Contrôle sur le Pi

Nombre de fichiers : doit être égal au nombre de la ligne `Périmètre` (plus les fichiers déjà
présents sur le Pi, qui ne sont jamais effacés).

```bash
ssh -4 pi@raspydarts.local 'find ~/dmd-medias -type f | wc -l'
```

Taille occupée : proche de celle de la ligne `Périmètre` (`du` compte des blocs, l'écart de
quelques pour cent est normal).

```bash
ssh -4 pi@raspydarts.local 'du -sh ~/dmd-medias'
```

Droits : aucun fichier ni dossier modifiable par tous (résultat attendu : `0`), puis un exemple de
dossier (`drwxr-xr-x`, 755) et de fichiers (`-rw-r--r--`, 644).

```bash
ssh -4 pi@raspydarts.local 'find ~/dmd-medias -perm -o+w | wc -l'
```

```bash
ssh -4 pi@raspydarts.local 'ls -ld ~/dmd-medias/Gifs; ls -l ~/dmd-medias/Fonts | head -3'
```

Nouvel essai à blanc : la liste des fichiers à transférer doit être vide.

```bash
tools/sync_medias.sh --source "/chemin/des/medias"
```

## 2. Services hwd et rendu du DMD

Installation de `hwd` et du service de rendu du DMD sur le Pi (spécification § 9.5 et § 9.9,
50e complément). Les deux unités systemd, `raspydarts-hwd.service` et `raspydarts-dmd.service`,
sont **installées sans être activées** : l'activation se fait à l'essai réel.

- **Code** : `/opt/raspydarts-pico/` (structure du dépôt : `pi/hwd`, `pi/dmd`, `shared`), à root,
  dossiers 755, fichiers 644 ; révision installée dans `/opt/raspydarts-pico/REVISION`. Rien n'y
  est effacé.
- **Configurations** : `/etc/raspydarts/hwd.toml` et `/etc/raspydarts/dmd.toml`, écrites depuis
  `pi/hwd/hwd.example.toml` et `pi/dmd/dmd.example.toml` **seulement si elles manquent** (jamais
  écrasées). Une clé inconnue refuse le fichier : le service s'arrête avec un message.
- **Dépendances** (installées par le jeu, `scripts/install_on_pi.sh` de RaspyDarts 4.6.5) : le
  `python3` du système, Pillow (`python3-pil` 11.1.0) et paho-mqtt 2 (pip) ; Mosquitto en local
  (`sudo apt install mosquitto mosquitto-clients`, spécification § 9.4). `shared/dmd_proto.py` et
  `shared/pico_proto.py` sont copiés avec le code : les deux services les trouvent par `../../shared`.
- **Médias** : `~/dmd-medias` de l'utilisateur des unités (§ 1), clé `dmd_media_dir`.
- **Utilisateur des unités** : option `--user`, obligatoire et sans défaut ; `User=@RASPYDARTS_USER@`
  et `Group=@RASPYDARTS_GROUP@` sont remplis sur le Pi, avant toute écriture, par
  `pi/systemd/fill_units.sh` (groupe : le groupe principal de l'utilisateur ; utilisateur inconnu ou
  marqueur restant : rien n'est installé). Pour `hwd`, l'utilisateur doit pouvoir ouvrir les ports
  série (`SupplementaryGroups=dialout`).
- **Règle udev** : `pi/udev/73-raspydarts-pico.rules`, marqueurs `<SERIE_PICO>` et `<SERIE_I75W>`
  remplis par `--pico-serial` et `--i75w-serial` (obligatoires, § 2.6), copiée dans
  `/etc/udev/rules.d/73-raspydarts-pico.rules`, **écrasée à chaque `--apply`**, puis
  `udevadm control --reload`. Elle crée `/dev/cible` (Pico 2) et `/dev/dmd` (Interstate 75 W) ;
  le `trigger` se fait à la main (§ 2.6).

> **Ne jamais lancer un `hwd` à la main (`start_hwd.sh`, par exemple) en même temps que l'unité
> `raspydarts-hwd.service`** : un seul programme peut tenir les ports `/dev/cible` et `/dev/dmd`.
> Avant de démarrer l'unité, arrêter le `hwd` lancé à la main (SIGTERM suffit pour `hwd`) ; avant de
> le relancer, arrêter l'unité (`sudo systemctl stop raspydarts-hwd`).

Toutes les commandes du § 2.1 et du § 2.2 se lancent **sur le Mac**, depuis le dossier
`RaspyDarts-Pico` du dépôt.

### 2.1 Essai à blanc

Rien n'est envoyé au Pi, aucune connexion : le script affiche l'hôte, la révision, les fichiers du
code et ce qui serait écrit, avec les marqueurs tels quels et ce qui les remplacera. Les trois
options sont obligatoires (numéros de série : § 2.6) :

```bash
tools/install_services.sh --user <utilisateur> --pico-serial <SERIE_PICO> --i75w-serial <SERIE_I75W>
```

### 2.2 Installation

À lancer par l'auteur seul. Le script envoie une archive par `ssh -4`, puis installe par
`ssh -4 -t` et `sudo` (mot de passe du Pi demandé si `sudo` en exige un) ; il finit par `systemctl daemon-reload`, sans
`enable` ni `start`. Il avertit si un `hwd` tourne déjà.

```bash
tools/install_services.sh --user <utilisateur> --pico-serial <SERIE_PICO> --i75w-serial <SERIE_I75W> --apply
```

### 2.3 Contrôle sur le Pi

```bash
ssh -4 pi@raspydarts.local 'cat /opt/raspydarts-pico/REVISION; systemctl is-enabled raspydarts-hwd raspydarts-dmd'
```

Résultat attendu : la révision, puis `disabled` deux fois.

```bash
ssh -4 pi@raspydarts.local 'ls -l /etc/raspydarts/'
```

Aucun `hwd` ne doit tourner avant le démarrage de l'unité (résultat attendu : rien) :

```bash
ssh -4 pi@raspydarts.local 'pgrep -af hwd_daemon.py'
```

### 2.4 Démarrage à la main (essai réel, avec l'auteur devant la carte)

```bash
ssh -4 pi@raspydarts.local 'sudo systemctl start raspydarts-hwd raspydarts-dmd'
```

Le service de rendu décode d'abord `Scores` et `SpecialsMoves` (environ 8 s) : pendant ce temps,
l'Interstate 75 W affiche son animation de secours, puis l'écran d'accueil « RASPYDARTS » paraît.
Journaux (démarrage, décodage et sa durée, état NTP, arrêt et sa cause) :

```bash
ssh -4 pi@raspydarts.local 'journalctl -u raspydarts-dmd -u raspydarts-hwd -n 50 --no-pager'
```

DMD virtuel : tunnel, puis `http://127.0.0.1:8081/` dans le navigateur du Mac.

```bash
ssh -4 -L 8081:127.0.0.1:8081 pi@raspydarts.local
```

Arrêt (journalisé avec sa cause, SIGTERM) :

```bash
ssh -4 pi@raspydarts.local 'sudo systemctl stop raspydarts-dmd raspydarts-hwd'
```

### 2.5 Activation au démarrage du Pi

Seulement après un essai réel concluant, et après avoir cessé de lancer `hwd` à la main :

```bash
ssh -4 pi@raspydarts.local 'sudo systemctl enable raspydarts-hwd raspydarts-dmd'
```

### 2.6 Règles udev : `/dev/cible` et `/dev/dmd`

Spécification § 2, complément 62. Une ligne par carte, par VID:PID et numéro de série USB
(`ATTRS{serial}`, **casse exacte**), avec `ENV{ID_MM_DEVICE_IGNORE}="1"` :

| Lien | Carte | VID:PID | `ATTRS{serial}` |
|---|---|---|---|
| `/dev/cible` | Pico 2 sous MicroPython | `2e8a:0005` | `<SERIE_PICO>` (16 chiffres hexadécimaux, minuscules) |
| `/dev/dmd` | Interstate 75 W, firmware C++ | `2e8a:0009` | `<SERIE_I75W>` (16 chiffres hexadécimaux, majuscules) |

Les marqueurs sont remplis par `tools/install_services.sh` (`--pico-serial`, `--i75w-serial`,
complément 77), qui refuse une série de mauvaise casse ou de mauvais format, et refuse aussi
(code 2) un marqueur absent de la règle ou restant après remplacement.

`tools/install_services.sh --apply` installe la règle et recharge udev, mais ne l'applique pas aux
cartes déjà branchées. **Avec l'auteur devant les cartes**, après l'installation (ou rebrancher les
deux cartes) :

```bash
ssh -4 -t pi@raspydarts.local 'sudo udevadm trigger --subsystem-match=tty --action=add'
```

Contrôle (attendu : `/dev/cible -> ttyACM…` et `/dev/dmd -> ttyACM…`) :

```bash
ssh -4 pi@raspydarts.local 'ls -l /dev/cible /dev/dmd /dev/serial/by-id/'
```

Dès que `/dev/dmd` existe, `hwd` ouvre la 75 W : **arrêter `hwd` pendant le banc de la 75 W**
(`tools/bench_i75w/PROCEDURE.md`).

**Première installation, carte ou firmware changé** (le lien n'apparaît plus et `hwd` reste
`absent`) : la casse du numéro de série dépend du firmware (minuscules sous MicroPython, majuscules
sous le firmware C++ de la 75 W). Relever `ATTRS{serial}` de chaque carte (remplacer `ttyACM0` par
le port de la carte, lu dans `/dev/serial/by-id/`) :

```bash
ssh -4 pi@raspydarts.local 'udevadm info -a -n /dev/ttyACM0 | grep -m3 -E "ATTRS\{(idVendor|idProduct|serial)\}"'
```

Puis relancer `tools/install_services.sh` avec ces valeurs, en recopiant la casse relevée
(`--pico-serial` pour le Pico 2, `--i75w-serial` pour la 75 W), d'abord à blanc, puis avec
`--apply`, et enfin le `trigger` ci-dessus. La règle du dépôt n'est jamais modifiée : elle garde ses
marqueurs. Le banc de la 75 W reçoit les mêmes séries par l'environnement (`PICO_SERIAL`,
`I75W_SERIAL`, `tools/bench_i75w/PROCEDURE.md`).

## 3. Écran HDMI dédié du DMD (à la place de la 75 W, ou avec elle)

Spécification § 9.9, « Écran HDMI dédié » (complément 72). Le service de rendu affiche ses images
en plein écran sur un écran branché au **2e port micro-HDMI** du Pi 4 (sortie **`HDMI-A-2`**, le
port le plus loin de l'alimentation USB-C). Le jeu reste sur le 1er port (`HDMI-A-1`) et n'est pas
modifié ; `hwd` non plus : avec une 75 W branchée, les deux affichent la même image. **Coupé par
défaut** (`hdmi_output = 0`). **Essai réel sur place : à faire**, rien n'est encore vérifié sur le
Pi (voir § 3.4).

### 3.1 Disposition des écrans (une fois, sur le Pi, avec un clavier et une souris)

Le jeu ouvre sa fenêtre en 0,0 à la taille du **premier** écran : la disposition doit être fixe.

1. Brancher l'écran du jeu sur le 1er port micro-HDMI et l'écran du DMD sur le 2e.
2. Ouvrir le **Centre de contrôle** (« Control Centre », menu Préférences, ou commande `rpcc`
   dans un terminal du bureau), onglet **« Screens »** (relevé du 26/11/2026 : `rpcc` 0.18,
   module `raindrop` 1.24).
3. Placer **`HDMI-A-1` à gauche, en haut (position 0,0)** et **`HDMI-A-2` à sa droite**, bords
   hauts alignés ; laisser les deux écrans actifs ; appliquer et enregistrer.

Contrôle depuis le Mac (attendu : `HDMI-A-1` en `Position: 0,0`, `HDMI-A-2` à droite, par exemple
`Position: 1920,0`) :

```bash
ssh -4 pi@raspydarts.local 'XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 wlr-randr | grep -E "^[A-Z]|Position|Enabled"'
```

Branchement vu par le noyau (attendu : `connected` pour les deux) :

```bash
ssh -4 pi@raspydarts.local 'cat /sys/class/drm/card1-HDMI-A-1/status /sys/class/drm/card1-HDMI-A-2/status'
```

### 3.2 Activer l'écran

Dans `/etc/raspydarts/dmd.toml` (fichier jamais écrasé par l'installation), mettre
`hdmi_output = 1` (`0` ou `1` seulement ; pas d'option en ligne de commande), puis relancer le
service :

```bash
ssh -4 -t pi@raspydarts.local 'sudo nano /etc/raspydarts/dmd.toml'
```

```bash
ssh -4 pi@raspydarts.local 'sudo systemctl restart raspydarts-dmd'
```

Journal (attendu : `HDMI screen output on (HDMI-A-2)`, puis `HDMI-A-2 connected` et
`full screen on HDMI-A-2`) :

```bash
ssh -4 pi@raspydarts.local 'journalctl -u raspydarts-dmd -n 30 --no-pager'
```

Pour couper : `hdmi_output = 0`, puis relancer le service.

### 3.3 Comportement

- Branchement relu **toutes les 5 s** : l'image paraît au plus 5 s après le branchement de l'écran
  et disparaît au débranchement ; sans 2e écran, rien n'est ouvert.
- L'image ne s'ouvre **que** sur l'écran que SDL nomme `HDMI-A-2` ; si ce nom n'est pas trouvé,
  rien n'est ouvert et le journal le dit une fois (`HDMI-A-2 not found among the screens …`,
  avec la liste des noms vus). **Jamais rien sur l'écran du jeu.**
- Pixels carrés nets, plus grande échelle entière (×15 en 1920 × 1080 : bande de 1920 × 480),
  centrée sur fond noir.
- Jamais en rejeu (`--replay`) : la clé est ignorée.

### 3.4 Essai réel sur place (à faire, avec l'auteur)

- Image sur l'écran du DMD, et nom de l'écran vu par SDL (journal) ; débranchement et
  rebranchement à chaud.
- Vidéos du jeu : `cvlc` les ouvre en plein écran sans choisir l'écran (limite acceptée) ;
  vérifier sur lequel elles paraissent.
- Clavier du jeu : vérifier qu'il garde la main quand l'écran du DMD s'ouvre.
- Charge du processeur, en partie, **avec et sans** l'écran (par exemple `top -b -n 5 -d 2`) ;
  seuil : **+5 % d'un cœur** au plus.
