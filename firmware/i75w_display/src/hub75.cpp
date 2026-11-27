// Pilote HUB75 de pimoroni-pico, copie et reduit (specification section 8,
// 34e, 36e et 37e complements).
//
// Origine : drivers/hub75/hub75.cpp de pimoroni-pico v1.29.0-2, commit
// 39b017a30717ed68e5ff15163bac89489acd09df.
//
// Modifications : Copyright (C) 2026 Gohy Yves, licence GPL v3 (voir LICENSE a
// la racine du depot). Le code d'origine reste sous la licence MIT ci-dessous.
// Corrections du code d'origine :
// - lignes 30 et 31 d'origine : gpio_put(pin_clk, ...) au lieu de pin_stb et
//   pin_oe, et niveau pose apres le passage en sortie : OE restait en sortie
//   au niveau bas, donc actif. Ici, chaque ligne recoit son niveau de repos
//   avant de passer en sortie (OE haut, inactif) ;
// - ligne 188 d'origine : premier DMA lu a &back_buffer (adresse du pointeur)
//   au lieu de back_buffer ;
// - gpio_set_function du SDK 2.1.0 remet a zero le forcage de sortie
//   (gpio_init et pio_gpio_init l'appellent) : start() met OE au niveau haut
//   cote PIO avant pio_gpio_init, puis force OE au niveau haut en sortie ;
// - stop() retire (le firmware n'arrete jamais le rafraichissement ; apres une
//   remise a zero, R8 de 10 kohm tient OE au niveau haut) ;
// - fin de cycle complet : appel de cycle_end (triple tampon, luminosite).
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

#include "hardware/clocks.h"
#include "hardware/gpio.h"

#include "hub75.hpp"

namespace pimoroni {

namespace {

// Niveau de repos, puis sortie (correction des lignes 15 a 31 d'origine).
void init_output(uint pin, bool level) {
    gpio_init(pin);
    gpio_put(pin, level);
    gpio_set_dir(pin, GPIO_OUT);
}

}  // namespace

Hub75::Hub75(uint width, uint height, const uint32_t *buffer)
 : width(width), height(height), back_buffer(buffer)
 {
    // OE d'abord : niveau haut (dalles eteintes) avant de passer en sortie.
    init_output(pin_oe, !oe_polarity);

    init_output(pin_r0, 0);
    init_output(pin_g0, 0);
    init_output(pin_b0, 0);

    init_output(pin_r1, 0);
    init_output(pin_g1, 0);
    init_output(pin_b1, 0);

    init_output(pin_row_a, 0);
    init_output(pin_row_b, 0);
    init_output(pin_row_c, 0);
    init_output(pin_row_d, 0);
    init_output(pin_row_e, 0);

    init_output(pin_clk, !clk_polarity);
    init_output(pin_stb, !stb_polarity);
}

void Hub75::start(irq_handler_t handler) {
    if(handler) {
        uint latch_cycles = clock_get_hz(clk_sys) / 4000000;

        // Claim the PIO so we can clean it upon soft restart
        pio_sm_claim(pio, sm_data);
        pio_sm_claim(pio, sm_row);

        data_prog_offs = pio_add_program(pio, &hub75_data_rgb888_program);
        row_prog_offs = pio_add_program(pio, &hub75_row_program);

        // OE au niveau haut cote PIO avant que pio_gpio_init ne donne la
        // broche a la PIO (et n'efface son forcage de sortie).
        pio_sm_set_pins_with_mask(pio, sm_row, 1u << pin_oe, 1u << pin_oe);

        hub75_data_rgb888_program_init(pio, sm_data, data_prog_offs, DATA_BASE_PIN, pin_clk);
        hub75_row_program_init(pio, sm_row, row_prog_offs, ROWSEL_BASE_PIN, ROWSEL_N_PINS, pin_stb, latch_cycles);

        // OE force au niveau haut jusqu'a ce que l'appelant le leve.
        gpio_set_outover(pin_oe, GPIO_OVERRIDE_HIGH);

        // Prevent flicker in Python caused by the smaller dataset just blasting through the PIO too quickly
        pio_sm_set_clkdiv(pio, sm_data, width <= 32 ? 2.0f : 1.0f);

        dma_channel = dma_claim_unused_channel(true);
        dma_channel_config config = dma_channel_get_default_config(dma_channel);
        channel_config_set_transfer_data_size(&config, DMA_SIZE_32);
        channel_config_set_bswap(&config, false);
        channel_config_set_dreq(&config, pio_get_dreq(pio, sm_data, true));
        dma_channel_configure(dma_channel, &config, &pio->txf[sm_data], NULL, 0, false);


        // Same handler for both DMA channels
        irq_add_shared_handler(DMA_IRQ_0, handler, PICO_SHARED_IRQ_HANDLER_DEFAULT_ORDER_PRIORITY);

        dma_channel_set_irq0_enabled(dma_channel, true);

        irq_set_enabled(DMA_IRQ_0, true);

        row = 0;
        bit = 0;

        hub75_data_rgb888_set_shift(pio, sm_data, data_prog_offs, bit);
        dma_channel_set_trans_count(dma_channel, width * 2, false);
        // Correction : back_buffer, et non &back_buffer (ligne 188 d'origine).
        dma_channel_set_read_addr(dma_channel, back_buffer, true);
    }
}

void Hub75::dma_complete() {

    if(dma_channel_get_irq0_status(dma_channel)) {
        dma_channel_acknowledge_irq0(dma_channel);

        // Push out a dummy pixel for each row
        pio_sm_put_blocking(pio, sm_data, 0);
        pio_sm_put_blocking(pio, sm_data, 0);

        // SM is finished when it stalls on empty TX FIFO
        hub75_wait_tx_stall(pio, sm_data);

        // Check that previous OEn pulse is finished, else things WILL get out of sequence
        hub75_wait_tx_stall(pio, sm_row);

        // Latch row data, pulse output enable for new row.
        pio_sm_put_blocking(pio, sm_row, row | (brightness << 5 << bit));

        row++;

        bool cycle_done = false;
        if(row == height / 2) {
            row = 0;
            bit++;
            if (bit == BIT_DEPTH) {
                bit = 0;
                cycle_done = true;
            }
            hub75_data_rgb888_set_shift(pio, sm_data, data_prog_offs, bit);
        }

        // Fin d'un cycle complet : la derniere ligne est deja verrouillee, le
        // cycle suivant peut partir d'une autre image sans la dechirer.
        if (cycle_done && cycle_end != nullptr) {
            cycle_end(*this);
        }

        dma_channel_set_trans_count(dma_channel, width * 2, false);
        dma_channel_set_read_addr(dma_channel, &back_buffer[row * width * 2], true);
    }
}

}
