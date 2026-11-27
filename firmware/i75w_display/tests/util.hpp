// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Outils des tests sur PC : lecture des fichiers de vecteurs (format de la
// specification section 8 et de firmware/i75w_display/README.md), hexadecimal,
// construction de trames, puits d'octets en memoire. Les tests peuvent allouer.

#ifndef I75W_TEST_UTIL_HPP
#define I75W_TEST_UTIL_HPP

#include <cstdint>
#include <cstdio>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "card.hpp"
#include "crc32.hpp"
#include "decoder.hpp"
#include "proto.hpp"

namespace util {

using Bytes = std::vector<uint8_t>;

struct Record {
    int line;
    std::string key;
    std::vector<std::string> fields;
};

// Lignes utiles d'un fichier de vecteurs (ni vides, ni commentaires).
inline std::vector<Record> read_records(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("fichier illisible : " + path);
    }
    std::vector<Record> out;
    std::string line;
    int no = 0;
    while (std::getline(in, line)) {
        ++no;
        if (line.empty() || line[0] == '#') {
            continue;
        }
        Record r;
        r.line = no;
        std::istringstream ls(line);
        ls >> r.key;
        std::string f;
        while (ls >> f) {
            r.fields.push_back(f);
        }
        out.push_back(std::move(r));
    }
    return out;
}

inline int hex_digit(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    throw std::runtime_error(std::string("chiffre hexadecimal invalide : ") + c);
}

// Octets d'un champ hexadecimal ; "-" = aucun octet.
inline Bytes unhex(const std::string& s) {
    Bytes out;
    if (s == "-") {
        return out;
    }
    if (s.size() % 2 != 0) {
        throw std::runtime_error("longueur hexadecimale impaire");
    }
    out.reserve(s.size() / 2);
    for (size_t i = 0; i < s.size(); i += 2) {
        out.push_back(static_cast<uint8_t>(hex_digit(s[i]) << 4 | hex_digit(s[i + 1])));
    }
    return out;
}

inline std::string hex(const uint8_t* p, size_t n) {
    if (n == 0) {
        return "-";
    }
    static const char kHex[] = "0123456789abcdef";
    std::string s;
    s.reserve(n * 2);
    for (size_t i = 0; i < n; ++i) {
        s += kHex[p[i] >> 4];
        s += kHex[p[i] & 0x0F];
    }
    return s;
}

inline std::string hex2(unsigned v) {
    char b[8];
    std::snprintf(b, sizeof(b), "%02x", v & 0xFFu);
    return b;
}

inline std::string crc_hex(const void* p, size_t n) {
    char b[16];
    std::snprintf(b, sizeof(b), "%08x",
                  static_cast<unsigned>(i75w::crc32(static_cast<const uint8_t*>(p), n)));
    return b;
}

// Trame complete (section 6.1), sans controle de longueur.
inline Bytes encode(uint8_t type, const Bytes& payload = Bytes()) {
    Bytes f = {i75w::kSync0, i75w::kSync1, type, static_cast<uint8_t>(payload.size()),
               static_cast<uint8_t>(payload.size() >> 8)};
    f.insert(f.end(), payload.begin(), payload.end());
    uint32_t c = i75w::crc32(f.data() + 2, f.size() - 2);
    for (int k = 0; k < 4; ++k) {
        f.push_back(static_cast<uint8_t>(c >> (8 * k)));
    }
    return f;
}

inline Bytes frame_payload(uint16_t number, const Bytes& pixels) {
    Bytes p = {static_cast<uint8_t>(number), static_cast<uint8_t>(number >> 8)};
    p.insert(p.end(), pixels.begin(), pixels.end());
    return p;
}

// Puits d'octets en memoire (reponses de la carte).
struct MemSink : i75w::ByteSink {
    Bytes data;
    void write(const uint8_t* p, size_t n) override { data.insert(data.end(), p, p + n); }
};

// Trames rendues par un Decoder, copiees.
struct Collect : i75w::FrameHandler {
    std::vector<std::pair<uint8_t, Bytes>> frames;
    void on_frame(uint8_t type, const uint8_t* p, size_t n) override {
        frames.emplace_back(type, Bytes(p, p + n));
    }
};

}  // namespace util

#endif  // I75W_TEST_UTIL_HPP
