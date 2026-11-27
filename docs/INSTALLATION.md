# Installation de DartsPico-Bridge

> **Brouillon : installation pas encore validée de bout en bout.**

Ce guide part d'un Raspberry Pi 4 neuf et va jusqu'au jeu RaspyDarts qui tourne avec la **carte Pico**
(Raspberry Pi Pico 2 sur le PCB « Darts ») et l'**Interstate 75 W** (DMD). Il s'adresse à quelqu'un qui
possède le PCB « Darts » et connaît les bases de SSH. **Toutes les commandes se tapent sur le Pi**
(clavier ou session SSH), sauf mention contraire.

La référence reste la spécification, [`SPECIFICATION.md`](SPECIFICATION.md) : chaque étape cite le
paragraphe dont elle vient.

## Marqueurs

| Marqueur | Sens |
|---|---|
| **[MANQUE]** | Rien dans le dépôt ne décrit encore cette étape |
| **[À ADAPTER]** | Le script existant se lance depuis un autre ordinateur (Mac) qui pilote le Pi par SSH ; ce guide décrit ce qu'il fait, à refaire sur le Pi |
| **[À DÉCIDER]** | Choix pas encore fait par l'auteur du projet |
| **[À VENIR]** | Prévu, pas encore disponible |
| *non vérifié* | Étape écrite d'après le code et la spécification, jamais essayée telle quelle |

Valeurs à remplacer par les vôtres :

| Valeur | Contenu |
|---|---|
| `<DOSSIER-PROJET>` | Dossier du projet, sur le Pi ou sur le Mac : racine du clone `DartsPico-Bridge`, ou sous-dossier `RaspyDarts-Pico/` (nom historique) de `My_GIT_Projects` ; seule forme de ce marqueur (complément 79) |
| `<COPIE-RASPYDARTS>` | Dossier de la copie git de RaspyDarts qui reçoit les patchs |
| `<SERIE_PICO>` | Numéro de série USB du Pico 2 sous MicroPython (minuscules), relevé à la section 8 ; même marqueur que dans `pi/udev/73-dartspico-bridge.rules` |
| `<SERIE_I75W>` | Numéro de série USB de l'Interstate 75 W sous le firmware C++ (majuscules), relevé à la section 8 ; même marqueur que dans la règle udev |
| `<utilisateur>` | Compte du Pi sous lequel tournent les deux services (unités systemd) et qui garde les médias |
| `<PORT>` | Port série d'une carte (`ttyACM0`, `ttyACM1`…), lu dans `/dev/serial/by-id/` |

## Plan

1. Sécurité électrique
2. Système du Pi
3. RaspyDarts 4.6.5 (jeu d'origine)
4. Ce dépôt et les patchs du mode Pico
5. Mosquitto et dépendances Python
6. Carte Pico : MicroPython et firmware
7. Interstate 75 W : firmware C++
8. Règle udev : `/dev/cible` et `/dev/dmd`
9. Services `hwd` et rendu du DMD
10. Médias du DMD
11. Premier démarrage du jeu en mode Pico
12. Mise en service des cartes
13. Écran HDMI dédié (facultatif)
14. Mise à jour

---

## 1. Sécurité électrique

Spécification § 4 (Interstate 75 W) et § 3.7 (carte Pico) ; procédure de banc :
[`tools/bench_i75w/PROCEDURE.md`](../tools/bench_i75w/PROCEDURE.md).

**Interstate 75 W et dalles :**

- Le VBUS de l'USB-C et les **bornes à vis** de la carte sont **le même nœud 5 V**, sans isolation.
  **La carte est alimentée uniquement par l'USB du Pi** (câble USB-C ordinaire, sans diode ni fil
  coupé). **Bornes à vis vides** : bornier retiré ou isolé, étiquette « NE RIEN BRANCHER ».
- Les dalles (2 × P4 64 × 32, chaînées) sont alimentées **directement par la répartition 5 V**,
  avec un fusible séparé de **4 A minimum par dalle**, jamais par les bornes de la carte.
- **Fil de masse épais** entre les dalles et l'alimentation : aucun retour de courant des dalles par la
  nappe HUB75 ni par la masse du câble USB. La nappe HUB75 ne porte aucune alimentation, seulement des
  signaux et la masse.
- **Pi et alimentation des dalles sous tension et hors tension ensemble** (même interrupteur ou même
  multiprise) dès que la nappe HUB75 est branchée : sinon les sorties de la carte, toujours actives,
  alimenteraient des dalles éteintes par leurs entrées.
- Pour brancher ou débrancher la nappe : arrêter le Pi proprement, couper la multiprise (Pi et dalles
  ensemble), brancher ou débrancher, remettre sous tension. Sans dalles, la carte peut tourner **seule**,
  nappe débranchée.

**Carte Pico :** les sorties (Toys, voyants, LED) sont **à zéro au démarrage** et coupées par un chien
de garde si le Pi ne répond plus (spécification § 5.4, [`README.md`](../README.md)). Avant la première
mise sous tension du PCB, suivre les contrôles hors tension et la mesure des rails de la section 12 ci-dessous
(spécification § 11.2, points 1 et 2).

## 2. Système du Pi

**[MANQUE]** Préparation du système (image, compte, nom d'hôte, SSH). Seul l'état du Pi de
l'auteur est relevé :

- **Raspberry Pi OS Trixie 32 bits** (Raspbian 13 : programmes armhf, noyau 64 bits), **Python 3.13.5**
  (spécification, journal v0.3.9 ; [`README.md`](../README.md)).
- **Compte au choix** (complément 77) : les unités systemd portent les marqueurs
  `User=@RASPYDARTS_USER@` et `Group=@RASPYDARTS_GROUP@` (`pi/systemd/*.service`), remplis à
  l'installation pour `<utilisateur>` et son groupe principal (section 9) ; le dossier des médias par
  défaut est `~/dmd-medias`, développé pour l'utilisateur du service (`pi/dmd/dmd.example.toml`,
  spécification § 9.9). L'auteur utilise le compte `pi`.

## 3. RaspyDarts 4.6.5 (jeu d'origine)

Spécification § 9.4 et § 9.8 ; [`UPSTREAM.md`](UPSTREAM.md).

Version de référence : dépôt `https://github.com/rmdelcelier/RaspyDarts`, commit
`8fa61ffe9c1c264f49c3300ea61a37100b84b10c` (étiquette `_v4.6.5`).

**[MANQUE]** Installation du jeu d'origine par son script `scripts/install_on_pi.sh` : le dépôt ne la
décrit pas. Ce que la spécification en dit :

- il installe les dépendances Python du jeu, dont Pillow (`python3-pil` 11.1.0) et paho-mqtt 2
  ([`PROCEDURE.md`](PROCEDURE.md) § 2, spécification § 9.9) ;
- il crée `/etc/sudoers.d/010_raspydarts-nopasswd` (`NOPASSWD: ALL`, spécification § 9.8) ;
- il extrait une archive d'exemple qui ne contient que `themes/`, sans configuration du jeu (§ 9.4).

**Installation actuelle** : clonage de l'amont, `checkout` de `BASE` et `tools/apply_patches.sh`, au § 4.

**[REPORTÉ]** Le fork public `DartsPico-Bridge-game` (`8fa61ff`, patchs 0001 à 0010 et fichiers nouveaux
déjà appliqués, spécification, complément 78) remplacera ces étapes. Il est reporté jusqu'à ce que la carte
Pico soit assemblée et le § 11.2 de la spécification passé au moins jusqu'au point 6 (cible) : sans la carte
Pico, le jeu patché n'est pas exploitable (spécification, complément 85). Le fork n'existe pas sur GitHub. Mise à jour du jeu installé : à la main, section 14 (spécification § 12, point 25, complément 88).

## 4. Ce dépôt et les patchs du mode Pico

Spécification § 9.1 ; [`PI_INTERFACE.md`](PI_INTERFACE.md) § 6 ;
[`pi/patches/README.md`](../pi/patches/README.md).

### 4.1 Cloner ce dépôt (*non vérifié*)

```bash
git clone https://github.com/diogeneyves/DartsPico-Bridge
```

Dans la suite, les commandes se lancent depuis `<DOSSIER-PROJET>`.

### 4.2 Copie de RaspyDarts au commit de base (*non vérifié*)

`tools/apply_patches.sh` refuse une copie dont `HEAD` n'est pas le commit de `pi/patches/BASE`, ou qui
contient des modifications (codes 2 et 3).

```bash
git clone https://github.com/rmdelcelier/RaspyDarts <COPIE-RASPYDARTS>
```

```bash
git -C <COPIE-RASPYDARTS> checkout 8fa61ffe9c1c264f49c3300ea61a37100b84b10c
```

### 4.3 Vérifier, puis appliquer les patchs 0001 à 0010 (*non vérifié sur le Pi*)

Vérification seule, dans un clone temporaire (la copie n'est pas modifiée) :

```bash
tools/apply_patches.sh --check <COPIE-RASPYDARTS>
```

Application : `git am` des patchs dans l'ordre de `pi/patches/series`, puis un commit des fichiers
nouveaux (`include/cpico.py`, `include/hwbus.py`, `include/pico_proto.py`,
`pico_shims/rpi_ws281x.py`) :

```bash
tools/apply_patches.sh <COPIE-RASPYDARTS>
```

Attendu : `OK : 10 patch(s) appliqué(s) et fichiers nouveaux copiés dans …`. Rôle de chaque patch :
[`pi/patches/README.md`](../pi/patches/README.md). Patch 0010 (galerie en lecture seule) : essai du
27/11/2026 sur le Pi de l'auteur, dans une copie d'essai du jeu (4.6.5 avec les patchs 0001 à 0010) :

- installé par `git apply` ; les 13 fichiers sont identiques à ceux du commit `1751f35` ;
- galerie lancée seule (pas par le jeu), sans mot de passe : page d'accueil en 200, bandeau « lecture
  seule », aucun formulaire ni bouton de suppression (page lue par `curl` sur le Pi) ; vu par l'auteur
  dans un navigateur, par tunnel SSH : bandeau « lecture seule », 3 parties d'essai, image affichée
  dans une partie, aucun bouton ni case de suppression ;
- `POST /delete-many` sans en-tête `Origin` : 403.

Essai du même jour, galerie **lancée par le jeu** (faux Pico, puis `hwd`, puis le jeu), contrôlée
par tunnel SSH (`curl`, puis navigateur de l'auteur) :

- **Lancement par le jeu** : si rien n'écoute sur le port 8080, le jeu démarre l'unité
  `raspydarts-gallery` par `systemd-run --user` (`ensure_running` de `include/cgallery.py`) ; elle
  écoute après quelques secondes et reste lancée quand le jeu s'arrête. Une galerie déjà lancée est
  gardée : pour un nouvel essai, l'arrêter d'abord (`systemctl --user stop raspydarts-gallery`).
- **Copie d'essai qui ne change que HOME** : `systemd-run --user` ne transmet pas l'environnement du
  jeu ; l'unité reçoit le HOME du gestionnaire systemd de `<utilisateur>` et la galerie lit la
  configuration de ce HOME (`~/.raspydarts/raspydarts.cfg`), pas celle de l'essai (mot de passe,
  dossiers des médias). Contournement d'essai, dans le shell où l'environnement de l'essai est
  chargé (HOME de l'essai), juste avant le jeu :

  ```bash
  systemctl --user set-environment HOME="$HOME"
  ```

  puis, dès que la galerie écoute sur le port 8080 :

  ```bash
  systemctl --user unset-environment HOME
  ```

  Contrôle avant et après par `systemctl --user show-environment`. Toute unité utilisateur démarrée
  pendant cette fenêtre reçoit ce HOME : la garder courte. Installation normale (HOME du compte) :
  sans objet.
- **Sans mot de passe** : page d'accueil en 200, bandeau « lecture seule », aucun bouton ni case de
  suppression ; tout `POST` de suppression est refusé (403), avec ou sans `Origin` correct ; un
  `POST` sans `Origin` correct est refusé (403), `/login` compris. Les deux refus (lecture seule,
  contrôle d'origine) renvoient **la même page** ; on les distingue par `POST /login` avec un
  `Origin` correct, qui répond 200 (« Mot de passe incorrect ») : le contrôle d'origine est passé.
- **Avec mot de passe** : `GET` sans cookie : page de connexion ; `POST` de suppression avec
  `Origin` correct mais sans cookie : **page de connexion (200), pas 403**, rien n'est supprimé ;
  sans `Origin` : 403 ; `/login` avec un mauvais mot de passe : « Mot de passe incorrect », aucun
  cookie. Après connexion dans le navigateur : cases et boutons de suppression visibles (aucun
  utilisé).
- **Mot de passe réglé à distance**, sans l'écran Galerie de la borne. Jeu arrêté, par précaution
  déduite du code (le jeu réécrit sa configuration, `write_file`, à chaque changement de réglage) et
  non observée à l'essai. Sauvegarde, puis saisie masquée (`getpass`) ; seul le haché de
  `cgallery.hash_password` est écrit, et seulement si la ligne `password:` de `[Gallery]` est
  remplacée exactement une fois ; le haché n'est jamais affiché. La commande n'importe que la
  bibliothèque standard (`include/cgallery.py`, `include/cpaths.py`) : `python3` du système suffit.

  ```bash
  cp -p ~/.raspydarts/raspydarts.cfg ~/.raspydarts/raspydarts.cfg.avant-galerie
  ```

  ```bash
  cd <COPIE-RASPYDARTS> && python3 -c '
  import getpass, os, re, sys
  from include import cgallery, cpaths
  f = os.path.join(cpaths.user_dir(), cpaths.CONFIG_NAME)
  t = open(f, encoding="utf-8").read()
  p = getpass.getpass("Mot de passe de la galerie : ")
  if not (p and p == getpass.getpass("Encore une fois : ")):
      sys.exit("Arrêt : vide ou différent, rien n’est écrit")
  t2, n = re.subn(r"(^\[Gallery\][^\[]*?^password:)[^\n]*", lambda m: m.group(1) + cgallery.hash_password(p), t, flags=re.M | re.S)
  if n != 1:
      sys.exit(f"Arrêt : {n} remplacement(s), rien n’est écrit")
  open(f, "w", encoding="utf-8").write(t2)
  print("OK : 1 remplacement dans", f)
  '
  ```

  Contrôle, sans afficher le haché (attendu : `1`) :

  ```bash
  diff ~/.raspydarts/raspydarts.cfg.avant-galerie ~/.raspydarts/raspydarts.cfg | grep -c '^>'
  ```

  La galerie relit sa configuration à chaque requête : pas besoin de la relancer. Retour sans mot de
  passe : recopier la sauvegarde sur `~/.raspydarts/raspydarts.cfg`.
- **Arrêt du jeu** : le jeu ne s'arrête pas sur SIGTERM ; le signal devient `pygame.QUIT`, traduit
  en « Échap » dans un menu, ou en bouton de jeu en partie (`include/craspberry.py:1139-1143` à
  `8fa61ff`). Arrêt par `kill -9` du PID relevé par `pgrep -af '[r]aspydarts\.py'` (un PID gardé
  dans un fichier peut être périmé) ; la galerie reste lancée.

*Non vérifié* : l'accès depuis un téléphone (QR code), une vraie suppression avec mot de passe, le
repli sans `systemd-run` (galerie lancée avec le jeu, journal `logs/gallery.log`), la remise à zéro
des sorties par `hwd` après `kill -9` du jeu.

## 5. Mosquitto et dépendances Python

Spécification § 9.4 et § 9.9.

Mosquitto n'est pas présent sur une installation neuve ; il s'installe par apt et n'écoute alors qu'en
local (127.0.0.1 et ::1, port 1883), ce que demandent `hwd`, le jeu et les serveurs LED :

```bash
sudo apt install mosquitto mosquitto-clients
```

Pillow et paho-mqtt 2 viennent de l'installation du jeu (section 3). Contrôle :

```bash
python3 -c "import PIL; print(PIL.__version__)"
```

```bash
python3 -c "import paho.mqtt.client; print('paho-mqtt présent')"
```

## 6. Carte Pico : MicroPython et firmware

Spécification § 5.1, § 5.4 et § 7 ; [`firmware/pico_target/README.md`](../firmware/pico_target/README.md).

### 6.1 MicroPython sur le Pico 2

**[MANQUE]** Le dépôt ne décrit pas l'installation de MicroPython sur le Pico 2. Version visée :
**MicroPython v1.29.0** (spécification § 5.4 et journal v0.3.12). Le Pico 2 (RP2350A) est la seule cible ; le
Pico H (RP2040) n'est plus pris en charge (§ 3.1).

### 6.2 Outils `mpremote` et `picotool`

**[MANQUE]** Leur installation sur le Pi n'est pas décrite. Versions employées par l'auteur :
`picotool` 2.1.1 ([`tools/bench_i75w/PROCEDURE.md`](../tools/bench_i75w/PROCEDURE.md)), `mpremote`
dans un environnement virtuel Python.

### 6.3 Copier le firmware (*non vérifié*)

Le firmware est l'ensemble des modules de `firmware/pico_target/` ; il n'importe que ses propres modules
et ceux de MicroPython. **`bench.py` ne se copie jamais** (lanceur de banc), et il n'y a **jamais de
`boot.py`** (README du firmware). `main.py` lance le firmware au démarrage (§ 5.1) :

- il éteint d'abord le voyant de vie (GP16) ;
- **issue de secours sur place** : si **GP18 (BTN15) est à la masse** au démarrage, le firmware n'est
  pas lancé et la console MicroPython reste utilisable ;
- **issue de secours à distance** : 2 s pendant lesquelles un Ctrl-C (par exemple un `mpremote` lancé
  juste après le branchement) arrête `main.py` ;
- puis `app.run()`.

Copie de tous les modules sauf `bench.py` (*non vérifié*) :

```bash
cd firmware/pico_target && mpremote cp $(ls *.py | grep -v '^bench\.py$') : ; cd -
```

Au banc, sans `main.py` (sorties testables, Ctrl-C actif, chien de garde matériel coupé au départ) :
`mpremote run bench.py` (§ 5.1, README du firmware).

## 7. Interstate 75 W : firmware C++

Spécification § 8 et § 11.3 ; [`tools/bench_i75w/PROCEDURE.md`](../tools/bench_i75w/PROCEDURE.md).

### 7.1 Récupérer le `.uf2`

**[À VENIR]** Le firmware `i75w_display.uf2`, avec son empreinte `i75w_display.uf2.sha256`, sera publié
dans une Release GitHub du dépôt. Aujourd'hui, il n'existe que comme artefact de la CI (job `i75w-uf2`,
gardé 30 jours, § 8) : un zip avec `i75w_display.uf2`, `i75w_display.uf2.sha256` (empreinte du seul
`.uf2`) et le dossier `licenses/` (licences du Pico SDK, de TinyUSB et de Pimoroni, complément 79).

### 7.2 Flasher (*non vérifié depuis un clone* ; essayé par l'auteur au banc)

Pendant le flash, **`hwd` ne doit pas tenir la carte** : arrêter le service s'il tourne (§ 11.3) :

```bash
sudo systemctl stop raspydarts-hwd
```

Toutes les commandes `picotool` passent par `tools/bench_i75w/i75w.sh`, qui vise la carte par son
numéro de série et jamais « le premier appareil trouvé ». Le script ne contient aucun numéro de série
(complément 77) : les donner par les variables `I75W_SERIAL` et `PICO_SERIAL` ; seul `list` marche
sans elles.

1. **Premier flash** (carte encore sous le MicroPython de Pimoroni) : maintenir **BOOT**, appuyer sur
   **RST** ; la carte passe en BOOTSEL (USB `2e8a:000f`) (§ 8).
2. Relever le numéro de série de la carte en BOOTSEL (ligne `000f`) :

   ```bash
   tools/bench_i75w/i75w.sh list
   ```

3. Contrôler la puce (RP2350 en QFN60), puis flasher le `.uf2` avec son empreinte :

   ```bash
   I75W_SERIAL=<SERIE_I75W> PICO_SERIAL=<SERIE_PICO> tools/bench_i75w/i75w.sh chip
   ```

   ```bash
   I75W_SERIAL=<SERIE_I75W> PICO_SERIAL=<SERIE_PICO> tools/bench_i75w/i75w.sh flash i75w_display.uf2 "$(cut -d ' ' -f 1 i75w_display.uf2.sha256)"
   ```

4. Contrôle (USB `2e8a:0009`, `HELLO` et `STATS`) :

   ```bash
   I75W_SERIAL=<SERIE_I75W> PICO_SERIAL=<SERIE_PICO> tools/bench_i75w/i75w.sh check
   ```

Ensuite, le passage en BOOTSEL se fait sans bouton (`i75w.sh to-bootsel`). Sans données du Pi, la carte
affiche son animation de secours « RASPYDARTS » (§ 8).

## 8. Règle udev : `/dev/cible` et `/dev/dmd`

Spécification § 2 (complément 62) ; [`PROCEDURE.md`](PROCEDURE.md) § 2.6.

La règle `pi/udev/73-dartspico-bridge.rules` crée `/dev/cible` (Pico 2 sous MicroPython, `2e8a:0005`) et
`/dev/dmd` (Interstate 75 W sous le firmware C++, `2e8a:0009`), par VID:PID et **numéro de série en
casse exacte** : minuscules sous MicroPython, majuscules sous le firmware C++. Le fichier du dépôt
contient les marqueurs `<SERIE_PICO>` et `<SERIE_I75W>` (complément 77) : **il faut y mettre vos
numéros**.

### 8.1 Relever les numéros de série

Les deux cartes branchées, lire leurs ports :

```bash
ls -l /dev/serial/by-id/
```

Pour chaque carte, en remplaçant `<PORT>` :

```bash
udevadm info -a -n /dev/<PORT> | grep -m3 -E "ATTRS\{(idVendor|idProduct|serial)\}"
```

`<SERIE_PICO>` est la valeur de `ATTRS{serial}` pour `2e8a:0005`, `<SERIE_I75W>` celle pour `2e8a:0009`,
recopiées **avec leur casse**.

### 8.2 Installer la règle avec vos numéros (*non vérifié*)

udev compare `ATTRS{serial}` octet par octet (spécification § 2, compléments 62 et 77) :
`<SERIE_PICO>` s'écrit en **minuscules**, `<SERIE_I75W>` en **majuscules**, 16 chiffres
hexadécimaux chacun, recopiés tels que la section 8.1 les affiche. Une casse fausse ne donne
aucune erreur dans udev : le lien n'apparaît pas, c'est tout.

Depuis la racine de `<DOSSIER-PROJET>` :

```bash
export PICO_SERIAL=<SERIE_PICO> I75W_SERIAL=<SERIE_I75W>
```

Contrôle des deux séries par l'essai à blanc de `tools/install_services.sh` : il ne se connecte
à rien et n'écrit rien ; une série de mauvaise casse ou de mauvais format est refusée (message
sur la sortie d'erreur, code 64) :

```bash
tools/install_services.sh --user <utilisateur> --pico-serial "$PICO_SERIAL" --i75w-serial "$I75W_SERIAL"
```

Attendu : `Essai à blanc terminé : rien n'a été envoyé au Pi.` La ligne `Hôte` ne sert qu'à
`--apply`, qui installe par SSH depuis un Mac (**[À ADAPTER]**, section 9) ; sur le Pi, la
règle s'installe à la main (les motifs `[<]SERIE_…[>]` visent les marqueurs du fichier ; ne pas
les remplacer à la main) :

```bash
sed -e "s/[<]SERIE_PICO[>]/$PICO_SERIAL/" -e "s/[<]SERIE_I75W[>]/$I75W_SERIAL/" pi/udev/73-dartspico-bridge.rules | sudo tee /etc/udev/rules.d/73-dartspico-bridge.rules
```

`install_services.sh --apply` fait le `reload`, jamais le `trigger` ([`PROCEDURE.md`](PROCEDURE.md)
§ 2.6) ; ici, les deux :

```bash
sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=tty --action=add
```

Contrôle (attendu : `/dev/cible -> ttyACM…` et `/dev/dmd -> ttyACM…`) :

```bash
ls -l /dev/cible /dev/dmd
```

Rien n'est activé ni démarré : services à la section 9. Carte ou firmware changé : le lien
disparaît et `hwd` voit la carte absente ; relever de nouveau le numéro de série (section 8.1)
et refaire la section 8.2.

## 9. Services `hwd` et rendu du DMD

Spécification § 9.5 et § 9.9 (50e complément) ; [`PROCEDURE.md`](PROCEDURE.md) § 2.

**[À ADAPTER]** `tools/install_services.sh --apply` se lance depuis un Mac et installe par SSH, avec
trois options obligatoires : `--user <utilisateur>`, `--pico-serial <SERIE_PICO>` (minuscules) et
`--i75w-serial <SERIE_I75W>` (majuscules). Ce qu'il fait sur le Pi, avec `sudo` :

- copie des fichiers de `pi/hwd`, `pi/dmd` et `shared` dans `/opt/dartspico-bridge/` (même structure que
  le dépôt), à root, dossiers 755, fichiers 644, et la révision dans `/opt/dartspico-bridge/REVISION` ;
  rien n'y est effacé ;
- unités `raspydarts-hwd.service` et `raspydarts-dmd.service` dans `/etc/systemd/system/`, après
  remplissage de `User=` et `Group=` par `pi/systemd/fill_units.sh` (utilisateur inconnu ou marqueur
  restant : rien n'est installé) ;
- `/etc/raspydarts/hwd.toml` et `/etc/raspydarts/dmd.toml` écrits depuis `pi/hwd/hwd.example.toml` et
  `pi/dmd/dmd.example.toml` **seulement s'ils n'existent pas** ;
- règle udev (section 8 ci-dessus), puis `udevadm control --reload` ;
- `systemctl daemon-reload`, **sans `enable` ni `start`**.

Équivalent sur le Pi, depuis `<DOSSIER-PROJET>` (*non vérifié*) :

```bash
sudo install -d -m 755 /opt/dartspico-bridge/pi && sudo cp -R pi/hwd pi/dmd /opt/dartspico-bridge/pi/ && sudo cp -R shared /opt/dartspico-bridge/ && git describe --always --dirty | sudo tee /opt/dartspico-bridge/REVISION
```

```bash
sudo chown -R root:root /opt/dartspico-bridge && sudo find /opt/dartspico-bridge -type d -exec chmod 755 {} + && sudo find /opt/dartspico-bridge -type f -exec chmod 644 {} +
```

```bash
d="$(mktemp -d)" && cp pi/systemd/raspydarts-hwd.service pi/systemd/raspydarts-dmd.service "$d"/ && sh pi/systemd/fill_units.sh "$d" <utilisateur> && sudo install -m 644 "$d"/raspydarts-hwd.service "$d"/raspydarts-dmd.service /etc/systemd/system/
```

```bash
sudo install -d -m 755 /etc/raspydarts && sudo cp -n pi/hwd/hwd.example.toml /etc/raspydarts/hwd.toml && sudo cp -n pi/dmd/dmd.example.toml /etc/raspydarts/dmd.toml
```

```bash
sudo systemctl daemon-reload
```

**Configuration.** Toutes les clés sont facultatives ; une clé inconnue fait refuser tout le fichier
(le service s'arrête avec un message). Dans `hwd.toml`, la section `[pico_params]` règle notamment le
nombre de LED par canal (`led0_count` : 204 LED de la cible sur J31 ; canaux 1 et 2 : 0 par défaut) ;
`hwd` les envoie au Pico à chaque connexion (§ 3.5, § 5.3).

**Démarrage à la main** (*non vérifié* : les unités n'ont encore jamais été démarrées par systemd) :

```bash
sudo systemctl start raspydarts-hwd raspydarts-dmd
```

Le rendu décode d'abord `Scores` et `SpecialsMoves` (environ 8 s) ; pendant ce temps, la 75 W affiche
son animation de secours. Journaux :

```bash
journalctl -u raspydarts-dmd -u raspydarts-hwd -n 50 --no-pager
```

DMD virtuel : page `http://127.0.0.1:8081/`, servie en local seulement (clé `web_port`, § 9.9). Depuis
un autre ordinateur, par un tunnel SSH vers le port 8081 du Pi.

**Activation au démarrage du Pi**, seulement après un essai concluant (*non vérifié*) :

```bash
sudo systemctl enable raspydarts-hwd raspydarts-dmd
```

**Retour arrière** (désinstallation, complément 86) : [`PROCEDURE.md`](PROCEDURE.md) § 2.7. Les
configurations de `/etc/raspydarts/` y sont gardées par défaut ; la galerie et les règles udev du
jeu ne sont jamais touchées.

## 10. Médias du DMD

Spécification § 9.3 et § 9.9 (41e, 42e, 48e et 49e compléments) ; code : `pi/dmd/dmd_core.py`,
`pi/dmd/dmd_render.py`, `pi/dmd/dmd_service.py`.

Les médias ne sont **pas fournis** par ce dépôt : chacun fournit les siens. Ils se placent dans le dossier
de la clé `dmd_media_dir` de `/etc/raspydarts/dmd.toml` (défaut `~/dmd-medias`, `~` développé pour
`<utilisateur>`, complément 77), lisible par ce compte ; l'auteur les garde en dossiers 755 et fichiers 644. **Jamais dans `/Raspy2DMD`** : le jeu
réagit à ce dossier (§ 6.6).

| Dossier | Contenu | Formats |
|---|---|---|
| `Gifs/600 Gratos/` | GIF de l'Attract Mode, tirés au hasard après `attract_delay_s` sans message ; chacun coupé à `attract_gif_max_s` | `.gif` (casse ignorée) |
| `Scores/<code>/` | Animation jouée pour une fléchette ; `<code>` : `S1`…`S20`, `D1`…`D20`, `T1`…`T20`, `SB`, `DB` ; un fichier tiré au hasard | `.gif`, `.ppm` |
| `SpecialsMoves/<nom>/` | Coups spéciaux d'une volée (clé `dmd_special_moves`) ; noms reconnus : `MAXIMUM_TON_80`, `DEVIL`, `BREAKFAST`, `CHAMPAGNE_BREAKFAST`, `ROUND_OF_TERMS`, `BUCKET_OF_NAIL`, `NOT_OLD`, `BLACK_HAT_THREE_IN_THE_BLACK`, `HAT_TRICK`, `THREE_IN_A_BED`, `LOW_TON`, `HIGH_TON`, `DINKY_DOO`, `FEATHERS`, `BAG_O_NUT`, `ROUTE_66`, `HAPPY_MEAL`, `SUNSET_STRIP`, `GARDEN_GATE_FAT_LADIES`, `BABY_TON` ; dans `DEVIL`, les fichiers `devil 2.gif` et `devil (triple) 2.gif` sont choisis par nom | `.gif`, `.ppm` |
| `Fonts/` | `Impact.ttf` (textes) et `Blackwood.ttf` (heure) ; à défaut, police intégrée de Pillow | `.ttf` |

- Fichiers et dossiers cachés ignorés ; les autres dossiers de `SpecialsMoves` ne sont pas chargés.
- Image plus grande que 128 × 32 : réduite proportionnellement ; toute image est centrée sur fond noir,
  sans étirement ni recadrage.
- Média manquant : journalisé une fois, texte seul, le service continue. Aucun GIF d'Attract Mode :
  écran d'accueil.

**[À ADAPTER]** `tools/sync_medias.sh` copie les médias d'un Mac vers le Pi par rsync (essai à blanc par
défaut, jamais de suppression, droits fixés à 755 et 644 après copie) ; sur le Pi, copier les dossiers
ci-dessus dans `dmd_media_dir` par tout autre moyen.

## 11. Premier démarrage du jeu en mode Pico

Spécification § 9.4 ; [`pi/patches/README.md`](../pi/patches/README.md).

**[À DÉCIDER]** Lancement du jeu patché (lié à son emplacement, section 3).

- **Sans configuration du jeu** et sans Blinka (cas d'une installation Pico) : le jeu démarre en mode
  Pico, l'écrit dans son journal et ouvre l'assistant. Le choix **« Carte Pico »** applique le préréglage
  (`[SectionAdvanced] hardware = pico`, boutons, Toys, voyants, `sound_output = hdmi` si la valeur était
  `auto` ou vide) et propose la table de l'anneau de LED de la cible : 204 LED sans pneu, 244 avec pneu,
  ou aucune (patchs 0002 et 0004).
- **Configuration existante** (`~/.raspydarts/raspydarts.cfg`) : `tools/migrate_to_pico.py` applique le
  même préréglage. Par défaut il écrit une copie `<fichier>.pico` ; `--dry-run` n'écrit rien ;
  `--in-place` remplace l'original après une sauvegarde horodatée (*non vérifié sur un autre Pi*) :

  ```bash
  python3 tools/migrate_to_pico.py --dry-run
  ```

- **Étalonnage de la cible** : en mode Pico, l'écran d'étalonnage s'ouvre tant qu'aucun segment n'est
  connu (patch 0004). **[MANQUE]** Procédure d'étalonnage (§ 11.2, point 6).

## 12. Mise en service des cartes

Spécification § 11.2 et § 11.3. **[MANQUE]** Aucune procédure détaillée ; ordre prévu pour la carte
Pico :

1. Hors tension : contrôle visuel, absence de court-circuit entre `+5V_*`, `+3V3` et la masse.
2. Rails : `+5V_LOG`, `+3V3`, VSYS.
3. Bus I2C : adresses **0x20, 0x21, 0x22** présentes.
4. Sorties : un Toy et un voyant à la fois, puis charge réelle ; état à la mise sous tension.
5. Boutons : les 15.
6. Cible : étalonnage, puis chaque segment.
7. LED : les 3 sorties, quelques LED puis la bande complète.
8. Détecteur de choc : polarité et seuil.
9. USB : énumération, règle udev, reprise après débranchement.

Interstate 75 W : carte seule, puis avec les dalles (consignes de la section 1), cadence d'images et température.
Avec les dalles : *non vérifié*.

## 13. Écran HDMI dédié (facultatif)

Spécification § 9.9, « Écran HDMI dédié » (complément 72) ; [`PROCEDURE.md`](PROCEDURE.md) § 3.
*Non vérifié* : jamais essayé sur un Pi.

Le service de rendu peut afficher le DMD en plein écran sur un écran branché au **2e port micro-HDMI**
(sortie `HDMI-A-2`), le jeu restant sur le 1er (`HDMI-A-1`). Coupé par défaut.

1. Dans le Centre de contrôle (onglet « Screens »), placer `HDMI-A-1` en haut à gauche (0,0) et
   `HDMI-A-2` à sa droite, bords hauts alignés.
2. Dans `/etc/raspydarts/dmd.toml`, mettre `hdmi_output = 1`, puis :

   ```bash
   sudo systemctl restart raspydarts-dmd
   ```

3. Journal attendu : `HDMI screen output on (HDMI-A-2)`, puis `full screen on HDMI-A-2`.

## 14. Mise à jour

Spécification § 12, point 25 (complément 88). Une installation en service se met à jour **à la
main**, élément par élément : code du Pi, patchs du jeu, firmware du Pico, firmware de
l'Interstate 75 W, médias du DMD. Aucun mécanisme automatique (toujours ouvert au point 25, à
reprendre avec le fork) ; en mode Pico, le bouton « Vérifier les mises à jour » du jeu reste
désactivé (patch 0007, spécification § 9.8). **Rien de cette section n'a encore été fait sur le
vrai Pi : tout y est *non vérifié*.**

### 14.1 Règle : un ensemble cohérent

- Un **ensemble cohérent** est formé du code de `/opt/dartspico-bridge`, des patchs du jeu et des
  deux firmwares **d'une même révision du dépôt**. Les médias, absents du dépôt, sont hors de
  cette règle.
- La révision installée fait foi : `/opt/dartspico-bridge/REVISION`, écrite par chaque
  installation du code (section 9). La noter **avant** toute mise à jour, pour pouvoir revenir en
  arrière :

  ```bash
  cat /opt/dartspico-bridge/REVISION
  ```

- Révision du clone du dépôt, après `git pull` (même valeur attendue sur chaque clone utilisé, au
  Mac et au Pi ; un suffixe `-dirty` signale des modifications locales, à éviter) :

  ```bash
  git -C <DOSSIER-PROJET> pull --ff-only && git -C <DOSSIER-PROJET> describe --always --dirty
  ```

- **Aucune carte ne donne la révision** : les firmwares annoncent seulement leur version `x.y.z`
  (champs `fw` et `dmd_fw` de `raspydarts/hw/status`), et `hwd` ne compare que la version du
  protocole. Noter à la main la révision des firmwares copiés et des patchs appliqués.
- **Retour arrière** vers la révision notée : `git -C <DOSSIER-PROJET> checkout` de cette
  révision, puis refaire l'élément voulu ; revenir ensuite sur la branche par
  `git -C <DOSSIER-PROJET> checkout main`.

### 14.2 Code du Pi : `/opt/dartspico-bridge` (*non vérifié*)

**Avant** : arrêter les deux services s'ils tournent, et tout `hwd` lancé à la main (un seul
programme peut tenir `/dev/cible` et `/dev/dmd`, [`PROCEDURE.md`](PROCEDURE.md) § 2) :

```bash
sudo systemctl stop raspydarts-dmd raspydarts-hwd
```

**Commandes** : essai à blanc, puis installation, comme à la section 9 (**[À ADAPTER]** :
`tools/install_services.sh --apply` se lance depuis le Mac ; sur le Pi, commandes équivalentes de
la section 9). Les configurations `/etc/raspydarts/*.toml` ne sont **jamais écrasées** : les
comparer aux exemples de la nouvelle révision (toutes les clés sont facultatives, mais une clé
inconnue fait refuser tout le fichier) :

```bash
diff /etc/raspydarts/hwd.toml pi/hwd/hwd.example.toml; diff /etc/raspydarts/dmd.toml pi/dmd/dmd.example.toml
```

Redémarrer ensuite les services s'ils tournaient :

```bash
sudo systemctl start raspydarts-hwd raspydarts-dmd
```

**Contrôle** : la révision installée est celle du clone, puis services et journaux :

```bash
cat /opt/dartspico-bridge/REVISION && systemctl is-active raspydarts-hwd raspydarts-dmd
```

```bash
journalctl -u raspydarts-dmd -u raspydarts-hwd -n 50 --no-pager
```

**Fichiers retirés du dépôt** : l'installation n'efface rien dans `/opt/dartspico-bridge` ; un
fichier retiré du dépôt y reste. Pour repartir propre : retour arrière du
[`PROCEDURE.md`](PROCEDURE.md) § 2.7 (étapes principales seulement : arrêt et `disable`, suppression
des unités, de la règle udev et de `/opt/dartspico-bridge`, rechargement ; les `.toml` sont
gardés), puis nouvelle installation. Le `disable` retire l'activation au démarrage : la refaire si
elle existait (`PROCEDURE.md` § 2.5).

**Retour arrière** : installer de nouveau le code de la révision notée (§ 14.1) ; les fichiers
ajoutés par la révision la plus récente restent, sauf à repartir propre comme ci-dessus.

### 14.3 Patchs du jeu (*non vérifié*)

**Avant** :

- arrêter le jeu (il ne s'arrête pas sur SIGTERM : `kill -9` du PID relevé par
  `pgrep -af '[r]aspydarts\.py'`, section 4.3) et la galerie :

  ```bash
  systemctl --user stop raspydarts-gallery
  ```

- sauvegarde nommée du dossier du joueur :

  ```bash
  cp -a ~/.raspydarts ~/.raspydarts.avant-mise-a-jour-$(date +%Y%m%d)
  ```

**Où le jeu range ses données** (vérifié dans RaspyDarts 4.6.5, `8fa61ff`, `include/cpaths.py`) :
configuration (`raspydarts.cfg`), thèmes, scores (`scores.db`) et trophées (`players_data.json`)
sont dans `~/.raspydarts` (`~/.pydarts` pour une installation d'avant le renommage), **hors du
clone** du jeu : ni le nouveau clone ni le basculement n'y touchent. Dans le dossier du jeu sont
repérés `logs/` (journaux) et `backup/` (archives de mise à jour de `include/cupdate.py`,
inutilisé en mode Pico) ; ils restent dans l'ancienne copie.

**Commandes** : nouveau clone **à côté** de la copie en service, au commit de `pi/patches/BASE` de
la nouvelle révision, puis vérification et application des patchs (section 4), depuis
`<DOSSIER-PROJET>` ; **jamais de `git reset --hard`** dans la copie en service :

```bash
git clone https://github.com/rmdelcelier/RaspyDarts <COPIE-RASPYDARTS>.nouveau
```

```bash
git -C <COPIE-RASPYDARTS>.nouveau checkout "$(cat pi/patches/BASE)"
```

```bash
tools/apply_patches.sh --check <COPIE-RASPYDARTS>.nouveau && tools/apply_patches.sh <COPIE-RASPYDARTS>.nouveau
```

**Basculement** : l'ancienne copie est gardée sous `<COPIE-RASPYDARTS>.ancien` ; le jeu se lance
toujours depuis le même chemin. `mv -T` refuse d'écraser un dossier non vide : si un
`<COPIE-RASPYDARTS>.ancien` reste d'une mise à jour précédente, le supprimer ou le renommer d'abord.
Si le dossier parent n'appartient pas à `<utilisateur>` (l'installeur de l'amont place le jeu dans
`/raspydarts`, à la racine), ces `mv` demandent `sudo` :

```bash
mv -T <COPIE-RASPYDARTS> <COPIE-RASPYDARTS>.ancien && mv -T <COPIE-RASPYDARTS>.nouveau <COPIE-RASPYDARTS>
```

**Contrôle** : `BASE` suivi de 11 commits exactement (10 patchs et les fichiers nouveaux), copie
sans modification (attendu : le commit de `BASE`, puis rien) ; puis lancement du jeu et journal
en mode Pico (section 11) :

```bash
git -C <COPIE-RASPYDARTS> rev-parse HEAD~11 && git -C <COPIE-RASPYDARTS> status --porcelain
```

**Retour arrière** : jeu et galerie arrêtés comme ci-dessus, échange inverse des deux copies :

```bash
mv -T <COPIE-RASPYDARTS> <COPIE-RASPYDARTS>.nouveau && mv -T <COPIE-RASPYDARTS>.ancien <COPIE-RASPYDARTS>
```

Si la configuration doit revenir aussi, la mettre de côté puis recopier la sauvegarde (rien n'est
effacé ; remplacer `<date>` par celle du nom de la sauvegarde) :

```bash
mv -T ~/.raspydarts ~/.raspydarts.apres-mise-a-jour && cp -a ~/.raspydarts.avant-mise-a-jour-<date> ~/.raspydarts
```

L'ancienne copie et les sauvegardes se suppriment à la main, plus tard, une fois la nouvelle
version validée.

### 14.4 Firmware du Pico (*non vérifié*)

**Avant** : arrêter `hwd`, qui tient `/dev/cible` (l'unité, et tout `hwd` lancé à la main). Sans
`hwd`, le chien de garde du Pico remet les sorties à zéro.

```bash
sudo systemctl stop raspydarts-hwd
```

En marche, le firmware coupe Ctrl-C (`micropython.kbd_intr(-1)`, `firmware/pico_target/app.py`) :
`mpremote` ne peut pas l'interrompre. Deux issues, celles de la section 6.3 :

- **sur place** : GP18 (BTN15) à la masse au démarrage, le firmware n'est pas lancé ;
- **fenêtre de 2 s** : débrancher puis rebrancher le Pico et lancer la copie aussitôt (le lien
  `/dev/cible` doit apparaître à temps : *non vérifié*).

**Commandes** : copie de tous les modules sauf `bench.py`, comme à la section 6.3, en visant le
Pico par son lien (et non « le premier port trouvé »), puis redémarrage :

```bash
cd firmware/pico_target && mpremote connect /dev/cible cp $(ls *.py | grep -v '^bench\.py$') : ; cd -
```

```bash
mpremote connect /dev/cible reset
```

```bash
sudo systemctl start raspydarts-hwd
```

**Contrôle** : voyant de vie à 1 Hz, puis statut de `hwd` (attendu : `"pico": "connected"` et
`"fw"` égal à `FW_VERSION` de la révision) :

```bash
mosquitto_sub -h 127.0.0.1 -t raspydarts/hw/status -C 1
```

```bash
grep -n '^FW_VERSION' firmware/pico_target/app.py
```

`mpremote cp` n'efface rien sur le Pico : un module retiré du dépôt y reste. Comparer la liste du
Pico à celle de `firmware/pico_target/` (jamais de `bench.py` ni de `boot.py`) :

```bash
mpremote connect /dev/cible ls :
```

**Retour arrière** : `checkout` de la révision notée (§ 14.1), même copie, puis retour sur
`main`. Firmware qui ne démarre plus : issue GP18 sur place, la console reste libre.

### 14.5 Firmware de l'Interstate 75 W (*non vérifié* ; flash essayé par l'auteur au banc)

**Avant** : arrêter `hwd`, qui tient `/dev/dmd` (section 7.2) :

```bash
sudo systemctl stop raspydarts-hwd
```

Récupérer l'artefact de la CI (job `i75w-uf2`) de l'exécution du **commit de la même révision**
(section 7.1 : pas encore de Release, artefact gardé 30 jours), et **garder le zip du firmware
installé** : c'est le seul moyen de revenir en arrière après ces 30 jours.

**Commandes** (depuis `<DOSSIER-PROJET>`, `.uf2` et `.sha256` dans le dossier courant, séries
exportées comme à la section 7.2) : passage en BOOTSEL sans bouton, flash avec l'empreinte
relevée dans le `.sha256` (le script attend la valeur, pas le nom du fichier), puis contrôle :

```bash
tools/bench_i75w/i75w.sh to-bootsel
```

```bash
tools/bench_i75w/i75w.sh flash i75w_display.uf2 "$(cut -d ' ' -f 1 i75w_display.uf2.sha256)"
```

```bash
tools/bench_i75w/i75w.sh check
```

```bash
sudo systemctl start raspydarts-hwd
```

**Contrôle** : `check` réussi (USB `2e8a:0009`, `HELLO` et `STATS`), puis statut de `hwd`
(attendu : `"dmd": "connected"` et la version dans `"dmd_fw"`) :

```bash
mosquitto_sub -h 127.0.0.1 -t raspydarts/hw/status -C 1
```

**Retour arrière** : mêmes commandes avec le `.uf2` et le `.sha256` gardés. Si `to-bootsel`
échoue (firmware qui ne répond plus) : BOOT + RST sur place (section 7.2, étape 1).

### 14.6 Médias du DMD (*non vérifié* comme mise à jour)

Hors de la règle du § 14.1 : les médias ne sont pas dans le dépôt.

**Avant** : aucun arrêt n'est demandé par la procédure actuelle ([`PROCEDURE.md`](PROCEDURE.md)
§ 1). Le service de rendu décode `Scores` et `SpecialsMoves` à son démarrage : le relancer après
la copie pour qu'il les relise (déduit, *non vérifié*).

**Commandes** : essai à blanc, puis copie réelle (**[À ADAPTER]** : `tools/sync_medias.sh` se
lance depuis le Mac, section 10) ; seuls les fichiers absents ou modifiés sont transférés :

```bash
tools/sync_medias.sh --source "/chemin/des/medias"
```

```bash
tools/sync_medias.sh --source "/chemin/des/medias" --apply
```

```bash
sudo systemctl restart raspydarts-dmd
```

**Contrôle** : comme au [`PROCEDURE.md`](PROCEDURE.md) § 1.4 (nombre, taille, droits) ; un nouvel
essai à blanc ne liste plus aucun fichier.

**Retour arrière** : aucun par le script. Il n'efface jamais rien (pas de `--delete`) et ne garde
pas l'ancienne version d'un fichier remplacé : garder l'ancienne version des médias sur le Mac, et
retirer à la main sur le Pi un fichier qui ne doit plus y être.
