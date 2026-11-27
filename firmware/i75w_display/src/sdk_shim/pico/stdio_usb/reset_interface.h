/*
 * Copyright (C) 2026 Gohy Yves
 * Licence : GPL v3 (voir LICENSE a la racine du depot).
 *
 * reset_interface.c du Pico SDK (compile avec le firmware, section 8) inclut
 * "pico/stdio_usb/reset_interface.h", qui ne fait qu'inclure
 * "pico/usb_reset_interface.h". Le dossier d'en-tetes de pico_stdio_usb n'est
 * pas ajoute : il contient aussi un tusb_config.h qui pourrait masquer le notre.
 */

#ifndef I75W_SHIM_STDIO_USB_RESET_INTERFACE_H
#define I75W_SHIM_STDIO_USB_RESET_INTERFACE_H

#include "pico/usb_reset_interface.h"

#endif
