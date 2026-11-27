# firmware/i75w_display

Firmware **C++** de la carte Interstate 75 W (spécification § 6 et § 8), avec le **Pico SDK** et le **pilote HUB75 de `pimoroni-pico`** (licence MIT, mention de copyright conservée). Jalon **M4** : la bibliothèque portable `lib/` et ses tests sur PC sont faits (M4-f1) ; M4-f2a ajoute le `CMakeLists.txt` de la carte, l'USB sans stdio, le chien de garde, un `main` minimal et le job CI `i75w-uf2` ; M4-f2b ajoute le pilote HUB75 copié et réduit, le cœur 1, le triple tampon `Pixel` et la luminosité (découpage : spécification § 8, 36e complément ; détails : 37e complément).

- **Cœur 0** : réception USB CDC (TinyUSB), trames, CRC-32, resynchronisation, décodage des zones dans le tampon arrière, réponses, télémétrie, animation de secours.
- **Cœur 1** : rafraîchissement des dalles par PIO et DMA, triple tampon `Pixel` du pilote, échange à la fin d'un cycle complet, luminosité (M4-f2b).

Arborescence :

```
CMakeLists.txt     compilation du .uf2 (Pico SDK 2.1.0, PICO_BOARD=pico2_w)
src/               main.cpp (cœur 0), board.hpp (broches), tusb_config.h et usb_descriptors.c (USB),
                   sdk_shim/ (en-tête pour reset_interface.c du SDK), display.* (cœur 1),
                   hub75.hpp, hub75.cpp (MIT, Pimoroni) et hub75.pio (BSD-3-Clause) : pilote copié et réduit
lib/               protocole portable, sans matériel : trame, CRC-32, resynchronisation, zones,
                   STATS, animation de secours et sa police
tests/             tests sur PC de lib/, vecteurs produits par shared/dmd_proto.py
external/          clones locaux du Pico SDK et de picotool, versions figées (ignoré par git)
```

La CI (job `i75w-uf2`, après `i75w-host-tests`) compile le `.uf2` et le publie comme artefact téléchargeable, avec son `.sha256`, gardé 30 jours. Le flashage reste manuel, avec l'accord de l'auteur.

## Firmware `src/` (M4-f2a à M4-f2c)

- **USB** (§ 8, 36e complément) : `2e8a:0009`, fabricant « RaspyDarts », produit « RaspyDarts DMD », numéro de série = identifiant de la puce (hexadécimal majuscule). Interfaces 0 et 1 : CDC (trames du § 6) ; interface 2 : remise à zéro du SDK (`picotool reboot -f`), pilote `reset_interface.c` du SDK compilé avec le firmware. **Ni stdio USB ni stdio UART** (l'UART par défaut est sur GPIO 0 et 1, lignes HUB75) ; pas de remise à zéro à 1 200 bauds. `tud_task()` dans la boucle du cœur 0.
- **Réponses** : une trame n'est jamais coupée ; sans DTR, ou sans place libérée en 20 ms, elle est abandonnée entière.
- **Broches au démarrage** : OE au niveau haut puis forcé au niveau haut (dalles éteintes) ; autres lignes HUB75 au niveau bas ; boutons A et B en tirage interne vers le haut ; LED (GPIO18) et WL_ON (GPIO23) au niveau bas.
- **Chien de garde** : 2 s, nourri par le cœur 0 seulement si le compteur de vie du cœur 1 a avancé (`lib/liveness.hpp`) ; ce compteur avance à chaque interruption `DMA_IRQ_0` du rafraîchissement.
- **Pilote HUB75** (`src/hub75.*`, pimoroni-pico `39b017a`, § 8, 36e et 37e compléments) : sans `pico_graphics`, FM6126A, strobe inversé, allocation ni `stop()` ; corrections : niveaux de repos de STB et de OE posés avant le passage en sortie, premier DMA sur `back_buffer`, OE mis au niveau haut côté PIO avant `pio_gpio_init` et forcé au niveau haut à la fin de `start()` (`gpio_set_function` du SDK efface le forçage de sortie). `pio0` (machines 0 et 1), un canal DMA, `DMA_IRQ_0` sur le cœur 1 ; diviseur de la machine de données à 1 [BANC].
- **Affichage** (`src/display.*`) : trois tampons `Pixel` de 16 Ko mis à zéro avant `start()` ; le cœur 0 convertit l'image (`lib/pixel.hpp`) dans un tampon libre et la publie (`lib/triple.hpp`), sans jamais attendre ; le cœur 1 prend le tampon en attente à la fin d'un cycle complet (16 lignes × 10 plans, environ 2,5 ms estimées [BANC]). Images publiées : l'image affichée à chaque échange et sur `CLEAR` (accroche de `Card`), du noir pendant l'animation locale (l'animation vient en M4-f2c), l'image affichée au retour en mode 0.
- **Luminosité** : publiée par le cœur 0 (`std::atomic<uint8_t>`), appliquée par le cœur 1 en fin de cycle : largeur OE = `ceil(b × 6 / 255)` ; 0 = OE forcé au niveau haut. Le forçage de OE n'est levé qu'à la fin du premier cycle complet, et seulement si la luminosité est > 0.
- **Mesures** (`lib/measures.hpp`) : 5 V par GPIO28 (ADC2) et température interne, toutes les 250 ms.
- **Animation de secours** (M4-f2c, § 8, 38e complément) : sur `MODE 1` ou après 3 s sans image, « RASPYDARTS » en orange (`0xFC00`, teinte à vérifier au banc), police 5 × 7 doublée, défilement de droite à gauche à 30 px/s, boucle de 246 px qui repart du bord droit à chaque entrée. Dessin (`lib/fallback.*`) sur le cœur 0 dans un tampon RGB565 statique de 8 Ko, publié par le triple tampon seulement quand la position change ; la luminosité du Pi s'applique (255 au démarrage). `CLEAR` pendant l'animation n'efface que les tampons du Pi ; au retour en mode 0, les dalles montrent l'image affichée du Pi.
- **`STATS`** (M4-f2c, § 6.2) : réponse à `STATS_REQ` construite par `Card` ; `main.cpp` fournit par une accroche `replaced` (triple tampon), `loop_max_us` (plus long tour de la boucle du cœur 0, remis à zéro à chaque `STATS`), `refresh_hz` (cycles complets pendant la dernière seconde) et `flags` (bit 0 : `watchdog_enable_caused_reboot()`, lu avant `watchdog_enable()`).

Compilation (comme la CI ; le Mac de l'auteur n'a ni CMake ni compilateur ARM : le `.uf2` n'est compilé que par la CI) :

```
cmake -S firmware/i75w_display -B build/i75w-fw -DPICO_SDK_PATH=<pico-sdk 2.1.0> -Dpicotool_DIR=<picotool 2.1.0 installé>/picotool
cmake --build build/i75w-fw
```

## Bibliothèque `lib/` (M4-f1)

C++17, sans SDK ni allocation dynamique ; même comportement que `shared/dmd_proto.py` et `tools/fake_i75w.py`.

| Fichier | Rôle |
|---|---|
| `proto.hpp` | Constantes du § 6 (types, longueurs, codes `ERROR`, tuiles) |
| `crc32.*` | CRC-32 « ZIP », table calculée à la compilation |
| `decoder.*` | Récepteur (§ 6.1) : tampon fixe de 8 203 octets, resynchronisation, compteurs exacts de `Decoder` ; résultat indépendant du découpage des octets |
| `delta.*` | En-tête et application d'un `DELTA` (§ 6.4.1) |
| `card.*` | Machine d'état de la carte : double tampon RGB565, échange et recopie, numérotation, images perdues, mode, luminosité, réponses ; horloge injectée (µs) pour les 3 s de l'animation locale et la `TELEMETRY` |
| `measures.hpp` | Conversions des mesures : 5 V (GPIO28, pont ÷ 2) et température interne (M4-f2a) |
| `liveness.hpp` | Porte du chien de garde : nourri seulement si le cœur 1 progresse (M4-f2a) |
| `gamma.hpp` | Table gamma 10 bits du pilote de Pimoroni (licence MIT, `39b017a`) (M4-f2b) |
| `pixel.hpp` | RGB565 → `Pixel` du pilote (réplication des bits hauts, gamma), lignes N et N + 16 entrelacées, largeur OE (M4-f2b) |
| `triple.hpp` | Triple tampon sans verrou : un écrivain (cœur 0), un lecteur (cœur 1), images remplacées comptées (M4-f2b) |
| `font5x7.hpp` | Police 5 × 7 de l'animation : les 7 glyphes R, A, S, P, Y, D, T, dessinés pour ce dépôt (M4-f2c) |
| `fallback.*` | Animation de secours : position dans le temps, dessin dans une image RGB565 fournie, redessin seulement quand elle bouge (M4-f2c) |
| `lib.cmake` | Cible `i75w_lib`, incluse par les tests et par le firmware |

Interface de `Card` pour `src/` : `feed(octets, n, maintenant_µs, puits)` à chaque réception USB, `tick(maintenant_µs, puits)` dans la boucle du cœur 0 ; les réponses sont écrites trame par trame dans un `ByteSink` ; `set_stats_hook()` fournit les champs de `STATS` propres à la carte (`BoardStats`) ; `set_swap_hook()` est appelée à chaque échange, avant la recopie dans le tampon arrière et sur `CLEAR` (le cœur 0 y convertit l'image dans le triple tampon du pilote, sans attendre) ; `mode()` et `front()` disent quoi afficher.

## Tests sur PC

Avec CMake (comme la CI, job `i75w-host-tests`) :

```
cmake -S firmware/i75w_display/tests -B build/i75w
cmake --build build/i75w
ctest --test-dir build/i75w --output-on-failure
```

Sans CMake, par le compilateur seul, depuis `firmware/i75w_display` (bash) :

```
F="-std=c++17 -Wall -Wextra -Werror -O2 -pthread -Ilib -Itests"
for t in test_lib test_card test_timing test_board test_display test_fallback; do c++ $F lib/*.cpp tests/runner.cpp tests/$t.cpp -o /tmp/$t || break; done
/tmp/test_lib tests/vectors/dmd_lib.txt && /tmp/test_card tests/vectors/dmd_card.txt && /tmp/test_timing && /tmp/test_board && /tmp/test_display && /tmp/test_fallback
```

`test_lib` (contre `dmd_lib.txt`, plus le même récepteur nourri par morceaux de 1 à 8 204 octets), `test_card` (contre `dmd_card.txt`), `test_timing` (horloge simulée : 3 s, `MODE`, `TELEMETRY`, accroche d'échange, y compris sur `CLEAR`, `STATS` champ par champ), `test_board` (mesures, porte du chien de garde), `test_display` (table gamma, RGB565 → `Pixel`, entrelacement, largeur OE, triple tampon, dont un essai à deux fils d'exécution), `test_fallback` (police, position dans le temps, dessin, découpe aux bords, redessin). Lanceur maison sans dépendance (`tests/runner.*`).

ThreadSanitizer (spécification § 8, 53e complément) : le job `i75w-host-tsan` compile `test_display` avec `-fsanitize=thread` et l'option CMake `I75W_TSAN=ON`, qui ajoute le témoin `tests/tsan_canary.cpp` (hors de `ctest`, accès concurrent volontaire : TSan doit le signaler, code 66 avec `TSAN_OPTIONS="halt_on_error=1 exitcode=66"`).

## Vecteurs de test (spécification § 8, 33e complément)

Produits par `tests/gen_vectors.py` à partir de `shared/dmd_proto.py` et de `tools/fake_i75w.py`, sans horloge, avec un générateur pseudo-aléatoire maison (même résultat sur toutes les versions de Python) :

```
python3 firmware/i75w_display/tests/gen_vectors.py          # réécrit les fichiers
python3 firmware/i75w_display/tests/gen_vectors.py --check  # code 1 s'ils ne sont pas à jour
```

Le test `tests/test_dmd_vectors.py` (lancé par la CI avec les autres) les régénère en mémoire et les compare octet par octet, puis les relit et les rejoue avec `dmd_proto.py` et `fake_i75w.py`.

Format commun : texte ASCII, fins de ligne LF, moins de 1 Mo en tout ; une ligne = un mot-clé puis des champs séparés par une espace ; lignes vides et lignes `#` ignorées ; octets en hexadécimal minuscule sans séparateur, `-` pour aucun octet ; CRC-32 « ZIP » sur 8 chiffres hexadécimaux ; nombres en décimal, sauf type, code et masque (hexadécimal).

`tests/vectors/dmd_lib.txt` (bibliothèque) :

| Ligne | Sens |
|---|---|
| `crc <octets> <crc32>` | CRC-32 des octets, valeur initiale 0 (dont `313233343536373839` → `cbf43926`) |
| `image <nom> <8 192 octets>` | Image RGB565 de base des cas `delta` |
| `decoder <nom>` | Nouveau récepteur (§ 6.1), vide |
| `feed <octets>` | Un appel de réception |
| `frame <type> <longueur> <crc32 des données>` | Trames rendues par le `feed` qui précède, dans l'ordre |
| `counters <crc> <longueur> <sautés> <trames> <en attente>` | Fin du cas : compteurs exacts de `Decoder` (CRC faux, longueurs au-delà du maximum du type, octets sautés, trames rendues, octets gardés en attente, dont un `0xAA` final) |
| `delta <nom> <image de base> <données>` | Décodage d'un `DELTA` (§ 6.4.1) appliqué à une copie de l'image |
| `result ok <numéro> <base> <masque> <crc32 de l'image>` / `result error <code>` | Résultat attendu (`02` : longueur ; `04` : contenu invalide) |

`tests/vectors/dmd_card.txt` (scénarios de la carte, sans animation locale ni `TELEMETRY`) :

| Ligne | Sens |
|---|---|
| `scenario <nom>` | Carte neuve, juste démarrée |
| `feed <octets>` | Octets reçus du Pi en un appel |
| `reply 81 <protocole> <largeur> <hauteur>` | `HELLO` (version du firmware et tension non comparées) |
| `reply 86 <octets>` | `PONG` |
| `reply e0 <code>` | `ERROR`, code seul (texte non comparé) |
| `reply 88 <longueur> <octets 4 à 35>` | `STATS` : longueur (47) et champs `frames_rx` à `swaps` ; `uptime_ms` et champs propres à la carte non comparés (38e complément) |
| `state <affiché> <arrière> <numéro> <perdues> <échanges> <delta_ignorés> <mode> <attend_frame> <luminosité>` | État après le `feed` : CRC-32 des deux tampons, numéro affiché (`-` : aucun), images perdues (B5), échanges de tampons, `DELTA` ignorés, mode, 1 si les `DELTA` sont ignorés jusqu'au prochain `FRAME`, luminosité |
