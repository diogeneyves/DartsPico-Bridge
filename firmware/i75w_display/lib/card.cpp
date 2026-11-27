// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).

#include "card.hpp"

#include <cstring>

#include "crc32.hpp"
#include "delta.hpp"

namespace i75w {

namespace {

bool received_type(uint8_t type) {
    switch (type) {
        case kHelloReq:
        case kFrame:
        case kBrightness:
        case kClear:
        case kMode:
        case kPing:
        case kDelta:
        case kStatsReq:
            return true;
        default:
            return false;
    }
}

}  // namespace

Card::Card() : buf_() { boot(0); }

void Card::boot(uint64_t now_us) {
    std::memset(buf_, 0, sizeof(buf_));
    front_ = 0;
    decoder_.clear();  // pas de Decoder temporaire : 8 ko sur la pile
    has_displayed_ = false;
    displayed_no_ = 0;
    need_frame_ = true;
    brightness_ = 255;
    forced_local_ = false;
    local_animation_ = false;
    has_last_no_ = false;
    last_no_ = 0;
    last_frame_us_ = now_us;
    next_telemetry_us_ = now_us + kTelemetryPeriodUs;
    swaps_ = 0;
    deltas_ignored_ = 0;
    lost_ = 0;
    seen_crc_errors_ = 0;
    swaps_since_telemetry_ = 0;
    boot_us_ = now_us;
    frames_rx_ = 0;
    deltas_rx_ = 0;
    bytes_rx_ = 0;
}

void Card::set_firmware_version(uint8_t major, uint8_t minor, uint8_t patch) {
    fw_[0] = major;
    fw_[1] = minor;
    fw_[2] = patch;
}

void Card::set_measures(uint16_t mv, int16_t temp_dc) {
    mv_ = mv;
    temp_dc_ = temp_dc;
}

void Card::set_swap_hook(SwapHook hook, void* ctx) {
    hook_ = hook;
    hook_ctx_ = ctx;
}

void Card::set_stats_hook(StatsHook hook, void* ctx) {
    stats_hook_ = hook;
    stats_ctx_ = ctx;
}

// ------------------------------------------------------------------ temps

void Card::tick(uint64_t now_us, ByteSink& out) {
    if (!forced_local_ && !local_animation_ && now_us >= last_frame_us_ &&
        now_us - last_frame_us_ >= kLocalAnimationDelayUs) {
        local_animation_ = true;
        need_frame_ = true;  // section 6.3 : seul un FRAME en sort
    }
    if (now_us >= next_telemetry_us_) {
        uint8_t p[kTelemetryLen];
        put_u16(p, mv_);
        put_u16(p + 2, static_cast<uint16_t>(temp_dc_));
        p[4] = static_cast<uint8_t>(swaps_since_telemetry_ > 255 ? 255 : swaps_since_telemetry_);
        put_u32(p + 5, lost_);
        swaps_since_telemetry_ = 0;
        next_telemetry_us_ = now_us + kTelemetryPeriodUs;
        ByteSink* saved = out_;
        out_ = &out;
        send(kTelemetry, p, sizeof(p));
        out_ = saved;
    }
}

// ------------------------------------------------------------- reception

void Card::feed(const uint8_t* data, size_t n, uint64_t now_us, ByteSink& out) {
    tick(now_us, out);
    bytes_rx_ += static_cast<uint32_t>(n);  // boucle (section 6.2)
    out_ = &out;
    now_us_ = now_us;
    decoder_.feed(data, n, *this);
    out_ = nullptr;
    const uint32_t crc = decoder_.crc_errors();
    lost_ += crc - seen_crc_errors_;  // trames rejetees pour CRC (B5)
    seen_crc_errors_ = crc;
}

void Card::send(uint8_t type, const uint8_t* payload, size_t n) {
    tx_[0] = kSync0;
    tx_[1] = kSync1;
    tx_[2] = type;
    put_u16(tx_ + 3, static_cast<uint16_t>(n));
    if (n > 0) {
        std::memcpy(tx_ + kHeaderLen, payload, n);
    }
    put_u32(tx_ + kHeaderLen + n, crc32(tx_ + 2, 3 + n));
    out_->write(tx_, kHeaderLen + n + kCrcLen);
}

void Card::send_error(uint8_t code, const char* text, uint8_t type) {
    static const char kHex[] = "0123456789abcdef";
    uint8_t p[1 + kErrorTextMax];
    size_t n = 0;
    p[n++] = code;
    while (*text != '\0' && n < 1 + kErrorTextMax - 5) {
        p[n++] = static_cast<uint8_t>(*text++);
    }
    // Suffixe " 0xNN" : type de la trame refusee.
    p[n++] = ' ';
    p[n++] = '0';
    p[n++] = 'x';
    p[n++] = static_cast<uint8_t>(kHex[type >> 4]);
    p[n++] = static_cast<uint8_t>(kHex[type & 0x0F]);
    send(kError, p, n);
}

void Card::on_frame(uint8_t type, const uint8_t* p, size_t n) {
    if (!received_type(type)) {
        send_error(kErrUnknownType, "type inconnu", type);
        return;
    }
    size_t min = 0;
    size_t max = 0;
    length_range(type, min, max);
    if (n < min || n > max) {
        send_error(kErrLength, "longueur invalide", type);
        return;
    }
    switch (type) {
        case kHelloReq: {
            uint8_t h[kHelloLen];
            h[0] = kProtoVersion;
            put_u16(h + 1, kWidth);
            put_u16(h + 3, kHeight);
            h[5] = fw_[0];
            h[6] = fw_[1];
            h[7] = fw_[2];
            put_u16(h + 8, mv_);
            send(kHello, h, sizeof(h));
            return;
        }
        case kPing:
            send(kPong, p, n);
            return;
        case kBrightness:
            brightness_ = p[0];
            return;
        case kClear:
            // Les deux tampons ; pas une image, numerotation inchangee (section 6.4).
            std::memset(buf_, 0, sizeof(buf_));
            need_frame_ = true;
            // Le tampon affiche est l'image des dalles : elles passent au noir
            // (37e complement). Pas un echange.
            if (hook_ != nullptr) {
                hook_(hook_ctx_, front());
            }
            return;
        case kMode:
            if (p[0] > kModeLocal) {
                send_error(kErrRange, "valeur hors plage", type);
                return;
            }
            forced_local_ = p[0] == kModeLocal;
            local_animation_ = forced_local_;
            need_frame_ = true;  // DELTA ignores jusqu'au FRAME (section 6.3)
            last_frame_us_ = now_us_;
            return;
        case kStatsReq:
            send_stats();  // tous les modes ; delai de 3 s inchange
            return;
        case kFrame:
            ++frames_rx_;
            handle_frame(p);
            return;
        case kDelta:
            ++deltas_rx_;
            handle_delta(p, n);
            return;
        default:
            send_error(kErrInternal, "non traite", type);
            return;
    }
}

void Card::send_stats() {
    BoardStats board;
    if (stats_hook_ != nullptr) {
        stats_hook_(stats_ctx_, board);
    }
    uint8_t p[kStatsLen];
    put_u32(p, static_cast<uint32_t>((now_us_ - boot_us_) / 1000));
    put_u32(p + 4, frames_rx_);
    put_u32(p + 8, deltas_rx_);
    put_u32(p + 12, bytes_rx_);
    put_u32(p + 16, decoder_.skipped());
    put_u32(p + 20, decoder_.crc_errors());
    put_u32(p + 24, decoder_.length_errors());
    put_u32(p + 28, deltas_ignored_);
    put_u32(p + 32, swaps_);
    put_u32(p + 36, board.replaced);
    put_u32(p + 40, board.loop_max_us);
    put_u16(p + 44, board.refresh_hz);
    p[46] = board.flags;
    send(kStats, p, sizeof(p));
}

void Card::count_gap(uint16_t number) {
    // Trous de numerotation depuis le dernier numero recu (B5).
    if (has_last_no_) {
        const uint16_t diff = static_cast<uint16_t>(number - last_no_);
        if (diff > 1 && diff < 0x8000) {
            lost_ += diff - 1u;
        }
    }
    has_last_no_ = true;
    last_no_ = number;
}

void Card::swap(uint16_t number) {
    front_ ^= 1;
    if (hook_ != nullptr) {
        hook_(hook_ctx_, front());
    }
    std::memcpy(back_mut(), front(), kPixelsBytes);  // recopie (section 6.4)
    has_displayed_ = true;
    displayed_no_ = number;
    ++swaps_;
    ++swaps_since_telemetry_;
}

void Card::handle_frame(const uint8_t* p) {
    const uint16_t number = get_u16(p);
    if (forced_local_) {
        has_last_no_ = true;  // ignoree, pas perdue (B6)
        last_no_ = number;
        return;
    }
    count_gap(number);
    local_animation_ = false;  // retour automatique au mode 0
    need_frame_ = false;
    last_frame_us_ = now_us_;
    std::memcpy(back_mut(), p + 2, kPixelsBytes);
    swap(number);
}

void Card::handle_delta(const uint8_t* p, size_t n) {
    DeltaHeader h;
    delta_header(p, n, h);  // longueur deja verifiee
    if (forced_local_) {
        has_last_no_ = true;  // ignore, pas perdu (B6)
        last_no_ = h.number;
        return;
    }
    last_frame_us_ = now_us_;  // CRC et longueur corrects (section 6.3)
    count_gap(h.number);
    if (need_frame_ || local_animation_ || !has_displayed_ || h.base != displayed_no_) {
        need_frame_ = true;  // sans ERROR (section 6.4)
        ++lost_;
        ++deltas_ignored_;
        return;
    }
    const uint8_t err = delta_apply(p, n, reinterpret_cast<uint8_t*>(back_mut()));
    if (err != kOk) {
        std::memcpy(back_mut(), front(), kPixelsBytes);
        need_frame_ = true;
        ++lost_;
        ++deltas_ignored_;
        send_error(err, "DELTA invalide", kDelta);
        return;
    }
    swap(h.number);
}

}  // namespace i75w
