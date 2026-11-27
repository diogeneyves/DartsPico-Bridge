# pi/dmd

Service de rendu du DMD (texte, défilement, GIF, images) en 128×32, envoi des trames à l'Interstate 75 W (spécification § 9.3).
Jalon **M4**. Rendu réimplémenté d'après la documentation publique de Raspy2DMD et le comportement observé : aucun code de Raspy2DMD n'est copié ici (spécification § 9.3, complément 76).

## Cœur (sous-phase SR-a, § 9.9)

`dmd_core.py` : Python pur, sans Pillow ni MQTT. Analyse des messages des sujets
`raspydarts/dmd` et `raspy2dmd` (§ 6.6), machine d'états (accueil, affichage, Attract
Mode), tirage « en sac » des GIF, heure toutes les 10 GIF si l'heure est synchronisée,
horloge injectable. Il rend des commandes abstraites (`Welcome`, `ShowText`,
`ShowMedia`, `ShowClock`, `ShowPattern`) que le rendu (SR-b, SR-c) traduit en images.
Tests : `tests/test_dmd_core.py`.

## Rendu du texte et publication (sous-phase SR-b, § 9.9, 46e complément)

`dmd_render.py` (Pillow 11.1.0) : traduit les commandes du cœur en scènes 128 × 32
(texte fixe, défilant, en couleur, écran d'accueil, mire RGB) ; police
`<dmd_media_dir>/Fonts/Impact.ttf`, sinon police intégrée de Pillow ; conversion RGB565
par `dmd_proto.rgb565`. `dmd_service.py` (sans Pillow) : publie les images complètes de
8 192 octets sur `raspydarts/hw/dmd/frame`, au plus 15 par seconde, image fixe répétée
toutes les secondes ; `MqttLink` (paho-mqtt 2) et `main()`. GIF, scores et heure : SR-c.
Tests : `tests/test_dmd_render.py` et `tests/test_dmd_service.py` (rendu sauté sans Pillow).

## GIF et PPM (sous-phase SR-c, § 9.9, 48e complément)

`dmd_gif.py` (bibliothèque standard seule) : délais des images lus dans les blocs GCE
(0, 1 ou absent : 10/100 s), taille affichée (inchangée si l'image tient dans 128 × 32,
sinon réduite proportionnellement), minutage d'un passage (Attract Mode : rejoué jusqu'à
5 s, coupé à 60 s, réglable par `attract_gif_max_s`, 50e complément). `dmd_render.py` : scènes `MediaScene` (GIF décodé au fur et à mesure,
voisin le plus proche, centré sur fond noir ; à chaque pas, l'image visible à cet instant,
les plus courtes sont sautées ; PPM affiché 2 s) et `FailedMediaScene` (fichier manquant
ou corrompu : texte de repli ou accueil, journalisé une fois par fichier).
`dmd_service.py` relaie la fin du média au cœur (`core.media_done(jeton)`).
Scores : `<--media-dir>/Scores/<code de la fléchette>/*.gif|*.ppm` lus et décodés
d'avance au démarrage (changement : redémarrer le service), fichier tiré au hasard, joué
une fois (PPM : 2 s), puis le texte du score fixe (le cœur le rend à `media_done`) ;
fichier corrompu journalisé une fois et jamais tiré ; aucun fichier lisible : texte seul.
Attract Mode branché dans `run()` et heure : commits suivants de SR-c.
Tests : `tests/test_dmd_gif.py` (partout), `tests/test_dmd_render.py` (sautés sans Pillow),
`tests/test_dmd_service.py`.

## Coups spéciaux et textes en attente (sous-phase SR-f, § 9.9, 49e complément)

`dmd_core.special_move()` reconnaît le coup spécial d'une volée complète (`score|…|O`,
aucun `X`) selon la table du 49e complément, dans son ordre de priorité ; valeur inconnue,
règle sans dossier (`RED_HAT`, `BAIL_OUT`) ou dossier vide : score ordinaire. Les dossiers
de `<--media-dir>/SpecialsMoves` que la table utilise sont décodés au démarrage et gardés
en mémoire, comme `Scores` (17,6 Mo pour les GIF de l'auteur) ; `DEVIL` joue un fichier
précis, les autres un fichier tiré au hasard. Pendant le média d'un score (coup spécial,
GIF ou PPM), les textes attendent : le dernier reçu s'affiche après le média, à la place du
texte du score ; seuls un nouveau score, `demo|gif` et `pico|test_rgb` interrompent.
Option `--no-special-moves` : coups spéciaux coupés (activés par défaut).

## DMD virtuel et mode rejeu (sous-phase SR-d, § 9.9, 47e complément)

`dmd_web.py` (bibliothèque standard seule) : page servie sur **`127.0.0.1` seulement**,
port `--web-port` (défaut **8081**, 0 = coupé). Elle affiche exactement les octets passés à
la publication (copie interne, sans abonnement MQTT), poussés par SSE (`/events`) ;
agrandissement ×4, ×6 ou ×8, ligne d'état (images/s, images reçues, âge, connexion) et
dernier message du jeu (affiché par `textContent`). Dernière image seulement par client,
4 clients au plus. Depuis le Mac :

    ssh -4 -L 8081:127.0.0.1:8081 pi@raspydarts.local     puis http://127.0.0.1:8081/

`dmd_replay.py` : mode rejeu, `dmd_service.py --replay <journal> [--loop] [--speed N]`.
**Jamais de MQTT** (rien n'est envoyé à `hwd`), page sur le port **8082** par défaut.
Utilisé sur le Pi (qui a Pillow) et regardé depuis le Mac par
`ssh -4 -L 8082:127.0.0.1:8082 pi@raspydarts.local` ; rien n'est installé sur le Mac.
Journal : une ligne `<délai en s depuis le message précédent> <sujet> <contenu>`, lignes `#`
ignorées ; exemple : `replay_example.txt`. Pour enregistrer une vraie partie sur le Pi :
`python3 tools/dmd_record.py -o partie.txt` (ajoute au fichier, Ctrl-C pour arrêter).
Tests : `tests/test_dmd_web.py` et `tests/test_dmd_replay.py` (essai avec le vrai rendu sauté
sans Pillow).

## Service systemd, configuration et installation (sous-phase SR-e, § 9.9, 50e complément)

`dmd_config.py` : configuration TOML (`/etc/raspydarts/dmd.toml` sur le Pi, exemple
`dmd.example.toml`), lue par `--config` ; clé inconnue ou valeur hors plage : arrêt avec un
message ; les options de la ligne de commande l'emportent. `dmd_service.py` : `../../shared`
ajouté à `sys.path` (comme `hwd`), événement d'arrêt (`ShutdownRequest`, SIGTERM et SIGINT),
journaux du démarrage, du décodage (avec sa durée), de l'état NTP et de l'arrêt (avec sa cause).
Attract Mode : GIF coupé à 60 s (`attract_gif_max_s`), heure toutes les 2 min
(`attract_clock_every_s`), après la fin du GIF en cours. Unité : `pi/systemd/raspydarts-dmd.service`
(et `raspydarts-hwd.service`), installées sans être activées par `tools/install_services.sh`
(procédure : `docs/PROCEDURE.md`, § 2). Tests : `tests/test_dmd_sre.py`.

## Écran HDMI dédié (§ 9.9, complément 72)

Alternative au DMD physique : clé `hdmi_output = 1` de `dmd.toml` (défaut 0, sans option en
ligne de commande, jamais en rejeu). `dmd_hdmi.py` (Python pur, testé) : `HdmiOutput` lance
`dmd_hdmi_screen.py` avec `SDL_VIDEODRIVER=wayland` et lui envoie les images depuis `publish()`
par un tube non bloquant (seule la dernière compte, la boucle des images n'attend jamais) ;
conversion RGB565 → RGB, échelle entière et place (`fit`), choix de l'écran `HDMI-A-2` par son
nom (`choose_display`), branchement relu toutes les 5 s (`Connector`). `dmd_hdmi_screen.py` :
couche pygame mince, **non testée hors du Pi** (ni le Mac ni la CI n'ont pygame). Depuis le complément 73 : image RGB565 copiée telle quelle dans une surface 16 bits
(`MASKS`), agrandie en C, seule la bande mise à jour, image identique non redessinée. Complément 74 : rendu accéléré
d'abord (texture 128 × 32 agrandie par le GPU, `pygame._sdl2.video`), repli sur la surface 16 bits ;
chemin (`accelerated` ou `surface`) écrit dans la ligne « full screen on HDMI-A-2 … ». Complément 75 : le chemin
accéléré ouvre sa propre fenêtre `pygame._sdl2.video.Window` (plein écran sur le bureau de
l'écran choisi, sans `set_mode`) ; en cas d'échec, elle est fermée et le chemin `set_mode` prend le relais.
Procédure : `docs/PROCEDURE.md`, § 3. Tests : `tests/test_dmd_hdmi.py`.
