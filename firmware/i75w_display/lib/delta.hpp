// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Trame DELTA (section 6.4.1) : 64 tuiles de 8 x 8 pixels, brutes ou RLE,
// meme validation que parse_delta de shared/dmd_proto.py.

#ifndef I75W_DELTA_HPP
#define I75W_DELTA_HPP

#include <cstddef>
#include <cstdint>

#include "proto.hpp"

namespace i75w {

struct DeltaHeader {
    uint16_t number;
    uint16_t base;
    uint64_t mask;
};

// En-tete d'un DELTA. Renvoie kOk, ou kErrLength si n est hors de 12 a 8 194.
uint8_t delta_header(const uint8_t* p, size_t n, DeltaHeader& h);

// Decode le DELTA et ecrit ses tuiles dans image (8 192 octets RGB565).
// Renvoie kOk, kErrLength (longueur) ou kErrDelta (mode inconnu, compte nul
// ou somme differente de 64, donnees trop courtes ou en trop). En cas
// d'erreur, les tuiles qui precedent ont deja ete ecrites : l'appelant
// restaure l'image (la carte recopie l'image affichee).
uint8_t delta_apply(const uint8_t* p, size_t n, uint8_t* image);

}  // namespace i75w

#endif  // I75W_DELTA_HPP
