// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).

#include "crc32.hpp"

namespace i75w {

namespace {

struct Table {
    uint32_t v[256];
    constexpr Table() : v() {
        for (uint32_t i = 0; i < 256; ++i) {
            uint32_t c = i;
            for (int k = 0; k < 8; ++k) {
                c = (c & 1u) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
            }
            v[i] = c;
        }
    }
};

// Table calculee a la compilation : en flash sur la carte, aucune initialisation.
constexpr Table kTable;

}  // namespace

uint32_t crc32(const uint8_t* data, size_t len, uint32_t crc) {
    crc = ~crc;
    for (size_t i = 0; i < len; ++i) {
        crc = kTable.v[(crc ^ data[i]) & 0xFFu] ^ (crc >> 8);
    }
    return ~crc;
}

}  // namespace i75w
