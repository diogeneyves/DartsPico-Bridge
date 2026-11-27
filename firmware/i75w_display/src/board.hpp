// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Broches de l'Interstate 75 W RP2350 (specification section 8, 36e
// complement). Source : schema officiel Pimoroni PIM727 (pages 1 et 2) et
// modules/rp2350/interstate75.py du depot pimoroni/interstate75.

#ifndef I75W_BOARD_HPP
#define I75W_BOARD_HPP

namespace board {

// HUB75 : R0 G0 B0 R1 G1 B1 (0 a 5), ROW_A a ROW_E (6 a 10), CLK (11),
// STB (12), OE (13, actif au niveau bas ; tire vers +3V3 par R8, 10 kohm).
constexpr unsigned kHub75First = 0;
constexpr unsigned kHub75Clk = 11;
constexpr unsigned kHub75Stb = 12;
constexpr unsigned kHub75Oe = 13;

// Boutons A et B, vers la masse sans tirage externe : inutilises (tirage interne vers le haut).
constexpr unsigned kSwitchA = 14;
constexpr unsigned kSwitchB = 15;

// LED SK6812 (LED_DATA) : inutilisee, sortie au niveau bas.
constexpr unsigned kLedData = 18;

// WL_ON de la puce sans fil : sortie au niveau bas (puce eteinte).
constexpr unsigned kWlOn = 23;

// 5V_SENSE : pont 10 kohm + 10 kohm (R3, R9), entree ADC2.
constexpr unsigned k5vSense = 28;
constexpr unsigned k5vSenseAdcInput = 2;

}  // namespace board

#endif  // I75W_BOARD_HPP
