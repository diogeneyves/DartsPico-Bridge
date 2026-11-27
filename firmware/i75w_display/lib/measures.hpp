// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Conversions des mesures de la carte (specification section 8), sans
// materiel : lectures brutes de l'ADC 12 bits du RP2350 (0 a 4 095).
//
// - 5 V : GPIO28 (ADC2), pont 10 kohm + 10 kohm (schema PIM727, R3 et R9) :
//   lecture x 3 300 mV x 2 / pleine echelle, tronquee (meme facteur que
//   tools/bench_i75w/rx_bench.py).
// - Temperature : capteur interne, formule du SDK,
//   T = 27 - (V - 0,706) / 0,001721, en degres x 10, tronquee vers zero
//   (comme rx_bench.py).

#ifndef I75W_MEASURES_HPP
#define I75W_MEASURES_HPP

#include <cstdint>

namespace i75w {

constexpr uint32_t kAdcFullScale = 4095;  // ADC 12 bits
constexpr uint32_t kAdcRefMv = 3300;

// Tension du 5 V en mV, d'apres la lecture brute de GPIO28.
inline uint16_t millivolts_5v(uint16_t raw) {
    uint32_t r = raw > kAdcFullScale ? kAdcFullScale : raw;
    return static_cast<uint16_t>(r * kAdcRefMv * 2 / kAdcFullScale);  // 6 600 au plus
}

// Temperature du RP2350 en degres x 10, d'apres la lecture brute du capteur interne.
inline int16_t temperature_dc(uint16_t raw) {
    uint32_t r = raw > kAdcFullScale ? kAdcFullScale : raw;
    double v = static_cast<double>(r) * (kAdcRefMv / 1000.0) / kAdcFullScale;
    double t = (27.0 - (v - 0.706) / 0.001721) * 10.0;
    return static_cast<int16_t>(t);  // de -14 802 a 4 372 : tient dans un i16
}

}  // namespace i75w

#endif  // I75W_MEASURES_HPP
