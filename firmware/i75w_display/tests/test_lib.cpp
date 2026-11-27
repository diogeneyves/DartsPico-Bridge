// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Bibliotheque contre vectors/dmd_lib.txt (specification section 8) : CRC-32,
// recepteur (trames rendues et compteurs exacts de Decoder), DELTA.
// Utilisation : test_lib <chemin de dmd_lib.txt>

#include <cinttypes>
#include <cstdio>
#include <map>
#include <memory>
#include <string>
#include <vector>

#include "crc32.hpp"
#include "decoder.hpp"
#include "delta.hpp"
#include "runner.hpp"
#include "util.hpp"

using util::Bytes;
using util::Record;

namespace {

const std::vector<Record>& records() {
    static std::vector<Record> r = [] {
        const char* path = runner::arg(1);
        if (path == nullptr) {
            std::printf("usage : test_lib <dmd_lib.txt>\n");
            return std::vector<Record>();
        }
        return util::read_records(path);
    }();
    return r;
}

std::string where(const Record& r, const std::string& name) {
    return "dmd_lib.txt:" + std::to_string(r.line) + " (" + name + ")";
}

std::string frame_line(uint8_t type, const Bytes& p) {
    return util::hex2(type) + " " + std::to_string(p.size()) + " " +
           util::crc_hex(p.data(), p.size());
}

std::string counters_line(const i75w::Decoder& d) {
    return std::to_string(d.crc_errors()) + " " + std::to_string(d.length_errors()) + " " +
           std::to_string(d.skipped()) + " " + std::to_string(d.frames()) + " " +
           std::to_string(d.pending());
}

std::string join(const std::vector<std::string>& f) {
    std::string s;
    for (size_t i = 0; i < f.size(); ++i) {
        s += (i ? " " : "") + f[i];
    }
    return s;
}

}  // namespace

TEST(file_loaded) { CHECK(records().size() > 50); }

TEST(crc_vectors) {
    int n = 0;
    for (const auto& r : records()) {
        if (r.key != "crc") {
            continue;
        }
        Bytes b = util::unhex(r.fields.at(0));
        CHECK_EQ_CTX(util::crc_hex(b.data(), b.size()), r.fields.at(1), where(r, "crc"));
        ++n;
    }
    CHECK(n >= 10);
    const uint8_t check[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
    CHECK_EQ(i75w::crc32(check, sizeof(check)), 0xCBF43926u);
}

TEST(crc_continuation) {
    const uint8_t a[] = {'1', '2', '3', '4'};
    const uint8_t b[] = {'5', '6', '7', '8', '9'};
    CHECK_EQ(i75w::crc32(b, sizeof(b), i75w::crc32(a, sizeof(a))), 0xCBF43926u);
    CHECK_EQ(i75w::crc32(nullptr, 0), 0u);
}

namespace {

struct DecoderCase {
    std::string name;
    std::vector<Bytes> feeds;
    std::vector<std::string> frames;  // toutes les trames du cas, dans l'ordre
    std::string counters;
};

// Rejoue les cas "decoder" : trames apres chaque "feed", puis compteurs.
std::vector<DecoderCase> replay_decoder_cases() {
    std::vector<DecoderCase> cases;
    std::unique_ptr<i75w::Decoder> dec;
    util::Collect got;
    size_t next = 0;  // prochaine trame attendue dans got.frames
    const Record* last_feed = nullptr;
    auto check_feed_done = [&](const Record* at) {
        if (dec && last_feed != nullptr) {
            CHECK_EQ_CTX(next, got.frames.size(),
                         where(*at, cases.back().name) + " trames en trop apres le feed ligne " +
                             std::to_string(last_feed->line));
        }
    };
    for (const auto& r : records()) {
        if (r.key == "decoder") {
            dec.reset(new i75w::Decoder());
            got.frames.clear();
            next = 0;
            last_feed = nullptr;
            cases.push_back({r.fields.at(0), {}, {}, ""});
        } else if (!dec) {
            continue;
        } else if (r.key == "feed") {
            check_feed_done(&r);
            Bytes b = util::unhex(r.fields.at(0));
            cases.back().feeds.push_back(b);
            dec->feed(b.data(), b.size(), got);
            last_feed = &r;
        } else if (r.key == "frame") {
            std::string want = join(r.fields);
            cases.back().frames.push_back(want);
            if (next < got.frames.size()) {
                CHECK_EQ_CTX(frame_line(got.frames[next].first, got.frames[next].second), want,
                             where(r, cases.back().name));
            } else {
                CHECK_EQ_CTX(std::string("aucune trame"), want, where(r, cases.back().name));
            }
            ++next;
        } else if (r.key == "counters") {
            check_feed_done(&r);
            cases.back().counters = join(r.fields);
            CHECK_EQ_CTX(counters_line(*dec), cases.back().counters, where(r, cases.back().name));
            dec.reset();
        } else {
            dec.reset();
        }
    }
    return cases;
}

}  // namespace

TEST(decoder_vectors) {
    std::vector<DecoderCase> cases = replay_decoder_cases();
    CHECK(cases.size() >= 20);
    for (const char* required : {"byte_by_byte", "split_frame", "sync_in_pixels",
                                 "trailing_aa_pending", "crc_error_in_pixels",
                                 "length_over_type_max", "false_header_plausible", "delta_8194",
                                 "delta_8195", "partial_at_end"}) {
        bool found = false;
        for (const auto& c : cases) {
            found = found || c.name == required;
        }
        CHECK_EQ_CTX(found, true, std::string("cas absent : ") + required);
    }
}

// Le resultat ne doit pas dependre du decoupage des octets entre les appels.
TEST(decoder_chunking) {
    std::vector<DecoderCase> cases = replay_decoder_cases();
    for (const auto& c : cases) {
        Bytes all;
        for (const auto& f : c.feeds) {
            all.insert(all.end(), f.begin(), f.end());
        }
        for (size_t chunk : {size_t(1), size_t(2), size_t(5), size_t(64), size_t(1000),
                             size_t(8203), size_t(8204), all.size() + 1}) {
            std::unique_ptr<i75w::Decoder> dec(new i75w::Decoder());
            util::Collect got;
            for (size_t k = 0; k < all.size(); k += chunk) {
                size_t m = all.size() - k < chunk ? all.size() - k : chunk;
                dec->feed(all.data() + k, m, got);
            }
            std::vector<std::string> lines;
            for (const auto& f : got.frames) {
                lines.push_back(frame_line(f.first, f.second));
            }
            std::string ctx = c.name + " par morceaux de " + std::to_string(chunk);
            CHECK_EQ_CTX(join(lines), join(c.frames), ctx);
            CHECK_EQ_CTX(counters_line(*dec), c.counters, ctx);
        }
    }
}

TEST(decoder_reset) {
    std::unique_ptr<i75w::Decoder> dec(new i75w::Decoder());
    util::Collect got;
    Bytes f = util::encode(i75w::kPing, Bytes{1, 2, 3});
    dec->feed(f.data(), 4, got);
    CHECK_EQ(dec->pending(), size_t(4));
    dec->reset();
    CHECK_EQ(dec->pending(), size_t(0));
    CHECK_EQ(dec->skipped(), 4u);
    dec->feed(f.data(), f.size(), got);
    CHECK_EQ(got.frames.size(), size_t(1));
    dec->clear();
    CHECK_EQ(dec->skipped(), 0u);
    CHECK_EQ(dec->frames(), 0u);
}

TEST(delta_vectors) {
    std::map<std::string, Bytes> images;
    std::string name;
    std::string got;
    int ok = 0;
    int errors = 0;
    for (const auto& r : records()) {
        if (r.key == "image") {
            images[r.fields.at(0)] = util::unhex(r.fields.at(1));
            CHECK_EQ_CTX(images[r.fields.at(0)].size(), i75w::kPixelsBytes, where(r, "image"));
        } else if (r.key == "delta") {
            name = r.fields.at(0);
            Bytes p = util::unhex(r.fields.at(2));
            Bytes img = images.at(r.fields.at(1));
            i75w::DeltaHeader h{};
            uint8_t err = i75w::delta_apply(p.data(), p.size(), img.data());
            if (err == i75w::kOk) {
                CHECK_EQ_CTX(int(i75w::delta_header(p.data(), p.size(), h)), int(i75w::kOk),
                             where(r, name));
                char mask[24];
                std::snprintf(mask, sizeof(mask), "%016" PRIx64, h.mask);
                got = "ok " + std::to_string(h.number) + " " + std::to_string(h.base) + " " +
                      mask + " " + util::crc_hex(img.data(), img.size());
            } else {
                got = "error " + util::hex2(err);
            }
        } else if (r.key == "result") {
            CHECK_EQ_CTX(got, join(r.fields), where(r, name));
            (r.fields.at(0) == "ok" ? ok : errors) += 1;
            got.clear();
        }
    }
    CHECK(ok >= 10);
    CHECK(errors >= 12);
}

TEST(delta_header_lengths) {
    Bytes p(i75w::kMaxPayload + 1, 0);
    i75w::DeltaHeader h{};
    CHECK_EQ(int(i75w::delta_header(p.data(), 11, h)), int(i75w::kErrLength));
    CHECK_EQ(int(i75w::delta_header(p.data(), 12, h)), int(i75w::kOk));
    CHECK_EQ(int(i75w::delta_header(p.data(), i75w::kMaxPayload, h)), int(i75w::kOk));
    CHECK_EQ(int(i75w::delta_header(p.data(), i75w::kMaxPayload + 1, h)), int(i75w::kErrLength));
    Bytes q = {0x34, 0x12, 0x33, 0x12, 1, 2, 3, 4, 5, 6, 7, 0x80};
    CHECK_EQ(int(i75w::delta_header(q.data(), q.size(), h)), int(i75w::kOk));
    CHECK_EQ(h.number, uint16_t(0x1234));
    CHECK_EQ(h.base, uint16_t(0x1233));
    CHECK_EQ(h.mask, uint64_t(0x8007060504030201ull));
}
