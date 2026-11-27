#!/bin/sh
# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
#
# Construit la branche main du fork RaspyDarts-Pico-game (spécification, compléments 78 à 80) :
# BASE (pi/patches/BASE) + patchs de pi/patches/series + 11e commit des fichiers nouveaux.
#
# Usage : tools/build_fork.sh [--upstream URL] <dossier>
#
#   --upstream  dépôt à cloner (défaut : https://github.com/rmdelcelier/RaspyDarts) ;
#               un dossier local est cloné avec --shared (rien n'y est écrit)
#   <dossier>   dossier de travail, inexistant ou vide, hors du dépôt de ce projet
#
# Clone l'amont dans <dossier>, place BASE en tête détachée, puis lance tools/apply_patches.sh
# complet. Auteur du 11e commit et committer des 11 commits : Gohy Yves
# <6154641+diogeneyves@users.noreply.github.com> (GIT_AUTHOR_* et GIT_COMMITTER_*, qui priment
# sur l'identité de apply_patches.sh) ; les 10 patchs gardent l'auteur et la date de leurs
# en-têtes. Ne pousse pas : affiche la commande de push, à lancer soi-même.
# Refuse les copies de référence (~/RaspyDarts-orig, ~/RaspyDarts-ref, ~/RaspyDarts-ref465,
# RASPYDARTS_ORIG) et ~/RaspyDarts-work, ainsi que tout dossier situé dessous. Ne supprime rien.
#
# Codes de sortie : 0 succès, 1 usage, 2 dossier refusé, 3 copie de référence refusée,
# 4 clone ou checkout impossible, 5 application des patchs impossible, 6 résultat inattendu.

set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
UPSTREAM=https://github.com/rmdelcelier/RaspyDarts
NAME="Gohy Yves"
EMAIL="6154641+diogeneyves@users.noreply.github.com"

while [ $# -gt 0 ]; do
    case "$1" in
        --upstream) [ $# -ge 2 ] || { echo "Option --upstream sans valeur." >&2; exit 1; }
                    UPSTREAM=$2; shift 2 ;;
        -h|--help) sed -n '4,24p' "$0"; exit 0 ;;
        -*) echo "Option inconnue : $1" >&2; exit 1 ;;
        *) break ;;
    esac
done
[ $# -eq 1 ] || { echo "Usage : $0 [--upstream URL] <dossier>" >&2; exit 1; }

# Chemin réel du dossier, sans le créer.
DEST=$1
if [ -e "$DEST" ]; then
    [ -d "$DEST" ] || { echo "Refusé : $DEST n'est pas un dossier." >&2; exit 2; }
    DEST_REAL=$(cd "$DEST" && pwd -P)
else
    PARENT=$(dirname "$DEST")
    [ -d "$PARENT" ] || { echo "Refusé : le dossier parent $PARENT n'existe pas." >&2; exit 2; }
    DEST_REAL=$(cd "$PARENT" && pwd -P)/$(basename "$DEST")
fi

real() { (cd "$1" 2>/dev/null && pwd -P) || echo "$1"; }

# Copies de référence et copie de travail : jamais, ni dessous.
for f in "$HOME/RaspyDarts-orig" "$HOME/RaspyDarts-ref" "$HOME/RaspyDarts-ref465" \
         "${RASPYDARTS_ORIG:-$HOME/RaspyDarts-ref465}" "$HOME/RaspyDarts-work"; do
    F=$(real "$f")
    case "$DEST_REAL/" in
        "$F/"*) echo "Refusé : $DEST_REAL est (ou est dans) la copie de référence ou de travail $F, jamais modifiée." >&2
                exit 3 ;;
    esac
done

# Hors du dépôt de ce projet (aujourd'hui My_GIT_Projects).
TOP=$(git -C "$ROOT" rev-parse --show-toplevel 2>/dev/null || echo "$ROOT")
TOP=$(real "$TOP")
case "$DEST_REAL/" in
    "$TOP/"*) echo "Refusé : $DEST_REAL est dans le dépôt $TOP ; choisir un dossier hors du dépôt." >&2
              exit 2 ;;
esac
if [ -d "$DEST_REAL" ] && [ -n "$(ls -A "$DEST_REAL")" ]; then
    echo "Refusé : $DEST_REAL n'est pas vide (rien n'est supprimé)." >&2
    exit 2
fi

BASE=$(head -n 1 "$ROOT/pi/patches/BASE" | tr -d '[:space:]')

SHARED=""
[ -d "$UPSTREAM" ] && SHARED="--shared"     # copie locale : objets lus, rien n'y est écrit
# shellcheck disable=SC2086
git -c advice.detachedHead=false clone --quiet $SHARED "$UPSTREAM" "$DEST_REAL" || {
    echo "Clone impossible : $UPSTREAM." >&2; exit 4; }
git -C "$DEST_REAL" -c advice.detachedHead=false checkout --quiet --detach "$BASE" || {
    echo "Le commit BASE $BASE est introuvable dans $UPSTREAM." >&2; exit 4; }

GIT_AUTHOR_NAME=$NAME GIT_AUTHOR_EMAIL=$EMAIL \
GIT_COMMITTER_NAME=$NAME GIT_COMMITTER_EMAIL=$EMAIL \
    "$ROOT/tools/apply_patches.sh" "$DEST_REAL" >/dev/null || {
    echo "Échec de tools/apply_patches.sh sur $DEST_REAL (dossier laissé tel quel)." >&2; exit 5; }

# Résultat : BASE + 11 commits.
[ "$(git -C "$DEST_REAL" rev-parse HEAD~11)" = "$BASE" ] &&
[ "$(git -C "$DEST_REAL" rev-list --count "$BASE..HEAD")" -eq 11 ] || {
    echo "Résultat inattendu : HEAD~11 n'est pas BASE $BASE, ou pas 11 commits." >&2; exit 6; }

echo "OK : fork construit dans $DEST_REAL (BASE $BASE + 11 commits, tête $(git -C "$DEST_REAL" rev-parse --short HEAD))."
echo "Contrôle conseillé : tools/check_fork.sh <fork> $DEST_REAL"
echo "Commande de push (non lancée, à lancer soi-même) :"
echo "  git -C $DEST_REAL push --force <URL-FORK> HEAD:main"
