// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Machine d'etat de la carte contre vectors/dmd_card.txt (specification
// section 8) : scenarios rejoues sans horloge (heure figee a 0, donc ni
// animation locale ni TELEMETRY). Apres chaque "feed" : reponses
// (HELLO : protocole, largeur, hauteur ; PONG : octets ; ERROR : code seul ;
// STATS : longueur et octets 4 a 35),
// puis etat complet de la carte.
// Utilisation : test_card <chemin de dmd_card.txt>

#include <cstdio>
#include <deque>
#include <memory>
#include <set>
#include <string>
#include <vector>

#include "card.hpp"
#include "decoder.hpp"
#include "runner.hpp"
#include "util.hpp"

using util::Bytes;
using util::Record;

namespace {

const std::vector<Record>& records() {
    static std::vector<Record> r = [] {
        const char* path = runner::arg(1);
        if (path == nullptr) {
            std::printf("usage : test_card <dmd_card.txt>\n");
            return std::vector<Record>();
        }
        return util::read_records(path);
    }();
    return r;
}

std::string join(const std::vector<std::string>& f) {
    std::string s;
    for (size_t i = 0; i < f.size(); ++i) {
        s += (i ? " " : "") + f[i];
    }
    return s;
}

std::string reply_line(uint8_t type, const Bytes& p) {
    if (type == i75w::kError && !p.empty()) {
        return "e0 " + util::hex2(p[0]);
    }
    if (type == i75w::kHello && p.size() == i75w::kHelloLen) {
        return "81 " + std::to_string(p[0]) + " " + std::to_string(i75w::get_u16(&p[1])) + " " +
               std::to_string(i75w::get_u16(&p[3]));
    }
    if (type == i75w::kStats) {
        // Longueur et octets 4 a 35 (frames_rx a swaps), 38e complement.
        return "88 " + std::to_string(p.size()) + " " +
               (p.size() >= 36 ? util::hex(p.data() + 4, 32) : std::string("?"));
    }
    return util::hex2(type) + " " + util::hex(p.data(), p.size());
}

std::string state_line(const i75w::Card& c) {
    std::string no = c.has_displayed() ? std::to_string(c.displayed_number()) : "-";
    return util::crc_hex(c.front(), i75w::kPixelsBytes) + " " +
           util::crc_hex(c.back(), i75w::kPixelsBytes) + " " + no + " " +
           std::to_string(c.lost()) + " " + std::to_string(c.swaps()) + " " +
           std::to_string(c.deltas_ignored()) + " " + std::to_string(c.mode()) + " " +
           (c.need_frame() ? "1" : "0") + " " + std::to_string(c.brightness());
}

}  // namespace

TEST(card_scenarios) {
    std::unique_ptr<i75w::Card> card;
    std::unique_ptr<i75w::Decoder> rx;
    std::deque<std::string> replies;
    std::vector<std::string> names;
    std::set<std::string> error_codes;
    int states = 0;
    for (const auto& r : records()) {
        std::string ctx = "dmd_card.txt:" + std::to_string(r.line) + " (" +
                          (names.empty() ? std::string("?") : names.back()) + ")";
        if (r.key == "scenario") {
            names.push_back(r.fields.at(0));
            card.reset(new i75w::Card());
            card->boot(0);
            rx.reset(new i75w::Decoder());
            replies.clear();
        } else if (r.key == "feed") {
            CHECK_EQ_CTX(replies.size(), size_t(0), ctx + " reponses non lues");
            replies.clear();
            Bytes b = util::unhex(r.fields.at(0));
            util::MemSink out;
            card->feed(b.data(), b.size(), 0, out);
            util::Collect got;
            rx->feed(out.data.data(), out.data.size(), got);
            CHECK_EQ_CTX(rx->pending(), size_t(0), ctx + " reponse incomplete");
            for (const auto& f : got.frames) {
                replies.push_back(reply_line(f.first, f.second));
            }
        } else if (r.key == "reply") {
            std::string want = join(r.fields);
            if (replies.empty()) {
                CHECK_EQ_CTX(std::string("aucune reponse"), want, ctx);
            } else {
                CHECK_EQ_CTX(replies.front(), want, ctx);
                replies.pop_front();
            }
            if (r.fields.at(0) == "e0") {
                error_codes.insert(r.fields.at(1));
            }
        } else if (r.key == "state") {
            CHECK_EQ_CTX(join(std::vector<std::string>(replies.begin(), replies.end())),
                         std::string(), ctx + " reponses en trop");
            replies.clear();
            CHECK_EQ_CTX(state_line(*card), join(r.fields), ctx);
            ++states;
        } else {
            CHECK_EQ_CTX(r.key, std::string("scenario|feed|reply|state"), ctx);
        }
    }
    CHECK(names.size() >= 17);
    CHECK(states >= 60);
    CHECK_EQ(error_codes.size(), size_t(4));  // 01, 02, 03, 04
}
