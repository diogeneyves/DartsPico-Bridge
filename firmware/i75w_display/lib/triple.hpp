// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Triple tampon du pilote HUB75 (specification section 8, 36e complement),
// sans materiel : seuls les numeros des trois tampons (0, 1, 2) circulent ;
// les tampons eux-memes sont ailleurs (src/display.cpp).
//
// - Un seul ecrivain (coeur 0) et un seul lecteur (coeur 1, interruption du
//   rafraichissement). Aucun des deux n'attend jamais.
// - L'ecrivain remplit write_slot() (ni affiche, ni en attente), puis
//   publish() : ce tampon devient le tampon en attente, et l'ecrivain recoit
//   l'ancien tampon en attente. Si celui-ci n'avait pas encore ete pris, il
//   est remplace (compte dans replaced(), champ du meme nom de STATS).
// - Le lecteur appelle take() a la fin d'un cycle complet : s'il y a un
//   tampon en attente, il devient read_slot() (affiche) et l'ancien tampon
//   affiche est rendu a l'echange.
//
// Un seul octet partage, change par echange atomique : numero du tampon
// d'echange, plus un bit « nouveau » (publie, pas encore pris).

#ifndef I75W_TRIPLE_HPP
#define I75W_TRIPLE_HPP

#include <atomic>
#include <cstdint>

namespace i75w {

class TripleBuffer {
public:
    static_assert(std::atomic<uint8_t>::is_always_lock_free, "echange atomique sans verrou requis");

    // Ecrivain (coeur 0).
    unsigned write_slot() const { return write_; }

    // true si un tampon publie et pas encore pris a ete remplace.
    bool publish() {
        const uint8_t old = middle_.exchange(static_cast<uint8_t>(write_ | kFresh),
                                             std::memory_order_acq_rel);
        write_ = old & kSlotMask;
        if ((old & kFresh) != 0) {
            ++replaced_;
            return true;
        }
        return false;
    }

    uint32_t replaced() const { return replaced_; }  // lu par l'ecrivain seulement

    // Lecteur (coeur 1).
    unsigned read_slot() const { return read_; }

    // true si un nouveau tampon a ete pris.
    bool take() {
        if ((middle_.load(std::memory_order_relaxed) & kFresh) == 0) {
            return false;
        }
        const uint8_t old = middle_.exchange(static_cast<uint8_t>(read_), std::memory_order_acq_rel);
        read_ = old & kSlotMask;
        return true;
    }

private:
    static constexpr uint8_t kSlotMask = 0x03;
    static constexpr uint8_t kFresh = 0x80;

    unsigned read_ = 0;              // affiche (lecteur)
    std::atomic<uint8_t> middle_{1};  // echange
    unsigned write_ = 2;             // libre (ecrivain)
    uint32_t replaced_ = 0;
};

}  // namespace i75w

#endif  // I75W_TRIPLE_HPP
