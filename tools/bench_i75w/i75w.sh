#!/usr/bin/env bash
# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
#
# Banc de l'Interstate 75 W : commandes picotool, sur le Pi, qui visent toujours la 75 W
# explicitement (numéro de série ou port by-id), jamais « le premier appareil trouvé ». Le
# Pico 2 de la cible reste branché sur le même Pi : il est exclu par son numéro de série
# (PICO_SERIAL) et chaque écriture est précédée d'un contrôle. Aucun appel à uhubctl : il
# couperait aussi le Pico. Firmware visé : le C++ de M4 (spécification § 8, 40e complément) ;
# retour au MicroPython de Pimoroni possible par flash --micropython.
#
#   i75w.sh list                   appareils 2e8a (sysfs) et ports by-id, sans rien écrire ;
#                                  marche sans PICO_SERIAL ni I75W_SERIAL (pour les relever)
#   i75w.sh bootsel                vérifie que la 75 W (I75W_SERIAL) est en BOOTSEL, une seule fois
#   i75w.sh info                   picotool info -a, sur ce numéro de série
#   i75w.sh chip                   puce et boîtier ; échoue si ce n'est pas un RP2350 en QFN60 (RP2350A)
#   i75w.sh flash <uf2> <sha256>   firmware C++ : i75w_display.uf2 seulement ; contrôles (empreinte,
#                                  nom, picotool info du fichier : programme « RaspyDarts DMD »
#                                  et RP2350, puce), puis picotool load -v -x
#   i75w.sh flash --micropython <uf2> <sha256>
#                                  retour au MicroPython : i75w_rp2350-…-micropython.uf2 seulement
#   i75w.sh to-bootsel             firmware C++ → BOOTSEL, sans bouton (picotool reboot -u -f --ser)
#   i75w.sh run                    BOOTSEL → firmware (picotool reboot)
#   i75w.sh port                   port by-id du firmware C++, construit depuis I75W_SERIAL
#   i75w.sh check                  sysfs (2e8a:0009, série, fabricant, produit), puis HELLO et STATS
#   i75w.sh tx [args]              tx_bench.py sur le port de la 75 W
#
# PICO_SERIAL et I75W_SERIAL : numéros de série des deux cartes, fournis par l'environnement
# (16 chiffres hexadécimaux, casse indifférente), sans valeur par défaut (complément 77) :
#   export PICO_SERIAL=<SERIE_PICO> I75W_SERIAL=<SERIE_I75W>
#
# Codes de sortie : 0 bon, 2 appareil introuvable ou ambigu, 3 contrôle refusé, 64 usage.

set -u

PICO_SERIAL="${PICO_SERIAL:-}"
I75W_SERIAL="${I75W_SERIAL:-}"
SYSFS="${I75W_SYSFS:-/sys/bus/usb/devices}"
BYID="${I75W_BYID:-/dev/serial/by-id}"
DMD_LINK="${I75W_DMD_LINK:-/dev/dmd}"
PICOTOOL="${PICOTOOL:-picotool}"
PYTHON="${PYTHON:-python3}"
FUSER="${FUSER:-fuser}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# Descripteurs USB du firmware C++ (firmware/i75w_display/src/usb_descriptors.c).
FW_PID="0009"
FW_MANUFACTURER="RaspyDarts"
FW_PRODUCT="RaspyDarts DMD"
FW_UF2="i75w_display.uf2"

die() { code="$1"; shift; echo "i75w : $*" >&2; exit "$code"; }
upper() { printf '%s' "$1" | tr '[:lower:]' '[:upper:]'; }

not_pico() {
    [ "$(upper "$I75W_SERIAL")" != "$(upper "$PICO_SERIAL")" ] || \
        die 3 "I75W_SERIAL est celui du Pico 2 : arrêt"
}

# Appareils USB 2e8a : « produit numéro_de_série bus adresse » par ligne.
usb_devices() {
    for d in "$SYSFS"/*; do
        [ -f "$d/idVendor" ] || continue
        [ "$(cat "$d/idVendor")" = "2e8a" ] || continue
        echo "$(cat "$d/idProduct" 2>/dev/null) $(cat "$d/serial" 2>/dev/null || echo -) \
$(cat "$d/busnum" 2>/dev/null || echo -) $(cat "$d/devnum" 2>/dev/null || echo -)"
    done
}

# Dossier sysfs des appareils 2e8a de produit $1 et de numéro de série I75W_SERIAL.
usb_dirs() {
    want="$(upper "$I75W_SERIAL")"
    for d in "$SYSFS"/*; do
        [ -f "$d/idVendor" ] || continue
        [ "$(cat "$d/idVendor")" = "2e8a" ] || continue
        [ "$(cat "$d/idProduct" 2>/dev/null)" = "$1" ] || continue
        [ "$(upper "$(cat "$d/serial" 2>/dev/null)")" = "$want" ] || continue
        echo "$d"
    done
}

cmd_list() {
    echo "# appareils 2e8a (produit, série, bus, adresse) ;" \
         "000f = BOOTSEL du RP2350, 0009 = firmware C++, 0005 = MicroPython"
    usb_devices
    echo "# ports by-id"
    ls -1 "$BYID" 2>/dev/null || echo "(aucun)"
    echo "# Pico 2 de la cible, exclu : ${PICO_SERIAL:-non défini (PICO_SERIAL)}"
    echo "# 75 W visée : ${I75W_SERIAL:-non défini (I75W_SERIAL)}"
}

# Numéro de série de la 75 W en BOOTSEL : toujours I75W_SERIAL, jamais « le premier trouvé ».
cmd_bootsel() {
    not_pico
    n="$(usb_dirs 000f | grep -c . || true)"
    [ "$n" = "1" ] || die 2 "75 W ($I75W_SERIAL) trouvée $n fois en BOOTSEL au lieu d'une (i75w.sh list)"
    echo "$I75W_SERIAL"
}

cmd_info() {
    ser="$(cmd_bootsel)" || exit $?
    "$PICOTOOL" info -a --ser "$ser"
}

# Affiche « type package chipid » ; échoue (3) si ce n'est pas un RP2350 en QFN60 (RP2350A).
# picotool 2.1.1 (main.cpp, info -d) tire « package » de la ligne OTP NUM_GPIOS gravée en
# usine : 30 GPIO → QFN60, 48 → QFN80, autre → unknown.
cmd_chip() {
    ser="$(cmd_bootsel)" || exit $?
    out="$("$PICOTOOL" info -d --ser "$ser")" || die 2 "picotool info -d a échoué"
    type="$(printf '%s\n' "$out" | awk '$1 == "type:" {print $2; exit}')"
    package="$(printf '%s\n' "$out" | awk '$1 == "package:" {print $2; exit}')"
    chipid="$(printf '%s\n' "$out" | awk '$1 == "chipid:" {print $2; exit}')"
    echo "série $ser : type ${type:-?}, boîtier ${package:-?}, chipid ${chipid:-?}"
    [ "$type" = "RP2350" ] || die 3 "puce ${type:-inconnue} : ce n'est pas un RP2350, procédure arrêtée"
    [ "$package" = "QFN60" ] || \
        die 3 "boîtier ${package:-illisible} : ce n'est pas un QFN60 (RP2350A), procédure arrêtée"
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

check_sha() {
    want="$(printf '%s' "$2" | tr '[:upper:]' '[:lower:]')"
    got="$(sha256_of "$1")"
    [ "$got" = "$want" ] || die 3 "empreinte $got différente de celle relevée ($want)"
}

# picotool info -a du fichier seul (aucun appareil visé) : nom du programme et puce cible.
# Nom du programme = pico_set_program_name (firmware/i75w_display/CMakeLists.txt), « RaspyDarts
# DMD », identique au produit USB (55e complément) : ligne « name: » lue en entier (le nom
# contient une espace), blancs de tête et de fin retirés, comparaison exacte à FW_PRODUCT.
check_fw_file() {
    out="$("$PICOTOOL" info -a "$1")" || die 3 "picotool info -a $(basename "$1") a échoué"
    name="$(printf '%s\n' "$out" | awk '$1 == "name:" {
        sub(/^[ \t]*name:[ \t]*/, ""); sub(/[ \t\r]+$/, ""); print; exit }')"
    target="$(printf '%s\n' "$out" | awk '$1 == "target" && $2 == "chip:" {print $3; exit}')"
    version="$(printf '%s\n' "$out" | awk '$1 == "version:" {print $2; exit}')"
    echo "fichier $(basename "$1") : programme ${name:-?}, version ${version:-?}, puce ${target:-?}"
    [ "$name" = "$FW_PRODUCT" ] || die 3 "programme ${name:-illisible} : ce n'est pas $FW_PRODUCT"
    [ "$target" = "RP2350" ] || die 3 "puce cible ${target:-illisible} : ce n'est pas un RP2350"
}

cmd_flash() {
    micropython=0
    if [ "${1:-}" = "--micropython" ]; then
        micropython=1; shift
    fi
    [ $# -eq 2 ] || die 64 "usage : i75w.sh flash [--micropython] <fichier.uf2> <sha256 relevé>"
    uf2="$1"
    [ -f "$uf2" ] || die 3 "fichier absent : $uf2"
    if [ "$micropython" = 1 ]; then
        case "$(basename "$uf2")" in
            i75w_rp2350-*-micropython.uf2) ;;
            *) die 3 "$(basename "$uf2") n'est pas un i75w_rp2350-…-micropython.uf2" ;;
        esac
        check_sha "$uf2" "$2"
    else
        [ "$(basename "$uf2")" = "$FW_UF2" ] || \
            die 3 "$(basename "$uf2") n'est pas $FW_UF2 (MicroPython : flash --micropython)"
        check_sha "$uf2" "$2"
        check_fw_file "$uf2"
    fi
    cmd_chip || exit $?
    ser="$(cmd_bootsel)" || exit $?
    echo "écriture de $(basename "$uf2") sur la 75 W (série $ser)"
    "$PICOTOOL" load -v -x "$uf2" --ser "$ser"
}

# La 75 W sous le firmware C++, une seule fois : 2e8a:0009, série, fabricant, produit.
fw_device() {
    not_pico
    dirs="$(usb_dirs "$FW_PID")"
    n="$(printf '%s' "$dirs" | grep -c . || true)"
    [ "$n" = "1" ] || \
        die 2 "75 W ($I75W_SERIAL) trouvée $n fois sous le firmware C++ (2e8a:$FW_PID) au lieu d'une"
    m="$(cat "$dirs/manufacturer" 2>/dev/null)"
    p="$(cat "$dirs/product" 2>/dev/null)"
    [ "$m" = "$FW_MANUFACTURER" ] || die 3 "fabricant « $m » au lieu de « $FW_MANUFACTURER »"
    [ "$p" = "$FW_PRODUCT" ] || die 3 "produit « $p » au lieu de « $FW_PRODUCT »"
}

# Firmware C++ → BOOTSEL par l'interface de remise à zéro du SDK, sans bouton.
# picotool 2.1.1 : type de redémarrage (-u) avant la sélection de l'appareil (-f, --ser) ;
# « reboot -f -u --ser … » échoue (« unexpected option: --ser », banc du 25/11/2026).
cmd_to_bootsel() {
    fw_device || exit $?
    "$PICOTOOL" reboot -u -f --ser "$(upper "$I75W_SERIAL")"
}

# BOOTSEL → firmware installé.
cmd_run() {
    ser="$(cmd_bootsel)" || exit $?
    "$PICOTOOL" reboot --ser "$ser"
}

# Port by-id du firmware C++ : produit « RaspyDarts DMD », série en majuscules (§ 8).
cmd_port() {
    not_pico
    port="$BYID/usb-RaspyDarts_RaspyDarts_DMD_$(upper "$I75W_SERIAL")-if00"
    [ -e "$port" ] || die 2 "port de la 75 W absent : $port (i75w.sh list)"
    echo "$port"
}

cmd_check() {
    fw_device || exit $?
    port="$(cmd_port)" || exit $?
    "$PYTHON" "$HERE/tx_bench.py" "$port" --check || exit $?
    echo "contrôle bon : $port" >&2
}

# hwd ne doit pas ouvrir la 75 W pendant l'essai (§ 11.3, 40e complément).
check_free() {
    if [ -e "$DMD_LINK" ]; then
        echo "i75w : attention, $DMD_LINK existe : hwd peut ouvrir la 75 W (arrêter hwd)" >&2
    fi
    if command -v "$FUSER" >/dev/null 2>&1 && "$FUSER" -s "$1" 2>/dev/null; then
        die 3 "$1 est déjà ouvert par un autre processus (hwd ?) : arrêter hwd"
    fi
}

cmd_tx() {
    fw_device || exit $?
    port="$(cmd_port)" || exit $?
    check_free "$port"
    exec "$PYTHON" "$HERE/tx_bench.py" "$port" "$@"
}

[ $# -ge 1 ] || die 64 "usage : i75w.sh list|bootsel|info|chip|flash|to-bootsel|run|port|check|tx"
for name in PICO_SERIAL I75W_SERIAL; do
    value="${!name}"
    # list sert à relever les séries : il marche sans elles (il ne vise aucune carte)
    [ -n "$value" ] || [ "$1" = "list" ] || die 64 "$name non défini : export $name=<numéro de série> (16 chiffres hexadécimaux)"
    [ -z "$value" ] || [[ "$value" =~ ^[0-9A-Fa-f]{16}$ ]] || die 64 "$name refusé : '$value' (16 chiffres hexadécimaux attendus)"
done
sub="$1"; shift
case "$sub" in
    list) cmd_list ;;
    bootsel) cmd_bootsel ;;
    info) cmd_info ;;
    chip) cmd_chip ;;
    flash) cmd_flash "$@" ;;
    to-bootsel) cmd_to_bootsel ;;
    run) cmd_run ;;
    port) cmd_port ;;
    check) cmd_check ;;
    tx) cmd_tx "$@" ;;
    *) die 64 "commande inconnue : $sub" ;;
esac
