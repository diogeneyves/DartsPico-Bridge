#!/usr/bin/env bash
# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
#
# Copie des médias du DMD du Mac vers le Pi (spécification § 12, 41e à 43e compléments ; procédure :
# docs/PROCEDURE.md, « Médias du DMD »). Lancé depuis le Mac par l'auteur seul.
#
# - essai à blanc par défaut ; copie réelle seulement avec --apply ;
# - jamais --delete : un fichier absent de la source reste sur le Pi ;
# - seulement Gifs, SpecialsMoves, Scores, Fonts et Raspy2DMD (Images retiré, 42e complément) ; sans fichiers ni dossiers
#   cachés, sans .cfg, .log, .json ni fichiers datés, sans dossiers vides ;
# - rsync par « ssh -4 » ; options limitées à celles d'openrsync (protocole 29) du Mac :
#   -r, -l, -t, -n, -m, -e, --include, --exclude, --out-format ; -rlt plutôt que -a : les droits de la
#   source (777) ne sont ni recopiés ni comparés, sinon chaque essai à blanc relisterait les dossiers ;
# - après une copie réelle seulement, droits fixés sur la destination : dossiers 755, fichiers 644
#   (la source du Mac est en 777 ; --chmod d'openrsync est accepté mais sans effet, 43e
#   complément). Destination distante : par « ssh -4 », seulement sous dmd-medias (chemin
#   relatif : dossier personnel de l'utilisateur distant), ~/dmd-medias ou
#   /home/<utilisateur>/dmd-medias si --dest nomme l'utilisateur (complément 77) ;
#   destination locale (tests) : en local ;
# - ne touche jamais ~/.raspydarts ni les thèmes du jeu.
#
# Codes de sortie : 0 bon, 1 échec de rsync, 2 source absente, 3 échec de la correction des
# droits, 64 usage.

set -euo pipefail

SOURCE=""
DEST="pi@raspydarts.local:dmd-medias/"
APPLY=0

# Liste blanche (premier niveau de la source) et exclusions (41e et 42e compléments)
FOLDERS=(Gifs SpecialsMoves Scores Fonts Raspy2DMD)
EXCLUDED_NAMES=('.*' '*.[cC][fF][gG]' '*.[lL][oO][gG]' '*.[jJ][sS][oO][nN]'
                '*20[0-9][0-9][01][0-9][0-3][0-9]*'
                '*20[0-9][0-9][-_][01][0-9][-_][0-3][0-9]*')

usage() {
    cat <<'EOF'
Usage : tools/sync_medias.sh --source DOSSIER [--apply] [--dest DESTINATION]

Copie les médias du DMD du Mac vers le Pi par rsync (« ssh -4 »).

  --apply             copie réelle ; sans cette option, essai à blanc (rien n'est copié)
  --source DOSSIER    dossier des médias sur le Mac (obligatoire)
  --dest DESTINATION  destination rsync, distante (hôte:chemin/) ou locale
                      (défaut : pi@raspydarts.local:dmd-medias/, chemin relatif au
                      dossier personnel de l'utilisateur distant)
  -h, --help          cette aide

Copiés : Gifs, SpecialsMoves, Scores, Fonts, Raspy2DMD.
Exclus : fichiers et dossiers cachés, .cfg, .log, .json, fichiers datés, dossiers vides,
et tout autre dossier de la source. Aucun fichier n'est jamais effacé sur la destination.

Après --apply, droits fixés sur la destination : dossiers 755, fichiers 644 (sur une
destination distante, seulement sous dmd-medias, ~/dmd-medias ou
/home/<utilisateur>/dmd-medias quand la destination nomme l'utilisateur).
EOF
}

die_usage() {
    echo "sync_medias : $1" >&2
    echo "Aide : tools/sync_medias.sh --help" >&2
    exit 64
}

while [ $# -gt 0 ]; do
    case "$1" in
        --apply) APPLY=1 ;;
        --source) [ $# -ge 2 ] || die_usage "--source attend un dossier"; SOURCE="$2"; shift ;;
        --dest) [ $# -ge 2 ] || die_usage "--dest attend une destination"; DEST="$2"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die_usage "option inconnue : $1" ;;
    esac
    shift
done

[ -n "$SOURCE" ] || die_usage "--source obligatoire (dossier des médias sur le Mac, sans défaut)"
if [ ! -d "$SOURCE" ]; then
    echo "sync_medias : dossier source introuvable : $SOURCE" >&2
    echo "Rien n'est copié. Indiquer le bon dossier avec --source." >&2
    exit 2
fi
[ -n "$DEST" ] || die_usage "destination vide"
SOURCE="${SOURCE%/}"
case "$DEST" in */) ;; *) DEST="$DEST/" ;; esac

# Droits après une copie réelle : dossiers 755, fichiers 644 (43e complément). Racine sur une
# destination distante (complément 77) : dmd-medias, relatif au dossier personnel de
# l'utilisateur distant (« ~/ » retiré : ssh lance la commande dans ce dossier), ou
# /home/<utilisateur>/dmd-medias si la destination nomme l'utilisateur (utilisateur@hôte:).
PERMISSIONS_DIR="dmd-medias"
fix_permissions() {
    local target="$DEST" host="" path root
    if [[ "$DEST" =~ ^[^/:]+: ]]; then
        host="${DEST%%:*}"
        target="${DEST#*:}"
    fi
    path="$target"
    while [ "${#path}" -gt 1 ] && [ "${path%/}" != "$path" ]; do path="${path%/}"; done
    if [ -z "$host" ]; then
        if find "$path" -type d -exec chmod 755 {} + && find "$path" -type f -exec chmod 644 {} +; then
            echo "Droits fixés : dossiers 755, fichiers 644 ($path)."
        else
            echo "sync_medias : échec de la correction des droits sur $path" >&2
            exit 3
        fi
        return
    fi
    case "$path" in "~/"*) path="${path#\~/}" ;; esac
    root="$PERMISSIONS_DIR"
    case "$path" in
        /*)
            if [[ "$host" == *@* ]]; then
                root="/home/${host%%@*}/$PERMISSIONS_DIR"
            else
                root="(utilisateur à nommer dans --dest)/$PERMISSIONS_DIR"
            fi ;;
    esac
    case "$path" in
        "$root"|"$root"/*) ;;
        *)
            echo "sync_medias : avertissement : $path n'est pas sous $root, droits laissés tels quels" >&2
            return ;;
    esac
    if [[ "$path" == *"/.."* || "$path" == *"/./"* || ! "$path" =~ ^[A-Za-z0-9._/\ -]+$ ]]; then
        echo "sync_medias : avertissement : chemin refusé pour la correction des droits : $path" >&2
        return
    fi
    if ssh -4 "$host" "find '$path' -type d -exec chmod 755 {} + && find '$path' -type f -exec chmod 644 {} +"; then
        echo "Droits fixés sur $host : dossiers 755, fichiers 644 ($path)."
    else
        echo "sync_medias : échec de la correction des droits sur $host:$path" >&2
        exit 3
    fi
}

# Résumé calculé localement, avec les mêmes règles que rsync
present=()
for folder in "${FOLDERS[@]}"; do
    [ -d "$SOURCE/$folder" ] && present+=("$folder")
done
if [ ${#present[@]} -eq 0 ]; then
    echo "sync_medias : aucun des dossiers ${FOLDERS[*]} dans $SOURCE" >&2
    exit 2
fi

prune=()
for name in "${EXCLUDED_NAMES[@]}"; do
    [ ${#prune[@]} -gt 0 ] && prune+=(-o)
    prune+=(-name "$name")
done

if stat -c %s / >/dev/null 2>&1; then
    size_of=(stat -c %s)      # GNU
else
    size_of=(stat -f %z)      # BSD (macOS)
fi

count=0
bytes=0
for folder in "${present[@]}"; do
    while IFS= read -r -d '' file; do
        count=$((count + 1))
        bytes=$((bytes + $("${size_of[@]}" "$file")))
    done < <(find "$SOURCE/$folder" \( "${prune[@]}" \) -prune -o -type f -print0)
done
megabytes=$(awk -v b="$bytes" 'BEGIN { printf "%.1f", b / 1048576 }')

echo "Source      : $SOURCE"
echo "Destination : $DEST"
echo "Dossiers    : ${present[*]}"
echo "Périmètre   : $count fichiers, $bytes octets ($megabytes Mio)"
if [ "$APPLY" -eq 1 ]; then
    echo "Mode        : COPIE RÉELLE (--apply)"
else
    echo "Mode        : essai à blanc (rien n'est copié ; ajouter --apply pour copier)"
fi
echo "Fichiers à transférer (absents ou différents sur la destination) :"

options=(-rlt -m -e "ssh -4" --out-format='%n')
[ "$APPLY" -eq 1 ] || options+=(-n)
for name in "${EXCLUDED_NAMES[@]}"; do
    options+=(--exclude="$name")
done
for folder in "${present[@]}"; do
    options+=(--include="/$folder/" --include="/$folder/**")
done
options+=(--exclude='*')

if ! rsync "${options[@]}" "$SOURCE/" "$DEST"; then
    echo "sync_medias : échec de rsync" >&2
    exit 1
fi

if [ "$APPLY" -eq 1 ]; then
    echo "Copie terminée."
    fix_permissions
else
    echo "Essai à blanc terminé : rien n'a été copié."
fi
