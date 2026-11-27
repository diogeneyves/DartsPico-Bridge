# Interface matérielle de RaspyDarts : cartographie et proposition (jalon M2, phase 1)

**Statut : cartographie validée par l'auteur le 4 novembre 2026.** Toutes les décisions sont prises (§ 8), dont D4 : pas de monnayeur en mode Pico.

Analyse faite sur RaspyDarts `85c9f81` (`V4.6.1_202610131831`), repères mis à jour pour `5ae22c5` (4.6.3) le 7 novembre, puis pour `5c85f6a` (`V4.6.4_202611132000`) le 18 novembre 2026, voir [`UPSTREAM.md`](UPSTREAM.md).
Les références `fichier:ligne` renvoient à `5c85f6a` (copie `~/RaspyDarts-ref`, ancienne référence ; les repères de la 4.6.5 sont notés « en 4.6.5 »). Abréviations :
`R` = `raspydarts.py`, `CR` = `include/craspberry.py`, `CG` = `include/cgpio_extender.py`,
`CT` = `include/ctarget_reader.py`, `CD` = `include/cdmd.py`, `CE` = `include/cevent.py`,
`CS` = `include/cscreen.py`, `CC` = `include/cconfig.py`, `CO` = `include/ccoins.py`,
`ST` = `addons/CStrip.py`.

Décisions possibles pour chaque élément : **Reprise** (inchangé), **Adaptation** (même interface, autre implémentation), **Suppression** (sans objet sur le nouveau matériel).

---

## 1. Vue d'ensemble

Trois constats orientent toute la suite.

1. **`Craspberry` ne gère pas que le matériel.** Il lit aussi le clavier, la souris et l'écran tactile (`CR:1024-1161`, `real_key`), les pavés USB (`CR:246`, `cusbpads`) et le monnayeur (`CR:257`, `ccoins`). Le jeu appelle `rpi.listen_inputs()` depuis 59 endroits (`R`, `CS`, `trophees.py`, `caccount`, `cchat`, `cexplorer`, `cmenueditor`, `games/sport/Football.py`). **On garde donc la classe `Craspberry`**, et on ne remplace que ses deux sources matérielles :
   - `self.gpio`, l'objet `Gpio_extender` des MCP23017 (`CR:244`) ;
   - la lecture de la cible, faite par le processus `ctarget_reader.py` (`CR:317-395`).
2. **La boucle du jeu lit l'état des entrées en continu, et non des événements.** `listen_inputs` tourne en boucle (`CR:893-1022`) et relit à chaque tour les MCP23017 (`CG:279-332`) et les GPIO (`CR:698-741`). Depuis la 4.6.3, `read_entries` en tire elle-même un appui par passage de relâché à appuyé, la répétition des flèches et le « bannissement » d'un bouton collé (§ 4, risque R2). La nouvelle couche doit donc fournir **un état** (le masque des boutons appuyés) **plus un verrou** qui garantit qu'un appui bref est vu au moins une fois.
3. **La cible passe déjà par une file d'attente.** Depuis la version actuelle, un processus à part (`ctarget_reader.py`) envoie un code par fléchette (`"2410"`) dans `self.target_darts` (`CR:197`, `CR:346-358`). Le jeu ne fait que `take_dart()`, `wait_dart()` et `hold()` (`CR:374-410`). Il suffit de remplir cette même file avec les fléchettes du Pico. **Format de la file depuis RaspyDarts 4.6.5** (`8fa61ff`) : un couple `(code, instant)` par fléchette, l'instant en `time.monotonic()` ; le lecteur écrit `"2610 123456789012"` (code et `time.monotonic_ns` du contact, `ctarget_reader.py:220-223` en 4.6.5), `receive_darts` en fait le couple (`craspberry.py:363-381` en 4.6.5), et `take_dart` le dépaquette (`code, self.dart_time = dart`, `craspberry.py:408` en 4.6.5). Un code seul dans la file fait tomber `take_dart`. En mode Pico, l'instant est celui de la réception par `include/cpico.py` (spécification, 19e complément de la v0.3.14).

Les LED et le DMD passent par MQTT (`CE`) vers des serveurs séparés (`addons/*_Server.py`). Le jeu n'y touche pas directement.

---

## 2. Ce que le jeu demande à la couche matérielle

### 2.1 Création et câblage des objets

| Élément | Où | Rôle actuel | Nouveau système |
|---|---|---|---|
| `cevent.Event(logs, config_events, subscribers, broker)` | `R:152` | Client MQTT unique (« dispatcher »), sujets `raspydarts/targetLeds`, `stripLeds`, `dmd`, `matrix`, `other` (`CE:41-45`) | **Reprise** |
| Abonnés `'StripLeds'`, `'TargetLeds'` | `R:134-138` | Noms qui ne correspondent pas à ceux qu'attend `Event` (`'Strip'`, `'Target'`, `CE:57-67`) : les drapeaux sont ensuite forcés à la main (`R:178`, `R:199`) | **Reprise**. Comportement implicite à connaître, sans effet |
| Lancement des serveurs LED, `sudo python3 addons/StripLeds_Server.py …` et `TargetLeds_Server.py` | `R:167-204` | Seulement si `PIN_STRIPLED` ou `PIN_TARGETLED` vaut 10, 12, 18 ou 21 (broches PWM ou SPI du Pi) | **Adaptation** : condition « carte Pico » et plus de `sudo` (§ 2.6) |
| `craspberry.Craspberry(logs, Config, config_target_leds, dispatcher, t_event)` | `R:277`, nouvel essai en `R:284` | Créé **deux fois** si la première création échoue ; `rpi_error` est alors affiché par `display.mcp_error` (`R:328-330`, `CS:464`) | **Adaptation** (§ 5.3) ; le lien au Pico doit être unique, comme `ccoins.shared` (`CO:62`) |
| `dispatcher.add_rpi(rpi)` | `R:290` | Permet à `Event` d'allumer des voyants et de lancer des stroboscopes (`CE:425-446`) | **Reprise** |
| `cdmd.Cdmd(logs, dispatcher, File)` | `R:293` | Messages DMD sur MQTT | **Reprise** (§ 2.7) |
| `rpi.set_play_sound`, `rpi.set_send_insult` | `R:325-326` | Rappels utilisés par les minuteurs d'événements (`CR:1077-1085`) | **Reprise** |
| `rpi.on_coins`, `rpi.target_enabled`, `rpi.set_intro_end`, `rpi.on_music` | `CS:226-236`, `CS:994`, `CS:1044` | Monnayeur, cible ignorée pendant la vidéo d'introduction, lecteur de musique | **Reprise** |

### 2.2 Cible

| Élément | Où | Rôle actuel | Nouveau système |
|---|---|---|---|
| `[Raspberry_BoardPinsIns]` (`PIN_1` à `PIN_16`), `[Raspberry_BoardPinsOuts]` (`PIN_1` à `PIN_10`) | `CC:470-477`, lus en `CR:188-206` | Broches GPIO des lignes et des colonnes de la matrice | **Suppression** en mode « carte Pico » (sections laissées vides) |
| `ctarget_reader.py` (`--lines`, `--columns`, `READY`, un code par ligne) | `CT:190-266`, lancé par `CR:317-344` | Seul processus qui touche les GPIO de la cible | **Suppression** en mode Pico : le balayage est fait par le firmware |
| `self.target_darts` (file), `receive_darts` (fil d'exécution) | `CR:197`, `CR:346-358` ; en 4.6.5 `craspberry.py:202`, `:363-381` | Codes reçus du lecteur ; en 4.6.5, couples `(code, instant time.monotonic())` | **Adaptation** : file remplie par la couche Pico, mêmes couples (instant de réception) |
| `self.target_reader` | `CR:196`, testé en `CR:403`, `CR:430`, `CR:560`, `CR:600`, `CR:974`, `CR:1013` | Non `None` = « la cible est lue par un processus » : active `hold()` et `wait_dart()` | **Adaptation** : objet de la couche Pico (non `None` quand le Pico est connecté) |
| `take_dart()`, `wait_dart(delay)`, `hold(delay)` | `CR:374-410` ; `hold(1.5)` en `R:1861` ; en 4.6.5 `craspberry.py:396-430` | Lecture non bloquante, attente bornée, maintien du nom du joueur ; en 4.6.5, `take_dart` met l'instant dans `dart_time` (`dart_before_targets_changed`, jeux contre la montre) | **Reprise** (même file, test `DartsReadByTheGame` de `tests/test_cpico.py` contre la base) |
| `read_target(raw)`, `test_target()` | `CR:595-646`, `CR:556-593` | Code → segment par `self.keys` ; `raw=True` pour l'assistant | **Reprise** du chemin « lecteur » ; le chemin GPIO devient inutile |
| `[SectionKeys]` : segment → code (`"2410"` = broche d'entrée, puis broche de sortie) | `CC:489-491`, `CR:226-237`, `CR:487-497` | Correspondance contact → segment | **Adaptation** : code **`R<ligne>C<colonne>`** (par exemple `R3C4`), au lieu d'un fichier `target_ed110.yaml` (décision D1) |
| `calibrate_target()`, `calibration_needed()`, `calibration_only()` | `CS:1266-1324` | Étalonnage dans le jeu : `listen_inputs(context='wizard')` renvoie le code brut | **Reprise** de l'écran ; **adaptation** de `calibration_needed`, qui teste `rpi.inputs` et `rpi.outputs` (vides en mode Pico) |
| Assistant de câblage GPIO, `init_gpio`, `set_inputs`, `set_outputs`, `*_conf` | `CS:1443-1493`, `CS:1531`, `CS:1604-1637`, `CR:445-530` | Saisie des numéros de broche | **Suppression** en mode Pico (étapes masquées) |
| `richard-mode` (`old_detection`) | `CR:290`, `CR:617` | Ancien balayage | **Suppression** (sans objet) |
| `target_enabled` | `CR:273`, `CR:972-976` | Fléchettes de l'intro jetées | **Reprise** |

### 2.3 Boutons

| Élément | Où | Rôle actuel | Nouveau système |
|---|---|---|---|
| `[Raspberry]` : `PIN_UP` … `PIN_CPTPLAYER` → broche de MCP (`A0`…`D7`) | `CC:216-…`, `CG:60-111` (`DEFAULTS`) | Correspondance fonction → broche | **Adaptation** : valeurs `BTN1`…`BTN15` (§ 3.3 de la spécification), préréglage « carte Pico » |
| `Gpio_extender.read_entries()` | `CG:279-332`, appelé en `CR:919` | Relit tout l'état ; renvoie le bouton **qui vient d'être appuyé** (rebonds de moins de 50 ms ignorés), ou une flèche maintenue (répétée après 0,5 s, puis toutes les 0,15 s) ; bannit un bouton maintenu plus de 10 s, jusqu'à son relâchement | **Adaptation** : même logique, sur le masque des boutons reçu du Pico, plus le verrou |
| `test_entries()` | `CG:334-350`, `CR:759`, `CR:913` | Liste des broches appuyées (`['A0', 'B1']`), menu « test des boutons » (`CS:6002-6028`) | **Adaptation** : noms `BTN1`… |
| `banned_button`, `banned_gpio` | `CG:190`, `CR:192`, affichés en `CS:9134-9139` | Message « bouton banni » au menu principal | **Reprise** de l'attribut |
| `mcp1`, `mcp2`, `chips`, `all_io`, `buttons_list`, `toys_list`, `pin_switcher`, `decode()` | `CG:126-265` ; `mcp1` testé en `CR:911`, `CS:6036`, `CS:1672` | « Il y a une carte d'extension » | **Adaptation** : `mcp1` non `None` quand le Pico est connecté ; `decode()` pour le monnayeur (§ 2.5) |
| `set_io(buttons)`, `get_defaults()`, `set_defaults()` | `CG:236-277`, `R:1161-1170`, `CS:6061` | Menu de configuration des boutons (broches `A0`…`D7`, `CS:6032-6067`) | **Adaptation** : listes `BTN1`…`BTN15`, `T1`…`T8`, `V1`…`V12` (décision D5) |
| Boutons sur GPIO du Pi : `PLAYERBUTTON`, `GAMEBUTTON`, `BACKUPBUTTON`, `EXTRABUTTON`, `MISSDART`, `SHOCKSENSOR` dans `[SectionKeys]` | `CC:480-487`, `CR:229-233`, `read_buttons` en `CR:698-741` | Boutons câblés directement sur le Pi | **Suppression** en mode Pico (plus aucun GPIO utilisé) |
| `PIN_MISSDART` (sur MCP) | `CG:78`, `CR:915-932` | Capteur de choc : `miss = 1`, puis au plus 10 tours (environ 30 ms) d'attente d'une fléchette, sinon `'MISS'` (`CR:1019-1022`) | **Adaptation** : événement `S` du Pico, avec fusion choc/fléchette dans `hwd` (risque R1, décision D3) |
| `PIN_MUSIC_*` (6) | `CG:79-84`, `CR:828` | Lecteur de musique | **Suppression** sur le Pico (pas de broche) ; restent possibles par les pavés USB |
| Répétition du bouton maintenu | `CR:886`, `CR:929` (`self.sleep`, `config.delay` = 0,1 s, `CC:1127-1130`) | Effet de la lecture d'état | **Reprise** du comportement (risque R2) |
| Lumière de navigation à chaque appui : `strobe_toys(['LIGHT_NAVIGATE'], 2)`, `LIGHT_PLAYERS` | `CR:925-928` | Retour visuel | **Reprise** |

### 2.4 Toys et voyants

| Élément | Où | Rôle actuel | Nouveau système |
|---|---|---|---|
| `TOY1` … `TOY16` | `CG:95-110` | Sorties de MCP | **Adaptation** : `TOY1`…`TOY8` → T1…T8 ; `TOY9`…`TOY16` **ignorés sans rien envoyer** (P4) |
| `LIGHT_*` (10) : `NEXTPLAYER`, `BACK`, `NAVIGATE`, `VALIDATE`, `LASER`, `FLASH`, `PLAYERS`, `CELEBRATION`, `CELEBRATION2`, **`LIGHT_LIGHT`** | `CG:85-94`, `CC:241-248` | Voyants | **Adaptation** : V1…V10 selon la correspondance proposée (§ 3.4). **Attention** : le jeu écrit `LIGHT_LIGHT` (`R:1800`), alors que `pinout.json` écrit `LIGHT` (décision D6) |
| `light_toys(names, state, delay, raw)` | `CR:1396-1423`, 30 appels dans `R`, `CS:12175`, `CE:426` | Allume ou éteint ; `delay` programme un minuteur pygame qui éteint `FLASH`, `CELEBRATION` et `CELEBRATION2` (`CR:100-109`, `CR:1050-1060`) | **Adaptation** de `gpio.light_toys` → `TOY` / `LGT`. **Reprise** des minuteurs pygame |
| `strobe_toys(names, iterations, delay_on, delay_off)` → liste de fils d'exécution | `CR:1425-1432`, `CG:352-365` et `CG:394-412` ; fils attendus par `join()` en `R:2391-2393` | Un fil par sortie, qui **inverse** l'état courant (`not pin.value`) | **Adaptation** → `TOYPULSE` / `LGTPULSE`, et renvoi d'un fil qui dure le temps du stroboscope, pour que `join()` attende comme avant (risque R4) |
| `gpio.blink_toy(led, cycles, on_ms, off_ms)` (4.6.4) | `CG:367-392`, appelé en `CR:1394` et `CS:9738` | LED d'un bouton qui clignote à partir de son état (`cbuttonleds.py`, réglages dans `button_leds.json`) | **Adaptation** : `PicoExtender.blink_toy`, mêmes états et mêmes attentes (test différentiel) |
| `gpio.hold_validate`, `PIN_CAPTURE_END` (4.6.4) | `CG:279-332`, mis par `include/ccapture.py:184` et `:330` | Mode capture : Valider maintenu 2 s termine la capture | **Adaptation** : même logique dans `PicoExtender.read_entries` (test différentiel) |
| `ci2c.follow_wiring` (4.6.4) | `R:270`, `include/ci2c.py` | Au démarrage, active ou coupe le bus I2C dans `config.txt` (`sudo`), puis demande un redémarrage | **Neutralisé en mode Pico** (patch 0003) : ni `config.txt`, ni `sudo`, ni redémarrage |
| `test_toys_menu` | `CS:6178-6243` | Liste `rpi.buttons` (`LIGHT_*`, `TOY*`), puis `gpio.strobe_toys(…, 1200, 400)` | **Adaptation** (noms `T1`, `V1`…) |
| `gpio_flush()` → `GPIO.cleanup()` | `CR:550-554`, `R:2876`, `CS:1609` | Libère les GPIO en quittant | **Adaptation** : tout éteindre (Toys, voyants, LED), puis se déconnecter |
| `self.gpio.light(light)` | `CR:888-889` | Méthode **absente** de `Gpio_extender` ; jamais appelée (aucun `listen_inputs(…, light=…)` dans le dépôt) | **Reprise** telle quelle (code mort) |

### 2.5 Monnayeur

| Élément | Où | Rôle actuel | Nouveau système |
|---|---|---|---|
| `ccoins.shared(logs, self.gpio)` ; `start_mcp`, `read_mcp` | `CR:257`, `CO:62`, `CO:193-235` | Lit **directement les registres** d'une broche de MCP (`extender.chips[index]._read_u8`, interruption sur changement, toutes les 20 ms, sous `I2C_LOCK`) | **Suppression en mode Pico (D4, décidé)** : sur la nouvelle carte, aucune entrée de MCP n'est libre (U4 GPA7 et GPB7 jamais en entrée ; U9 et U10 n'ont que des sorties ou des broches non reliées). Le patch du jeu désactivera `ccoins` en mode Pico |

### 2.6 LED (serveurs de `addons/`)

| Élément | Où | Rôle actuel | Nouveau système |
|---|---|---|---|
| `[Raspberry_Leds]` : `PIN_TARGETLED`, `BRI_TARGETLED`, `TYP_TARGETLED`, `PIN_STRIPLED`, `NBR_STRIPLED`, `BRI_STRIPLED`, `TYP_STRIPLED` | `CC:206-214` | Broche, luminosité, ordre des couleurs | **Adaptation** : canaux 0 et 1 (§ 9.2), nombre de LED, `LEDB`, `led<n>_order` |
| `[LEDTarget]` : segment → numéros de LED | `CC:493-495`, nombre de LED en `CR:209-212` | Plan des LED de la cible | **Reprise** |
| `TargetLeds_Server.py`, `StripLeds_Server.py` | `addons/` | Clients MQTT, une animation par fil d'exécution, interrompue par `t_event` | **Reprise** sans modification |
| `CStrip` : `PixelStrip(…)`, `begin`, `setPixelColor`, `getPixelColor`, `show`, `setBrightness` de **`rpi_ws281x`** | `ST:15`, `ST:45-49`, `ST:309-379` | Seul point de contact avec le matériel | **Adaptation** : module de remplacement `rpi_ws281x` (mêmes noms), qui publie l'image vers `hwd` à chaque `show()` (§ 5.4) |
| Sous-processus avec `sudo` | `R:170`, `R:189` | Accès DMA du Pi | **Suppression** du `sudo` |
| `Matrix_Server.py` (MAX7219 en SPI), `Hue_Server.py`, `Dmx_Server.py`, `Other_Server.py` | `addons/` | Hors périmètre (§ 1 de la spécification) | **Reprise** |

### 2.7 DMD et événements

| Élément | Où | Rôle actuel | Nouveau système |
|---|---|---|---|
| `dmd.send_text`, `send_score`, `send_insult` → `msg|…`, `msgmove|…`, `score|…` | `CD:48-220` (dont `msgcolor|…` et `score|…||R;G;B`, couleur du joueur, option `dmd_player_color`, depuis la 4.6.3) ; 18 appels dans `R` | Publié sur `raspydarts/dmd` (`CE:247-258`) | **Reprise** : le service de rendu (M4) s'abonne à `raspydarts/dmd` |
| `dmd.game_start`, `game_end`, `score_text`, `send_miss` (4.6.4) | `CD:151-220`, `include/cdmdscore.py`, `R:1690`, `R:2894` | Styles de score : `score|` (classique), `score2|` (moderne), `score1|` (« canard »), tous `…|X - X - X||R;G;B|O/N` ; fond `fond1|` / `fond2|` `start|O/N` puis `stop` ; menu `cdmdmenu.py` : `msg|`, `pico|test_rgb`, `demo|gif`, `time|start` | **Décidé** (spécification § 6.6, v0.3.7 et v0.3.14) : tous les styles affichés comme `score|` ; `fond1|`, `fond2|`, `demo|`, `time|`, `pico|` ignorés et journalisés |
| `dmd.shutdown(enabled)` → `shutdwn|` | `CD:62-98`, `R:2925` | Éteint le Pi du DMD si l'option `dmd_shutdown` le demande ; si Raspy2DMD est sur la même machine, il est d'abord arrêté. Depuis la 4.6.3, l'événement `quit` n'envoie plus `shutdwn|` (`CE`) | **Adaptation au jalon M4** : le service de rendu ignore `shutdwn|` (sinon il éteindrait le seul Pi) |
| `dispatcher.publish(event, data, limit)`, `base`, `quiet` | `CE:339-450`, `R:476-490` et partout | Animations LED, DMD, voyants et stroboscopes par événement (`[Events]`) | **Reprise** |
| Minuteurs pygame `USEREVENT + 1…7` (`STROBE`, `EVENT`, `SOUND`, `DMD`) | `CR:100-109`, `CR:872-883`, `CR:1046-1085` | Événements périodiques pendant l'attente | **Reprise** |
| `rpi.target_leds`, `set_target_leds`, `set_target_leds_blink` | `CR:538-548` ; 151 appels dans `games/*` ; publiés par `R:1904-1907` | Cibles à éclairer (`goal`, `blink`) | **Reprise** (simple mémoire, sans matériel) |
| `rpi.event.publish('simon' / 'goal', …)` | `games/fun/Simon.py:116-118`, `games/sport/Football.py:248` | Accès direct au dispatcher | **Reprise** |

### 2.8 Fils d'exécution et processus existants

| Fil ou processus | Où | Remarque |
|---|---|---|
| Boucle principale pygame (un seul fil) | `R`, `CS` | Tout l'affichage, et `listen_inputs` |
| Fil `receive_darts` | `CR:343` | Remplacé par le fil de réception MQTT de la couche Pico |
| Processus `ctarget_reader.py` | `CR:331` | Supprimé en mode Pico |
| Fils `thread_toy` (un par stroboscope) | `CG:352-365`, `CG:394-412` | Remplacés par des commandes au Pico |
| Fil du monnayeur `read_mcp` | `CO:215` | Supprimé en mode Pico (D4) |
| Fil réseau MQTT (`loop_start`) | `CE:83` | Reprise ; c'est lui qui livre les messages |
| Fils d'animation des serveurs LED | `addons/*_Server.py` | Reprise |
| Fil de surveillance de l'intro | `CS:231` | Reprise |

---

## 3. Ce qui reste à décider

| N° | Question | Proposition |
|---|---|---|
| D1 | Correspondance (ligne, colonne) → segment : `config/target_ed110.yaml` (§ 7.2 de la spécification) ou `[SectionKeys]` du jeu ? | `[SectionKeys]` avec des codes `R<l>C<c>`. L'écran d'étalonnage du jeu (`CS:1291-1324`) fonctionne alors sans modification, sans nouvel outil ni nouveau fichier. Il faudra modifier le § 7.2 |
| D2 | Transport jeu ↔ `hwd` | MQTT local (§ 2 de la spécification) ; mesurer la latence (objectif : moins de 5 ms), et passer à un socket Unix si elle est dépassée |
| D3 | Qui décide « fléchette manquée » ? | `hwd`, à partir des `t_µs` : un choc `S` sans fléchette `D` à ± `miss_window_ms` (par défaut 60 ms) devient un `MISS` ; un choc proche d'une fléchette est ignoré. Le jeu reçoit alors `PIN_MISSDART` comme aujourd'hui |
| D4 | Monnayeur : plus d'entrée de MCP libre | (a) ne plus le gérer en mode Pico ; (b) l'ajouter au Pico (nouvelle entrée, modification de carte) ; (c) le brancher sur un GPIO libre du Pi. À trancher par l'auteur ; (a) en attendant |
| D5 | Menus de configuration des boutons et des Toys (`CS:6032-6243`), assistant de câblage (`CS:1443-1675`) | En mode Pico : masquer les étapes GPIO ; listes `BTN1`…`BTN15`, `T1`…`T8`, `V1`…`V12` à la place de `A0`…`D7` |
| D6 | `LIGHT_LIGHT` (jeu) contre `LIGHT` (`pinout.json`, `lights_default_mapping_raspydarts`) | Utiliser `LIGHT_LIGHT` ; corriger `pinout.json` **avec votre accord** |
| D7 | Après une reconnexion ou un redémarrage du Pico, que remet `hwd` ? | Paramètres (`SET`), LED (`LEDCFG`, `LEDB`, ordre) et voyants : oui. Toys : **non** (restent à 0 jusqu'à la prochaine commande du jeu) |
| D8 | Où ranger les paramètres du Pico (`min_contact_us`…) ? | Fichier de `hwd` (`/etc/raspydarts/hwd.toml`), envoyés par `SET` à chaque `HELLO`. **`hwd` n'envoie jamais `SAVE`**, ce qui respecte « jamais pendant une partie » |
| D9 | État d'un voyant après un stroboscope : le jeu inverse l'état courant, `TOYPULSE` finit à 0 | `hwd` remet l'état d'avant à la fin du stroboscope (il connaît la durée). Ou bien modifier le § 5.3 pour que le stroboscope restaure l'état précédent |
| D10 | Qui possède `/dev/dmd` : `hwd` ou le service de rendu ? | `hwd` (§ 2 de la spécification) ; le rendu lui publie les images (§ 5.1) |

---

## 4. Risques

| N° | Risque | Conséquence | Parade proposée |
|---|---|---|---|
| R1 | **Ordre et délai entre choc et fléchette.** Le jeu attend au plus 10 tours, soit environ 30 ms (`CR:1013-1022`), après un `PIN_MISSDART`. Un `D` qui arrive après un `S`, ou un `S` qui arrive juste après un `D` | Faux `MISS`, ou `MISS` compté sur la fléchette suivante | Fusion dans `hwd` sur les `t_µs` (D3). Tests avec le faux Pico : `S` avant, après et sans `D` |
| R2 | **Comportements nés de la lecture d'état** : depuis la 4.6.3, répétition des seules flèches (après 0,5 s, puis toutes les 0,15 s), rebonds de 50 ms, bannissement après 10 s (`CG:279-332`, `CG:324-327`) ; pause du jeu après chaque bouton (`CR:886`, `CR:929`) | Avec des événements seuls, plus de répétition, et menus moins pratiques | La couche expose **l'état** (masque mis à jour par `B`) dans `read_entries()`, avec la même logique temporelle (test différentiel sur une horloge simulée) |
| R3 | Un appui plus court qu'un tour de boucle (le jeu peut être bloqué plusieurs centaines de millisecondes par un dessin ou une vidéo) | Appui perdu (c'est déjà le cas aujourd'hui) | **Verrou** : un `B n 1` est rendu au moins une fois, même si le `B n 0` est déjà arrivé |
| R4 | Stroboscopes : le jeu attend leurs fils (`R:2391-2393`), et ils inversent l'état courant | Fin d'attente trop tôt, ou voyant éteint à tort | Fil « témoin » de même durée ; restauration de l'état (D9) |
| R5 | Afflux d'images LED : un `show()` toutes les 10 ms environ par animation ; 300 LED = 1 807 caractères par `LEDF` | Saturation de MQTT ou de l'USB, retard des fléchettes sur le même port | `hwd` ne garde que **la dernière image** de chaque canal et en envoie au plus `led_max_fps` (par défaut 30, spécification § 9.5) ; les commandes et le `PING` passent en priorité |
| R6 | Le jeu sort sans tout éteindre (plantage, `os._exit`, `R:2918`) alors que `hwd` continue | Toys et voyants restent allumés | **Testament MQTT** (« last will ») du client du jeu : `hwd` coupe tout s'il le reçoit. Le chien de garde du Pico protège seulement contre la perte de `hwd` |
| R7 | Imports au chargement : `RPi.GPIO` (`CR:16`), Blinka et `adafruit_mcp230xx` (`CG:23-27`) | Échec d'import, donc `rpi_error`, si ces bibliothèques manquent | Import différé de `cgpio_extender` en mode Pico ; `RPi.GPIO` existe toujours sur le Pi 4 |
| R8 | Double création de `Craspberry` (`R:277-284`) | Deux connexions MQTT et deux fils | Lien unique partagé, comme `ccoins.shared` |
| R9 | Fil MQTT et fil pygame | Accès concurrents | Seulement des `queue.Queue` et des variables simples protégées par un verrou entre les deux |
| R10 | Le jeu change vite (commit du jour même) | Patchs qui ne s'appliquent plus | Commit figé (`UPSTREAM.md`) ; vérification de l'application des patchs dans la CI ; patchs petits |
| R11 | Menus et assistant écrits pour les broches du Pi et les lettres de MCP | Écrans incohérents en mode Pico | D5 |
| R12 | `shutdwn|` publié par le jeu en quittant, si l'option `dmd_shutdown` est à Oui (valeur par défaut, 4.6.3) | Le service de rendu éteindrait le Pi | Ignoré au jalon M4 |
| R13 | `hwd` ou le broker absents au démarrage du jeu | Jeu sans cible | Le jeu démarre quand même (« sans cible », § 2 de la spécification) ; état publié par `hwd` (message retenu), service systemd `After=mosquitto` |
| R14 | Calibration en mode `CAL 1` (contacts bruts) | Rebonds pris pour le segment suivant | L'étalonnage du jeu utilise le mode normal (`CAL 0`) ; `CAL 1` est réservé au diagnostic |

---

## 5. Architecture proposée

```
  addons/TargetLeds_Server.py ─┐ rpi_ws281x (remplacé)          ┌─ /dev/cible ── Pico
  addons/StripLeds_Server.py  ─┤── raspydarts/hw/led/<canal> ──┐  │
                               │                               ▼  │
  raspydarts.py ── Craspberry ─┼── raspydarts/hw/cmd ────────► hwd ┤
     (couche Pico)  ◄──────────┼── raspydarts/hw/events ◄──────    │
                               │   raspydarts/hw/status (retenu)   └─ /dev/dmd ─── Interstate 75 W
  service de rendu DMD (M4) ───┴── raspydarts/hw/dmd/frame ──────►
         ▲
         └── raspydarts/dmd (msg|…, score|…, inchangé)
```

### 5.1 Sujets MQTT

Tous les sujets sont préfixés par la valeur `topic` du jeu (`raspydarts`, `CE:36`). QoS 0, pas de message retenu sauf l'état.

| Sujet | Sens | Contenu |
|---|---|---|
| `raspydarts/hw/events` | `hwd` → jeu | Texte, une ligne par événement, **un seul sujet pour garder l'ordre** : `D R3C4 <t_µs> <durée_µs>`, `B <n> <0|1> <t_µs>`, `MISS <t_µs>`, `S <t_µs> <crête>` (brut, pour le diagnostic), `E <code> <texte>` |
| `raspydarts/hw/cmd` | jeu → `hwd` | Texte : `TOY 3 1`, `TOYS A5`, `TOYPULSE …`, `LGT …`, `CAL 1`, `BTNPOLL`, `OFF` (tout éteindre). Même grammaire que le § 5.3 quand c'est possible, validée par `shared/pico_proto.py` |
| `raspydarts/hw/led/<canal>` | serveurs LED → `hwd` | Binaire : 3 octets RVB par LED (`<canal>` = 0 cible, 1 bande, 2 secours) |
| `raspydarts/hw/dmd/frame` | rendu → `hwd` | Binaire : une image complète, 8 192 octets RGB565, au moins une par seconde ; `hwd` en tire les `FRAME` et `DELTA` (spécification § 6.4 et § 9.5, v0.3.14, 30e complément) |
| `raspydarts/hw/status` | `hwd` → tous | JSON retenu : `{"pico": "connected" | "absent", "fw": "0.1.0", "uid": "…", "dmd": "absent" | "handshake" | "connected", "dmd_fw": "0.1.0"}` (champs `dmd` et `dmd_fw` : spécification § 9.5, 32e complément ; `dmd` vaut `absent` si `dmd_device` est vide) |
| `raspydarts/hw/cmd` (testament du jeu) | broker → `hwd` | `OFF`, publié par le broker si le jeu disparaît sans se déconnecter (R6) |

### 5.2 Démon `hwd`

- **Processus** : service systemd, utilisateur sans droits root, membre du groupe `dialout`. Il est seul propriétaire de `/dev/cible` et `/dev/dmd`.
- **Structure** : un fil de lecture par port, plus une file d'écriture par port avec priorités : commandes et `PING` d'abord, images LED ensuite (dernière image seulement). Le fil MQTT est `loop_start` de paho.
- **Code** : `shared/pico_proto.py` et `shared/dmd_proto.py`. La logique se place dans une classe sans entrée-sortie (`feed` / `tick`, comme les simulateurs), entourée de deux adaptateurs : port série et MQTT. C'est cette séparation qui rend `hwd` testable.
- **Reconnexion du Pico**
  1. Ouverture de `/dev/cible`. En cas d'échec, état `absent` et nouvel essai chaque seconde.
  2. Vidage de la ligne [v0.3.14, 28e complément de la spécification, § 9.5] : envoi de `\n` sans préfixe, puis toutes les lignes reçues pendant `handshake_flush_ms` (défaut 50 ms) sont ignorées, sans événement publié. Ensuite, envoi de `@n WHO`, attente de `HELLO` et `CAPS` (délai de 1 s, puis nouveau `WHO`), vérification de la version du protocole.
  3. Initialisation : `SET` de chaque paramètre du fichier de `hwd` (D8), `LEDCFG`, `LEDB`, `SET led<n>_order`, remise des voyants (D7). État `connected` publié.
  4. En marche : `PING` toutes les `watchdog_ms / 3`. Commandes préfixées `@n`, `K` attendu sous 200 ms. En cas de `N` ou de délai dépassé : journal, pas de nouvel essai, sauf pour les commandes idempotentes.
  5. Un `HELLO` non demandé signale un redémarrage du Pico, et `E WDG resumed` une coupure du chien de garde : retour à l'étape 3.
  6. Fin de fichier ou erreur d'entrée-sortie (débranchement) : état `absent`, retour à l'étape 1. Les événements reçus pendant la coupure sont perdus, et c'est voulu.
- **Fusion choc / fléchette** (D3) : un `S` est mis en attente `miss_window_ms` ; il est oublié si un `D` arrive dans cette fenêtre (écart des `t_µs` modulo 2³⁰), sinon il est publié comme `MISS`.
- **Codes de segment** : `D <l> <c>` → `D R<l>C<c>` ; la correspondance vers le segment reste dans le jeu (`[SectionKeys]`, D1).

### 5.3 Couche d'intégration dans RaspyDarts

Un nouveau module `include/cpico.py` (GPL v3), **sans pygame ni RPi.GPIO**, donc testable sur le Mac :

- `PicoLink` (unique) : client MQTT, abonné à `raspydarts/hw/events` et `raspydarts/hw/status`, avec un testament `OFF`. Il tient l'état des boutons (masque, verrou, horodatage) et une file de codes de fléchettes.
- `PicoExtender` : remplace `Gpio_extender` avec **la même interface** :
  - `mcp1` (non `None` si connecté) ;
  - `buttons_list`, `toys_list`, `banned_button` ;
  - `set_io`, `get_defaults`, `read_entries`, `test_entries`, `light_toys`, `strobe_toys`, `decode` ;
  - depuis la 4.6.4 : `blink_toy` (LED des boutons, `cbuttonleds.py`) et `hold_validate` (mode capture).

  `read_entries()` reproduit la logique d'origine (un appui = une action, rebonds, répétition des flèches, bannissement après 10 s ; en mode capture, Valider rendu au relâchement ou `PIN_CAPTURE_END` après 2 s), mais sur le masque et le verrou. `blink_toy()` envoie chaque état (`LGT`/`TOY`) depuis un fil, à partir de l'état demandé par `light_toys` (le Pi ne relit pas les sorties). `strobe_toys()` envoie `TOYPULSE` / `LGTPULSE` et renvoie des fils témoins.
- `PicoTargetSource` : objet mis dans `Craspberry.target_reader` ; il remplit `Craspberry.target_darts` avec des couples `(R<l>C<c>, time.monotonic())`, l'instant pris à la réception de l'événement `D` (format de RaspyDarts 4.6.5 ; le `t_µs` du Pico n'est pas converti). `MISS` arrive à part, sous la forme `PIN_MISSDART`.

Modifications dans le code du jeu, à garder minimales :

| Fichier | Modification |
|---|---|
| `include/craspberry.py` | En mode Pico (nouvelle clé de configuration, par exemple `[SectionAdvanced] hardware = pico`) : ne pas lancer `ctarget_reader`, `self.gpio = cpico.PicoExtender(…)`, `self.target_reader = cpico.PicoTargetSource(…)`, `gpio_flush` envoie `OFF`. Import différé de `cgpio_extender` (R7) |
| `raspydarts.py` | Condition de lancement des serveurs LED (`R:135-138`, `R:167-204`) : mode Pico, sans `sudo`, avec le module `rpi_ws281x` de remplacement en tête du chemin Python |
| `include/cscreen.py` | `calibration_needed` (`CS:1271`) ; étapes GPIO de l'assistant et menus des boutons et des Toys masqués ou adaptés (D5) |
| `include/cconfig.py` | Préréglage « carte Pico » : `[Raspberry]` = `BTN1`…, `T1`…, `V1`… selon le § 3.3 et le § 3.4 de la spécification ; `[Raspberry_Leds]` |

### 5.4 Serveurs LED

Un module `rpi_ws281x.py` de remplacement (mêmes noms : `PixelStrip`, `Color`, `WS2811_STRIP_RGB`, `WS2811_STRIP_GRB`) garde les couleurs en mémoire. `show()` publie l'image RVB sur `raspydarts/hw/led/<canal>`. `setBrightness` et l'ordre des couleurs sont transmis à `hwd`, qui les traduit en `LEDB` et `SET led<n>_order`. Le canal est déduit de l'argument `pin` (valeur fictive du préréglage) ou d'une variable d'environnement. **`CStrip.py` et les deux serveurs restent inchangés.**

### 5.5 Tests sans carte

| Niveau | Quoi | Comment |
|---|---|---|
| Logique de `hwd` | Reconnexion, initialisation, fusion choc/fléchette, coalescence des LED, priorités, testament | Classe sans entrée-sortie, avec le **faux Pico en mémoire** et une horloge simulée, plus un faux broker en mémoire |
| `hwd` complet | Port série et MQTT réels | Faux Pico sur un **pseudo-terminal** (`tools/fake_pico.py`, `--pico /dev/ttys00N`), broker `mosquitto` (installé dans la CI par `apt` ; test sauté s'il est absent) ; débranchement simulé par `PtyServer.stop()` |
| `include/cpico.py` | `read_entries` (appui, rebonds, répétition des flèches, bannissement, mode capture, verrou), `blink_toy`, file des fléchettes, stroboscopes et fils témoins, `TOY9` à `TOY16` ignorés | Testé seul (pas de pygame), contre un faux bus ou contre `hwd` et le faux Pico |
| `rpi_ws281x` de remplacement | Images publiées, ordre des couleurs, luminosité | Avec `CStrip.py` réel, chargé depuis la copie de RaspyDarts patchée (§ 6) |
| Patchs | Ils s'appliquent sur `BASE` (`8fa61ff`) | Dans la CI : clone du commit figé, puis `git apply --check` |
| Jeu complet | RaspyDarts patché, faux Pico, `hwd` | Sur le Pi, ou sur un PC Linux avec pygame. **Pas en CI** : demande un écran, pygame et `RPi.GPIO` |

---

## 6. Méthode de modification de RaspyDarts

- **Ne jamais toucher `~/RaspyDarts-ref465`** (référence en lecture seule, extraite sur le commit de `UPSTREAM.md`), **ni les anciennes copies `~/RaspyDarts-ref`** (4.6.4) **et `~/RaspyDarts-orig`** (4.6.3). Toute exception vous sera signalée avant d'agir.
- **Copie de travail** : `git clone ~/RaspyDarts-ref ~/RaspyDarts-work`, puis une branche à partir de `<BASE>` (aujourd'hui `pico-8fa61ff` ; au changement de base : nouvelle branche, `git am` des patchs, conflits résolus, patchs régénérés). Un clone local, et non `git worktree`, qui écrirait dans `~/RaspyDarts-orig/.git`.
- **Patchs** dans `pi/patches/` :
  - un patch par sujet, numérotés (`0001-craspberry-mode-pico.patch`, `0002-serveurs-led-sans-sudo.patch`…), produits par `git format-patch` depuis `~/RaspyDarts-work` ;
  - un fichier `pi/patches/series` donne l'ordre, et `pi/patches/BASE` contient le commit de référence.
- **Fichiers nouveaux** (`include/cpico.py`, `rpi_ws281x.py` de remplacement) : ils vivent dans ce dépôt (`pi/raspydarts_layer/`), testés par notre CI, avec un en-tête GPL v3. Les patchs ne contiennent que les modifications des fichiers existants. Le script d'installation copie les nouveaux fichiers.
- **Application** : un script `tools/apply_patches.sh <dossier RaspyDarts>`. Il vérifie que `HEAD` vaut `BASE`, applique `git am` dans l'ordre de `series`, puis copie les fichiers nouveaux et ajoute le bandeau de `pi/patches/FORK_README_BANNER.md` en tête du `README.md` (complément 83), dans un même commit. Il n'agit que sur le dossier qu'on lui donne, jamais sur `~/RaspyDarts-orig`.
- **Changement de version de RaspyDarts** : décision de l'auteur. Il faut alors mettre à jour `BASE` et `UPSTREAM.md`, recommencer la cartographie des lignes citées ici, puis régénérer les patchs.
- **Licence** : tout code dérivé de RaspyDarts garde la GPL v3, avec les en-têtes.

---

## 7. Ce qui n'a pas été vérifié

- Rien n'a été exécuté : cette cartographie vient de la lecture du code, et non d'un essai du jeu.
- Sur les 59 appels à `listen_inputs`, seuls ceux de `R` et les écrans d'étalonnage et de test de `CS` ont été lus en détail. Les autres sont supposés utiliser la même interface.
- Les jeux de `games/*` n'ont été parcourus que pour leurs accès à `rpi` (`set_target_leds`, `event.publish`, `listen_inputs`).
- La durée réelle d'un tour de `listen_inputs` sur le Pi 4 avec le nouveau système n'est pas connue : elle conditionne R1 et R3.

---

## 8. Décisions de l'auteur (4 novembre 2026)

| N° | Décision |
|---|---|
| D1 | **Oui** : étalonnage par l'écran du jeu, codes `R<ligne>C<colonne>` dans `[SectionKeys]`. §§ 7.2 et 9.2 de la spécification mis à jour (v0.3) ; un test couvre ce chemin (`tests/test_hwd_core.py`, classe `CalibrationPath`) |
| D3 | **Oui** : `hwd` décide d'après les `t_µs`, fenêtre **symétrique** configurable (`miss_window_ms`), **50 ms** par défaut, valeur à mesurer au banc |
| R2 | **Accepté** : état des boutons et verrou, dans la couche du jeu |
| R5 | **Accepté** : dernière image par canal, débit plafonné |
| R6 | **Accepté** : testament MQTT `OFF`. Durée de maintien de **5 s** pour le client du jeu et celui de `hwd` (§ 9) |
| D6 | **Oui** : `LIGHT_LIGHT` ; `hardware/pinout.json`, § 3.4 et test corrigés, avec l'accord de l'auteur |
| D9 | `hwd` **restaure l'état d'avant** à la fin prévue du stroboscope, sauf commande intermédiaire sur la même sortie. Le Pico termine toujours à 0 (§ 5.3 inchangé) |
| D4 | **Pas de monnayeur** en mode Pico : aucune entrée sur le PCB, aucun code. Le patch du jeu devra désactiver le code du monnayeur (`include/ccoins.py`) en mode Pico. L'étude du § 10 reste pour mémoire |
| D2, D5, D7, D8, D10 | **Validées** telles que proposées au § 3 ([DÉCIDÉ] dans la spécification v0.3.1) |

## 9. Durée de maintien MQTT et testament (R6)

- **Version vérifiée** : paho-mqtt **2.1.0** (dernière version sur PyPI le 4 novembre 2026 ; RaspyDarts demande `paho-mqtt>=2`, `requirements.txt:29`). paho n'est installé ni sur le Mac de développement ni dans ce dépôt : la valeur a été lue dans le code source de la roue `paho_mqtt-2.1.0-py3-none-any.whl` (téléchargée de PyPI dans un dossier temporaire, rien n'a été exécuté).
- **Valeur par défaut** : `keepalive = 60` s (`Client.connect(…, keepalive: int = 60, …)`, `Client.connect_async`, et `self._keepalive = 60` dans `Client.__init__`).
- **Conséquence** : le jeu appelle `connect(broker, port)` sans durée de maintien (`CE:78`, `CE:226`), donc 60 s. Le broker ne publie le testament qu'après 1,5 fois cette durée sans nouvelle (règle de MQTT 3.1.1, § 3.1.2.10), soit **jusqu'à 90 s**.
- **Décision** : le client du jeu (futur patch de `cevent.py`) et celui de `hwd` passent `keepalive=5`. Le testament est alors publié au plus tard 1,5 × 5 = **7,5 s** après la disparition du client, donc en moins de 8 s.
- **Test** : `tests/test_hwd_daemon.py` vérifie que `hwd` crée son client avec `keepalive=5`, l'API de rappel 2 et un testament, sans avoir besoin de paho (client injecté). Le délai réel de 7,5 s, qui dépend du broker, n'est pas testé (pas de `mosquitto` ici).
- **Rôle de chaque testament** : celui du **jeu** publie `OFF` sur `raspydarts/hw/cmd`, et `hwd` éteint tout. Celui de **`hwd`** publie `{"pico": "absent"}` (retenu) sur `raspydarts/hw/status`, pour que le jeu sache qu'il n'y a plus de cible. La perte de `hwd` lui-même est couverte par le chien de garde du Pico (`watchdog_ms`).

## 10. D4 : ce que demanderait un monnayeur sur U9 GPB0 (étude, non retenue, rien n'est codé)

**Décision de l'auteur (4 novembre 2026) : pas de monnayeur.** Il est supprimé en mode Pico : le PCB n'a aucune entrée pour lui, et aucun code n'est écrit. Le patch du jeu devra désactiver le code du monnayeur (`include/ccoins.py`) en mode Pico. En attendant, `PicoExtender` n'expose aucune puce (`chips` vide) : `ccoins.start_mcp` s'arrête alors sur « no MCP found » (`CO:197-199`). L'étude ci-dessous est gardée pour mémoire.

Hypothèse : contact sec du relais du monnayeur, fermé vers la masse pendant environ 0,1 s par pièce (`CO:1-15`), câblé sur **U9 (0x21) GPB0**, aujourd'hui libre.

- **Carte et `pinout.json`** : il faut une piste et un connecteur pour GPB0, absents du PCB actuel (`"free": ["GPB0..GPB7"]`). Il faudrait aussi une protection (résistance série, et éventuellement un filtre RC), puisque le contact vient d'un appareil à 12 V à travers un relais. **Modification de `pinout.json` et de la carte : décision de l'auteur.** GPB0 en entrée respecte l'erratum, qui ne vise que GPA7 et GPB7 ; les autres broches libres de U9 restent des sorties au niveau bas.
- **Firmware (MicroPython)** :
  - U9 GPB0 en entrée, avec la résistance de rappel interne (`GPPUB`, bit 0) ;
  - interruption sur changement (`GPINTENB` bit 0, `INTCONB` bit 0 = comparaison à l'état précédent), pour que l'état soit **capturé** dans `INTCAPB` au front ;
  - l'INTB de U9 n'est pas relié au Pico : lecture de `INTFB` toutes les 10 ms sur le cœur 0. Si le bit est levé, lecture de `INTCAPB` (ce qui efface l'interruption) ; un niveau bas capturé compte une pièce ;
  - antirebond `coin_debounce_ms` (par exemple 30 ms) ;
  - un **compteur de pièces** sur 16 bits, qui boucle, gardé en mémoire vive, pour qu'aucune pièce ne soit perdue lors d'une reconnexion.
- **Protocole (§ 5)** :
  - nouveau message `C <compteur> <t_µs>`, envoyé à chaque pièce ;
  - `CAPS … coin=1` ;
  - nouvelle commande `COINS`, qui renvoie `C <compteur> <t_µs>` (resynchronisation) ;
  - paramètres `coin_enable` (0 ou 1) et `coin_debounce_ms`.

  Version du protocole inchangée si ces ajouts restent compatibles (un Pi plus ancien ignore `C`). Les tests de `shared/pico_proto.py` et le faux Pico seraient étendus.
- **`hwd`** : relaie `C` sur `raspydarts/hw/events`. Après chaque `HELLO`, il envoie `COINS` et publie l'écart avec le dernier compteur connu, ce qui rattrape les pièces tombées pendant une coupure, sauf en cas de redémarrage du Pico (compteur remis à zéro, à signaler par un `E`). Une pièce n'est jamais filtrée ni fusionnée.
- **Couche du jeu** : `ccoins.Coins.start_mcp` lit aujourd'hui directement les registres d'un MCP du Pi (`CO:193-235`). En mode Pico, une nouvelle source (dans `include/cpico.py`) appellerait `Coins.coin()` une fois par incrément du compteur. Le réglage `mcp` de `monnayeur.json` prendrait une valeur fixe, par exemple `PICO`. `I2C_LOCK` deviendrait sans objet.
- **Tests sans carte** : compteur et resynchronisation avec le faux Pico (pièce pendant une coupure, pendant un redémarrage) ; `Coins.coin()` appelé le bon nombre de fois.

---

## Annexe A. Couverture des accès `rpi.` et `gpio.` du dépôt

Relevé par `grep` sur RaspyDarts `85c9f81`, sans les commentaires. Contrôlé sur `5ae22c5` (4.6.3) : seuls quelques nombres d'appels changent (`listen_inputs`, `set_target_leds`, `target_leds`, `usb_pads`), plus un appel à `rpi.strobe_buttons` dans `games/classic/Chti_501.py:738`, une méthode qui **n'existe pas** dans `Craspberry` : défaut de l'amont, présent aussi en mode normal, hors de notre couche. Contrôlé sur `5c85f6a` (4.6.4) le 18 novembre : appel à `strobe_buttons` mis en commentaire par l'auteur ; nouveaux accès `gpio.blink_toy`, `gpio.hold_validate`, `gpio.toys_list` (couverts, § 2) ; nouveaux accès `rpi.` (`autodarts`, `autodarts_waiting`, `button_led_*`, `button_leds_*`, `capture`, `darts_per_turn`, `on_idle`, `poll_menu_buttons`, `scripted`) : méthodes de `Craspberry` elles-mêmes, vérifiées en mode Pico au § A.4.

- Les `gpio.` de `include/craspberry.py` sont de deux sortes : le module `RPi.GPIO` (importé sous le nom `gpio`) et l'attribut `self.gpio` (`Gpio_extender`).
- « Couvert » signifie que l'accès est traité dans le présent document (§ entre parenthèses).
- « Reprise, hors matériel » signifie que l'accès ne touche pas au matériel et reste inchangé.

### A.1 Accès `rpi.` (316 au total)

| Fichier | Nombre | Attributs | Couverture |
|---|---|---|---|
| `include/cscreen.py` | 89 | `listen_inputs` (43) | Couvert (§ 1, § 2.3) |
| | | `target_enabled` (3), `set_intro_end`, `on_coins`, `on_music` | Couvert (§ 2.1) |
| | | `inputs` (2), `outputs` (3), `set_inputs`, `set_outputs`, `init_gpio` (4), `set_inputs_conf`, `set_outputs_conf`, `set_buttons_conf`, `set_keys_conf` (2), `set_leds_conf` | Couvert (§ 2.2, assistant ; D5) |
| | | `gpio` (2), `gpio.mcp` (1, `mcp1`), `gpio.get_defaults`, `gpio.strobe_toys`, `gpio.banned_button` (2), `banned_gpio` (2), `buttons` (3), `light_toys` (2), `test_buttons`, `gpio_flush` | Couvert (§ 2.3, § 2.4) |
| | | `coins` | Couvert (§ 2.5, supprimé en mode Pico : D4) |
| | | `usb_pads` (2), `shift`, `input_time`, `newresolution` (2) | Reprise, hors matériel (clavier, pavés USB, mesure du temps, taille de la fenêtre) |
| `raspydarts.py` | 61 | `light_toys` (30), `strobe_toys`, `hold`, `listen_inputs` (6), `target_leds` (6), `target_leds_blink` (6), `set_io`, `get_defaults`, `gpio.get_defaults`, `gpio_flush`, `set_play_sound`, `set_send_insult` | Couvert (§ 2.1 à § 2.7) |
| | | `config.set_config` (4) | Reprise, hors matériel (écriture de la configuration) |
| | | `newresolution` | Reprise, hors matériel |
| `include/cevent.py` | 4 | `light_toys`, `strobe_toys` (3) | Couvert (§ 2.4) |
| `trophees.py` | 4 | `listen_inputs` | Couvert (§ 1) |
| `include/cmenueditor.py` | 2 | `listen_inputs` | Couvert (§ 1) |
| `include/caccount.py`, `include/cchat.py`, `include/cexplorer.py` | 1 chacun | `listen_inputs` | Couvert (§ 1) |
| `include/cdebug.py` | 1 | `set_target_leds` | Couvert (§ 2.7) |
| `games/*` (50 fichiers) | 152 | `set_target_leds` (124), `set_target_leds_blink` (23), `target_leds` (1), `event.publish` (3), `listen_inputs` (1) | Couvert (§ 2.7, § 1) |

Détail de `games/*` : `games/sport/Snooker.py` 31, `games/classic/Big6.py` 12, `games/classic/Slider.py` 10, `games/fun/Challenge.py` 10, `games/classic/Shoot-out.py` 6, `games/fun/Voleur.py` 5, `games/sport/Football.py` 5, 4 chacun pour `games/classic/Kinito.py`, `games/fun/Hangman.py` et `games/fun/Yahtzydarts.py`, 3 chacun pour `games/classic/Practice.py`, `games/fun/Simon.py` et `games/sport/Golf.py`, 1 ou 2 accès pour chacun des 37 autres fichiers.

### A.2 Accès `gpio.` (76 au total)

| Fichier | Nombre | Attributs | Couverture |
|---|---|---|---|
| `include/craspberry.py` (module `RPi.GPIO`) | 50 | `setup` (9), `output` (8), `IN` (7), `LOW` (6), `input` (5), `HIGH` (4), `PUD_DOWN` (4), `OUT` (2), `PUD_UP`, `BCM`, `setmode`, `setwarnings`, `cleanup` | Couvert : balayage de la cible par le jeu et boutons sur GPIO du Pi, **supprimés** en mode Pico (§ 2.2, § 2.3) ; `cleanup` (§ 2.4, `gpio_flush`) |
| `include/craspberry.py` (`self.gpio`) | 13 | `read_entries`, `test_entries` (2), `mcp` (1, `mcp1`), `buttons_list`, `set_io`, `light_toys`, `strobe_toys` (5), `light` | Couvert (§ 2.3, § 2.4) ; `light` = code mort (§ 2.4) |
| `include/cscreen.py` | 12 | `get_defaults` (3), `mcp` (3, `mcp1`), `banned_button` (2), `strobe_toys` | Couvert (§ 2.3, § 2.4) |
| | | `chips`, `pins_of`, `all_io` | **Couvert en partie** : `setup_io_extender` (`CS:1665-1715`) pilote **directement** les broches des MCP par `digitalio`, en dehors de `Gpio_extender`. Écran à masquer en mode Pico (D5) ; `PicoExtender` ne fournira **pas** `chips` (pas de puce accessible depuis le Pi) |
| `raspydarts.py` | 1 | `get_defaults` | Couvert (§ 2.3) |

### A.3 Accès non couverts

Aucun accès matériel n'est hors du document. Deux points demandent une attention particulière lors des patchs :
- **`setup_io_extender`** (`CS:1665-1715`) : seul écran qui contourne `Gpio_extender` (A.2).
- **`ccoins`** : il contourne lui aussi `Gpio_extender` (`extender.chips[index]._read_u8`, `CO:204-228`), mais ne passe ni par `rpi.` ni par `gpio.`, ce qui explique qu'il n'apparaisse pas dans ce relevé (§ 2.5 ; supprimé en mode Pico, D4).

### A.4 Méthodes de `Craspberry` ajoutées en 4.6.4 : comportement en mode Pico

Vérifié le 19 novembre 2026 dans `~/RaspyDarts-work`, branche `pico-5c85f6a` (RaspyDarts `5c85f6a` avec les patchs 0001 à 0003), en lisant le code de chaque méthode et de ses appelants. Les repères `CR`, `CS` renvoient à la version patchée ; `R` à `5c85f6a`.

| Méthode ou attribut | Rôle | Accès au matériel | En mode Pico | Statut |
|---|---|---|---|---|
| `scan_output`, `scan_input`, `scan_setup_input` | Balayage de la cible par les GPIO du Pi, broche refusée mise de côté (`CR:715-764`) | `RPi.GPIO` | Jamais appelées : `read_target` et `test_target` rendent la fléchette de `PicoTargetSource` avant le balayage (`CR:623-680`) | Sans objet |
| `poll_menu_buttons` | Bouton des menus sans attente, pour les fenêtres qui lisent la souris (`CR:856-874`) | `read_buttons` (GPIO), `gpio.read_entries`, `button_led_press` | `self.pins` vide (aucun bouton sur les GPIO du Pi, patch 0001) ; `PicoExtender.mcp1` et `read_entries` | Couverte par `PicoExtender` |
| `confirm_screen_mode` | Garde le mode d'écran essayé à la première action (`CR:915-928`) | Aucun (écran, `sudo raspydarts-display`) | Identique au mode normal | Sans objet |
| `set_button_led`, `button_led_continuous`, `button_led_pulses`, `button_led_wait_start`, `button_led_wait` | LED des boutons : allumée, éteinte, clignotements (fils du jeu) | `gpio.light_toys` seulement | `PicoExtender.light_toys` (`LGT` / `TOY` vers `hwd`) | Couvertes par `PicoExtender` |
| `button_led_press` | Clignotement N fois à l'appui (`CR:1451-1461`) | `gpio.blink_toy` | `PicoExtender.blink_toy` (ajouté le 18 novembre) | Couverte par `PicoExtender` |
| `button_led_values`, `button_led_state`, `button_led_period`, `button_led_period_of` | Réglages et calculs, sans sortie | Aucun | Identique | Sans objet |
| `button_leds_context`, `button_leds_reload` | Début et fin de partie (`R:1757`, `R:2825`), réglages enregistrés (`CS:9675`) | Par `set_button_led` | Comme ci-dessus ; rien si `self.gpio` vaut `None` | Couvertes par `PicoExtender` |
| Attribut `capture` (`include/ccapture.py`) | Mode capture : F1, Impr. écran, Échap, Valider maintenu | `gpio.hold_validate` (`ccapture.py:184`, `:330`) | `PicoExtender.hold_validate`, `PIN_CAPTURE_END` rendu en `BTN_CAPTURE_END` | Couvert par `PicoExtender` |
| Attributs `scripted`, `on_idle`, `in_game`, `darts_per_turn`, `led_asked`, `led_wanted`, `led_threads`, `button_leds` | Entrées données par le jeu, fond animé, état des LED de boutons | Aucun en propre | Identique | Sans objet |
| Attributs `autodarts`, `autodarts_waiting` | Autodarts | Réseau (port 3180) | Hors périmètre (spécification § 9.6) | Sans objet |

**`cbuttonleds` (`include/cbuttonleds.py`, réglages dans `~/.raspydarts/button_leds.json`)** : appelé par `craspberry.py` (11 fois : `load` au démarrage et au rechargement, `KIND`, `PRESSES`, `CONTINUOUS`, `LEDS`, `DEFAULTS`) et par `cscreen.py` (12 fois, écran Paramétrage > LED des boutons, `CS:9575-9760`). Le module ne touche aucun matériel : il lit et écrit le fichier JSON. En mode Pico, il fonctionne tel quel ; les LED de boutons sont les voyants du Pico (`LIGHT_*` → `V1`…`V10`, § 3.4 de la spécification), trouvés par `led in gpio.toys_list` (`CS:9578`).

**Manque corrigé** : `cscreen.button_led_try` (`CS:9709`, essai d'un clignotement) lit `gpio.toys_list[led][1].value`, la broche du MCP23017. Dans `PicoExtender`, ce champ était le texte `"LGT"`, d'où une `AttributeError` à l'essai. C'est maintenant un objet `PicoOutput` : `value` vaut 1 si `light_toys` a allumé la sortie (le Pi ne relit pas les sorties du Pico), et l'écrire envoie la commande. Tests : `test_cpico.OutputPinValue`, `test_read_entries_diff.test_output_value_same`.

**Reste en dehors de ce relevé** : `init_gpio` (`CR:508`), appelé par l'assistant de câblage, configure les GPIO du Pi ; en mode Pico, ces étapes de l'assistant sont masquées par le futur patch de `cscreen.py` (D5).
