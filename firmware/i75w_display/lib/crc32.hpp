// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// CRC-32 "ZIP" (section 6.1) : polynome reflechi 0xEDB88320, compatible zlib
// et binascii.crc32. Valeur initiale 0 ; crc32(b, n2, crc32(a, n1)) = CRC de a + b.

#ifndef I75W_CRC32_HPP
#define I75W_CRC32_HPP

#include <cstddef>
#include <cstdint>

namespace i75w {

uint32_t crc32(const uint8_t* data, size_t len, uint32_t crc = 0);

}  // namespace i75w

#endif  // I75W_CRC32_HPP
