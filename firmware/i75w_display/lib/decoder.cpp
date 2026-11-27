// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).

#include "decoder.hpp"

#include <cstring>

#include "crc32.hpp"

namespace i75w {

void Decoder::feed(const uint8_t* data, size_t n, FrameHandler& handler) {
    // Par morceaux qui tiennent dans le tampon. Quand il est plein, l'analyse
    // avance toujours : une trame complete tient dans kMaxFrame octets.
    while (n > 0) {
        size_t room = sizeof(buf_) - len_;
        size_t take = n < room ? n : room;
        std::memcpy(buf_ + len_, data, take);
        len_ += take;
        data += take;
        n -= take;
        process(handler);
    }
}

void Decoder::reset() {
    skipped_ += static_cast<uint32_t>(len_);
    len_ = 0;
}

void Decoder::clear() {
    len_ = 0;
    crc_errors_ = 0;
    length_errors_ = 0;
    skipped_ = 0;
    frames_ = 0;
}

void Decoder::process(FrameHandler& handler) {
    const size_t n = len_;
    size_t pos = 0;
    for (;;) {
        // Recherche de 0xAA 0x55 a partir de pos.
        size_t i = pos;
        while (i + 1 < n && !(buf_[i] == kSync0 && buf_[i + 1] == kSync1)) {
            ++i;
        }
        if (i + 1 >= n) {
            // Pas de synchronisation : garder un 0xAA final, qui peut la commencer.
            size_t keep = (n > pos && buf_[n - 1] == kSync0) ? 1 : 0;
            skipped_ += static_cast<uint32_t>(n - pos - keep);
            pos = n - keep;
            break;
        }
        skipped_ += static_cast<uint32_t>(i - pos);
        pos = i;
        if (n - i < kHeaderLen) {
            break;
        }
        const uint8_t type = buf_[i + 2];
        const size_t length = get_u16(buf_ + i + 3);
        if (length > max_length(type)) {
            ++length_errors_;
            ++skipped_;
            pos = i + 1;
            continue;
        }
        const size_t end = i + kHeaderLen + length + kCrcLen;
        if (end > n) {
            break;
        }
        const size_t body_end = i + kHeaderLen + length;
        if (crc32(buf_ + i + 2, 3 + length) != get_u32(buf_ + body_end)) {
            ++crc_errors_;
            ++skipped_;
            pos = i + 1;
            continue;
        }
        ++frames_;
        handler.on_frame(type, buf_ + i + kHeaderLen, length);
        pos = end;
    }
    if (pos > 0) {
        std::memmove(buf_, buf_ + pos, n - pos);
        len_ = n - pos;
    }
}

}  // namespace i75w
