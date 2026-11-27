// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Format des tampons du pilote HUB75 (specification section 8, 36e et 37e
// complements), sans materiel :
//
// - Pixel du pilote : 32 bits, rouge sur les bits 0 a 9, vert sur 10 a 19,
//   bleu sur 20 a 29 (ordre RGB, valeurs gamma sur 10 bits).
// - RGB565 -> Pixel : 8 bits par canal par replication des bits hauts
//   (r8 = r5 << 3 | r5 >> 2, g8 = g6 << 2 | g6 >> 4), puis kGamma10.
// - Disposition : les lignes N et N + 16 sont balayees ensemble ; elles sont
//   entrelacees, pixel par pixel : indice ((y % 16) * 128 + x) * 2, plus 1
//   pour la moitie basse (y >= 16).
// - Largeur de l'impulsion OE : ceil(b * 6 / 255) ; 0 = dalles eteintes (OE
//   force au niveau haut par le coeur 1, la PIO gardant OE actif au moins un
//   cycle).

#ifndef I75W_PIXEL_HPP
#define I75W_PIXEL_HPP

#include <cstddef>
#include <cstdint>

#include "gamma.hpp"
#include "proto.hpp"

namespace i75w {

constexpr int kScanRows = kHeight / 2;   // 16 lignes balayees
constexpr unsigned kOeWidthMax = 6;      // largeur pour BRIGHTNESS 255 (section 8)

constexpr uint32_t rgb565_to_pixel(uint16_t c) {
    const unsigned r5 = (c >> 11) & 0x1Fu;
    const unsigned g6 = (c >> 5) & 0x3Fu;
    const unsigned b5 = c & 0x1Fu;
    const unsigned r8 = (r5 << 3) | (r5 >> 2);
    const unsigned g8 = (g6 << 2) | (g6 >> 4);
    const unsigned b8 = (b5 << 3) | (b5 >> 2);
    return static_cast<uint32_t>(kGamma10[r8]) |
           (static_cast<uint32_t>(kGamma10[g8]) << 10) |
           (static_cast<uint32_t>(kGamma10[b8]) << 20);
}

// Indice du pixel (x, y) de l'image 128 x 32 dans un tampon du pilote.
constexpr size_t pixel_index(int x, int y) {
    return (static_cast<size_t>(y % kScanRows) * kWidth + static_cast<size_t>(x)) * 2 +
           (y >= kScanRows ? 1u : 0u);
}

// Image RGB565 (kPixels valeurs, lignes de haut en bas) -> tampon du pilote
// (kPixels mots de 32 bits).
inline void convert_frame(const uint16_t* rgb565, uint32_t* out) {
    for (int y = 0; y < kHeight; ++y) {
        const uint16_t* line = rgb565 + static_cast<size_t>(y) * kWidth;
        uint32_t* dst = out + (static_cast<size_t>(y % kScanRows) * kWidth) * 2 +
                        (y >= kScanRows ? 1u : 0u);
        for (int x = 0; x < kWidth; ++x) {
            dst[static_cast<size_t>(x) * 2] = rgb565_to_pixel(line[x]);
        }
    }
}

// Largeur de l'impulsion OE pour BRIGHTNESS = b : ceil(b * 6 / 255).
constexpr unsigned oe_width(uint8_t b) {
    return (static_cast<unsigned>(b) * kOeWidthMax + 254u) / 255u;
}

}  // namespace i75w

#endif  // I75W_PIXEL_HPP
