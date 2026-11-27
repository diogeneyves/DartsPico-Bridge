// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Recepteur de trames avec resynchronisation (section 6.1, B2), meme
// comportement et memes compteurs que Decoder de shared/dmd_proto.py.
//
// Tampon fixe d'une trame maximale (8 203 octets), aucune allocation. Les
// octets recus y sont ajoutes, puis analyses : une trame complete et juste est
// rendue au FrameHandler, un pointeur vers ses donnees valable pendant l'appel
// seulement. Sur une fausse en-tete (longueur au-dela du maximum du type, ou
// CRC faux), la recherche reprend a l'octet qui suit son 0xAA. Le resultat ne
// depend pas du decoupage des octets entre les appels de feed().

#ifndef I75W_DECODER_HPP
#define I75W_DECODER_HPP

#include <cstddef>
#include <cstdint>

#include "proto.hpp"

namespace i75w {

class FrameHandler {
public:
    // Donnees de la trame (len octets), valables pendant l'appel seulement.
    virtual void on_frame(uint8_t type, const uint8_t* payload, size_t len) = 0;

protected:
    ~FrameHandler() = default;
};

class Decoder {
public:
    Decoder() = default;

    // Ajoute n octets et rend au handler les trames completes, dans l'ordre.
    // Le handler ne doit pas rappeler feed().
    void feed(const uint8_t* data, size_t n, FrameHandler& handler);

    // Oublie une trame partielle (par exemple apres une reconnexion) : octets sautes.
    void reset();

    // Recepteur neuf : tampon vide, compteurs a zero.
    void clear();

    uint32_t crc_errors() const { return crc_errors_; }
    uint32_t length_errors() const { return length_errors_; }
    uint32_t skipped() const { return skipped_; }
    uint32_t frames() const { return frames_; }
    size_t pending() const { return len_; }  // octets gardes en attente

private:
    void process(FrameHandler& handler);

    uint8_t buf_[kMaxFrame] = {};
    size_t len_ = 0;
    uint32_t crc_errors_ = 0;
    uint32_t length_errors_ = 0;
    uint32_t skipped_ = 0;
    uint32_t frames_ = 0;
};

}  // namespace i75w

#endif  // I75W_DECODER_HPP
