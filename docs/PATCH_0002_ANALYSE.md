# Patch 0002 (`include/cconfig.py`) : analyse

**Statut : analyse seulement, aucun patch écrit. À valider par l'auteur (questions au § 6).**

Lecture faite sur RaspyDarts `85c9f81` (`docs/UPSTREAM.md`). Document historique, non recartographié lors du passage à la 4.6.3 (`5ae22c5`, où `cconfig.py` est décalé de 4 à 9 lignes) : les références `fichier:ligne` renvoient à `85c9f81` : `CC` = `include/cconfig.py`, `CS` = `include/cscreen.py`, `R` = `raspydarts.py`, `CP` = `include/cpaths.py`. Le patch 0001 (`include/craspberry.py`) est supposé appliqué.

Objectifs du patch 0002 :
- **a.** La clé `[SectionAdvanced] hardware` (`normal` par défaut) est connue de `cconfig`, chargée et réécrite par `write_file`.
- **b.** En mode Pico, le préréglage de `[Raspberry]` donne `BTN1`…`BTN15`, `T1`…`T8` et `V1`…`V12` (spécification § 3), et `[Raspberry_BoardPinsIns]` et `[Raspberry_BoardPinsOuts]` restent vides.
- **c.** Le mode normal reste strictement identique.

---

## 1. Lecture, validation et réécriture de la configuration

### 1.1 Où est le fichier

- `cpaths.config_file()` (`CP:56-70`) choisit le fichier : `~/.raspydarts/raspydarts.cfg`.
- Un ancien `pydarts.cfg` est **renommé** en `raspydarts.cfg` s'il est le seul, ou s'il est plus récent (`CP:63-66`, `os.replace`). Ce renommage a lieu dès l'appel à `config_file()`, donc dès la création de l'objet `Config` (`CC:966`).
- Le dossier est `~/.raspydarts`, ou `~/.pydarts` pour les anciennes installations (`CP:30-38`). Avec `sudo`, c'est celui de `SUDO_USER` (`CP:24-28`).
- `Config.__init__` lit aussi `/proc/device-tree/model` et `/sys/firmware/devicetree/base/serial-number` (`CC:1042-1049`), ainsi que le fichier `VERSION` du jeu (`CC:1071-1079`). Les tests devront fournir de faux fichiers à leur place (§ 4.1).

### 1.2 Lecture : `read_file(section)` (`CC:1664-1755`)

Le jeu appelle `read_file` section par section au démarrage (`R:55-80`) : `SectionGlobals`, `SectionAdvanced`, `SectionKeys`, `Raspberry`, `Raspberry_Leds`, `LEDTarget`, `Favorites`, `Events`, `Raspberry_BoardPinsIns`, `Raspberry_BoardPinsOuts`, `Camera`, `Gallery`, puis une section `game-<jeu>` par jeu de `game-options-list` (`R:106-107`). **`SectionAdvanced` est lue (`R:57`) avant `Raspberry` (`R:61`) et avant les deux sections de broches (`R:75`, `R:77`).**

Pour chaque section :

1. **Le fichier entier est relu** à chaque appel, par `ConfigParser(inline_comment_prefixes="#")`, avec `optionxform = str`, sans encodage précisé et **avec l'interpolation par défaut** (`CC:1668-1674`). Une erreur de lecture du fichier (par exemple un fichier qui commence par une marque BOM, qui donne `MissingSectionHeaderError`) provoque `logs.fatal`, puis `exit(1)` (`CC:1675-1677`).
2. **Point de départ : les valeurs par défaut**, `options = init_section(section)` (`CC:1680-1684`, `CC:1468-1475`). C'est une copie de `DefaultConfig[section]`, **dans l'ordre de `DefaultConfig`**.
3. **Section absente du fichier** (`CC:1686-1699`) :
   - si elle est connue de `DefaultConfig`, un avertissement est émis et les valeurs par défaut sont gardées ;
   - sinon, la section est créée vide et la fonction renvoie `False` (ou `''` si `none` est donné).
4. **Clé par clé** (`CC:1701-1725`) :
   - **une clé inconnue de `DefaultConfig[section]` est écartée** avec l'avertissement « Unexpected `<clé>` in `<section>` » (`CC:1702-1703`), sauf dans les sections `game-*`. Elle n'est **ni chargée ni gardée**, et elle disparaît donc à la prochaine réécriture ;
   - conversion des valeurs : `''` ou `"''"` deviennent `''` ; les guillemets simples extérieurs sont retirés ; `Events` et `Colorset-*` passent par `ast.literal_eval` ; `'False'` et `'True'` deviennent des booléens ; tout le reste est gardé tel quel, en texte ; `font = Casino.ttf` devient `MaPolice.ttf` ;
   - en cas d'erreur sur une valeur (par exemple `%` et l'interpolation), la valeur par défaut est prise, avec un message d'erreur (`CC:1721-1725`).
5. **Cas particuliers** :
   - `[Raspberry]` d'une configuration V2, où **toutes** les valeurs valent `'0'`, est remplacée par `AlternateConfig['Raspberry']` (`CC:1727-1738`, `CC:404-437`). **C'est un précédent de conversion faite à la lecture** (voir § 2.4) ;
   - `[Favorites]` : l'ancien serveur est retiré (`CC:1740-1747`).
6. `self.config[section] = options` (`CC:1750`).

**Ordre des clés** : celui de `DefaultConfig[section]` pour les sections connues ; les clés du fichier qui sont connues ne font que remplacer les valeurs. Pour une section `game-*`, l'ordre est celui des valeurs par défaut (aucune), puis celui du fichier.

**Piège important : `self.config` est le dictionnaire `DefaultConfig` lui-même** (`CC:983`, `self.config = DefaultConfig`). `read_file` remplace donc `DefaultConfig[section]` par les valeurs lues. Un appel ultérieur à `init_section` (par exemple `apply_deposit`, `CC:1152`, ou `write_file` pour `Gallery`, `CC:1442`) renvoie alors les valeurs **chargées**, et non les valeurs d'origine. Cet état est global au module : les tests devront recharger `cconfig` pour chaque cas (§ 4.1).

### 1.3 Réécriture : `write_file()` (`CC:1271-1466`)

Le fichier est **entièrement régénéré** à partir de `self.config`, sans rien relire, puis écrit par `open(self.configFile, 'w')` : sans encodage précisé (donc celui de la langue du système), sans marque BOM, et sans copie de sauvegarde (`CC:1460-1466`).

| Section | Mode d'écriture | Lignes |
|---|---|---|
| `[SectionGlobals]` | **Liste explicite, clé par clé**, avec des commentaires `###` | `CC:1275-1359` |
| `[SectionAdvanced]` | **Liste explicite, clé par clé** : seules 28 des 39 clés de `DefaultConfig['SectionAdvanced']` sont écrites (liste ci-dessous) | `CC:1361-1415` |
| `[Server]` | Liste explicite | `CC:1417-1437` |
| `[Camera]`, `[Gallery]`, `[Favorites]`, `[Raspberry_BoardPinsIns]`, `[Raspberry_BoardPinsOuts]`, `[Raspberry_Leds]`, `[LEDTarget]`, `[SectionKeys]`, `[Raspberry]`, `[Events]` | `config_section` : **toutes les clés du dictionnaire, dans son ordre**, au format `clé:valeur` | `CC:1439-1452`, `CC:1183-1194` |
| `[game-*]` | `config_section`, dans l'ordre de `self.config` | `CC:1454-1457` |

Dans `[SectionAdvanced]`, les clés **jamais écrites** sont : `selectedgame`, `gametype`, `netgamename`, `servername`, `serverport`, `masterserver`, `masterport`, `serveralias`, `listen`, `masterclientpolltime` et `animationduration`. Elles sont lues si elles figurent dans le fichier, mais disparaissent à la première réécriture.

**Conséquence pour la clé `hardware`** :
- l'ajouter à `DefaultConfig['SectionAdvanced']` suffit pour qu'elle soit chargée ;
- mais elle ne sera **réécrite** que si `write_file` l'écrit **explicitement**, puisque `[SectionAdvanced]` ne passe pas par `config_section`.

C'est le risque cité dans l'objectif a. Aujourd'hui, le jeu réécrit le fichier depuis **20 endroits** (`R:991`, `R:1000`, `R:1014`, `R:1061`, `R:1073`, `R:1105`, `R:1121`, `R:1139`, `R:1172`, `R:1237`, `CS:1094`, `CS:1456`, `CS:8256`, `CS:9203`, `CS:9838`, `CS:10732`, `CS:11719`, `include/cmusic.py:138`, `include/cmusic.py:176`, `CC:1154`). Le premier enregistrement depuis un menu ferait donc repasser le jeu en mode normal, sans aucun message.

### 1.4 Autres lecteurs

- **Configuration déposée** (`raspydarts.cfg.depose`, `CC:1081-1165`, appelée en `R:484-498`) : lue avec `interpolation=None` et `utf-8-sig`. Les clés inconnues de `DefaultConfig` y sont écartées (`CC:1130-1132`). Une fois `hardware` connue, un fichier déposé pourra donc changer le mode, ce qui est le comportement voulu.
- **Patch 0001** : `read_hardware_mode` relit le fichier avec les options de la configuration déposée (`interpolation=None`, `utf-8-sig`). Il ne dépend pas de `cconfig`.

---

## 2. Préréglages de cartes

### 2.1 Ce qu'est un préréglage

Un préréglage est un dictionnaire de module, par exemple `JiCardConfig` (`CC:740-766`), avec :
- `CARD_NAME` : un libellé, **qui n'est jamais utilisé** : l'assistant a ses propres libellés ;
- `INPUT` et `OUTPUT` : les broches GPIO des lignes et colonnes de la cible, pour `[Raspberry_BoardPinsIns]` et `[Raspberry_BoardPinsOuts]` ;
- `KEYS` : les codes de segment, pour `[SectionKeys]` ;
- `EXTENDED` : les boutons, Toys et voyants sur MCP23017, pour `[Raspberry]`. C'est presque toujours `EXTENDED_CONFIG` (`CC:585-595`).

Il y en a 11 : `GBCardConfig`, `ClkCardConfig`, `JtCardConfig`, `Jt2CardConfig`, `ED900Config`, `JiCardConfig`, `Ji2CardConfig`, `JizCardConfig`, `JoCardConfig` (« Extendart ED110 7*10 »), `Jo416CardConfig` et `MMWorksVDartsCardConfig`. S'y ajoutent deux préréglages de LED : `JiLedsConfig` et `MMWorkshopVdartsLedsConfig`.

### 2.2 Comment il est appliqué

Une méthode par préréglage, `set_<carte>_card_config()` (`CC:1509-1620`), appelle `set_config(section, valeurs)` pour chacune des quatre sections. `GBCardConfig` et `ED900Config` règlent aussi `interior = True` (`CC:1571`, `CC:1581`).

`set_config` (`CC:1477-1485`) **parcourt les clés de la section existante** et y copie la valeur du préréglage :
- une clé absente du préréglage donne l'avertissement « No config for … » et garde son ancienne valeur. C'est le cas de `EXTENDED_CONFIG`, qui ne donne que `TOY1` à `TOY4` (`CC:594`) : `TOY5` à `TOY16` gardent leur valeur ;
- une clé du préréglage absente de la section est **ignorée** ;
- **l'ordre des clés ne change pas**.

### 2.3 Comment il est choisi et mémorisé

- **Choix** : seulement dans l'assistant `Screen.wizard` (`CS:1134-1456`), par une liste déroulante (`CS:1146-1148`), puis par une seconde liste pour Jimmy ou Joffrey (`CS:1152-1164`). Les branches `if`/`elif` (`CS:1166-1187`) appellent ensuite la méthode `set_…` correspondante. Le choix `Au` (« Autre ») fait saisir les broches à la main (`CS:1196-1435`).
- **Quand l'assistant se lance** : au premier démarrage, quand il n'y a aucun fichier de configuration (`R:465-482`, `get_rpi_config`, `CS:1036-1040`), et après Paramétrage > Réinitialiser, qui **renomme tout le fichier** en `.back` puis redémarre le jeu (`R:1040-1046`).
- **Mémorisation** : **aucune**. Le nom de la carte n'est enregistré nulle part : seules les valeurs copiées dans les sections le sont, par `write_file` en fin d'assistant (`CS:1456`).
- Au passage, un bogue existe dans le jeu : le choix « MM-Workshop VDarts » appelle `self.set_mmworks_vdartscard_config()` **sur l'écran** et non sur `self.config` (`CS:1175`), ce qui lèverait `AttributeError`. Ce bogue est hors du périmètre ; je n'y touche pas.

### 2.4 Proposition : un préréglage « Carte Pico »

Plutôt que du code spécial dispersé, je propose un préréglage comme les autres, plus une seule ligne propre au mode Pico :

```python
PicoCardConfig = {
    'CARD_NAME': 'Carte Pico (DartsPico-Bridge)',
    'INPUT':  {f'PIN_{n}': '' for n in range(1, 17)},       # aucune broche GPIO
    'OUTPUT': {f'PIN_{n}': '' for n in range(1, 11)},
    'KEYS':   {k: '' for k in DefaultConfig['SectionKeys']}, # étalonnage à refaire (codes R<l>C<c>)
    'EXTENDED': <correspondance du § 3 : BTN1…BTN15, MISS, T1…T8, V1…V10, TOY9…TOY16 et PIN_MUSIC_* vides>,
}

def set_pico_card_config(self):
    self.set_config('Raspberry_BoardPinsIns', PicoCardConfig['INPUT'])
    self.set_config('Raspberry_BoardPinsOuts', PicoCardConfig['OUTPUT'])
    self.set_config('SectionKeys', PicoCardConfig['KEYS'])
    self.set_config('Raspberry', PicoCardConfig['EXTENDED'])
    self.config['SectionAdvanced']['hardware'] = 'pico'      # comme interior pour GB et ED900
```

- Le préréglage couvre **toutes** les clés des quatre sections : `set_config` n'émet donc aucun avertissement « No config for ».
- **Source de la correspondance** (question Q5) : je propose de **ne pas la recopier**. `EXTENDED` serait `cpico.DEFAULTS`, importé seulement dans `set_pico_card_config`. `cconfig` n'importerait donc jamais `cpico` en mode normal. Le test vérifie de toute façon l'égalité avec `hardware/pinout.json`.

**Ce que cela change dans l'interface (Paramétrage)**. Ces changements relèvent d'un patch de `cscreen.py`, **pas du patch 0002** :

| Écran | Aujourd'hui | Avec le préréglage |
|---|---|---|
| Assistant du premier démarrage, et après Paramétrage > Réinitialiser (`CS:1146-1187`) | 9 choix de carte | Un choix de plus, « Carte Pico (DartsPico-Bridge) », qui appelle `set_pico_card_config` et saute les étapes de broches GPIO et de LED de la cible |
| Étalonnage proposé au démarrage (`R:500-506`, `CS:1073-1081`) | Si la cible est sur les GPIO et qu'aucun segment n'est connu | **À adapter** : `calibration_needed` teste `rpi.inputs` et `rpi.outputs`, vides en mode Pico ; sans adaptation, l'étalonnage ne serait jamais proposé |
| Paramétrage > Boutons (`CS:5795-5830`) | Broches `A0`…`D7` | À adapter (D5) : `BTN1`…`BTN15`, `T1`…`T8`, `V1`…`V12` |
| Paramétrage > Tester les Toys (`CS:5941-6006`) | Valeurs `C0`… | Affiche `T1`, `V1`…, sans autre changement |
| Paramétrage > Monnayeur | Réglages du monnayeur | À masquer en mode Pico (D4) |
| Paramétrage > Broches des LED | Broches GPIO des bandes | À adapter avec les serveurs LED (canaux 0 et 1) |

---

## 3. Lignes à modifier dans `include/cconfig.py`

**Aucune ligne existante n'a besoin d'être modifiée** : toutes les modifications sont des **ajouts**. C'est la même règle que pour le patch 0001, et le même test de « pureté » s'applique, avec 0 ligne retirée attendue.

| # | Où | Ajout | Effet en mode normal |
|---|---|---|---|
| 1 | Après `CC:133` (`'game-options-list':'',`), avant `'richard-mode'` | `'hardware': 'normal',` dans `DefaultConfig['SectionAdvanced']` | La clé existe dans `self.config`, avec la valeur `normal`. Aucun code ne parcourt toutes les clés de cette section (vérifié par `grep`), sauf `print()` (`CC:1061-1069`, affichage de débogage) et `deposit_changes` (clé par clé) |
| 2 | Après `CC:951` (fin de `MMWorksVDartsCardConfig`) | Constante `PicoCardConfig` (§ 2.4) | Aucun : la constante n'est lue que par l'ajout 3 |
| 3 | Après `CC:1620` (fin de `set_mmworks_leds_config`) | Méthode `set_pico_card_config` (§ 2.4) | Aucun : elle n'est appelée que par l'assistant, après le patch de `cscreen.py` |
| 4 | Dans `write_file`, entre `CC:1413` et `CC:1414` (avant `### For Richard's case`) | `if str(Advanced.get('hardware', 'normal')).strip().lower() != 'normal':` suivi de deux lignes `self.conf += …` (commentaire et `hardware:<valeur>`) | **Rien n'est écrit** en mode normal : le fichier reste identique octet pour octet (Q1) |
| 5 | *Facultatif, selon Q2* : dans `read_file`, après `CC:1738` (fin du cas V2) | Conversion en mémoire vers le préréglage Pico, quand `hardware = pico` mais que `[Raspberry]` n'a encore aucune valeur `BTN…` ; sections de broches vidées en mémoire | Aucun : la condition est fausse en mode normal |

Le comportement en mode normal reste identique pour un fichier **sans** clé `hardware`. Le seul écart visible concerne un fichier qui contient déjà `hardware:normal` : le jeu d'origine émet « Unexpected hardware in SectionAdvanced » et retire la ligne, alors que le jeu patché ne dit rien et retire aussi la ligne (ajout 4). **Le fichier écrit est identique ; seul le journal diffère.**

---

## 4. Plan de tests

Les tests suivent la même règle que pour le patch 0001 :
- `~/RaspyDarts-ref` est lu en lecture seule, sans bytecode, avec un état git vérifié avant et après ;
- le `cconfig.py` patché est pris dans un clone temporaire préparé par `tools/apply_patches.sh` ;
- **aucun fichier réel de la machine n'est utilisé** : tout se passe dans un dossier temporaire.

### 4.1 Banc d'essai

- **Faux `include.cpaths`** : `config_file()` renvoie un fichier du dossier temporaire ; `user_dir()` renvoie ce dossier ; `ROOT_DIR` pointe vers un faux dossier du jeu qui contient un fichier `VERSION`.
- **`open` du module remplacé** (`module.open = …`) : seuls `/proc/device-tree/model` et `/sys/firmware/devicetree/base/serial-number` (`CC:1042-1049`) sont simulés, par exemple « Raspberry Pi 4 Model B Rev 1.5 » ; tout autre chemin passe à l'`open` réel.
- **Module rechargé à chaque cas**, à cause de `self.config is DefaultConfig` (§ 1.2) : un nouveau nom de module, chargé depuis le fichier, à chaque fois.
- **Journal factice**, qui garde les messages par niveau.
- **Séquence commune** : `Config(logs)`, `check_config_file()`, puis `read_file` de toutes les sections, **dans l'ordre de `R:55-80`**, puis des sections `game-*`. Ensuite, `write_file()`. Le banc relève les octets écrits, le contenu de `self.config` et le journal.

### 4.2 Test différentiel du mode normal (cconfig d'origine contre cconfig patché)

Pour chaque fichier d'entrée, les deux versions font la même séquence. Le test exige que **les fichiers écrits soient identiques octet pour octet**, et que `self.config` soit identique, clé `hardware` mise à part.

| Fichier d'entrée (fabriqué par le test) | Ce qu'il exerce |
|---|---|
| Fichier vide (0 octet) | Sections absentes : valeurs par défaut partout |
| « Par défaut » : le fichier que le cconfig d'origine écrit à partir de ses valeurs par défaut | Aller-retour stable |
| Ancien type `pydarts.cfg`, fabriqué : `[Raspberry]` en V2 (tout à `'0'`), `font:Casino.ttf`, valeurs entre guillemets, `True`/`False`, clés inconnues, sections manquantes, une section `game-Cricket`, `[Events]` avec un `dict`, codes GPIO dans `[SectionKeys]`, broches dans `[Raspberry_BoardPinsIns]`, une valeur avec `%` | Conversions et cas particuliers de `read_file` |
| Le même fichier ancien, avec `hardware:normal` | Ligne retirée des deux côtés ; seul le journal diffère (§ 3), et le test le vérifie explicitement |
| Fichier qui commence par une marque BOM | Les deux versions s'arrêtent sur `exit(1)` (`SystemExit`) : comportement identique, défaut d'origine non corrigé |

Scénarios rejoués sur chaque fichier :
1. lecture puis réécriture ;
2. lecture, puis `set_config('Raspberry', …)` comme dans Paramétrage > Boutons (`R:1060`), puis réécriture ;
3. lecture, puis `set_game_config(…)`, puis réécriture ;
4. lecture, puis chaque `set_<carte>_card_config()` d'origine, puis réécriture. Le préréglage Pico ne doit rien changer aux autres.

Le test vérifie aussi que **le patch ne retire aucune ligne** de `cconfig.py`, avec le même test de pureté que pour 0001.

### 4.3 Mode Pico

- **La clé survit** : `hardware:pico` est lu, réécrit, puis relu en `pico`. Une seconde réécriture donne exactement le même fichier (idempotence). Les variantes `Pico`, avec des espaces et des majuscules, et une valeur inconnue sont réécrites telles quelles (Q1).
- **Le préréglage est appliqué** : après `set_pico_card_config()` :
  - `[Raspberry]` vaut `cpico.DEFAULTS` et correspond à `hardware/pinout.json` : 15 boutons, `MISS`, `T1` à `T8`, `V1` à `V10`, `TOY9` à `TOY16` vides ;
  - `[Raspberry_BoardPinsIns]` et `[Raspberry_BoardPinsOuts]` sont vides, et `[SectionKeys]` aussi ;
  - `hardware` vaut `pico` ;
  - aucun avertissement « No config for » n'est émis ;
  - le fichier écrit contient bien ces valeurs, **dans l'ordre d'origine des clés**.
- **Lien avec le patch 0001** : avec le fichier écrit, le `craspberry.py` patché (chargé comme dans `tests/test_patches.py`) passe en mode Pico, n'appelle aucun GPIO, et trouve 15 boutons dans `PicoExtender`.
- **Conversion à la lecture** (si Q2 = (b)) : un ancien fichier avec `hardware:pico` est converti en mémoire, avec une copie de sauvegarde ; un fichier déjà au format Pico ne change pas.
- **Fichier déposé** : un `raspydarts.cfg.depose` qui contient `hardware:pico` est accepté par `deposit_changes`, et la clé n'y est plus écartée.

---

## 5. Risques

| N° | Risque | Parade |
|---|---|---|
| R1 | **Migration de votre configuration**, importée de l'ancienne installation, vers le mode Pico. Elle contient les broches GPIO de l'ancienne cible, des codes de segment GPIO (`"2410"`), un `[Raspberry]` en `A0`…, les broches des LED (18, 21…), et des événements qui citent peut-être `TOY9` à `TOY16`. Ajouter seulement `hardware:pico` donne un jeu **sans boutons** (valeurs `A0` non reconnues par `PicoExtender`) et **sans segments** (anciens codes) | Une migration qui **garde tout le reste** (favoris, joueurs, événements, couleurs, options des jeux) et ne touche qu'à `[Raspberry]`, aux deux sections de broches et aux codes de `[SectionKeys]`, avec une copie de sauvegarde (Q2) |
| R2 | « Réinitialiser » pour passer en mode Pico **renomme tout le fichier** (`R:1044`) : favoris, joueurs, événements et options des jeux sont perdus | Ne pas utiliser « Réinitialiser » pour la migration ; le dire dans la documentation d'installation |
| R3 | La clé `hardware` doit être réécrite explicitement par `write_file`, sinon elle est perdue au premier enregistrement (§ 1.3) | Ajout 4 et test « la clé survit » |
| R4 | `self.config` est `DefaultConfig` (`CC:983`) : `init_section` renvoie des valeurs chargées, et non les valeurs par défaut | Les tests rechargent le module à chaque cas ; le patch ne s'appuie jamais sur `init_section` pour retrouver un défaut |
| R5 | **Ordre de lecture** : la conversion à la lecture (ajout 5) suppose que `SectionAdvanced` est lue avant `Raspberry` et les sections de broches, ce qui est le cas aujourd'hui (`R:57`, puis `R:61`, `R:75`, `R:77`) | Le test reproduit cet ordre. Si l'ordre change dans une future version de RaspyDarts, la conversion ne se fait pas, mais le mode normal n'est pas touché |
| R6 | `calibration_needed` (`CS:1078`) ne propose jamais l'étalonnage en mode Pico, puisque `inputs` et `outputs` sont vides | Patch de `cscreen.py` à prévoir ; en attendant, l'étalonnage reste possible depuis l'assistant |
| R7 | **Serveurs LED** : `R:134-137` et `R:166-203` lancent les serveurs avec `sudo` et le vrai `rpi_ws281x` si `PIN_STRIPLED` ou `PIN_TARGETLED` vaut 10, 12, 18 ou 21. Une ancienne configuration en mode Pico les lancerait donc sur les GPIO du Pi | Patch des serveurs LED (canaux 0 et 1, remplaçant LED) ; en attendant, le préréglage pourrait vider ces deux valeurs (Q4) |
| R8 | Une marque BOM dans `raspydarts.cfg` fait sortir le jeu d'origine (`CC:1674-1677`), alors que le patch 0001 la lit sans problème | Défaut d'origine, **non corrigé** dans 0002 (mode normal strictement identique) ; à corriger seulement avec votre accord |
| R9 | `[SectionAdvanced]` d'origine ne réécrit pas 11 de ses clés (§ 1.3) | Hors du périmètre ; non corrigé |
| R10 | La correspondance Pico existe à deux endroits (`cpico.DEFAULTS` et le préréglage) | Import différé de `cpico.DEFAULTS` (Q5), plus un test d'égalité avec `pinout.json` |
| R11 | Fichier déposé : une fois `hardware` connue, un `raspydarts.cfg.depose` peut faire passer le jeu en mode Pico, ou le faire revenir au mode normal | Comportement voulu ; à signaler dans la documentation |

---

## 6. Questions à trancher avant d'écrire le patch

| N° | Question | Proposition |
|---|---|---|
| Q1 | Écrire la ligne `hardware` **toujours**, ou **seulement si elle ne vaut pas `normal`** ? | **Seulement si elle ne vaut pas `normal`**, en gardant la valeur lue telle quelle (une faute de frappe reste visible). C'est la seule façon de garder le mode normal identique octet pour octet |
| Q2 | Comment migrer une configuration existante vers le mode Pico ? (a) **un outil à part**, `tools/migrate_to_pico.py`, lancé à la main sur une copie, avec sauvegarde ; (b) une conversion en mémoire dans `read_file` (ajout 5), écrite au premier enregistrement ; (c) un choix dans Paramétrage | **(a)** : rien de plus dans le jeu, l'outil est testé dans ce dépôt, et l'opération reste explicite. Le préréglage sert pour les nouvelles installations et pour l'assistant |
| Q3 | Le préréglage (et la migration) vident-ils les codes de `[SectionKeys]`, pour forcer un nouvel étalonnage ? | **Oui** : les anciens codes GPIO ne correspondent à aucun contact `R<l>C<c>` |
| Q4 | Le préréglage touche-t-il `[Raspberry_Leds]` ? | **Non dans 0002**. Il sera traité avec le patch des serveurs LED (R7) |
| Q5 | Correspondance du préréglage : recopiée dans `cconfig`, ou `cpico.DEFAULTS` importé seulement en mode Pico ? | **Importée** (une seule source), avec un test d'égalité contre `pinout.json` |
