// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).

#include "fallback.hpp"

#include <cstring>

namespace i75w {

int fallback_x(uint64_t elapsed_us) {
    const uint64_t k = elapsed_us * kFallbackSpeed / 1000000u;
    return kWidth - static_cast<int>(k % kFallbackPeriod);
}

void fallback_draw(int x, uint16_t* image) {
    std::memset(image, 0, kPixelsBytes);
    for (int i = 0; i < kFallbackChars; ++i) {
        const Glyph* g = find_glyph(kFallbackText[i]);
        if (g == nullptr) {
            continue;
        }
        const int gx = x + i * kFallbackAdvance;
        if (gx >= kWidth || gx + kGlyphWidth * kFallbackScale <= 0) {
            continue;
        }
        for (int row = 0; row < kGlyphHeight; ++row) {
            for (int col = 0; col < kGlyphWidth; ++col) {
                if ((g->rows[row] & (0x10 >> col)) == 0) {
                    continue;
                }
                for (int dy = 0; dy < kFallbackScale; ++dy) {
                    const int py = kFallbackTop + row * kFallbackScale + dy;
                    for (int dx = 0; dx < kFallbackScale; ++dx) {
                        const int px = gx + col * kFallbackScale + dx;
                        if (px >= 0 && px < kWidth) {
                            image[py * kWidth + px] = kFallbackColor;
                        }
                    }
                }
            }
        }
    }
}

void Fallback::start(uint64_t now_us) {
    start_us_ = now_us;
    drawn_ = false;
}

bool Fallback::step(uint64_t now_us, uint16_t* image) {
    const int x = fallback_x(now_us >= start_us_ ? now_us - start_us_ : 0);
    if (drawn_ && x == x_) {
        return false;
    }
    x_ = x;
    drawn_ = true;
    fallback_draw(x, image);
    return true;
}

}  // namespace i75w
