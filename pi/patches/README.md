# pi/patches

Modifications des fichiers **existants** de RaspyDarts, pour le mode « carte Pico » (méthode : `docs/PI_INTERFACE.md` § 6). Les fichiers **nouveaux** sont dans `pi/raspydarts_layer/`. Le cœur du jeu (`raspydarts.py`) n'est pas réécrit. Licence GPL v3.

| Fichier | Rôle |
|---|---|
| `BASE` | Commit de RaspyDarts sur lequel s'appliquent les patchs (`docs/UPSTREAM.md`) |
| `series` | Ordre d'application |
| `0001-craspberry-mode-pico.patch` | `include/craspberry.py` : mode Pico (`[SectionAdvanced] hardware = pico`) |
| `0002-cconfig-mode-pico.patch` | `include/cconfig.py` : clé `hardware` connue et réécrite (seulement si elle ne vaut pas `normal`), préréglage « Carte Pico » (`set_pico_card_config`) |
| `0003-ci2c-mode-pico.patch` | `include/ci2c.py` (RaspyDarts 4.6.4) : en mode Pico, `follow_wiring` ne fait rien (ni `config.txt`, ni `sudo`, ni redémarrage) |
| `0004-cscreen-mode-pico.patch` | `include/cscreen.py` : en mode Pico, étalonnage tant qu'aucun segment n'est connu (`CAL 0` au début, jamais `CAL 1`), monnayeur masqué, textes « MCP » adaptés, menus Boutons et Toys, choix « Carte Pico » dans l'assistant (table de l'anneau de la cible : 204 LED sans pneu par défaut, 244 LED avec pneu, ou aucune) |
| `0005-raspydarts-serveurs-led-pico.patch` | `raspydarts.py` : en mode Pico, `PIN_TARGETLED` et `PIN_STRIPLED` sont des canaux de la carte (0 à 2 ; cible 0, ruban 1) ; serveurs LED lancés sans `sudo`, avec le remplaçant de `rpi_ws281x` (`pico_shims/` en tête de `PYTHONPATH`, canal dans `RASPYDARTS_PICO_LED_CHANNEL`, broche fictive 18) ; fonctions dans `include/cpico.py` |
| `0006-statistiques-option-send-stats.patch` | `include/cconfig.py`, `include/cupdate.py`, `raspydarts.py` : option `[SectionAdvanced] send-stats` (`True` par défaut) ; `False` : aucune statistique de partie envoyée à `raspystats.raspyhub.fr`, une ligne de journal |
| `0007-mises-a-jour-mode-pico.patch` | `include/cscreen.py`, `raspydarts.py`, `include/cdmdmenu.py` : en mode Pico, bouton « Vérifier les mises à jour » remplacé par un message (`pico-update-disabled`, `pico_text`), ni installation en attente ni vérification au démarrage, boutons Raspy2DMD `install`, `start`, `update` et `uninstall` masqués (spécification § 9.8) |
| `0008-reglages-systeme-mode-pico.patch` | `raspydarts.py` : en mode Pico, `cupdate.apply_system_settings` n'est pas appelée (ni `sudo -n bash scripts/reglages_systeme.sh`, ni sur changement de version, ni sur écoute MQTT doublée), une ligne de journal (spécification § 9.8) |
| `0009-langues-mode-pico.patch` | `locales/*/LC_MESSAGES/pydarts.po` et `.mo` : les clés des textes du mode Pico (`pico_text`, patchs 0004 et 0007) dans les 6 langues, `.mo` recompilés par `msgfmt` (spécification, 23e complément) |
| `0010-galerie-lecture-seule-csrf.patch` | `include/cgallery.py` et `locales/*/LC_MESSAGES/pydarts.po` et `.mo` : sans mot de passe, galerie en lecture seule (suppressions refusées en 403, boutons masqués) ; tout POST, `/login` compris, refusé (403) si `Origin`, ou à défaut `Referer`, n'a pas l'hôte et le port de `Host` ; textes `gallery-warning` et `gallery-no-password` réécrits, `.mo` recompilés par `msgfmt` (spécification, complément 70) |

## Application

```bash
tools/apply_patches.sh --check ~/RaspyDarts-ref465
```

```bash
tools/apply_patches.sh ~/RaspyDarts-work
```

- `--check` applique les patchs dans un clone temporaire, placé sur `BASE`. Il est autorisé sur la copie de référence (`RASPYDARTS_ORIG`, défaut `~/RaspyDarts-ref465`, extraite sur `8fa61ff`), qui n'est pas modifiée ; la copie doit seulement contenir `BASE`.
- Tests : `python3 -m unittest discover -s tests` lit la même copie de référence ; ses fichiers Python doivent être ceux de `BASE`.
- Sans `--check`, le script lance `git am` de chaque patch, puis fait un commit des fichiers nouveaux : `include/cpico.py`, `include/hwbus.py`, `include/pico_proto.py` et `pico_shims/rpi_ws281x.py`.
- **Refus, sans rien modifier** :
  - `HEAD` différent de `BASE` ; avec `--check`, `BASE` absent de la copie (code 2) ;
  - copie qui contient des modifications (code 3) ;
  - copie de référence (`RASPYDARTS_ORIG`), ou `~/RaspyDarts-ref465`, `~/RaspyDarts-ref`, `~/RaspyDarts-orig` (code 4) ;
  - patch qui ne s'applique pas (code 5).

## Produire un patch

Dans `~/RaspyDarts-work` (branche `pico-<BASE>`, aujourd'hui `pico-8fa61ff`, partie de `BASE` ; `pico-5c85f6a` gardée pour la 4.6.4), un commit par patch, puis :

```bash
git -C ~/RaspyDarts-work format-patch -1 --stdout --no-signature HEAD > pi/patches/000N-sujet.patch
```

Ajouter le nom du fichier à `series`. La CI vérifie que tous les patchs s'appliquent sur `BASE`.

### Régénérer toute la série

Après une correction dans `~/RaspyDarts-work` (`git commit --fixup=<commit>` puis `git rebase --autosquash <BASE>`), les patchs se régénèrent tous d'un coup, dans un dossier temporaire :

```bash
git -C ~/RaspyDarts-work format-patch -N --no-signature -o "$TMPDIR/patchs" 8fa61ff..HEAD
```

Puis comparer chaque fichier à celui de `pi/patches/` (`cmp`) : les patchs non touchés doivent ressortir **identiques octet pour octet**, sinon on s'arrête et on cherche pourquoi. Copier ensuite seulement le patch corrigé, sous son nom de `series`, et lancer `tools/apply_patches.sh --check ~/RaspyDarts-ref465`.

Pourquoi ces options :

- `-N` (`--no-numbered`) : sans elle, une série de plusieurs commits donne des sujets `[PATCH 3/10]` ; les fichiers du dépôt portent `[PATCH]`, comme ceux faits un par un avec `-1` ;
- `--no-signature` : sans elle, git ajoute à la fin une ligne `-- ` et sa version (`2.55.0`), qui changerait les fichiers à chaque mise à jour de git ;
- les noms produits par git (tirés du sujet) diffèrent de ceux de `series` : on compare et on copie par numéro (`0001-…`).

Les `.mo` se recompilent par `msgfmt -o locales/<langue>/LC_MESSAGES/pydarts.mo locales/<langue>/LC_MESSAGES/pydarts.po` (GNU gettext), qui redonne à l'octet près les `.mo` de l'amont et ceux des patchs 0009 et 0010.
