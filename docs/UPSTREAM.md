# Version de RaspyDarts de référence

Les modifications de RaspyDarts (`pi/patches/`) s'appliquent sur ce commit précis.

| Élément | Valeur |
|---|---|
| Dépôt | `https://github.com/rmdelcelier/RaspyDarts` (branche `main`) |
| Commit | `8fa61ffe9c1c264f49c3300ea61a37100b84b10c` (étiquette `_v4.6.5`) |
| Date du commit | 20 novembre 2026, 15:20:08 +01:00 |
| Fichier `VERSION` | `V4.6.5_202611201520` |
| Copie locale | `~/RaspyDarts-ref465` (clone du 22 novembre 2026, tête `32edb12`, **extraite (détachée) sur `8fa61ff`** par l'auteur ; hors de ce dépôt, en lecture seule, jamais modifiée ; aucun code copié ici). Copie par défaut des tests et de `tools/apply_patches.sh` (`RASPYDARTS_ORIG`). Anciennes références gardées sans y toucher : `~/RaspyDarts-ref` (4.6.4, `5c85f6a`) et `~/RaspyDarts-orig` (4.6.3) |
| Copie de travail | `~/RaspyDarts-work`, branche `pico-8fa61ff` (la branche `pico-5c85f6a` est gardée telle quelle) |
| Base adoptée le | 22 novembre 2026 |
| Fork publié | **[À VENIR]** `RaspyDarts-Pico-game`, fork public de `rmdelcelier/RaspyDarts` (spécification, complément 78), branche `main` : `8fa61ff` + patchs de `pi/patches/series` + un 11e commit avec les fichiers nouveaux et le bandeau du `README.md` (complément 83). Adresse : à inscrire à sa création |

**Écart avec la tête du clone.** `~/RaspyDarts-ref465` est en `32edb12` (20 novembre 2026, 21:51), 6 commits après `8fa61ff` : `e506946` (configuration, section absente), `ce4bf55` (`scripts/prepare_image.sh`), `db3e431` (export de thème), `8b5914c` et `5be080e` (fond animé, Pi Zero 2 W), `58bb4b4` (fond animé sous l'effet), `32edb12` (manifeste Raspberry Pi Imager). Leur `VERSION` reste `V4.6.5_202611201520`, mais ils touchent du code : `include/cconfig.py` et `include/cscreen.py` (deux fichiers patchés), `include/cbganim.py`, `include/cthemefiles.py`. Ils ne sont **pas** dans la base, qui reste l'archive publiée (`update_V4.6.5_20261120_1520.tgz`). `tools/apply_patches.sh --check ~/RaspyDarts-ref465` passe (le clone temporaire est placé sur `8fa61ff`). Les tests, eux, lisent les fichiers Python extraits de la copie : ils vérifient qu'ils sont ceux de `8fa61ff` (`tests/_orig.py`) et s'arrêtent en erreur sinon ; c'est pourquoi `~/RaspyDarts-ref465` reste extraite sur `8fa61ff`, et non sur sa tête `32edb12`.

Changer de commit de référence est une décision de l'auteur : il faut alors refaire la cartographie de `docs/PI_INTERFACE.md` et vérifier que chaque patch s'applique encore.

## Fork `RaspyDarts-Pico-game` (complément 78)

- **Contenu** : le commit de `pi/patches/BASE` (`8fa61ff`), les patchs dans l'ordre de `pi/patches/series`, puis un 11e commit des fichiers nouveaux (`include/cpico.py`, `include/hwbus.py`, `include/pico_proto.py`, `pico_shims/rpi_ws281x.py`) et du `README.md` avec bandeau (complément 83), comme `tools/apply_patches.sh`. Les commits amont postérieurs à `8fa61ff` n'y sont pas.
- **Règle de régénération** : RaspyDarts-Pico est la source de vérité (`pi/patches/` et les 4 fichiers). Le fork n'est **jamais modifié à la main** : toute modification passe par un patch ou un fichier de ce dépôt, puis le fork est régénéré par `tools/build_fork.sh <dossier>` (complément 80) : clone de l'amont (`--upstream URL`, défaut `https://github.com/rmdelcelier/RaspyDarts`) dans un dossier inexistant ou vide hors de `My_GIT_Projects`, `BASE` en tête détachée, `tools/apply_patches.sh` complet ; auteur du 11e commit et committer des 11 commits : `Gohy Yves <6154641+diogeneyves@users.noreply.github.com>`. Le script ne pousse pas : il affiche `git -C <dossier> push --force <URL-FORK> HEAD:main`, que l'auteur lance lui-même (force-push limité au fork, complément 79). Un changement de base (ci-dessus) entraîne une nouvelle régénération.
- **Contrôle** : `tools/check_fork.sh FORK RÉFÉRENCE` (complément 80), RÉFÉRENCE étant un fork fraîchement construit par `tools/build_fork.sh` : `FORK~11` = `BASE`, 11 commits sans fusion, et pour chacun arbre, message complet et auteur égaux (date d'auteur comparée pour les 10 patchs seulement) ; pas de SHA reproductibles. Testé par `tests/test_build_fork.py`, avec deux témoins (arbre altéré, message altéré). Le job de CI `fork-check` viendra après la création du fork.
- **README du fork** (complément 83, qui clôt l'écart du complément 80, Q7) : modifié **dans le 11e commit**, toujours 11 commits exactement. Un bandeau, en français puis en anglais, est ajouté **en tête** du `README.md` de l'amont, dont le texte reste intact en dessous, après une ligne `---` : version de RaspyDarts 4.6.5 (commit `8fa61ff`) modifiée en 2026, adaptée à la carte Pico, liens vers l'original et vers RaspyDarts-Pico, GPL v3, modifications et dates dans l'historique git. Le bandeau vit dans `pi/patches/FORK_README_BANNER.md` (fichier modèle versionné, hors de `series`) ; `tools/apply_patches.sh` (jeu complet) l'ajoute, sujet du 11e commit : « RaspyDarts-Pico: add new files (cpico, hwbus, pico_proto, rpi_ws281x) and README banner ».

## Historique des bases

| Date | Commit | Version | Remarque |
|---|---|---|---|
| 4 novembre 2026 | `85c9f81` | V4.6.1 | Première base |
| 7 novembre 2026 | `5ae22c5` | V4.6.3 | Patchs 0001 et 0002 régénérés (seuls les numéros de ligne changent). `cgpio_extender.read_entries` réécrit en amont (un appui = une action) : `cpico.read_entries` aligné |
| 18 novembre 2026 | `5c85f6a` | V4.6.4 | Patch 0001 : un conflit résolu (`Gpio_extender(…, use_i2c=…)` sous le `else` du mode Pico). Nouveau patch 0003 (`ci2c` : rien en mode Pico). `PicoExtender` : `blink_toy` et mode capture. Copie de référence : `~/RaspyDarts-ref` |
| 22 novembre 2026 | `8fa61ff` | V4.6.5 | `raspydarts.py` et `include/cscreen.py` passés de CRLF à LF en amont : patchs 0004, 0005 et 0007 régénérés en LF, sans autre changement. Patch 0001 : deux conflits résolus (imports de `cplatform` et `cgpio_none` gardés avec la garde `PICO` ; `pins_usable` combiné à `PICO`), et sur une machine sans GPIO l'absence de Blinka ne donne plus le mode Pico (spécification, 21e complément de la v0.3.14). Patch 0006 : garde `send-stats` placée avant le nouvel import de `cplatform` dans `send_infos`. `cpico` : file des fléchettes en couples `(code, instant)` (19e complément). Copie de référence : `~/RaspyDarts-ref465` |

## Versions publiées depuis la base

| Date | Tag | Remarque |
|---|---|---|
| 20 novembre 2026 | `_v4.6.5` | Archive légère `update_V4.6.5_20261120_1520.tgz`. Passage à cette base : plus tard. En mode Pico, le jeu ne la propose plus (patch 0007, spécification § 9.8) |
