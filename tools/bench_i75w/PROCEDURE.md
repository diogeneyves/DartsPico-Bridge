# Banc de l'Interstate 75 W : procédure

> **Sécurité (§ 4 de la spécification).** Le VBUS de l'USB-C et les bornes à vis de la carte sont **le même nœud 5 V**.
> **La carte est alimentée uniquement par l'USB du Pi** (câble USB-C ordinaire) ; **bornes à vis vides** (bornier retiré ou isolé, étiquette « NE RIEN BRANCHER »).
> Dalles alimentées directement par l'alimentation 5 V, avec un **fil de masse épais** entre dalles et alimentation (aucun retour de courant par la nappe HUB75).
> **Pi et dalles sous tension et hors tension ensemble** (même multiprise) dès que la nappe HUB75 est branchée : la carte alimenterait des dalles éteintes par leurs entrées. Sinon, carte **seule** : nappe HUB75 débranchée.
> **Passage de la carte seule aux dalles** (ou retour) : **[Opérateur]** arrêter le Pi proprement, couper la multiprise (Pi et dalles ensemble), brancher ou débrancher la nappe, remettre la multiprise sous tension. Le Pi peut vider `/tmp` au démarrage : si `/tmp/bench_i75w` manque, refaire le § 1 (le `.uf2` n'est plus utile après le § 4).

Ce banc installe et essaie le **firmware C++ de M4** (spécification § 8) : c'est l'**essai de débit du § 11.3**, compteurs lus par `STATS` (§ 6.2) et `TELEMETRY`. Le banc précédent, sous le MicroPython de Pimoroni (23 novembre 2026), est gardé en annexe.

**Organisation.** La 75 W est branchée en USB sur le **Pi**. Tout se fait par SSH depuis le Mac, avec `picotool` 2.1.1 du Pi ; rien à installer sur le Mac. Le rôle de l'opérateur : récupérer l'artefact de la CI, brancher et débrancher, appuyer sur BOOT et RST, alimenter ou non les dalles (étapes marquées **[Opérateur]**).

**Ciblage.** Le Pico 2 de la cible (`<SERIE_PICO>`) reste branché sur le même Pi. Toutes les commandes `picotool` passent par `i75w.sh`, qui vise la 75 W (`<SERIE_I75W>`) par son **numéro de série** (BOOTSEL `2e8a:000f`, firmware C++ `2e8a:0009`) ou son **port by-id**, refuse s'il y a zéro ou plusieurs candidats, et exclut le Pico 2. Avant chaque écriture : contrôle de l'empreinte, du fichier et de la puce (`flash`). **Jamais `uhubctl`** dans ce banc : il couperait aussi le Pico. Jamais `picotool` lancé directement.

Fichiers (`tools/bench_i75w/`) :

| Fichier | Où | Rôle |
|---|---|---|
| `i75w.sh` | Pi | Ciblage et contrôles : `list`, `bootsel`, `chip`, `info`, `flash`, `flash --micropython`, `to-bootsel`, `run`, `port`, `check`, `tx` |
| `tx_bench.py` | Pi | Émetteur : 15 images/s puis au maximum ; `HELLO`, `STATS` avant et après chaque phase, `TELEMETRY` ; `--check` : `HELLO` et `STATS` seulement |
| `shared/dmd_proto.py` | Pi | Format des trames (§ 6) |
| `i75w_display.uf2` | carte | Firmware C++, artefact de la CI (pas dans le dépôt) |
| `testcard.py`, `rx_bench.py`, `bench_config.py`, `bench_fast.py` | carte | Banc MicroPython (annexe) |

Codes de sortie de `i75w.sh` : 0 bon, 2 appareil introuvable ou ambigu, 3 contrôle refusé, 64 usage.

## A. Premier flash du firmware C++ sur place

### A1. État de départ

**[Opérateur]** Carte **seule** (§ 11.3 : premier essai sans dalles) : bornes à vis vides, nappe HUB75 débranchée, USB-C de la carte sur le Pi. La carte tourne encore sous MicroPython, récepteur du banc en `main.py` (annexe, § 10).

`hwd` ne doit pas ouvrir la 75 W pendant le banc (§ 11.3, 40e complément) : il ouvre `dmd_device`, `/dev/dmd` par défaut. Sur le Pi :

```bash
ls -l /dev/dmd
```

Depuis le complément 62 de la spécification, la règle udev `73-dartspico-bridge.rules` crée `/dev/dmd` dès que la 75 W est branchée sous le firmware C++ : `ls -l /dev/dmd` peut donc répondre un lien vers `ttyACM…` (avant l'installation de la règle : `No such file or directory`). Dans tous les cas, **`hwd` doit être arrêté pendant tout le banc** :

```bash
pgrep -af hwd
```

`i75w.sh tx` refuse (code 3) si le port de la 75 W est déjà ouvert par un autre processus (`fuser`), et prévient si `/dev/dmd` existe.

### A2. Récupérer le `.uf2` de la CI (sur le Mac)

**[Opérateur]** Sur GitHub, onglet **Actions**, exécution du workflow des tests pour le commit voulu (job `i75w-uf2` réussi) : télécharger l'artefact **`i75w_display-uf2-<commit>`** (zip : `i75w_display.uf2`, `i75w_display.uf2.sha256`, et le dossier `licenses/` avec les licences du Pico SDK, de TinyUSB et de Pimoroni, complément 79). Le résumé de l'exécution affiche aussi le SHA-256 du `.uf2`. L'artefact est gardé 30 jours.

```bash
cd <DOSSIER-PROJET>
```

Adresse du Pi (`raspydarts.local`, toujours en IPv4 : `-4` pour `ssh` et `scp`) ; adapter le nom du zip :

```bash
PI=pi@raspydarts.local
```

```bash
rm -rf /tmp/i75w-uf2 && mkdir -p /tmp/i75w-uf2 && unzip -o ~/Downloads/i75w_display-uf2-<commit>.zip -d /tmp/i75w-uf2
```

### A3. Copier le banc et le `.uf2` sur le Pi (depuis le Mac)

```bash
ssh -4 $PI 'rm -rf /tmp/bench_i75w && mkdir -p /tmp/bench_i75w' && scp -4 shared/dmd_proto.py tools/bench_i75w/*.py tools/bench_i75w/i75w.sh /tmp/i75w-uf2/i75w_display.uf2 /tmp/i75w-uf2/i75w_display.uf2.sha256 $PI:/tmp/bench_i75w/
```

```bash
ssh -4 $PI
```

**Toutes les commandes suivantes se tapent sur le Pi**, dans ce dossier :

```bash
cd /tmp/bench_i75w && chmod +x i75w.sh
```

Numéros de série des deux cartes, sans valeur par défaut dans `i75w.sh` (complément 77) ; `<SERIE_PICO>` et `<SERIE_I75W>` : `ATTRS{serial}` relevés par `udevadm info` (casse indifférente pour `i75w.sh`). Seul `./i75w.sh list` marche sans eux :

```bash
export PICO_SERIAL=<SERIE_PICO> I75W_SERIAL=<SERIE_I75W>
```

### A4. Contrôler le fichier

```bash
sha256sum -c i75w_display.uf2.sha256
```

Attendu : `i75w_display.uf2: OK`, et la même empreinte que le résumé de la CI.

```bash
picotool info -a i75w_display.uf2
```

(Lecture du fichier seul, aucun appareil visé.) Attendu : `name: RaspyDarts DMD` (nom de programme, identique au produit USB, 55e complément), version `0.1.0`, `target chip: RP2350`.

### A5. BOOTSEL par BOOT + RST

Le MicroPython de Pimoroni n'a pas l'interface de remise à zéro du SDK, et le récepteur du banc coupe Ctrl-C : le premier passage en BOOTSEL se fait aux boutons. **[Opérateur]** Carte branchée : maintenir **BOOT**, appuyer brièvement sur **RST**, relâcher BOOT.

```bash
./i75w.sh list
```

Attendu : une ligne `000f` portant `<SERIE_I75W>`, et le Pico 2 (`<SERIE_PICO>`) sous son propre produit.

```bash
./i75w.sh chip
```

Attendu : `série <SERIE_I75W> : type RP2350, boîtier QFN60, chipid …`. **Toute autre réponse : arrêter et demander.**

### A6. Installer le firmware

`flash` refuse si le nom n'est pas `i75w_display.uf2`, si l'empreinte diffère, si `picotool info` du fichier ne donne pas le programme `RaspyDarts DMD` (ligne `name:` entière, comparaison exacte) pour un RP2350, si la puce n'est pas un RP2350 en QFN60, ou si la 75 W n'est pas en BOOTSEL une fois et une seule :

```bash
./i75w.sh flash i75w_display.uf2 "$(cut -d ' ' -f 1 i75w_display.uf2.sha256)"
```

La carte redémarre sur le firmware C++. Noter dans le compte rendu :

| Date | Commit de la CI | SHA-256 de `i75w_display.uf2` |
|---|---|---|
| 25/11/2026 | `0b712e2` | `8810b1172c7e69ad31657e32f4383f32f0e1bcd1125d9308abe71bb7991f4312` |

### A7. Contrôles après le flash

```bash
./i75w.sh list
```

Attendu : une ligne `0009` portant `<SERIE_I75W>`.

```bash
ls /dev/serial/by-id/
```

Attendu : `usb-RaspyDarts_RaspyDarts_DMD_<SERIE_I75W>-if00` (série **en majuscules**, § 8) [BANC]. Si le nom diffère, ou si la série diffère de celle du BOOTSEL : s'arrêter, `i75w.sh` est à corriger.

```bash
./i75w.sh check
```

`check` vérifie dans sysfs `2e8a:0009`, la série, le fabricant « RaspyDarts » et le produit « RaspyDarts DMD », puis envoie `HELLO_REQ` et `STATS_REQ`. Attendu :

```text
HELLO : protocole 1, 128 × 32, version 0.1.0, 5 V = 5… mV
STATS : uptime …, 0 FRAME, … octets reçus, … octets sautés, 0 CRC faux, 0 longueurs refusées, flags 0x00
```

La version est affichée, pas exigée. Refus (code 3) si le protocole n'est pas 1 ou la taille pas 128 × 32 ; code 2 sans `HELLO`. Des octets sautés au départ sont possibles (ModemManager sonde un nouveau port) : sans gravité.

### A8. Remise à zéro à distance

Sans bouton, par l'interface de remise à zéro du SDK :

```bash
./i75w.sh to-bootsel
```

```bash
./i75w.sh list
```

Attendu : `000f` avec la série de la 75 W. Retour au firmware :

```bash
./i75w.sh run
```

```bash
./i75w.sh check
```

Attendu : `uptime` de quelques secondes et **`flags 0x00`** : un redémarrage volontaire ne met pas le bit 0 à 1 (§ 6.2, [BANC] à confirmer ici). Les flashages suivants se font sans bouton : `to-bootsel`, puis `flash`.

### A9. Essai de débit, carte seule

```bash
systemctl is-active ModemManager
```

```bash
./i75w.sh tx
```

10 s à 15 images/s, puis 10 s au maximum. `tx_bench.py` lit `STATS` après `HELLO` puis après chaque phase et affiche, par phase : `FRAME` reçus sur envoyés, octets reçus sur envoyés, octets sautés, CRC faux, longueurs refusées, échanges, images remplacées, plus long tour du cœur 0, cycles de rafraîchissement par seconde. Critère du § 11.3 : **≥ 15 images/s** dans la phase au maximum, et pendant chaque phase **aucun octet sauté, aucun CRC faux, aucune longueur refusée**, autant de `FRAME` reçus qu'envoyés. Messages possibles :

- « démarrage précédent par le chien de garde » à la première `STATS` : avertissement, l'essai continue ;
- « Essai non valable » : le bit 0 de `flags` est passé à 1 ou `uptime_ms` a reculé pendant l'essai (la carte a redémarré) ;
- « bits inconnus dans flags » : avertissement ;
- « STATS invalide » ou « TELEMETRY invalide » : réponse trop courte, comptée, la lecture continue.

Facultatif, phase lente seule et plus longue :

```bash
./i75w.sh tx --duration 60 --no-max
```

Copier toute la sortie dans le compte rendu.

### A10. Avec les dalles

**[Opérateur]** Passage aux dalles (encadré de sécurité) : arrêt du Pi, multiprise coupée, nappe HUB75 branchée, Pi et dalles remis sous tension ensemble. Refaire A1 (`hwd`), puis :

```bash
./i75w.sh check
```

Sans images du Pi, au bout de 3 s : **animation de secours**, « RASPYDARTS » orange qui défile de droite à gauche (teinte `0xFC00` à vérifier [BANC]).

```bash
./i75w.sh tx
```

Image visible pendant l'essai : barre blanche qui défile sur un dégradé ; au premier pixel, les octets `AA 55 03 03` (fausse synchronisation). Noter : photo, ordre et sens des dalles, couleurs, température donnée par `TELEMETRY`, cycles de rafraîchissement (environ 400/s attendus, [BANC]).

### A11. Dépannage

- **La carte ne s'énumère pas** (ni `0009` ni `000f`) : **[Opérateur]** BOOT + RST (A5), puis réinstaller (A6).
- **`check` : code 2** : carte absente, en double, ou pas de `HELLO` (firmware bloqué : le chien de garde de 2 s doit la relancer, `flags 0x01` à la `STATS` suivante).
- **`check` : code 3** : fabricant, produit, protocole ou taille inattendus : s'arrêter et demander.
- **Dalles noires** : luminosité 0 envoyée par le Pi, ou nappe d'entrée sur la mauvaise dalle ; réglages des dalles figés à la compilation (§ 8) : à revoir après l'essai.
- **Retour au MicroPython de Pimoroni** : `.uf2` téléchargé et contrôlé comme à l'annexe, § 3, puis BOOTSEL (`to-bootsel` ou BOOT + RST), puis :

```bash
./i75w.sh flash --micropython /tmp/bench_i75w/i75w_rp2350-v0.0.5-micropython.uf2 8de1b799249015beb4674b67cd0f605c21c0ce3b9bafb00ffb4afc8723b06bf4
```

### A12. Ce qui ne se vérifie que sur la carte

- Égalité entre la série du BOOTSEL et celle du firmware. Nom du port by-id sous le firmware C++ relevé le 25/11/2026 (complément 62) : `usb-RaspyDarts_RaspyDarts_DMD_<SERIE_I75W>-if00`, `ATTRS{serial}=="<SERIE_I75W>"` (majuscules).
- `picotool reboot -u -f --ser` sur le firmware C++ (picotool 2.1.1 : type de redémarrage `-u` avant la sélection `-f --ser` ; `-f -u --ser` est refusé, « unexpected option: --ser », banc du 25/11/2026) (interface de remise à zéro, `--ser` sur un appareil hors BOOTSEL) ; bit 0 de `flags` à 0 après un redémarrage volontaire.
- Débit tenu, `loop_max_us`, `refresh_hz`, images remplacées, température.
- Dalles : ordre, sens, couleurs, type de dalle, teinte de l'animation.

## Annexe : banc MicroPython du 23 novembre 2026

Banc jetable sous le **MicroPython de Pimoroni**, gardé pour mémoire : mire et première mesure de débit (20,1 images/s envoyées, 20,0 reçues, § 11.3). **Les commandes `./i75w.sh mp` et `./i75w.sh check` par `mpremote` citées ci-dessous n'existent plus** depuis M4-f2d (40e complément) ; pour rejouer ce banc, reprendre `i75w.sh` du commit `5796e44`. L'installation du MicroPython se fait maintenant par `./i75w.sh flash --micropython` (A11). Les renvois « § 1 » à « § 10 » de cette annexe désignent ses propres sections.

### 1. Copier le banc sur le Pi (depuis le Mac)

```bash
cd <DOSSIER-PROJET>
```

Adresse du Pi (`-4` pour `ssh` et `scp`, comme au § A2) :

```bash
PI=pi@raspydarts.local
```

```bash
ssh -4 $PI 'rm -rf /tmp/bench_i75w && mkdir -p /tmp/bench_i75w' && scp -4 shared/dmd_proto.py tools/bench_i75w/*.py tools/bench_i75w/i75w.sh $PI:/tmp/bench_i75w/
```

```bash
ssh -4 $PI
```

**Toutes les commandes suivantes se tapent sur le Pi**, dans ce dossier :

```bash
cd /tmp/bench_i75w && chmod +x i75w.sh
```

Numéros de série des deux cartes, sans valeur par défaut dans `i75w.sh` (complément 77) ; `<SERIE_PICO>` et `<SERIE_I75W>` : `ATTRS{serial}` relevés par `udevadm info` (casse indifférente pour `i75w.sh`). Seul `./i75w.sh list` marche sans eux :

```bash
export PICO_SERIAL=<SERIE_PICO> I75W_SERIAL=<SERIE_I75W>
```

```bash
picotool version && ~/mpremote-venv/bin/mpremote version
```

Attendu : `picotool v2.1.1`, et une version de `mpremote`. État de départ (le Pico 2 en `0005` avec son numéro de série, pas de 75 W) :

```bash
./i75w.sh list
```

### 2. 75 W en BOOTSEL et modèle de la puce

**[Opérateur]** Carte **seule** : bornes à vis vides, nappe HUB75 débranchée. Maintenir **BOOT**, brancher l'USB-C sur le Pi, relâcher BOOT.

```bash
./i75w.sh list
```

Attendu : une ligne `000f` (BOOTSEL du RP2350) portant le numéro de série de la 75 W, **`<SERIE_I75W>`** (variable `I75W_SERIAL` de `i75w.sh`). Toutes les commandes visent ce numéro, jamais « le premier appareil trouvé » ; `bootsel` échoue (code 2) s'il est absent ou présent deux fois.

```bash
./i75w.sh bootsel
```

```bash
./i75w.sh chip
```

Attendu : `série <SERIE_I75W> : type RP2350, boîtier QFN60, chipid …` (QFN60 = RP2350A, § 4). Noter le `chipid`.

Source du boîtier : `picotool info -d` (picotool 2.1.1, `main.cpp`) lit la ligne OTP `NUM_GPIOS`, gravée en usine, et affiche `package: QFN60` pour 30 GPIO, `QFN80` pour 48, `unknown` sinon (l'ancien calcul par `SYS_INFO_CHIP_INFO` est marqué « Not correct on A2 » dans ce code). `chip` refuse (code 3) tout autre type que `RP2350` et tout autre boîtier que `QFN60`, y compris un boîtier illisible.

**Si `chip` répond autre chose que RP2350 en QFN60 (ou ne trouve pas la carte) : arrêter la procédure ici et demander.** Une puce RP2040 serait l'ancienne 75 W (Pico W), avec un autre `.uf2` (`i75w_rp2040-…`) : ce choix n'est pas fait d'avance.

Informations complètes, pour le compte rendu :

```bash
./i75w.sh info
```

### 3. Télécharger le `.uf2` et relever son SHA-256

Firmware choisi pour un RP2350 : **`i75w_rp2350-v0.0.5-micropython.uf2`**, publication **v0.0.5** du dépôt `pimoroni/interstate75` (31 janvier 2025) : https://github.com/pimoroni/interstate75/releases/tag/v0.0.5. La variante `…-with-filesystem.uf2` ajoute les exemples de Pimoroni et efface le système de fichiers : inutile ici.

```bash
curl -L -o /tmp/bench_i75w/i75w_rp2350-v0.0.5-micropython.uf2 https://github.com/pimoroni/interstate75/releases/download/v0.0.5/i75w_rp2350-v0.0.5-micropython.uf2
```

```bash
sha256sum /tmp/bench_i75w/i75w_rp2350-v0.0.5-micropython.uf2
```

```bash
picotool info -a /tmp/bench_i75w/i75w_rp2350-v0.0.5-micropython.uf2
```

(Lecture du fichier seul, aucun appareil visé.) Attendu : `target chip: RP2350`.

**SHA-256 relevé au moment de l'essai (à écrire ici, puis commit) :**

| Date | Fichier | SHA-256 |
|---|---|---|
| 23/11/2026 | `i75w_rp2350-v0.0.5-micropython.uf2` (2 578 944 octets) | `8de1b799249015beb4674b67cd0f605c21c0ce3b9bafb00ffb4afc8723b06bf4` |

Aucune empreinte publiée par GitHub pour v0.0.5 ; contrôlés : taille (2 578 944 octets), date de l'asset (31/01/2025 17:43:32 UTC, déposé par `github-actions[bot]` du dépôt `pimoroni/interstate75`, publication signée `Gadgetoid`, membre de `pimoroni`), target chip (RP2350).

### 4. Installer le `.uf2`

La commande refuse si l'empreinte ne correspond pas, si le nom n'est pas un `i75w_rp2350-…-micropython.uf2`, si la puce n'est pas un RP2350 en QFN60, ou si la 75 W (`<SERIE_I75W>`) n'est pas en BOOTSEL une fois et une seule :

```bash
./i75w.sh flash /tmp/bench_i75w/i75w_rp2350-v0.0.5-micropython.uf2 <SHA-256 relevé>
```

La carte redémarre sous MicroPython. Puis :

```bash
./i75w.sh list
```

```bash
./i75w.sh check
```

Attendu : `contrôle bon : /dev/serial/by-id/usb-MicroPython_Board_in_FS_mode_<SERIE_I75W>-if00, Interstate 75 W with RP2350, identifiant <SERIE_I75W>` (dans le nom du port, la série est en minuscules). `port` et `check` visent ce seul port, construit depuis `I75W_SERIAL` en minuscules, et refusent (code 2) s'il est absent. `check` (donc `mp`) refuse aussi (code 3) si l'identifiant lu par `machine.unique_id()` diffère de `I75W_SERIAL`.

Port by-id et égalité série du BOOTSEL = chipid = `machine.unique_id()` : relevés le 23/11 (compte rendu, sans journal) ; reconfirmer au prochain banc :

```bash
ls /dev/serial/by-id/
```

Si le port de la 75 W n'y porte pas ce nom, ou si l'identifiant diffère de `<SERIE_I75W>` : s'arrêter, `i75w.sh` est à corriger.

Modules utilisés par le banc :

```bash
./i75w.sh mp exec "import hub75, picographics, interstate75, binascii, select, micropython, sys; print(sys.version); print(binascii.crc32(b'123456789') == 0xCBF43926, picographics.PEN_RGB565)"
```

Attendu : la version de MicroPython, puis `True` et un nombre.

### 5. Copier les scripts

```bash
./i75w.sh mp cp dmd_proto.py bench_config.py bench_fast.py rx_bench.py testcard.py :
```

```bash
./i75w.sh mp exec "import bench_fast; print('viper ok')"
```

Si viper échoue, le récepteur passe en Python pur (plus lent, affiché « python » sur la dalle).

### 6. Mire

La carte reste sur l'USB du Pi (seule source d'alimentation, bornes à vis vides). **[Opérateur]** Passage aux dalles (voir l'encadré de sécurité) : arrêt du Pi, multiprise coupée, nappe HUB75 branchée, Pi et dalles remis sous tension ensemble. Puis, sur le Pi :

```bash
./i75w.sh mp run testcard.py
```

Pages (5 s chacune, bouton **A** pour passer à la suivante) :

1. **Dalles** : à gauche fond rouge sombre et grand **1**, à droite fond bleu sombre et grand **2**, cadre blanc autour de chaque dalle. Coins de chaque dalle : **rouge** en haut à gauche, **vert** en haut à droite, **bleu** en bas à gauche, **jaune** en bas à droite. Le « 1 » à droite : nappe d'entrée sur l'autre dalle. Image à l'envers : dalles montées tête-bêche.
2. **Abscisses** : un trait tous les 8 pixels, long tous les 32, repères 0, 32, 64, 96 de gauche à droite, sans trou ni doublon à la jonction (x = 64).
3. **Couleurs** : barres rouge, vert, bleu, blanc ; sinon changer `COLOR_ORDER` dans `bench_config.py`.
4. **Balayage 1/16** : moitié haute rouge (lignes 0 à 15), moitié basse verte (16 à 31), traits blancs aux lignes 0, 15, 16 et 31. Moitiés mêlées ou lignes en double : balayage ou type de dalle à revoir.

Dalle noire ou lignes parasites : `PANEL_TYPE = "FM6126A"`, puis `STB_INVERT = True`. Pour modifier : arrêter la mire par Ctrl-C (dalles laissées sous tension), puis :

```bash
nano /tmp/bench_i75w/bench_config.py
```

```bash
./i75w.sh mp cp bench_config.py :
```

Noter : photo de chaque page, réglages retenus, température de la carte au toucher.

### 7. Installer le récepteur

USB sur le Pi ; dalles sous tension avec le Pi (suite du § 6), ou carte seule (nappe débranchée).

```bash
./i75w.sh mp cp rx_bench.py :main.py
```

```bash
./i75w.sh mp reset
```

Après 2 s, la dalle (si elle est alimentée) affiche `RX banc viper` et des compteurs à 0. À partir de là, **Ctrl-C ne l'arrête plus** (les pixels contiennent 0x03) et `./i75w.sh check` échoue : c'est attendu. Pour reprendre la console : **[Opérateur]** maintenir le bouton **A** et appuyer sur **RST** ; `main.py` rend alors la main.

### 8. Essai de débit

- **Essai B** (suite du § 6) : dalles sous tension avec le Pi. Image visible : barre blanche qui défile sur un dégradé.
- **Essai A** (facultatif) : carte seule, nappe débranchée, ce qui demande un passage par l'arrêt commun (encadré de sécurité). Mesure la réception seule (le pilote HUB75 tourne, sans dalle).

`hwd` ouvre aussi `dmd_device` (défaut `/dev/dmd`) depuis M4-d : l'arrêter pendant le banc (§ A1 ci-dessus ; `/dev/dmd` existe une fois la règle udev du complément 62 installée). ModemManager peut sonder un nouveau port au branchement (trames rejetées, comptées, sans gravité) :

```bash
systemctl is-active ModemManager
```

Port visé (by-id, construit depuis `I75W_SERIAL`) :

```bash
./i75w.sh port
```

Essai, 10 s à 15 images/s puis 10 s au maximum :

```bash
./i75w.sh tx
```

Facultatif, phase lente seule et plus longue :

```bash
./i75w.sh tx --duration 60 --no-max
```

Copier toute la sortie dans le compte rendu : `HELLO`, débit envoyé de chaque phase, cadence annoncée par la carte, images perdues, 5 V, température, résultat.

### 9. Ce qui ne se vérifie que sur la carte

- Prise en charge des dalles P4 64 × 32 au balayage 1/16 par le pilote HUB75 de Pimoroni, ordre des couleurs, type de dalle, sens et ordre des deux dalles.
- Nom du port by-id de la carte sous le MicroPython de Pimoroni (`usb-MicroPython_Board_in_FS_mode_<SERIE_I75W>-if00`, série en minuscules) et égalité entre le numéro de série du BOOTSEL et `machine.unique_id()` : relevés le 23/11 (compte rendu, sans journal) ; reconfirmer au prochain banc (`ls /dev/serial/by-id/`, `./i75w.sh check`).
- `sys.stdin.buffer.readinto`, `select.poll` sur la console USB, `sys.stdout.buffer.write` sans conversion de fin de ligne, `micropython.kbd_intr(-1)` avec des 0x03 dans les pixels.
- Disponibilité de viper (`bench_fast.py`) et de `PEN_RGB565` avec l'affichage `DISPLAY_INTERSTATE75_128X32`.
- Tension 5 V par `GPIO28` (diviseur par 2, d'après le § 4 : à confirmer ; c'est le 5 V de l'USB du Pi) ; température par le capteur interne (formule du RP2040, approximative sur le RP2350).
- Débit réel : il dépend du port USB du Pi, du câble et du MicroPython ; **le chiffre du § 11.3 reste à mesurer avec le programme C++**.

### 10. Après l'essai

Laisser la carte telle quelle (récepteur en `main.py`) : le firmware C++ de M4 remplacera tout au flashage. Sur le Pi, seul `/tmp/bench_i75w` a été créé.
