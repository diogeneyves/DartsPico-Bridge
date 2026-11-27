// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Chien de garde materiel de la carte (specification section 8) : la boucle
// du coeur 0 ne le nourrit que si le coeur 1 a progresse depuis la derniere
// fois. Le coeur 1 fait avancer un compteur de vie (a chaque interruption du
// rafraichissement, a partir de M4-f2b) ; le coeur 0 le lit et appelle
// should_feed(). Si l'un des deux coeurs se bloque, le chien de garde n'est
// plus nourri et la carte redemarre au bout de 2 s.

#ifndef I75W_LIVENESS_HPP
#define I75W_LIVENESS_HPP

#include <cstdint>

namespace i75w {

class LivenessGate {
public:
    // start : valeur du compteur de vie du coeur 1 au demarrage du chien de garde.
    explicit LivenessGate(uint32_t start) : seen_(start) {}

    // true si le compteur a change depuis le dernier true (le coeur 1 a
    // progresse) : nourrir le chien de garde. Le compteur peut boucler.
    bool should_feed(uint32_t counter) {
        if (counter == seen_) {
            return false;
        }
        seen_ = counter;
        return true;
    }

private:
    uint32_t seen_;
};

}  // namespace i75w

#endif  // I75W_LIVENESS_HPP
