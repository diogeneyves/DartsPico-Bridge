#!/bin/sh
# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
#
# Contrôle « fork = BASE + série » (spécification, compléments 79 et 80), sans SHA reproductibles.
#
# Usage : tools/check_fork.sh FORK RÉFÉRENCE
#
#   FORK       clone du fork RaspyDarts-Pico-game, branche à contrôler extraite (HEAD)
#   RÉFÉRENCE  fork fraîchement construit par tools/build_fork.sh
#
# Vérifie, pour FORK et RÉFÉRENCE : HEAD~11 = BASE (pi/patches/BASE), 11 commits exactement,
# sans fusion. Puis, commit par commit : arbre (^{tree}), message complet (%B) et auteur
# (nom et adresse) égaux, date d'auteur égale pour les 10 patchs (pas pour le 11e commit,
# daté de sa fabrication). Le committer n'est pas comparé. Tous les écarts sont signalés.
#
# Codes de sortie : 0 conforme, 1 usage, 2 structure (BASE, nombre de commits, fusion),
# 3 écart de contenu (arbre, message ou auteur).

set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd -P)

case "${1:-}" in
    -h|--help) sed -n '4,19p' "$0"; exit 0 ;;
esac
[ $# -eq 2 ] || { echo "Usage : $0 FORK RÉFÉRENCE" >&2; exit 1; }
FORK=$1
REF=$2
BASE=$(head -n 1 "$ROOT/pi/patches/BASE" | tr -d '[:space:]')
N=11

structure() {   # $1 dossier, $2 nom
    git -C "$1" rev-parse --verify --quiet HEAD >/dev/null || {
        echo "Structure : $2 ($1) n'est pas un dépôt git avec un HEAD." >&2; exit 2; }
    count=$(git -C "$1" rev-list --count "$BASE..HEAD" 2>/dev/null || echo "?")
    [ "$count" = "$N" ] || {
        echo "Structure : $2 a $count commit(s) après BASE $BASE ; $N commits attendus." >&2; exit 2; }
    [ "$(git -C "$1" rev-parse HEAD~$N)" = "$BASE" ] || {
        echo "Structure : HEAD~$N de $2 n'est pas BASE $BASE." >&2; exit 2; }
    [ "$(git -C "$1" rev-list --count --merges "$BASE..HEAD")" = 0 ] || {
        echo "Structure : $2 contient un commit de fusion." >&2; exit 2; }
}
structure "$FORK" "le fork"
structure "$REF" "la référence"

FORK_LIST=$(git -C "$FORK" rev-list --reverse "$BASE..HEAD")
REF_LIST=$(git -C "$REF" rev-list --reverse "$BASE..HEAD")

ERR=0
i=0
for f in $FORK_LIST; do
    i=$((i + 1))
    r=$(echo "$REF_LIST" | sed -n "${i}p")
    ft=$(git -C "$FORK" rev-parse "$f^{tree}")
    rt=$(git -C "$REF" rev-parse "$r^{tree}")
    [ "$ft" = "$rt" ] || {
        echo "Écart : commit $i/$N : arbre différent (fork $ft, référence $rt)." >&2; ERR=1; }
    # Messages comparés octet par octet (sortie de cat-file, après la ligne vide).
    fm=$(git -C "$FORK" cat-file commit "$f" | sed '1,/^$/d' | git hash-object --stdin)
    rm_=$(git -C "$REF" cat-file commit "$r" | sed '1,/^$/d' | git hash-object --stdin)
    [ "$fm" = "$rm_" ] || {
        echo "Écart : commit $i/$N : message différent (sujet du fork : « $(git -C "$FORK" log -1 --format=%s "$f") »)." >&2
        ERR=1; }
    if [ "$i" -lt "$N" ]; then fmt='%an <%ae> %ad'; else fmt='%an <%ae>'; fi
    fa=$(git -C "$FORK" log -1 --date=raw --format="$fmt" "$f")
    ra=$(git -C "$REF" log -1 --date=raw --format="$fmt" "$r")
    [ "$fa" = "$ra" ] || {
        echo "Écart : commit $i/$N : auteur différent (fork « $fa », référence « $ra »)." >&2; ERR=1; }
done

if [ "$ERR" -ne 0 ]; then
    echo "Le fork n'est pas BASE + série : voir les écarts ci-dessus." >&2
    exit 3
fi
echo "OK : le fork est BASE $BASE + $N commits conformes à la référence (arbres, messages, auteurs)."
