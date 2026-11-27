// Pilote HUB75 de pimoroni-pico, copie et reduit (specification section 8,
// 34e, 36e et 37e complements).
//
// Origine : drivers/hub75/hub75.hpp de pimoroni-pico v1.29.0-2, commit
// 39b017a30717ed68e5ff15163bac89489acd09df.
//
// Modifications : Copyright (C) 2026 Gohy Yves, licence GPL v3 (voir LICENSE a
// la racine du depot). Le code d'origine reste sous la licence MIT ci-dessous.
// - sans pico_graphics, sans FM6126A, sans strobe inverse, sans ordre des
//   couleurs (RGB fige), sans allocation : les tampons sont fournis ;
// - retires : set_pixel, set_color, copy_to_back_buffer, update, clear,
//   hsv_to_rgb, stop, Pixel et GAMMA_10BIT (table dans lib/gamma.hpp,
//   conversion dans lib/pixel.hpp), broches des boutons et de la LED ;
// - back_buffer en mots de 32 bits (format de lib/pixel.hpp), changeable a la
//   fin d'un cycle complet par cycle_end (triple tampon) ;
// - corrections : initialisation de STB et de OE, premier DMA, OE tenu au
//   niveau haut pendant start() (voir hub75.cpp).
//
// ---------------------------------------------------------------------------
// MIT License
//
// Copyright (c) 2021 Pimoroni Ltd
//
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
//
// The above copyright notice and this permission notice shall be included in all
// copies or substantial portions of the Software.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
// SOFTWARE.
// ---------------------------------------------------------------------------

#ifndef I75W_HUB75_HPP
#define I75W_HUB75_HPP

#include <stdint.h>
#include "pico/stdlib.h"

#include "hardware/pio.h"
#include "hardware/dma.h"
#include "hardware/irq.h"

#include "hub75.pio.h"

namespace pimoroni {
const uint DATA_BASE_PIN = 0;
const uint DATA_N_PINS = 6;
const uint ROWSEL_BASE_PIN = 6;
const uint ROWSEL_N_PINS = 5;
const uint BIT_DEPTH = 10;

class Hub75 {
    public:
    uint width;
    uint height;
    // Image affichee : width * height mots de 32 bits, lignes N et N + height / 2
    // entrelacees (lib/pixel.hpp). Lue par le DMA ; ne la changer que dans cycle_end.
    const uint32_t *back_buffer;

    // Appelee dans l'interruption, a la fin de chaque cycle complet
    // (height / 2 lignes x BIT_DEPTH plans), avant le DMA du cycle suivant :
    // peut changer back_buffer et brightness sans dechirer l'image.
    void (*cycle_end)(Hub75 &hub75) = nullptr;

    // DMA & PIO
    int dma_channel = -1;
    uint bit = 0;
    uint row = 0;

    PIO pio = pio0;
    uint sm_data = 0;
    uint sm_row = 1;

    uint data_prog_offs = 0;
    uint row_prog_offs = 0;

    // Largeur de l'impulsion OE (1 a 6 ; 6 = valeur par defaut du pilote).
    uint brightness = 6;


    // Top half of display - 16 rows on a 32x32 panel
    unsigned int pin_r0 = 0;
    unsigned int pin_g0 = 1;
    unsigned int pin_b0 = 2;

    // Bottom half of display - 16 rows on a 64x64 panel
    unsigned int pin_r1 = 3;
    unsigned int pin_g1 = 4;
    unsigned int pin_b1 = 5;

    // Address pins, 5 lines = 2^5 = 32 values (max 64x64 display)
    unsigned int pin_row_a = 6;
    unsigned int pin_row_b = 7;
    unsigned int pin_row_c = 8;
    unsigned int pin_row_d = 9;
    unsigned int pin_row_e = 10;

    // Sundry things
    unsigned int pin_clk = 11;    // Clock
    unsigned int pin_stb = 12;    // Strobe/Latch
    unsigned int pin_oe = 13;     // Output Enable

    const bool clk_polarity = 1;
    const bool stb_polarity = 1;
    const bool oe_polarity = 0;

    Hub75(uint width, uint height, const uint32_t *buffer);

    // A appeler sur le coeur qui recevra DMA_IRQ_0 (coeur 1). En sortie, OE
    // est force au niveau haut (gpio_set_outover) : a lever par l'appelant.
    void start(irq_handler_t handler);
    void dma_complete();
    };
}

#endif  // I75W_HUB75_HPP
