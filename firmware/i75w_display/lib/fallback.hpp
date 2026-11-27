// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Animation de secours de l'Interstate 75 W (specification section 6.3 et
// section 8, 36e et 38e complements), sans materiel :
// - texte « RASPYDARTS », orange (RGB565 0xFC00, teinte a verifier au banc),
//   police 5 x 7 doublee (10 x 14), une colonne d'ecart entre les lettres
//   (2 px), soit 118 px de large, lignes 9 a 22 (centre verticalement) ;
// - defilement de droite a gauche a 30 px/s : colonne gauche du texte
//   x = 128 - (k mod 246), k = pas de 1/30 s depuis l'entree dans
//   l'animation ; le texte entre par la droite, sort completement a gauche,
//   puis recommence (environ 8,2 s).
// Le dessin se fait dans une image RGB565 fournie par l'appelant (tampon
// statique sur la carte) : rien sur la pile.

#ifndef I75W_FALLBACK_HPP
#define I75W_FALLBACK_HPP

#include <cstdint>

#include "font5x7.hpp"
#include "proto.hpp"

namespace i75w {

constexpr char kFallbackText[] = "RASPYDARTS";
constexpr int kFallbackChars = sizeof(kFallbackText) - 1;              // 10
constexpr uint16_t kFallbackColor = 0xFC00;                            // orange
constexpr int kFallbackScale = 2;                                      // 5 x 7 -> 10 x 14
constexpr int kFallbackGap = 1;                                        // colonnes de la police
constexpr int kFallbackAdvance = (kGlyphWidth + kFallbackGap) * kFallbackScale;  // 12
constexpr int kFallbackTextWidth = kFallbackChars * kFallbackAdvance - kFallbackGap * kFallbackScale;  // 118
constexpr int kFallbackTextHeight = kGlyphHeight * kFallbackScale;     // 14
constexpr int kFallbackTop = (kHeight - kFallbackTextHeight) / 2;     // 9
constexpr int kFallbackPeriod = kWidth + kFallbackTextWidth;          // 246 px
constexpr uint32_t kFallbackSpeed = 30;                                // px/s

static_assert(kFallbackTextWidth == 118, "texte de 118 px (38e complement)");
static_assert(kFallbackPeriod == 246, "boucle de 246 px (38e complement)");

// Colonne gauche du texte, elapsed_us apres l'entree dans l'animation.
int fallback_x(uint64_t elapsed_us);

// Image complete : fond noir, texte avec sa colonne gauche en x (decoupe aux
// bords de l'ecran).
void fallback_draw(int x, uint16_t* image);

// Suit l'animation : start() a chaque entree, puis step() a chaque tour de
// boucle ; step() redessine l'image et rend true seulement quand la position
// change (environ 30 fois par seconde), l'image est alors a publier.
class Fallback {
public:
    void start(uint64_t now_us);
    bool step(uint64_t now_us, uint16_t* image);
    int x() const { return x_; }

private:
    uint64_t start_us_ = 0;
    int x_ = 0;
    bool drawn_ = false;
};

}  // namespace i75w

#endif  // I75W_FALLBACK_HPP
