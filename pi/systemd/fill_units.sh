#!/bin/sh
# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
#
# Remplit les marqueurs des unités systemd (complément 77) : User=@RASPYDARTS_USER@ et
# Group=@RASPYDARTS_GROUP@. Lancé sur le Pi par tools/install_services.sh --apply, avant toute
# écriture, sur la copie extraite de l'archive :
#
#   sh fill_units.sh <dossier des unités> <utilisateur>
#
# - l'utilisateur doit exister (id) ; le groupe est son groupe principal (id -gn) ;
# - tous les *.service du dossier sont remplis dans des fichiers temporaires, puis remplacés
#   seulement si aucun marqueur @RASPYDARTS_…@ ne reste : sinon, rien n'est modifié.
#
# Codes de sortie : 0 bon, 1 refus (rien n'est modifié).

set -eu

die() { echo "fill_units : $*" >&2; exit 1; }

[ $# -eq 2 ] || die "usage : fill_units.sh <dossier> <utilisateur>"
dir="$1"
user="$2"

printf '%s\n' "$user" | grep -Eqx '[a-z_][a-z0-9_-]{0,31}' || die "nom d'utilisateur refusé : '$user'"
id -u "$user" >/dev/null 2>&1 || die "utilisateur inconnu sur cette machine : $user"
group="$(id -gn "$user")" || die "groupe principal illisible pour $user"
printf '%s\n' "$group" | grep -Eqx '[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}' || die "nom de groupe refusé : '$group'"

found=0
for unit in "$dir"/*.service; do
    [ -f "$unit" ] || continue
    found=1
    sed -e "s/@RASPYDARTS_USER@/$user/g" -e "s/@RASPYDARTS_GROUP@/$group/g" "$unit" > "$unit.rempli"
    if grep -q '@RASPYDARTS_[A-Z_]*@' "$unit.rempli"; then
        rm -f "$dir"/*.service.rempli
        die "marqueur restant après remplacement dans $(basename "$unit") : rien n'est installé"
    fi
done
[ "$found" -eq 1 ] || die "aucune unité dans $dir"

for unit in "$dir"/*.service; do
    mv "$unit.rempli" "$unit"
done
echo "Unités remplies : User=$user, Group=$group."
