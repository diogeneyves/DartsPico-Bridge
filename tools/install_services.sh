#!/usr/bin/env bash
# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
#
# Installation de hwd et du service de rendu du DMD sur le Pi (spécification § 9.9,
# 50e complément ; procédure : docs/PROCEDURE.md, « Services hwd et rendu du DMD »).
# Lancé depuis le Mac, à la racine du dépôt, par l'auteur seul (jamais par Claude Code).
#
# - essai à blanc par défaut : rien n'est envoyé au Pi, aucune connexion SSH ; le script
#   affiche ce qui serait copié et écrit ;
# - --apply : envoie une archive au Pi par « ssh -4 », puis, par « ssh -4 -t » et sudo (mot de
#   passe tapé par l'auteur) :
#     * code (pi/hwd, pi/dmd, shared, fichiers suivis par git) dans /opt/raspydarts-pico/,
#       à root, dossiers 755, fichiers 644, et la révision dans /opt/raspydarts-pico/REVISION ;
#       rien n'y est effacé (un fichier retiré du dépôt reste sur le Pi) ;
#     * unités raspydarts-hwd.service et raspydarts-dmd.service dans /etc/systemd/system/, après
#       remplissage de User=@RASPYDARTS_USER@ (--user) et de Group=@RASPYDARTS_GROUP@ (groupe
#       principal de cet utilisateur) par pi/systemd/fill_units.sh, sur le Pi et avant toute
#       écriture (utilisateur inconnu ou marqueur restant : rien n'est installé, complément 77) ;
#     * /etc/raspydarts/hwd.toml et /etc/raspydarts/dmd.toml écrits depuis les exemples
#       seulement s'ils n'existent pas (jamais écrasés) ;
#     * règle udev pi/udev/73-raspydarts-pico.rules dans /etc/udev/rules.d/ (/dev/cible et
#       /dev/dmd, complément 62), marqueurs <SERIE_PICO> et <SERIE_I75W> remplis sur le Mac
#       (--pico-serial, --i75w-serial ; complément 77), écrasée à chaque fois, puis udevadm control --reload ; le
#       « udevadm trigger » reste à faire à la main (docs/PROCEDURE.md, § 2.6) ;
#     * systemctl daemon-reload, sans enable ni start : les unités ne sont pas activées.
# - avertit si un hwd tourne déjà sur le Pi (un hwd lancé à la main, par exemple).
# - options --user, --pico-serial et --i75w-serial obligatoires, essai à blanc compris ; casse
#   des séries contrôlée (udev compare ATTRS{serial} octet par octet).
#
# Codes de sortie : 0 bon, 1 échec de l'envoi ou de l'installation, 2 dépôt incomplet ou
# marqueur udev absent ou restant, 64 usage.

set -euo pipefail

HOST="pi@raspydarts.local"
APPLY=0
RUN_USER=""
PICO_SERIAL=""
I75W_SERIAL=""
REMOTE_ARCHIVE="/tmp/raspydarts-pico-install.tgz"
OPT_DIR="/opt/raspydarts-pico"
ETC_DIR="/etc/raspydarts"
UNIT_DIR="/etc/systemd/system"
UNITS=(raspydarts-hwd.service raspydarts-dmd.service)
CODE_DIRS=(pi/hwd pi/dmd shared)
UDEV_NAME="73-raspydarts-pico.rules"
UDEV_RULE="pi/udev/$UDEV_NAME"
UDEV_DIR="/etc/udev/rules.d"

usage() {
    cat <<'EOF'
Usage : tools/install_services.sh --user UTILISATEUR --pico-serial SÉRIE --i75w-serial SÉRIE
                                  [--apply] [--host HÔTE]

Installe hwd et le service de rendu du DMD sur le Pi, sans activer les unités.

  --user UTILISATEUR    utilisateur des unités (User=) ; Group= : son groupe principal,
                        lu sur le Pi à --apply (obligatoire)
  --pico-serial SÉRIE   numéro de série USB du Pico 2 sous MicroPython, 16 chiffres
                        hexadécimaux en minuscules (obligatoire)
  --i75w-serial SÉRIE   numéro de série USB de l'Interstate 75 W sous le firmware C++,
                        16 chiffres hexadécimaux en majuscules (obligatoire)
  --apply               installation réelle (ssh -4, puis sudo sur le Pi) ; sans cette
                        option, essai à blanc : rien n'est envoyé, aucune connexion
  --host HÔTE           hôte SSH (défaut : pi@raspydarts.local)
  -h, --help            cette aide

Numéros de série : « udevadm info -a -n /dev/ttyACMx » sur le Pi, ATTRS{serial}.

Code dans /opt/raspydarts-pico, unités dans /etc/systemd/system, configurations
/etc/raspydarts/hwd.toml et dmd.toml écrites depuis les exemples si elles manquent
(jamais écrasées). Règle udev de /dev/cible et /dev/dmd dans /etc/udev/rules.d (écrasée),
puis udevadm control --reload ; trigger à la main. Ni enable ni start : voir docs/PROCEDURE.md.
EOF
}

die_usage() {
    echo "install_services : $1" >&2
    echo "Aide : tools/install_services.sh --help" >&2
    exit 64
}

while [ $# -gt 0 ]; do
    case "$1" in
        --apply) APPLY=1 ;;
        --host) [ $# -ge 2 ] || die_usage "--host attend un hôte"; HOST="$2"; shift ;;
        --user) [ $# -ge 2 ] || die_usage "--user attend un utilisateur"; RUN_USER="$2"; shift ;;
        --pico-serial) [ $# -ge 2 ] || die_usage "--pico-serial attend un numéro de série"; PICO_SERIAL="$2"; shift ;;
        --i75w-serial) [ $# -ge 2 ] || die_usage "--i75w-serial attend un numéro de série"; I75W_SERIAL="$2"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die_usage "option inconnue : $1" ;;
    esac
    shift
done
[[ "$HOST" =~ ^[A-Za-z0-9._@-]+$ ]] || die_usage "hôte refusé : $HOST"
[ -n "$RUN_USER" ] || die_usage "--user obligatoire (utilisateur des unités, sans défaut)"
[[ "$RUN_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die_usage "--user refusé : '$RUN_USER' (nom POSIX en minuscules, 32 caractères au plus)"
[ -n "$PICO_SERIAL" ] || die_usage "--pico-serial obligatoire (numéro de série USB du Pico 2)"
[[ "$PICO_SERIAL" =~ ^[0-9a-f]{16}$ ]] || die_usage "--pico-serial refusé : '$PICO_SERIAL' (16 chiffres hexadécimaux en minuscules, comme sous MicroPython)"
[ -n "$I75W_SERIAL" ] || die_usage "--i75w-serial obligatoire (numéro de série USB de l'Interstate 75 W)"
[[ "$I75W_SERIAL" =~ ^[0-9A-F]{16}$ ]] || die_usage "--i75w-serial refusé : '$I75W_SERIAL' (16 chiffres hexadécimaux en majuscules, comme sous le firmware C++)"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Fichiers du code : ceux que git suit dans pi/hwd, pi/dmd et shared (ni cache ni brouillon)
files=()
while IFS= read -r -d '' file; do
    files+=("$file")
done < <(git ls-files -z -- "${CODE_DIRS[@]}")
required=(pi/hwd/hwd_daemon.py pi/hwd/hwd.example.toml pi/dmd/dmd_service.py
          pi/dmd/dmd.example.toml shared/dmd_proto.py shared/pico_proto.py
          pi/systemd/raspydarts-hwd.service pi/systemd/raspydarts-dmd.service
          pi/systemd/fill_units.sh "$UDEV_RULE")
for file in "${required[@]}"; do
    if [ ! -f "$file" ]; then
        echo "install_services : fichier absent du dépôt : $file" >&2
        exit 2
    fi
done
revision="$(git describe --always --dirty 2>/dev/null || echo inconnue)"

# Règle udev : chaque marqueur une fois exactement, puis aucun marqueur restant (complément 77)
udev_text="$(cat "$UDEV_RULE")"
for marker in "<SERIE_PICO>" "<SERIE_I75W>"; do
    n="$({ grep -o -- "$marker" "$UDEV_RULE" || true; } | wc -l | tr -d ' ')"
    if [ "$n" != "1" ]; then
        echo "install_services : marqueur $marker trouvé $n fois dans $UDEV_RULE au lieu d'une" >&2
        exit 2
    fi
done
udev_text="${udev_text//<SERIE_PICO>/$PICO_SERIAL}"
udev_text="${udev_text//<SERIE_I75W>/$I75W_SERIAL}"
if printf '%s\n' "$udev_text" | grep -q '<SERIE_[A-Za-z0-9_]*>'; then
    echo "install_services : marqueur restant dans $UDEV_RULE après remplacement : rien n'est envoyé" >&2
    exit 2
fi

echo "Hôte        : $HOST"
echo "Révision    : $revision"
echo "Code        : ${#files[@]} fichiers de ${CODE_DIRS[*]} -> $OPT_DIR/ (rien n'y est effacé)"
echo "Unités      : ${UNITS[*]} -> $UNIT_DIR/ (ni enable ni start)"
echo "              User=@RASPYDARTS_USER@ -> $RUN_USER"
echo "              Group=@RASPYDARTS_GROUP@ -> groupe principal de $RUN_USER, lu sur le Pi à --apply"
echo "Udev        : $UDEV_RULE -> $UDEV_DIR/$UDEV_NAME (écrasée), puis udevadm control --reload ; trigger à la main (docs/PROCEDURE.md, § 2.6)"
echo "              <SERIE_PICO> -> $PICO_SERIAL (Pico 2, MicroPython)"
echo "              <SERIE_I75W> -> $I75W_SERIAL (Interstate 75 W, firmware C++)"
echo "Config.     : $ETC_DIR/hwd.toml et $ETC_DIR/dmd.toml, depuis les exemples, seulement s'ils manquent"
if [ "$APPLY" -eq 1 ]; then
    echo "Mode        : INSTALLATION RÉELLE (--apply)"
else
    echo "Mode        : essai à blanc (rien n'est envoyé ; ajouter --apply pour installer)"
fi
echo "Fichiers du code :"
for file in "${files[@]}"; do
    echo "  $file"
done

if [ "$APPLY" -eq 0 ]; then
    echo "Essai à blanc terminé : rien n'a été envoyé au Pi."
    exit 0
fi

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/payload/opt" "$stage/payload/systemd" "$stage/payload/etc" "$stage/payload/udev"
for file in "${files[@]}"; do
    mkdir -p "$stage/payload/opt/$(dirname "$file")"
    cp "$file" "$stage/payload/opt/$file"
done
echo "$revision" > "$stage/payload/opt/REVISION"
for unit in "${UNITS[@]}"; do
    cp "pi/systemd/$unit" "$stage/payload/systemd/$unit"
done
printf '%s\n' "$udev_text" > "$stage/payload/udev/$UDEV_NAME"
cp pi/systemd/fill_units.sh "$stage/payload/fill_units.sh"
cp pi/hwd/hwd.example.toml "$stage/payload/etc/hwd.toml"
cp pi/dmd/dmd.example.toml "$stage/payload/etc/dmd.toml"

# Installation sur le Pi, lancée par sudo avec le dossier extrait en argument
cat > "$stage/payload/install_remote.sh" <<EOF
set -eu
src="\$1"
# Unités : User et Group remplis avant toute écriture ; refus si l'utilisateur manque (complément 77)
sh "\$src/fill_units.sh" "\$src/systemd" $RUN_USER
install -d -m 755 "$OPT_DIR"
cp -R "\$src/opt/." "$OPT_DIR/"
chown -R root:root "$OPT_DIR"
find "$OPT_DIR" -type d -exec chmod 755 {} +
find "$OPT_DIR" -type f -exec chmod 644 {} +
echo "Code copié dans $OPT_DIR (révision \$(cat "$OPT_DIR/REVISION"))."
for unit in ${UNITS[*]}; do
    install -m 644 -o root -g root "\$src/systemd/\$unit" "$UNIT_DIR/\$unit"
    echo "Unité écrite : $UNIT_DIR/\$unit"
done
install -d -m 755 "$ETC_DIR"
for name in hwd.toml dmd.toml; do
    if [ -e "$ETC_DIR/\$name" ]; then
        echo "$ETC_DIR/\$name existe : gardé tel quel."
    else
        install -m 644 -o root -g root "\$src/etc/\$name" "$ETC_DIR/\$name"
        echo "$ETC_DIR/\$name écrit depuis l'exemple."
    fi
done
install -m 644 -o root -g root "\$src/udev/$UDEV_NAME" "$UDEV_DIR/$UDEV_NAME"
echo "Règle udev écrite : $UDEV_DIR/$UDEV_NAME"
udevadm control --reload
echo "Règles udev rechargées ; trigger à faire à la main (docs/PROCEDURE.md, § 2.6)."
systemctl daemon-reload
echo "Unités installées, ni activées ni démarrées."
if pgrep -f hwd_daemon.py >/dev/null; then
    echo "ATTENTION : un hwd tourne déjà ; ne pas démarrer raspydarts-hwd.service avant de l'avoir arrêté :"
    pgrep -af hwd_daemon.py || true
fi
EOF

COPYFILE_DISABLE=1 tar --format ustar -czf "$stage/install.tgz" -C "$stage/payload" .
echo "Envoi de l'archive vers $HOST:$REMOTE_ARCHIVE..."
if ! ssh -4 "$HOST" "cat > '$REMOTE_ARCHIVE'" < "$stage/install.tgz"; then
    echo "install_services : échec de l'envoi" >&2
    exit 1
fi
echo "Installation (sudo demande le mot de passe du Pi)..."
remote="dir=\$(mktemp -d) && tar -xzf '$REMOTE_ARCHIVE' -C \"\$dir\" && sudo sh \"\$dir/install_remote.sh\" \"\$dir\"; rc=\$?; rm -rf \"\$dir\" '$REMOTE_ARCHIVE'; exit \$rc"
if ! ssh -4 -t "$HOST" "$remote"; then
    echo "install_services : échec de l'installation" >&2
    exit 1
fi
echo "Installation terminée. Activation et démarrage : docs/PROCEDURE.md."
