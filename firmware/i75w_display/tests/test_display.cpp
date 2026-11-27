// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Affichage sans materiel (specification section 8, 36e et 37e complements) :
// table gamma, conversion RGB565 -> Pixel du pilote, disposition entrelacee
// des lignes N et N + 16, largeur de l'impulsion OE, triple tampon (dont un
// essai a deux fils d'execution, comme les deux coeurs).

#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <memory>
#include <thread>
#include <vector>

#include "gamma.hpp"
#include "pixel.hpp"
#include "runner.hpp"
#include "triple.hpp"

using i75w::kGamma10;
using i75w::oe_width;
using i75w::pixel_index;
using i75w::rgb565_to_pixel;

namespace {

constexpr uint32_t px(unsigned r10, unsigned g10, unsigned b10) {
    return r10 | (g10 << 10) | (b10 << 20);
}

}  // namespace

TEST(gamma_table_shape) {
    // Les valeurs sont celles du pilote (copie verifiee a l'ecriture) ; elles
    // s'ecartent d'au plus 1 de la formule donnee en commentaire.
    int prev = -1;
    int formula_prev = -1;
    for (int x = 0; x < 256; ++x) {
        int f = static_cast<int>(std::lround(1024.0 * std::pow(x / 255.0, 2.2)));
        if (x > 0 && f <= formula_prev) {
            f = formula_prev + 1;
        }
        formula_prev = f;
        const int v = kGamma10[x];
        CHECK(v > prev);               // strictement croissante
        CHECK(std::abs(v - f) <= 1);
        if (x < 80) {
            CHECK_EQ(v, x);            // identite en bas de la table
        }
        prev = v;
    }
    CHECK_EQ(int(kGamma10[0]), 0);
    CHECK_EQ(int(kGamma10[88]), 98);   // valeur du pilote (la formule donne 99)
    CHECK_EQ(int(kGamma10[255]), 1023);
}

TEST(rgb565_to_pixel_primaries) {
    CHECK_EQ(rgb565_to_pixel(0x0000), px(0, 0, 0));
    CHECK_EQ(rgb565_to_pixel(0xFFFF), px(1023, 1023, 1023));
    CHECK_EQ(rgb565_to_pixel(0xF800), px(1023, 0, 0));  // rouge
    CHECK_EQ(rgb565_to_pixel(0x07E0), px(0, 1023, 0));  // vert
    CHECK_EQ(rgb565_to_pixel(0x001F), px(0, 0, 1023));  // bleu
    CHECK_EQ(rgb565_to_pixel(0xFC00), px(1023, kGamma10[130], 0));  // orange de l'animation
}

TEST(rgb565_to_pixel_replicates_high_bits) {
    // r5 = 16 -> r8 = 16 << 3 | 16 >> 2 = 132 ; g6 = 32 -> g8 = 130 ; b5 = 1 -> b8 = 8.
    const uint16_t c = static_cast<uint16_t>((16u << 11) | (32u << 5) | 1u);
    CHECK_EQ(rgb565_to_pixel(c), px(kGamma10[132], kGamma10[130], kGamma10[8]));
    CHECK_EQ(int(kGamma10[132]), 240);
    // Tous les r5 : r8 = r5 << 3 | r5 >> 2, croissant, 0 et 255 aux bouts.
    for (unsigned r5 = 0; r5 < 32; ++r5) {
        const unsigned r8 = (r5 << 3) | (r5 >> 2);
        CHECK_EQ(rgb565_to_pixel(static_cast<uint16_t>(r5 << 11)), px(kGamma10[r8], 0, 0));
    }
    for (unsigned g6 = 0; g6 < 64; ++g6) {
        const unsigned g8 = (g6 << 2) | (g6 >> 4);
        CHECK_EQ(rgb565_to_pixel(static_cast<uint16_t>(g6 << 5)), px(0, kGamma10[g8], 0));
    }
    // Bits 30 et 31 toujours a zero.
    for (unsigned c2 = 0; c2 < 0x10000; c2 += 7) {
        CHECK_EQ(rgb565_to_pixel(static_cast<uint16_t>(c2)) >> 30, 0u);
    }
}

TEST(pixel_index_interleaves_rows_n_and_n_plus_16) {
    CHECK_EQ(pixel_index(0, 0), size_t(0));
    CHECK_EQ(pixel_index(0, 16), size_t(1));
    CHECK_EQ(pixel_index(1, 0), size_t(2));
    CHECK_EQ(pixel_index(127, 15), size_t((15 * 128 + 127) * 2));
    CHECK_EQ(pixel_index(127, 31), size_t((15 * 128 + 127) * 2 + 1));
    CHECK_EQ(pixel_index(5, 17), size_t((1 * 128 + 5) * 2 + 1));
    // Bijection sur les 4 096 pixels.
    std::vector<int> seen(i75w::kPixels, 0);
    for (int y = 0; y < i75w::kHeight; ++y) {
        for (int x = 0; x < i75w::kWidth; ++x) {
            const size_t i = pixel_index(x, y);
            CHECK(i < i75w::kPixels);
            if (i < i75w::kPixels) {
                ++seen[i];
            }
        }
    }
    int ones = 0;
    for (int n : seen) {
        ones += n == 1 ? 1 : 0;
    }
    CHECK_EQ(ones, int(i75w::kPixels));
}

TEST(convert_frame_matches_pixel_by_pixel) {
    std::unique_ptr<uint16_t[]> img(new uint16_t[i75w::kPixels]);
    std::unique_ptr<uint32_t[]> out(new uint32_t[i75w::kPixels]);
    uint32_t seed = 12345;
    for (size_t i = 0; i < i75w::kPixels; ++i) {
        seed = seed * 1103515245u + 12345u;
        img[i] = static_cast<uint16_t>(seed >> 16);
        out[i] = 0xDEADBEEFu;
    }
    i75w::convert_frame(img.get(), out.get());
    int ok = 0;
    for (int y = 0; y < i75w::kHeight; ++y) {
        for (int x = 0; x < i75w::kWidth; ++x) {
            const uint16_t c = img[static_cast<size_t>(y) * i75w::kWidth + x];
            ok += out[pixel_index(x, y)] == rgb565_to_pixel(c) ? 1 : 0;
        }
    }
    CHECK_EQ(ok, int(i75w::kPixels));  // chaque mot ecrit, aucun oublie
}

TEST(oe_width_is_ceil_b_times_6_over_255) {
    CHECK_EQ(oe_width(0), 0u);
    CHECK_EQ(oe_width(1), 1u);
    CHECK_EQ(oe_width(42), 1u);
    CHECK_EQ(oe_width(43), 2u);
    CHECK_EQ(oe_width(85), 2u);
    CHECK_EQ(oe_width(86), 3u);
    CHECK_EQ(oe_width(128), 4u);
    CHECK_EQ(oe_width(212), 5u);
    CHECK_EQ(oe_width(213), 6u);
    CHECK_EQ(oe_width(255), 6u);
    unsigned prev = 0;
    for (unsigned b = 1; b < 256; ++b) {
        const unsigned w = oe_width(static_cast<uint8_t>(b));
        CHECK(w >= 1 && w <= 6);          // seul 0 eteint
        CHECK(w == prev || w == prev + 1);
        CHECK_EQ(w * 255 >= b * 6, true);  // plafond : w >= b * 6 / 255
        CHECK_EQ((w - 1) * 255 < b * 6, true);
        prev = w;
    }
    // Champ de la PIO : largeur << 5 << plan (0 a 9) tient sur 27 bits.
    CHECK((static_cast<uint32_t>(oe_width(255)) << 5 << 9) < (1u << 27));
}

TEST(triple_buffer_slots_stay_distinct) {
    i75w::TripleBuffer t;
    CHECK_EQ(t.read_slot(), 0u);
    CHECK_EQ(t.write_slot(), 2u);
    CHECK(!t.take());              // rien de publie : le lecteur garde son tampon
    CHECK_EQ(t.read_slot(), 0u);

    const unsigned a = t.write_slot();
    CHECK(!t.publish());           // rien a remplacer
    CHECK(t.write_slot() != a);
    CHECK(t.write_slot() != t.read_slot());
    CHECK(t.take());
    CHECK_EQ(t.read_slot(), a);    // le tampon publie est affiche
    CHECK(!t.take());              // une seule fois
    CHECK_EQ(t.replaced(), 0u);
    CHECK(t.write_slot() != t.read_slot());
}

TEST(triple_buffer_replaces_untaken_image) {
    i75w::TripleBuffer t;
    const unsigned first = t.write_slot();
    t.publish();
    const unsigned second = t.write_slot();
    CHECK(t.publish());            // first n'a pas ete pris : remplace
    CHECK_EQ(t.replaced(), 1u);
    CHECK_EQ(t.write_slot(), first);  // l'ecrivain recupere le tampon remplace
    CHECK(t.take());
    CHECK_EQ(t.read_slot(), second);  // seule la derniere image est affichee
    CHECK(!t.publish());
    CHECK_EQ(t.replaced(), 1u);
}

TEST(triple_buffer_two_threads) {
    // L'ecrivain ecrit le numero d'image dans tout son tampon puis le publie ;
    // le lecteur verifie qu'un tampon pris est complet et que les numeros
    // croissent. Aucun des deux n'attend l'autre.
    constexpr uint32_t kImages = 200000;
    constexpr size_t kWords = 64;
    static uint32_t bufs[3][kWords];
    for (auto& b : bufs) {
        for (uint32_t& w : b) {
            w = 0;
        }
    }
    i75w::TripleBuffer t;
    std::atomic<bool> done{false};
    std::atomic<uint32_t> torn{0};
    std::atomic<uint32_t> backwards{0};
    std::atomic<uint32_t> taken{0};

    std::thread reader([&] {
        uint32_t last = 0;
        for (;;) {
            const bool finished = done.load(std::memory_order_acquire);
            if (t.take()) {
                taken.fetch_add(1, std::memory_order_relaxed);
                const uint32_t* b = bufs[t.read_slot()];
                const uint32_t n = b[0];
                for (size_t i = 1; i < kWords; ++i) {
                    if (b[i] != n) {
                        torn.fetch_add(1, std::memory_order_relaxed);
                        break;
                    }
                }
                if (n < last) {
                    backwards.fetch_add(1, std::memory_order_relaxed);
                }
                last = n;
            } else if (finished) {
                break;
            }
        }
    });

    for (uint32_t n = 1; n <= kImages; ++n) {
        uint32_t* b = bufs[t.write_slot()];
        for (size_t i = 0; i < kWords; ++i) {
            b[i] = n;
        }
        t.publish();
    }
    done.store(true, std::memory_order_release);
    reader.join();

    CHECK_EQ(torn.load(), 0u);
    CHECK_EQ(backwards.load(), 0u);
    CHECK(taken.load() >= 1u);
    CHECK_EQ(taken.load() + t.replaced(), kImages);  // chaque image : prise ou remplacee
    CHECK_EQ(bufs[t.read_slot()][0], kImages);       // la derniere est affichee
}
