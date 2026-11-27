// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Affichage de l'Interstate 75 W (specification section 8, 36e et 37e
// complements). Voir display.hpp.
//
// - Trois tampons Pixel de 16 Ko (lib/pixel.hpp), circulant par
//   lib/triple.hpp : le coeur 0 convertit dans un tampon libre et publie ; le
//   coeur 1 prend le tampon en attente a la fin d'un cycle complet
//   (16 lignes x 10 plans de bits).
// - Luminosite : valeur atomique publiee par le coeur 0, appliquee par le
//   coeur 1 en fin de cycle : largeur OE = ceil(b x 6 / 255) ; 0 = OE force au
//   niveau haut (la PIO garde OE actif au moins un cycle).
// - OE reste force au niveau haut jusqu'a la fin du premier cycle complet,
//   puis tant que la luminosite vaut 0 (37e complement).

#include "display.hpp"

#include <atomic>
#include <cstring>
#include <new>

#include "hardware/gpio.h"
#include "hardware/sync.h"

#include "board.hpp"
#include "hub75.hpp"
#include "pixel.hpp"
#include "proto.hpp"
#include "triple.hpp"

namespace display {

namespace {

alignas(4) uint32_t g_pixels[3][i75w::kPixels];
i75w::TripleBuffer g_ring;

std::atomic<uint8_t> g_brightness{255};  // section 8 : 255 au demarrage
std::atomic<uint32_t> g_alive{0};
std::atomic<uint32_t> g_cycles{0};

// Pilote, construit sur le coeur 1 (core1_main) ; memoire statique.
alignas(pimoroni::Hub75) unsigned char g_hub75_storage[sizeof(pimoroni::Hub75)];
pimoroni::Hub75* g_hub75 = nullptr;

// Coeur 1, dans l'interruption, a la fin d'un cycle complet.
void on_cycle_end(pimoroni::Hub75& hub75) {
    if (g_ring.take()) {
        hub75.back_buffer = g_pixels[g_ring.read_slot()];
    }
    const uint8_t b = g_brightness.load(std::memory_order_relaxed);
    if (b == 0) {
        gpio_set_outover(board::kHub75Oe, GPIO_OVERRIDE_HIGH);  // dalles eteintes
    } else {
        hub75.brightness = i75w::oe_width(b);
        gpio_set_outover(board::kHub75Oe, GPIO_OVERRIDE_NORMAL);  // OE rendu a la PIO
    }
    g_cycles.fetch_add(1, std::memory_order_relaxed);
}

void dma_irq() {
    g_alive.fetch_add(1, std::memory_order_relaxed);
    g_hub75->dma_complete();
}

}  // namespace

void init() {
    std::memset(g_pixels, 0, sizeof(g_pixels));
}

void core1_main() {
    g_hub75 = new (g_hub75_storage)
        pimoroni::Hub75(i75w::kWidth, i75w::kHeight, g_pixels[g_ring.read_slot()]);
    g_hub75->cycle_end = on_cycle_end;
    const uint8_t b = g_brightness.load(std::memory_order_relaxed);
    if (b != 0) {
        g_hub75->brightness = i75w::oe_width(b);
    }
    g_hub75->start(dma_irq);  // DMA_IRQ_0 sur ce coeur ; OE force au niveau haut
    for (;;) {
        __wfi();
    }
}

void publish_rgb565(const uint16_t* image) {
    i75w::convert_frame(image, g_pixels[g_ring.write_slot()]);
    g_ring.publish();
}

void publish_black() {
    std::memset(g_pixels[g_ring.write_slot()], 0, sizeof(g_pixels[0]));
    g_ring.publish();
}

void set_brightness(uint8_t b) {
    g_brightness.store(b, std::memory_order_relaxed);
}

uint32_t alive() {
    return g_alive.load(std::memory_order_relaxed);
}

uint32_t cycles() {
    return g_cycles.load(std::memory_order_relaxed);
}

uint32_t replaced() {
    return g_ring.replaced();
}

}  // namespace display
