#!/bin/sh
# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
#
# Applique les patchs de DartsPico-Bridge sur une copie de RaspyDarts (docs/PI_INTERFACE.md § 6).
#
#   tools/apply_patches.sh [--check] [--patches DOSSIER] <copie de RaspyDarts>
#
#   --check    vérifie seulement : les patchs sont appliqués dans un clone temporaire,
#              la copie donnée n'est pas modifiée (autorisé sur la copie de référence)
#   --patches  dossier des patchs (défaut : pi/patches), avec BASE et series
#
# Refuse (sans rien modifier) : HEAD différent de BASE (avec --check : BASE absent de la
# copie), copie avec des modifications,
# application sur la copie de référence (RASPYDARTS_ORIG, défaut ~/RaspyDarts-ref465), ou sur
# ~/RaspyDarts-orig (4.6.3), ~/RaspyDarts-ref (4.6.4) et ~/RaspyDarts-ref465, gardées intactes.
# Sinon : git am de chaque patch de series, dans l'ordre, puis un commit à part : fichiers nouveaux
# (cpico.py, hwbus.py, pico_proto.py, rpi_ws281x.py) et bandeau en tête du README.md (complément 83).
#
# Codes de sortie : 0 succès, 1 usage, 2 HEAD différent de BASE (--check : BASE absent), 3 copie modifiée,
# 4 copie de référence refusée, 5 patch qui ne s'applique pas, 6 modèle du bandeau ou README.md absent.

set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PATCHES="$ROOT/pi/patches"
CHECK=0

while [ $# -gt 0 ]; do
    case "$1" in
        --check) CHECK=1; shift ;;
        --patches) PATCHES=$2; shift 2 ;;
        -h|--help) sed -n '4,22p' "$0"; exit 0 ;;
        -*) echo "Option inconnue : $1" >&2; exit 1 ;;
        *) break ;;
    esac
done
[ $# -eq 1 ] || { echo "Usage : $0 [--check] [--patches DOSSIER] <copie de RaspyDarts>" >&2; exit 1; }

TARGET=$(cd "$1" && pwd -P)
PATCHES=$(cd "$PATCHES" && pwd -P)
ORIG=${RASPYDARTS_ORIG:-$HOME/RaspyDarts-ref465}
ORIG_REAL=$( (cd "$ORIG" 2>/dev/null && pwd -P) || echo "")

[ -f "$PATCHES/BASE" ] && [ -f "$PATCHES/series" ] || {
    echo "BASE ou series absent dans $PATCHES" >&2; exit 1; }
BASE=$(head -n 1 "$PATCHES/BASE" | tr -d '[:space:]')

for old in "$HOME/RaspyDarts-orig" "$HOME/RaspyDarts-ref" "$HOME/RaspyDarts-ref465"; do
    OLD_REAL=$( (cd "$old" 2>/dev/null && pwd -P) || echo "")
    if [ "$CHECK" -eq 0 ] && [ -n "$OLD_REAL" ] && [ "$TARGET" = "$OLD_REAL" ]; then
        echo "Refusé : $TARGET est une copie de référence (ancienne ou actuelle), jamais modifiée." >&2
        exit 4
    fi
done
if [ "$CHECK" -eq 0 ] && [ -n "$ORIG_REAL" ] && [ "$TARGET" = "$ORIG_REAL" ]; then
    echo "Refusé : $TARGET est la copie de référence, jamais modifiée (utiliser --check ou une autre copie)." >&2
    exit 4
fi

# Bandeau du README du fork (complément 83), lu dans pi/patches/ du dépôt (pas dans --patches).
# Vérifié avant toute modification ; inutile avec --check, qui s'arrête avant le 11e commit.
BANNER="$ROOT/pi/patches/FORK_README_BANNER.md"
if [ "$CHECK" -eq 0 ]; then
    [ -f "$BANNER" ] || { echo "Modèle du bandeau absent : $BANNER (rien n'est modifié)." >&2; exit 6; }
    [ -f "$TARGET/README.md" ] || { echo "README.md absent de $TARGET (rien n'est modifié)." >&2; exit 6; }
fi

HEAD=$(git -C "$TARGET" rev-parse HEAD)
if [ "$CHECK" -eq 1 ]; then
    # Vérification : le clone temporaire est placé sur BASE, la copie doit seulement le contenir
    # (par exemple ~/RaspyDarts-ref465, tête 32edb12, pour BASE 8fa61ff)
    if ! git -C "$TARGET" cat-file -e "$BASE^{commit}" 2>/dev/null; then
        echo "Refusé : $TARGET ne contient pas le commit BASE $BASE." >&2
        exit 2
    fi
elif [ "$HEAD" != "$BASE" ]; then
    echo "Refusé : HEAD de $TARGET vaut $HEAD, et non BASE $BASE." >&2
    exit 2
fi
if [ -n "$(git -C "$TARGET" status --porcelain --untracked-files=all)" ]; then
    echo "Refusé : $TARGET contient des modifications." >&2
    exit 3
fi

# Patchs de series, dans l'ordre (lignes vides et commentaires ignorés).
LIST=$(grep -v '^[[:space:]]*#' "$PATCHES/series" | grep -v '^[[:space:]]*$' || true)
for p in $LIST; do
    [ -f "$PATCHES/$p" ] || { echo "Patch absent : $PATCHES/$p" >&2; exit 5; }
done

WORK=$TARGET
if [ "$CHECK" -eq 1 ]; then
    TMP=$(mktemp -d)
    trap 'rm -rf "$TMP"' EXIT
    git clone --quiet --shared "$TARGET" "$TMP/copie"    # lecture seule de la copie
    git -C "$TMP/copie" checkout --quiet "$BASE"
    WORK="$TMP/copie"
fi

n=0
for p in $LIST; do
    if ! git -C "$WORK" -c user.name=DartsPico-Bridge -c user.email=noreply@invalid \
            am --quiet --keep-cr "$PATCHES/$p"; then
        git -C "$WORK" am --abort 2>/dev/null || true
        echo "Le patch $p ne s'applique pas sur $BASE." >&2
        exit 5
    fi
    n=$((n + 1))
done

if [ "$CHECK" -eq 1 ]; then
    echo "OK : $n patch(s) appliqué(s) sans erreur sur $BASE (vérification seulement)."
    exit 0
fi

# Fichiers nouveaux, copiés depuis ce dépôt.
cp "$ROOT/pi/raspydarts_layer/include/cpico.py" "$TARGET/include/cpico.py"
cp "$ROOT/pi/hwd/hwbus.py" "$TARGET/include/hwbus.py"
cp "$ROOT/shared/pico_proto.py" "$TARGET/include/pico_proto.py"
mkdir -p "$TARGET/pico_shims"
cp "$ROOT/pi/raspydarts_layer/shims/rpi_ws281x.py" "$TARGET/pico_shims/rpi_ws281x.py"
# Bandeau ajouté en tête du README.md, le texte de l'amont reste intact en dessous.
cat "$BANNER" "$TARGET/README.md" > "$TARGET/README.md.tmp"
mv "$TARGET/README.md.tmp" "$TARGET/README.md"
git -C "$TARGET" add include/cpico.py include/hwbus.py include/pico_proto.py pico_shims/rpi_ws281x.py README.md
git -C "$TARGET" -c user.name=DartsPico-Bridge -c user.email=noreply@invalid \
    commit --quiet -m "DartsPico-Bridge: add new files (cpico, hwbus, pico_proto, rpi_ws281x) and README banner"
echo "OK : $n patch(s) appliqué(s) et fichiers nouveaux copiés dans $TARGET."
