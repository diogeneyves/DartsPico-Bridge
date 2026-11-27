#!/bin/sh
# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
#
# Prépare le dossier du commit initial du dépôt diogeneyves/RaspyDarts-Pico (spécification,
# compléments 79 et 81), à partir du HEAD du dépôt qui contient ce projet (My_GIT_Projects).
#
# Usage : tools/export_repo.sh [--run-tests] <dossier>
#
#   <dossier>    destination, inexistante ou vide, hors du dépôt (prévu : ~/RaspyDarts-Pico-export)
#   --run-tests  lance ensuite la suite de tests dans le dossier exporté (python3.13)
#
# Contenu : l'arbre HEAD du projet (git archive), sans CLAUDE.md ; le workflow du dépôt,
# transformé par motifs (préfixe RaspyDarts-Pico/ retiré, blocs paths supprimés, commentaire
# « My_GIT_Projects » réécrit) ; le .gitignore du projet, augmenté des règles utiles du
# .gitignore racine. Puis git init -b main et un seul commit, auteur et committer Gohy Yves
# <6154641+diogeneyves@users.noreply.github.com>. Ne pousse pas : affiche les commandes à
# lancer soi-même. Lit seulement le dépôt source ; ne supprime rien.
# Refuse les copies de référence (~/RaspyDarts-orig, ~/RaspyDarts-ref, ~/RaspyDarts-ref465,
# RASPYDARTS_ORIG) et ~/RaspyDarts-work, et tout dossier dessous, comme tools/build_fork.sh.
#
# Codes de sortie : 0 succès, 1 usage, 2 dossier refusé, 3 copie de référence refusée,
# 4 dépôt source inutilisable, 5 transformation ou contrôle de l'export en échec,
# 6 tests en échec dans l'export (--run-tests).

set -eu

# Note historique : a servi au commit initial 215eb9c, sous l'ancien nom ; script figé,
# constantes inchangées (spécification, complément 84).
ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
NAME="Gohy Yves"
EMAIL="6154641+diogeneyves@users.noreply.github.com"
MESSAGE="Initial commit: RaspyDarts-Pico"
URL="https://github.com/diogeneyves/RaspyDarts-Pico.git"
WORKFLOW=.github/workflows/raspydarts-pico-tests.yml
RUN_TESTS=0

while [ $# -gt 0 ]; do
    case "$1" in
        --run-tests) RUN_TESTS=1; shift ;;
        -h|--help) sed -n '4,25p' "$0"; exit 0 ;;
        -*) echo "Option inconnue : $1" >&2; exit 1 ;;
        *) break ;;
    esac
done
[ $# -eq 1 ] || { echo "Usage : $0 [--run-tests] <dossier>" >&2; exit 1; }

# Chemin réel de la destination, sans la créer.
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

for f in "$HOME/RaspyDarts-orig" "$HOME/RaspyDarts-ref" "$HOME/RaspyDarts-ref465" \
         "${RASPYDARTS_ORIG:-$HOME/RaspyDarts-ref465}" "$HOME/RaspyDarts-work"; do
    F=$(real "$f")
    case "$DEST_REAL/" in
        "$F/"*) echo "Refusé : $DEST_REAL est (ou est dans) la copie de référence ou de travail $F, jamais modifiée." >&2
                exit 3 ;;
    esac
done

TOP=$(git -C "$ROOT" rev-parse --show-toplevel 2>/dev/null) || {
    echo "Dépôt source introuvable autour de $ROOT." >&2; exit 4; }
TOP=$(real "$TOP")
case "$DEST_REAL/" in
    "$TOP/"*) echo "Refusé : $DEST_REAL est dans le dépôt $TOP ; choisir un dossier hors du dépôt." >&2
              exit 2 ;;
esac
if [ -d "$DEST_REAL" ] && [ -n "$(ls -A "$DEST_REAL")" ]; then
    echo "Refusé : $DEST_REAL n'est pas vide (rien n'est supprimé)." >&2
    exit 2
fi

PREFIX=$(git -C "$ROOT" rev-parse --show-prefix)
PREFIX=${PREFIX%/}
[ -n "$PREFIX" ] || {
    echo "Le projet est déjà à la racine de son dépôt ($TOP) : rien à exporter." >&2; exit 4; }
git -C "$TOP" cat-file -e "HEAD:$WORKFLOW" 2>/dev/null || {
    echo "Workflow $WORKFLOW absent du HEAD de $TOP." >&2; exit 4; }
if [ -n "$(git -C "$TOP" status --porcelain -- "$PREFIX" "$WORKFLOW")" ]; then
    echo "Attention : modifications non commitées dans $PREFIX ; seul HEAD est exporté." >&2
fi
REV=$(git -C "$TOP" rev-parse --short HEAD)

# 1. Arbre HEAD du projet, sans CLAUDE.md (modes et contenus tels quels, sans conversion).
mkdir -p "$DEST_REAL"
git -C "$TOP" -c core.autocrlf=false archive --format=tar "HEAD:$PREFIX" -- ':(exclude)CLAUDE.md' |
    tar -x -f - -C "$DEST_REAL"

# 2. Workflow, transformé par motifs (jamais par numéros de ligne).
mkdir -p "$DEST_REAL/.github/workflows"
git -C "$TOP" show "HEAD:$WORKFLOW" | awk -v pfx="$PREFIX" -v skip=-1 '
    function indent(s) { match(s, /^ */); return RLENGTH }
    skip >= 0 {
        if ($0 ~ /^ *- / && indent($0) > skip) next
        skip = -1
    }
    /^ *paths: *$/ { skip = indent($0); next }
    {
        line = $0
        if (line ~ ("working-directory: " pfx " *$")) sub(("working-directory: " pfx " *$"), "working-directory: .", line)
        gsub(pfx "/", "", line)
        if (line ~ /^ *#.*My_GIT_Projects/) sub(/de My_GIT_Projects/, "du dépôt", line)
        print line
    }
' > "$DEST_REAL/$WORKFLOW"
if grep -q "$PREFIX/\|paths:\|My_GIT_Projects\|working-directory: $PREFIX" "$DEST_REAL/$WORKFLOW"; then
    echo "Transformation du workflow incomplète : il reste un motif à retirer." >&2; exit 5
fi

# 3. .gitignore : celui du projet, plus les règles utiles du .gitignore racine.
cat >> "$DEST_REAL/.gitignore" <<'EOF'

# Règles reprises du .gitignore de My_GIT_Projects (complément 81)
__pycache__/
*.py[codz]
build/
dist/
*.egg-info/
*.mo
*.log
.env
.venv
venv/
.DS_Store
lib/
# Bibliothèque portable du firmware C++ : exception répétée après la règle lib/ ci-dessus
# (la dernière règle qui correspond l'emporte)
!firmware/i75w_display/lib/

# Consignes de Claude Code, jamais publiées (complément 79, Q8)
CLAUDE.md
.claude/
EOF

# 4. Dépôt git : branche main, un seul commit.
git -C "$DEST_REAL" init --quiet -b main
git -C "$DEST_REAL" add -A
# Après add -A, un fichier suivi n'apparaît plus comme ignoré : ls-files -c -i les retrouve.
IGNORED=$(git -C "$DEST_REAL" ls-files -c -i --exclude-standard)
[ -z "$IGNORED" ] || { echo "Fichiers exportés ignorés par le .gitignore :" >&2; echo "$IGNORED" >&2; exit 5; }
GIT_AUTHOR_NAME=$NAME GIT_AUTHOR_EMAIL=$EMAIL GIT_COMMITTER_NAME=$NAME GIT_COMMITTER_EMAIL=$EMAIL \
    git -C "$DEST_REAL" -c core.hooksPath=/dev/null -c commit.gpgSign=false \
    commit --quiet --no-verify -m "$MESSAGE"
[ -z "$(git -C "$DEST_REAL" status --porcelain --untracked-files=all)" ] || {
    echo "Export incomplet : des fichiers ne sont pas dans le commit." >&2; exit 5; }

N=$(git -C "$DEST_REAL" ls-files | wc -l | tr -d ' ')
echo "OK : export de $PREFIX ($REV) dans $DEST_REAL : $N fichiers, 1 commit sur main ($(git -C "$DEST_REAL" rev-parse --short HEAD))."

if [ "$RUN_TESTS" -eq 1 ]; then
    echo "Tests dans l'export (python3.13 -m unittest discover -s tests) :"
    (cd "$DEST_REAL" && python3.13 -m unittest discover -s tests) || {
        echo "Tests en échec dans l'export." >&2; exit 6; }
fi

echo "Commandes à lancer soi-même (non lancées) :"
echo "  git -C $DEST_REAL remote add origin $URL"
echo "  git -C $DEST_REAL push -u origin main"
