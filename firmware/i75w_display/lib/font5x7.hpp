// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Police bitmap 5 x 7 de l'animation de secours (specification section 8,
// 36e et 38e complements) : seulement les 7 glyphes de « RASPYDARTS »
// (R, A, S, P, Y, D, T), dessines pour ce depot, sans reprise d'une police
// existante. Une ligne = 5 bits, bit 4 = colonne de gauche.

#ifndef I75W_FONT5X7_HPP
#define I75W_FONT5X7_HPP

#include <cstdint>

namespace i75w {

constexpr int kGlyphWidth = 5;
constexpr int kGlyphHeight = 7;

struct Glyph {
    char c;
    uint8_t rows[kGlyphHeight];
};

constexpr Glyph kFont5x7[] = {
    {'R', {0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11}},
    {'A', {0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11}},
    {'S', {0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E}},
    {'P', {0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10}},
    {'Y', {0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04}},
    {'D', {0x1C, 0x12, 0x11, 0x11, 0x11, 0x12, 0x1C}},
    {'T', {0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04}},
};

constexpr int kFontGlyphs = sizeof(kFont5x7) / sizeof(kFont5x7[0]);

// Glyphe d'un caractere, nullptr s'il n'est pas dans la police.
inline const Glyph* find_glyph(char c) {
    for (const Glyph& g : kFont5x7) {
        if (g.c == c) {
            return &g;
        }
    }
    return nullptr;
}

}  // namespace i75w

#endif  // I75W_FONT5X7_HPP
