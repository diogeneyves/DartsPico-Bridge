// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).

#include "delta.hpp"

#include <cstring>

namespace i75w {

namespace {

constexpr size_t kRowBytes = static_cast<size_t>(kWidth) * 2;
constexpr size_t kTileRowBytes = static_cast<size_t>(kTile) * 2;

// Adresse du pixel en haut a gauche de la tuile i dans l'image.
inline size_t tile_start(int i) {
    return static_cast<size_t>(i / kTilesX) * kTile * kRowBytes +
           static_cast<size_t>(i % kTilesX) * kTileRowBytes;
}

}  // namespace

uint8_t delta_header(const uint8_t* p, size_t n, DeltaHeader& h) {
    if (n < kDeltaHeader || n > kMaxPayload) {
        return kErrLength;
    }
    h.number = get_u16(p);
    h.base = get_u16(p + 2);
    h.mask = static_cast<uint64_t>(get_u32(p + 4)) |
             (static_cast<uint64_t>(get_u32(p + 8)) << 32);
    return kOk;
}

uint8_t delta_apply(const uint8_t* p, size_t n, uint8_t* image) {
    DeltaHeader h;
    uint8_t err = delta_header(p, n, h);
    if (err != kOk) {
        return err;
    }
    size_t pos = kDeltaHeader;
    for (int i = 0; i < kTileCount; ++i) {
        if (((h.mask >> i) & 1u) == 0) {
            continue;
        }
        if (pos >= n) {
            return kErrDelta;  // tuile absente
        }
        const uint8_t mode = p[pos++];
        uint8_t* dst = image + tile_start(i);
        if (mode == kTileRaw) {
            if (pos + kTileBytes > n) {
                return kErrDelta;  // tuile tronquee
            }
            for (int y = 0; y < kTile; ++y) {
                std::memcpy(dst + y * kRowBytes, p + pos + y * kTileRowBytes, kTileRowBytes);
            }
            pos += kTileBytes;
        } else if (mode == kTileRle) {
            int k = 0;
            while (k < kTilePixels) {
                if (pos + 3 > n) {
                    return kErrDelta;  // plage tronquee
                }
                const int count = p[pos];
                if (count == 0 || k + count > kTilePixels) {
                    return kErrDelta;  // compte nul ou somme au-dela de 64
                }
                const uint8_t c0 = p[pos + 1];
                const uint8_t c1 = p[pos + 2];
                pos += 3;
                for (int j = k; j < k + count; ++j) {
                    uint8_t* px = dst + (j / kTile) * kRowBytes + (j % kTile) * 2;
                    px[0] = c0;
                    px[1] = c1;
                }
                k += count;
            }
        } else {
            return kErrDelta;  // mode inconnu
        }
    }
    if (pos != n) {
        return kErrDelta;  // octets en trop
    }
    return kOk;
}

}  // namespace i75w
