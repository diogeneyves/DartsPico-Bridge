/*
 * Copyright (C) 2026 Gohy Yves
 * Licence : GPL v3 (voir LICENSE a la racine du depot).
 *
 * Configuration de TinyUSB pour le firmware de l'Interstate 75 W
 * (specification section 8, 36e complement) : un port CDC pour les trames du
 * section 6 et l'interface de remise a zero du Pico SDK (pilote fourni par
 * reset_interface.c du SDK, sans classe TinyUSB). Pas de stdio USB.
 */

#ifndef I75W_TUSB_CONFIG_H
#define I75W_TUSB_CONFIG_H

#define CFG_TUSB_RHPORT0_MODE (OPT_MODE_DEVICE)
#define CFG_TUD_ENDPOINT0_SIZE (64)

#define CFG_TUD_CDC (1)
#define CFG_TUD_MSC (0)
#define CFG_TUD_HID (0)
#define CFG_TUD_MIDI (0)
#define CFG_TUD_VENDOR (0)  /* pas de descripteur Microsoft OS 2.0 */

/* Reception : un FRAME fait 8 203 octets ; un tampon large laisse la boucle
   du coeur 0 decoder pendant que l'USB continue de recevoir. Emission : la
   plus longue reponse (ERROR) fait 138 octets. */
#define CFG_TUD_CDC_RX_BUFSIZE (2048)
#define CFG_TUD_CDC_TX_BUFSIZE (512)
#define CFG_TUD_CDC_EP_BUFSIZE (64)

#endif /* I75W_TUSB_CONFIG_H */
