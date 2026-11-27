/*
 * Copyright (C) 2026 Gohy Yves
 * Licence : GPL v3 (voir LICENSE a la racine du depot).
 *
 * Structure reprise de src/rp2_common/pico_stdio_usb/stdio_usb_descriptors.c
 * du Pico SDK 2.1.0 (commit 95ea6acad131124694cda1c162c52cd30e0aece0) :
 *   Copyright (c) 2020 Raspberry Pi (Trading) Ltd.
 *   SPDX-License-Identifier: BSD-3-Clause
 *
 * Descripteurs USB de l'Interstate 75 W (specification section 8, 36e
 * complement) : 2e8a:0009 (PID du Pico SDK pour le RP2350), fabricant
 * "RaspyDarts", produit "RaspyDarts DMD", numero de serie = identifiant unique
 * de la puce en hexadecimal majuscule. Interfaces 0 et 1 : CDC (trames du
 * section 6) ; interface 2 : remise a zero du SDK (picotool reboot -f), dont
 * le pilote est reset_interface.c du SDK. Pas de descripteur Microsoft OS 2.0
 * (bcdUSB 2.00).
 */

#include "pico/unique_id.h"
#include "pico/usb_reset_interface.h"
#include "tusb.h"

#define USBD_VID (0x2E8A)  /* Raspberry Pi */
#define USBD_PID (0x0009)  /* Pico SDK CDC, RP2350 */

#define USBD_MANUFACTURER "RaspyDarts"
#define USBD_PRODUCT "RaspyDarts DMD"

#define USBD_ITF_CDC (0)        /* deux interfaces : 0 et 1 */
#define USBD_ITF_RPI_RESET (2)
#define USBD_ITF_MAX (3)

#define USBD_CDC_EP_CMD (0x81)
#define USBD_CDC_EP_OUT (0x02)
#define USBD_CDC_EP_IN (0x82)
#define USBD_CDC_CMD_MAX_SIZE (8)
#define USBD_CDC_IN_OUT_MAX_SIZE (64)

#define USBD_MAX_POWER_MA (250)

#define USBD_STR_0 (0x00)
#define USBD_STR_MANUF (0x01)
#define USBD_STR_PRODUCT (0x02)
#define USBD_STR_SERIAL (0x03)
#define USBD_STR_CDC (0x04)
#define USBD_STR_RPI_RESET (0x05)

#define TUD_RPI_RESET_DESC_LEN (9)
#define USBD_DESC_LEN (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN + TUD_RPI_RESET_DESC_LEN)

#define TUD_RPI_RESET_DESCRIPTOR(_itfnum, _stridx)                                   \
    9, TUSB_DESC_INTERFACE, _itfnum, 0, 0, TUSB_CLASS_VENDOR_SPECIFIC,                \
        RESET_INTERFACE_SUBCLASS, RESET_INTERFACE_PROTOCOL, _stridx,

static const tusb_desc_device_t usbd_desc_device = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,
    .bDeviceClass = TUSB_CLASS_MISC,
    .bDeviceSubClass = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = USBD_VID,
    .idProduct = USBD_PID,
    .bcdDevice = 0x0100,
    .iManufacturer = USBD_STR_MANUF,
    .iProduct = USBD_STR_PRODUCT,
    .iSerialNumber = USBD_STR_SERIAL,
    .bNumConfigurations = 1,
};

static const uint8_t usbd_desc_cfg[USBD_DESC_LEN] = {
    TUD_CONFIG_DESCRIPTOR(1, USBD_ITF_MAX, USBD_STR_0, USBD_DESC_LEN, 0, USBD_MAX_POWER_MA),

    TUD_CDC_DESCRIPTOR(USBD_ITF_CDC, USBD_STR_CDC, USBD_CDC_EP_CMD, USBD_CDC_CMD_MAX_SIZE,
                       USBD_CDC_EP_OUT, USBD_CDC_EP_IN, USBD_CDC_IN_OUT_MAX_SIZE),

    TUD_RPI_RESET_DESCRIPTOR(USBD_ITF_RPI_RESET, USBD_STR_RPI_RESET)
};

static char usbd_serial_str[PICO_UNIQUE_BOARD_ID_SIZE_BYTES * 2 + 1];

static const char *const usbd_desc_str[] = {
    [USBD_STR_MANUF] = USBD_MANUFACTURER,
    [USBD_STR_PRODUCT] = USBD_PRODUCT,
    [USBD_STR_SERIAL] = usbd_serial_str,
    [USBD_STR_CDC] = USBD_PRODUCT,
    [USBD_STR_RPI_RESET] = "Reset",
};

const uint8_t *tud_descriptor_device_cb(void) {
    return (const uint8_t *)&usbd_desc_device;
}

const uint8_t *tud_descriptor_configuration_cb(uint8_t index) {
    (void)index;
    return usbd_desc_cfg;
}

#define USBD_DESC_STR_MAX (20)

const uint16_t *tud_descriptor_string_cb(uint8_t index, uint16_t langid) {
    static uint16_t desc_str[USBD_DESC_STR_MAX];
    (void)langid;

    /* Numero de serie : identifiant unique de la puce, hexadecimal majuscule. */
    if (!usbd_serial_str[0]) {
        pico_get_unique_board_id_string(usbd_serial_str, sizeof(usbd_serial_str));
    }

    uint8_t len;
    if (index == 0) {
        desc_str[1] = 0x0409; /* anglais */
        len = 1;
    } else {
        if (index >= sizeof(usbd_desc_str) / sizeof(usbd_desc_str[0])) {
            return NULL;
        }
        const char *str = usbd_desc_str[index];
        for (len = 0; len < USBD_DESC_STR_MAX - 1 && str[len]; ++len) {
            desc_str[1 + len] = (uint16_t)(uint8_t)str[len];
        }
    }

    /* Premier mot : longueur totale en octets et type de descripteur. */
    desc_str[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2 * len + 2));
    return desc_str;
}
