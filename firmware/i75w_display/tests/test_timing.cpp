// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Comportement dans le temps, avec une horloge simulee (specification
// section 6.2 et 6.3, comme tools/fake_i75w.py) : animation locale apres 3 s
// sans FRAME ni DELTA valide, retour par FRAME, MODE 1 et MODE 0, TELEMETRY
// chaque seconde (echanges de tampons, images perdues), accroche d'echange,
// STATS (section 6.2, 38e complement).

#include <memory>
#include <utility>
#include <vector>

#include "card.hpp"
#include "decoder.hpp"
#include "runner.hpp"
#include "util.hpp"

using util::Bytes;

namespace {

constexpr uint64_t kSecond = 1000000;
constexpr uint64_t kT0 = 5 * kSecond + 123;  // heure de demarrage non nulle

using Replies = std::vector<std::pair<uint8_t, Bytes>>;

struct Bench {
    std::unique_ptr<i75w::Card> card{new i75w::Card()};
    uint64_t now = kT0;

    Bench() {
        card->set_measures(5010, -15);
        card->boot(now);
    }

    static Replies decode(const util::MemSink& out) {
        std::unique_ptr<i75w::Decoder> rx(new i75w::Decoder());
        util::Collect got;
        rx->feed(out.data.data(), out.data.size(), got);
        return got.frames;
    }

    Replies feed(const Bytes& b) {
        util::MemSink out;
        card->feed(b.data(), b.size(), now, out);
        return decode(out);
    }

    Replies tick() {
        util::MemSink out;
        card->tick(now, out);
        return decode(out);
    }

    Replies at(uint64_t t) {
        now = t;
        return tick();
    }
};

Bytes frame(uint16_t number, uint8_t fill = 0x11) {
    return util::encode(i75w::kFrame, util::frame_payload(number, Bytes(i75w::kPixelsBytes, fill)));
}

// DELTA sans tuile (masque nul) : valide, l'echange a lieu si la base est bonne.
Bytes delta_empty(uint16_t number, uint16_t base) {
    Bytes p = {static_cast<uint8_t>(number), static_cast<uint8_t>(number >> 8),
               static_cast<uint8_t>(base), static_cast<uint8_t>(base >> 8), 0, 0, 0, 0, 0, 0, 0, 0};
    return util::encode(i75w::kDelta, p);
}

// DELTA au contenu invalide (tuile 0 en mode 2), CRC et longueur corrects.
Bytes delta_invalid(uint16_t number, uint16_t base) {
    Bytes p = {static_cast<uint8_t>(number), static_cast<uint8_t>(number >> 8),
               static_cast<uint8_t>(base), static_cast<uint8_t>(base >> 8), 1, 0, 0, 0, 0, 0, 0, 0,
               2};
    return util::encode(i75w::kDelta, p);
}

struct Telemetry {
    uint16_t mv;
    int16_t temp_dc;
    uint8_t fps;
    uint32_t lost;
};

Telemetry parse_telemetry(const Bytes& p) {
    return {i75w::get_u16(&p[0]), static_cast<int16_t>(i75w::get_u16(&p[2])), p[4],
            i75w::get_u32(&p[5])};
}

}  // namespace

TEST(telemetry_every_second) {
    Bench b;
    CHECK(b.at(kT0 + kSecond - 1).empty());
    Replies r = b.at(kT0 + kSecond);
    CHECK_EQ(r.size(), size_t(1));
    CHECK_EQ(int(r[0].first), int(i75w::kTelemetry));
    CHECK_EQ(r[0].second.size(), i75w::kTelemetryLen);
    Telemetry t = parse_telemetry(r[0].second);
    CHECK_EQ(t.mv, uint16_t(5010));
    CHECK_EQ(t.temp_dc, int16_t(-15));
    CHECK_EQ(int(t.fps), 0);
    CHECK_EQ(t.lost, 0u);
    // Une seule par seconde ; la suivante une seconde apres l'emission (en retard ici).
    CHECK(b.at(kT0 + kSecond + 10).empty());
    CHECK_EQ(b.at(kT0 + 2 * kSecond + 500000).size(), size_t(1));
    CHECK(b.at(kT0 + 3 * kSecond + 499999).empty());
    CHECK_EQ(b.at(kT0 + 3 * kSecond + 500000).size(), size_t(1));
}

TEST(telemetry_counts_swaps_and_lost) {
    Bench b;
    b.feed(frame(1));
    b.feed(delta_empty(2, 1));
    b.feed(frame(5));                 // trou : 3 et 4 perdues
    b.feed(delta_empty(9, 1));        // mauvaise base : ignore (1) et trou (3)
    b.now = kT0 + kSecond;
    Replies r = b.feed(util::encode(i75w::kHelloReq));
    CHECK_EQ(r.size(), size_t(2));
    CHECK_EQ(int(r[0].first), int(i75w::kTelemetry));  // TELEMETRY avant les reponses
    CHECK_EQ(int(r[1].first), int(i75w::kHello));
    Telemetry t = parse_telemetry(r[0].second);
    CHECK_EQ(int(t.fps), 3);
    CHECK_EQ(t.lost, 6u);
    CHECK_EQ(b.card->lost(), 6u);
    // Seconde suivante : compteur d'echanges remis a zero, images perdues cumulees.
    b.feed(frame(10));
    t = parse_telemetry(b.at(kT0 + 2 * kSecond).at(0).second);
    CHECK_EQ(int(t.fps), 1);
    CHECK_EQ(t.lost, 6u);
}

TEST(telemetry_fps_saturates) {
    Bench b;
    for (int k = 0; k < 300; ++k) {
        b.feed(frame(static_cast<uint16_t>(k + 1)));
    }
    CHECK_EQ(b.card->swaps(), 300u);
    Telemetry t = parse_telemetry(b.at(kT0 + kSecond).at(0).second);
    CHECK_EQ(int(t.fps), 255);
    CHECK_EQ(t.lost, 0u);
}

TEST(hello_carries_version_and_voltage) {
    Bench b;
    b.card->set_firmware_version(0, 1, 0);
    Replies r = b.feed(util::encode(i75w::kHelloReq));
    CHECK_EQ(r.size(), size_t(1));
    const Bytes& p = r[0].second;
    CHECK_EQ(p.size(), i75w::kHelloLen);
    CHECK_EQ(int(p[0]), 1);
    CHECK_EQ(i75w::get_u16(&p[1]), uint16_t(128));
    CHECK_EQ(i75w::get_u16(&p[3]), uint16_t(32));
    CHECK_EQ(int(p[5]), 0);
    CHECK_EQ(int(p[6]), 1);
    CHECK_EQ(int(p[7]), 0);
    CHECK_EQ(i75w::get_u16(&p[8]), uint16_t(5010));
}

TEST(local_animation_after_3s_from_boot) {
    Bench b;
    b.at(kT0 + 3 * kSecond - 1);
    CHECK_EQ(int(b.card->mode()), 0);
    b.at(kT0 + 3 * kSecond);
    CHECK_EQ(int(b.card->mode()), 1);
    CHECK(b.card->local_animation());
    CHECK(!b.card->forced_local());
    CHECK(b.card->need_frame());
}

TEST(local_animation_after_3s_without_frame) {
    Bench b;
    b.now = kT0 + 10 * kSecond;
    b.feed(frame(1));
    CHECK_EQ(int(b.card->mode()), 0);
    b.at(kT0 + 13 * kSecond - 1);
    CHECK_EQ(int(b.card->mode()), 0);
    b.at(kT0 + 13 * kSecond);
    CHECK_EQ(int(b.card->mode()), 1);
    CHECK(b.card->need_frame());
    // Un DELTA de bonne base ne fait pas sortir de l'animation : ignore et perdu.
    b.feed(delta_empty(2, 1));
    CHECK_EQ(int(b.card->mode()), 1);
    CHECK_EQ(b.card->deltas_ignored(), 1u);
    CHECK_EQ(b.card->lost(), 1u);
    CHECK_EQ(b.card->swaps(), 1u);
    // Le FRAME suivant repasse en mode 0.
    b.feed(frame(3));
    CHECK_EQ(int(b.card->mode()), 0);
    CHECK(!b.card->need_frame());
    CHECK_EQ(b.card->swaps(), 2u);
    CHECK_EQ(b.card->lost(), 1u);
}

TEST(other_types_do_not_reset_delay) {
    Bench b;
    b.feed(frame(1));
    b.now = kT0 + 2 * kSecond;
    b.feed(util::encode(i75w::kPing, Bytes{1}));
    b.feed(util::encode(i75w::kBrightness, Bytes{9}));
    b.feed(util::encode(i75w::kHelloReq));
    b.feed(util::encode(i75w::kClear));
    b.feed(util::encode(0x42, Bytes{1, 2}));
    Bytes bad = frame(2);
    bad.back() ^= 0xFF;  // CRC faux : ne compte pas
    b.feed(bad);
    b.at(kT0 + 3 * kSecond);
    CHECK_EQ(int(b.card->mode()), 1);
}

TEST(valid_delta_resets_delay_even_if_ignored) {
    // DELTA de bonne base.
    Bench a;
    a.feed(frame(1));
    a.now = kT0 + 2 * kSecond;
    a.feed(delta_empty(2, 1));
    a.at(kT0 + 5 * kSecond - 1);
    CHECK_EQ(int(a.card->mode()), 0);
    a.at(kT0 + 5 * kSecond);
    CHECK_EQ(int(a.card->mode()), 1);

    // DELTA de mauvaise base : ignore, mais le Pi est toujours la.
    Bench b;
    b.feed(frame(1));
    b.now = kT0 + 2 * kSecond;
    b.feed(delta_empty(2, 7));
    CHECK_EQ(b.card->deltas_ignored(), 1u);
    b.at(kT0 + 4 * kSecond);
    CHECK_EQ(int(b.card->mode()), 0);
    b.at(kT0 + 5 * kSecond);
    CHECK_EQ(int(b.card->mode()), 1);

    // DELTA au contenu invalide : ERROR 0x04, delai remis a zero quand meme.
    Bench c;
    c.feed(frame(1));
    c.now = kT0 + 2 * kSecond;
    Replies r = c.feed(delta_invalid(2, 1));
    CHECK_EQ(r.size(), size_t(2));  // TELEMETRY due depuis 1 s, puis ERROR
    CHECK_EQ(int(r.at(0).first), int(i75w::kTelemetry));
    CHECK_EQ(int(r.at(1).first), int(i75w::kError));
    CHECK_EQ(int(r.at(1).second.at(0)), int(i75w::kErrDelta));
    c.at(kT0 + 4 * kSecond);
    CHECK_EQ(int(c.card->mode()), 0);
    c.at(kT0 + 5 * kSecond);
    CHECK_EQ(int(c.card->mode()), 1);
}

TEST(mode_1_has_no_delay_and_mode_0_restarts_it) {
    Bench b;
    b.feed(frame(1));
    b.feed(util::encode(i75w::kMode, Bytes{1}));
    CHECK(b.card->forced_local());
    b.at(kT0 + 60 * kSecond);
    CHECK_EQ(int(b.card->mode()), 1);
    CHECK(b.card->forced_local());
    CHECK(!b.card->local_animation() || b.card->forced_local());
    // FRAME et DELTA ignores sans compter comme perdus.
    b.feed(frame(2));
    b.feed(delta_empty(3, 2));
    CHECK_EQ(b.card->swaps(), 1u);
    CHECK_EQ(b.card->lost(), 0u);
    CHECK_EQ(b.card->deltas_ignored(), 0u);
    // MODE 0 : delai repris a l'heure du MODE 0 ; DELTA ignores jusqu'au FRAME.
    b.now = kT0 + 61 * kSecond;
    b.feed(util::encode(i75w::kMode, Bytes{0}));
    CHECK_EQ(int(b.card->mode()), 0);
    CHECK(b.card->need_frame());
    b.at(kT0 + 64 * kSecond - 1);
    CHECK_EQ(int(b.card->mode()), 0);
    b.at(kT0 + 64 * kSecond);
    CHECK_EQ(int(b.card->mode()), 1);
    CHECK(!b.card->forced_local());
}

namespace {

struct HookLog {
    std::vector<const uint16_t*> fronts;
    std::vector<uint16_t> first_pixel_of_back;  // tampon arriere vu pendant l'accroche
    const i75w::Card* card = nullptr;
};

void hook(void* ctx, const uint16_t* front) {
    HookLog* log = static_cast<HookLog*>(ctx);
    log->fronts.push_back(front);
    log->first_pixel_of_back.push_back(log->card->back()[0]);
}

}  // namespace

TEST(swap_hook_before_copy) {
    Bench b;
    HookLog log;
    log.card = b.card.get();
    b.card->set_swap_hook(hook, &log);
    b.card->boot(b.now);  // l'accroche reste apres boot()
    b.feed(frame(1, 0x22));
    b.feed(frame(2, 0x33));
    b.feed(delta_empty(3, 2));
    CHECK_EQ(log.fronts.size(), size_t(3));
    CHECK(log.fronts[0] != log.fronts[1]);
    CHECK(log.fronts[0] == log.fronts[2]);
    CHECK(log.fronts.back() == b.card->front());
    // Pendant l'accroche, l'ancien tampon affiche n'est pas encore recopie.
    CHECK_EQ(log.first_pixel_of_back[0], uint16_t(0x0000));
    CHECK_EQ(log.first_pixel_of_back[1], uint16_t(0x2222));
    // Apres l'echange, les deux tampons sont egaux.
    CHECK_EQ(b.card->back()[0], uint16_t(0x3333));
    CHECK_EQ(b.card->front()[4095], uint16_t(0x3333));
}

TEST(swap_hook_on_clear_without_swap) {
    // CLEAR : les dalles passent au noir (37e complement), sans echange.
    Bench b;
    HookLog log;
    log.card = b.card.get();
    b.card->set_swap_hook(hook, &log);
    b.feed(frame(1, 0x44));
    CHECK_EQ(log.fronts.size(), size_t(1));
    b.feed(util::encode(i75w::kClear));
    CHECK_EQ(log.fronts.size(), size_t(2));
    CHECK(log.fronts[1] == b.card->front());
    CHECK_EQ(log.first_pixel_of_back[1], uint16_t(0x0000));  // deja efface pendant l'accroche
    CHECK_EQ(b.card->front()[0], uint16_t(0x0000));
    CHECK_EQ(b.card->front()[4095], uint16_t(0x0000));
    CHECK_EQ(b.card->swaps(), 1u);
    CHECK_EQ(b.card->displayed_number(), uint16_t(1));
    CHECK(b.card->need_frame());
}

TEST(lost_counter_from_crc_errors) {
    Bench b;
    b.feed(frame(1));
    Bytes bad = frame(2);
    bad[100] ^= 1;
    b.feed(bad);
    CHECK_EQ(b.card->lost(), 1u);
    b.feed(frame(3));  // trou : 2 perdue encore une fois (indicateur, pas un compte exact)
    CHECK_EQ(b.card->lost(), 2u);
    CHECK_EQ(b.card->decoder().crc_errors(), 1u);
}

// ------------------------------------------------------------------ STATS

namespace {

struct StatsCtx {
    int calls = 0;
};

void fill_board(void* ctx, i75w::BoardStats& st) {
    StatsCtx* c = static_cast<StatsCtx*>(ctx);
    ++c->calls;
    st.replaced = 0x01020304;
    st.loop_max_us = 0x0A0B0C0D;
    st.refresh_hz = 0x1234;
    st.flags = i75w::kStatsFlagWatchdog;
}

const Bytes* find(const Replies& r, uint8_t type) {
    for (const auto& f : r) {
        if (f.first == type) {
            return &f.second;
        }
    }
    return nullptr;
}

}  // namespace

TEST(stats_fields_and_byte_order) {
    Bench b;
    StatsCtx ctx;
    b.card->set_stats_hook(fill_board, &ctx);
    Bytes sent = frame(1);
    Bytes d = delta_empty(2, 1);
    sent.insert(sent.end(), d.begin(), d.end());
    Bytes inv = delta_invalid(3, 2);
    sent.insert(sent.end(), inv.begin(), inv.end());
    b.now = kT0 + 1234567;
    Bytes junk = {0x00, 0x01, 0x02};
    sent.insert(sent.begin(), junk.begin(), junk.end());
    b.feed(sent);
    Bytes req = util::encode(i75w::kStatsReq);
    Replies r = b.feed(req);
    const Bytes* p = find(r, i75w::kStats);
    CHECK(p != nullptr);
    if (p == nullptr) {
        return;
    }
    CHECK_EQ(p->size(), size_t(47));
    CHECK_EQ(ctx.calls, 1);
    CHECK_EQ(i75w::get_u32(&(*p)[0]), 1234u);                        // uptime_ms
    CHECK_EQ(i75w::get_u32(&(*p)[4]), 1u);                           // frames_rx
    CHECK_EQ(i75w::get_u32(&(*p)[8]), 2u);                           // deltas_rx
    CHECK_EQ(i75w::get_u32(&(*p)[12]), uint32_t(sent.size() + req.size()));  // bytes_rx
    CHECK_EQ(i75w::get_u32(&(*p)[16]), 3u);                          // skipped
    CHECK_EQ(i75w::get_u32(&(*p)[20]), 0u);                          // crc_errors
    CHECK_EQ(i75w::get_u32(&(*p)[24]), 0u);                          // length_errors
    CHECK_EQ(i75w::get_u32(&(*p)[28]), 1u);                          // deltas_ignored
    CHECK_EQ(i75w::get_u32(&(*p)[32]), 2u);                          // swaps
    // Champs de la carte, petit-boutistes.
    const uint8_t board[] = {0x04, 0x03, 0x02, 0x01, 0x0D, 0x0C, 0x0B, 0x0A, 0x34, 0x12, 0x01};
    for (size_t i = 0; i < sizeof(board); ++i) {
        CHECK_EQ(int((*p)[36 + i]), int(board[i]));
    }
}

TEST(stats_without_hook_and_in_mode_1) {
    Bench b;
    Bytes m = util::encode(i75w::kMode, Bytes{1});
    Bytes f = frame(1);
    m.insert(m.end(), f.begin(), f.end());
    b.feed(m);
    Replies r = b.feed(util::encode(i75w::kStatsReq));
    CHECK_EQ(r.size(), size_t(1));
    const Bytes* p = find(r, i75w::kStats);
    CHECK(p != nullptr);
    if (p == nullptr) {
        return;
    }
    CHECK_EQ(i75w::get_u32(&(*p)[4]), 1u);  // FRAME recu pendant MODE 1 compte
    CHECK_EQ(i75w::get_u32(&(*p)[32]), 0u);
    for (size_t i = 36; i < 47; ++i) {
        CHECK_EQ(int((*p)[i]), 0);
    }
}

TEST(stats_does_not_reset_delay_and_follows_telemetry) {
    Bench b;
    b.feed(frame(1));
    b.now = kT0 + kSecond;  // TELEMETRY due
    Replies r = b.feed(util::encode(i75w::kStatsReq));
    CHECK_EQ(r.size(), size_t(2));
    if (r.size() == 2) {
        CHECK_EQ(int(r[0].first), int(i75w::kTelemetry));  // 35e complement
        CHECK_EQ(int(r[1].first), int(i75w::kStats));
    }
    b.now = kT0 + 2 * kSecond + 999999;
    b.feed(util::encode(i75w::kStatsReq));
    b.at(kT0 + 3 * kSecond);
    CHECK_EQ(int(b.card->mode()), int(i75w::kModeLocal));
}

TEST(stats_bad_length_and_wrong_direction) {
    Bench b;
    // Donnees en trop : au-dela du maximum du type, rejetee par le recepteur, sans reponse.
    CHECK(b.feed(util::encode(i75w::kStatsReq, Bytes{0})).empty());
    CHECK_EQ(b.card->decoder().length_errors(), 1u);
    Replies r = b.feed(util::encode(i75w::kStats, Bytes(47, 0)));
    CHECK_EQ(r.size(), size_t(1));
    if (r.size() == 1) {
        CHECK_EQ(int(r[0].first), int(i75w::kError));
        CHECK_EQ(int(r[0].second.at(0)), int(i75w::kErrUnknownType));
    }
}

TEST(stats_codes_and_lengths) {
    static_assert(i75w::kStatsLen == 11 * 4 + 2 + 1, "STATS : 47 octets");
    size_t min = 0;
    size_t max = 0;
    CHECK(i75w::length_range(i75w::kStatsReq, min, max));
    CHECK_EQ(max, size_t(0));
    CHECK(i75w::length_range(i75w::kStats, min, max));
    CHECK_EQ(min, size_t(47));
    CHECK_EQ(int(i75w::kStats), int(i75w::kStatsReq | 0x80));
}
