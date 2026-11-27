// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Machine d'etat de la carte Interstate 75 W (sections 6.2 a 6.4), sans
// materiel : meme comportement que tools/fake_i75w.py.
//
// - Double tampon RGB565 (2 x 8 192 octets, membres de l'objet, aucune
//   allocation). Les donnees recues vont dans le tampon arriere ; echange sur
//   un FRAME, ou sur un DELTA dont la base est l'image affichee, puis recopie
//   de l'image affichee dans le tampon arriere.
// - Numerotation commune FRAME / DELTA, images perdues (B5), DELTA ignores,
//   mode (0 : images du Pi, 1 : animation locale), luminosite.
// - Horloge injectee : chaque appel recoit l'heure en microsecondes
//   (time_us_64() sur la carte, horloge simulee dans les tests). Animation
//   locale apres 3 s sans FRAME ni DELTA valide, TELEMETRY chaque seconde.
// - Reponses (HELLO, PONG, ERROR, TELEMETRY) ecrites dans un ByteSink, trame
//   par trame (port USB sur la carte, memoire dans les tests).
// - STATS (section 6.2) : compteurs de la machine d'etat, plus les champs
//   propres a la carte, fournis par une accroche (38e complement).
//
// Le dessin de l'animation locale et le rafraichissement des dalles ne sont
// pas ici : la carte lit mode() et front().

#ifndef I75W_CARD_HPP
#define I75W_CARD_HPP

#include <cstddef>
#include <cstdint>

#include "decoder.hpp"
#include "proto.hpp"

namespace i75w {

constexpr uint64_t kLocalAnimationDelayUs = 3000000;  // section 6.3
constexpr uint64_t kTelemetryPeriodUs = 1000000;

class ByteSink {
public:
    // Une trame complete de reponse (en-tete, donnees et CRC).
    virtual void write(const uint8_t* data, size_t len) = 0;

protected:
    ~ByteSink() = default;
};

// Appelee a chaque changement de l'image affichee (tampon affiche, front) :
// - a chaque echange, juste apres le changement de tampon affiche et avant la
//   recopie dans le tampon arriere ;
// - sur CLEAR, apres l'effacement des deux tampons (front tout noir ; pas un
//   echange : swaps() ne change pas) [37e complement].
// Sur la carte (section 8, 36e complement) : convertit front dans un tampon
// libre du triple tampon du pilote et le publie au coeur 1, sans jamais
// l'attendre. front ne doit etre lu que pendant l'appel.
using SwapHook = void (*)(void* ctx, const uint16_t* front);

// Champs de STATS que seule la carte connait (section 6.2, 38e complement).
struct BoardStats {
    uint32_t replaced = 0;     // images remplacees avant d'etre affichees (triple tampon)
    uint32_t loop_max_us = 0;  // plus long tour de la boucle du coeur 0 depuis le STATS precedent
    uint16_t refresh_hz = 0;   // cycles complets de rafraichissement pendant la derniere seconde
    uint8_t flags = 0;         // bit 0 : demarrage cause par le chien de garde
};

// Appelee a chaque STATS_REQ, juste avant la reponse : remplit stats (a zero
// a l'appel) ; sur la carte, remet aussi loop_max_us a zero. Sans accroche,
// ces champs valent 0.
using StatsHook = void (*)(void* ctx, BoardStats& stats);

class Card final : private FrameHandler {
public:
    Card();

    // Carte neuve, juste demarree, a l'heure now_us. Garde la version du
    // firmware, les mesures et l'accroche d'echange.
    void boot(uint64_t now_us);

    // Octets recus du Pi. Appelle d'abord tick(now_us).
    void feed(const uint8_t* data, size_t n, uint64_t now_us, ByteSink& out);

    // Delai de 3 s et TELEMETRY ; a appeler souvent, meme sans donnees.
    void tick(uint64_t now_us, ByteSink& out);

    void set_firmware_version(uint8_t major, uint8_t minor, uint8_t patch);
    void set_measures(uint16_t mv, int16_t temp_dc);  // 5 V en mV, temperature x 10
    void set_swap_hook(SwapHook hook, void* ctx);
    void set_stats_hook(StatsHook hook, void* ctx);

    const uint16_t* front() const { return buf_[front_]; }      // tampon affiche
    const uint16_t* back() const { return buf_[front_ ^ 1]; }   // tampon arriere
    bool has_displayed() const { return has_displayed_; }       // false : aucune image du Pi
    uint16_t displayed_number() const { return displayed_no_; }
    uint32_t lost() const { return lost_; }                     // images perdues (B5)
    uint32_t swaps() const { return swaps_; }                   // echanges de tampons
    uint32_t deltas_ignored() const { return deltas_ignored_; }
    uint8_t mode() const { return (forced_local_ || local_animation_) ? kModeLocal : kModePi; }
    bool forced_local() const { return forced_local_; }         // MODE 1 demande par le Pi
    bool local_animation() const { return local_animation_; }   // animation de secours
    bool need_frame() const { return need_frame_; }             // DELTA ignores jusqu'au FRAME
    uint8_t brightness() const { return brightness_; }
    uint32_t frames_rx() const { return frames_rx_; }   // FRAME recus, MODE 1 compris
    uint32_t deltas_rx() const { return deltas_rx_; }   // DELTA recus, valides ou non
    uint32_t bytes_rx() const { return bytes_rx_; }     // octets recus
    const Decoder& decoder() const { return decoder_; }

private:
    void on_frame(uint8_t type, const uint8_t* p, size_t n) override;
    void send(uint8_t type, const uint8_t* payload, size_t n);
    void send_error(uint8_t code, const char* text, uint8_t type);
    void count_gap(uint16_t number);
    void swap(uint16_t number);
    void handle_frame(const uint8_t* p);
    void handle_delta(const uint8_t* p, size_t n);
    void send_stats();
    uint16_t* back_mut() { return buf_[front_ ^ 1]; }

    alignas(4) uint16_t buf_[2][kPixels];
    unsigned front_ = 0;
    Decoder decoder_;

    uint8_t fw_[3] = {0, 0, 0};
    uint16_t mv_ = 0;
    int16_t temp_dc_ = 0;
    SwapHook hook_ = nullptr;
    void* hook_ctx_ = nullptr;
    StatsHook stats_hook_ = nullptr;
    void* stats_ctx_ = nullptr;

    bool has_displayed_ = false;
    uint16_t displayed_no_ = 0;
    bool need_frame_ = true;
    uint8_t brightness_ = 255;
    bool forced_local_ = false;
    bool local_animation_ = false;
    bool has_last_no_ = false;
    uint16_t last_no_ = 0;          // dernier numero recu (FRAME ou DELTA)
    uint64_t last_frame_us_ = 0;    // dernier FRAME ou DELTA valide, ou MODE
    uint64_t next_telemetry_us_ = 0;
    uint32_t swaps_ = 0;
    uint32_t deltas_ignored_ = 0;
    uint32_t lost_ = 0;
    uint32_t seen_crc_errors_ = 0;
    uint32_t swaps_since_telemetry_ = 0;
    uint64_t boot_us_ = 0;
    uint32_t frames_rx_ = 0;
    uint32_t deltas_rx_ = 0;
    uint32_t bytes_rx_ = 0;

    // Contexte de feed(), pour on_frame().
    ByteSink* out_ = nullptr;
    uint64_t now_us_ = 0;

    uint8_t tx_[kHeaderLen + 1 + kErrorTextMax + kCrcLen] = {};
};

}  // namespace i75w

#endif  // I75W_CARD_HPP
