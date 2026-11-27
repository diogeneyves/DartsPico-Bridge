// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Constantes du protocole Pi <-> Interstate 75 W (specification section 6),
// identiques a shared/dmd_proto.py. Aucune dependance materielle.

#ifndef I75W_PROTO_HPP
#define I75W_PROTO_HPP

#include <cstddef>
#include <cstdint>

#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "bibliotheque prevue pour une machine petit-boutiste (RP2350, PC)"
#endif

namespace i75w {

constexpr uint8_t kProtoVersion = 1;
constexpr int kWidth = 128;
constexpr int kHeight = 32;
constexpr size_t kPixels = static_cast<size_t>(kWidth) * kHeight;  // 4 096
constexpr size_t kPixelsBytes = kPixels * 2;                         // 8 192, RGB565

constexpr uint8_t kSync0 = 0xAA;
constexpr uint8_t kSync1 = 0x55;
constexpr size_t kHeaderLen = 5;                       // synchronisation + type + longueur
constexpr size_t kCrcLen = 4;
constexpr size_t kMaxPayload = 2 + kPixelsBytes;       // 8 194 (FRAME)
constexpr size_t kMaxFrame = kHeaderLen + kMaxPayload + kCrcLen;  // 8 203
constexpr size_t kPingMax = 64;
constexpr size_t kErrorTextMax = 128;

// Types (section 6.2).
constexpr uint8_t kHelloReq = 0x01;
constexpr uint8_t kFrame = 0x02;
constexpr uint8_t kBrightness = 0x03;
constexpr uint8_t kClear = 0x04;
constexpr uint8_t kMode = 0x05;
constexpr uint8_t kPing = 0x06;
constexpr uint8_t kDelta = 0x07;
constexpr uint8_t kStatsReq = 0x08;
constexpr uint8_t kHello = 0x81;
constexpr uint8_t kPong = 0x86;
constexpr uint8_t kStats = 0x88;
constexpr uint8_t kTelemetry = 0x90;
constexpr uint8_t kError = 0xE0;

constexpr uint8_t kModePi = 0;
constexpr uint8_t kModeLocal = 1;

// Codes ERROR (section 6.2) ; 0 = pas d'erreur.
constexpr uint8_t kOk = 0x00;
constexpr uint8_t kErrUnknownType = 0x01;
constexpr uint8_t kErrLength = 0x02;
constexpr uint8_t kErrRange = 0x03;
constexpr uint8_t kErrDelta = 0x04;
constexpr uint8_t kErrInternal = 0x10;

// Longueurs des donnees de reponse.
constexpr size_t kHelloLen = 10;
constexpr size_t kTelemetryLen = 9;
constexpr size_t kStatsLen = 47;  // 11 x u32, u16, u8 (section 6.2)
constexpr uint8_t kStatsFlagWatchdog = 0x01;  // dernier demarrage : chien de garde

// Tuiles du DELTA (section 6.4.1).
constexpr int kTile = 8;
constexpr int kTilesX = kWidth / kTile;      // 16
constexpr int kTilesY = kHeight / kTile;     // 4
constexpr int kTileCount = kTilesX * kTilesY;  // 64
constexpr int kTilePixels = kTile * kTile;   // 64
constexpr size_t kTileBytes = kTilePixels * 2;  // 128
constexpr size_t kDeltaHeader = 12;          // numero, base, masque u64
constexpr uint8_t kTileRaw = 0;
constexpr uint8_t kTileRle = 1;

// Longueur minimale et maximale des donnees d'un type connu.
// Renvoie false pour un type inconnu (min et max non modifies).
inline bool length_range(uint8_t type, size_t& min, size_t& max) {
    switch (type) {
        case kHelloReq: min = 0; max = 0; return true;
        case kHello: min = kHelloLen; max = kHelloLen; return true;
        case kFrame: min = kMaxPayload; max = kMaxPayload; return true;
        case kBrightness: min = 1; max = 1; return true;
        case kClear: min = 0; max = 0; return true;
        case kMode: min = 1; max = 1; return true;
        case kPing: min = 0; max = kPingMax; return true;
        case kDelta: min = kDeltaHeader; max = kMaxPayload; return true;
        case kStatsReq: min = 0; max = 0; return true;
        case kPong: min = 0; max = kPingMax; return true;
        case kStats: min = kStatsLen; max = kStatsLen; return true;
        case kTelemetry: min = kTelemetryLen; max = kTelemetryLen; return true;
        case kError: min = 1; max = 1 + kErrorTextMax; return true;
        default: return false;
    }
}

// Longueur maximale acceptee par le recepteur : celle du type, 8 194 pour un type inconnu.
inline size_t max_length(uint8_t type) {
    size_t min = 0;
    size_t max = kMaxPayload;
    length_range(type, min, max);
    return max;
}

inline uint16_t get_u16(const uint8_t* p) {
    return static_cast<uint16_t>(p[0] | (p[1] << 8));
}

inline uint32_t get_u32(const uint8_t* p) {
    return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) |
           (static_cast<uint32_t>(p[2]) << 16) | (static_cast<uint32_t>(p[3]) << 24);
}

inline void put_u16(uint8_t* p, uint16_t v) {
    p[0] = static_cast<uint8_t>(v);
    p[1] = static_cast<uint8_t>(v >> 8);
}

inline void put_u32(uint8_t* p, uint32_t v) {
    p[0] = static_cast<uint8_t>(v);
    p[1] = static_cast<uint8_t>(v >> 8);
    p[2] = static_cast<uint8_t>(v >> 16);
    p[3] = static_cast<uint8_t>(v >> 24);
}

}  // namespace i75w

#endif  // I75W_PROTO_HPP
