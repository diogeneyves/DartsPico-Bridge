// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Animation de secours (specification section 8, 36e et 38e complements) :
// police (7 glyphes), position dans le temps (30 px/s, boucle de 246 px,
// depart du bord droit), dessin (couleur, lignes 9 a 22, 118 px, ecart
// entre les lettres, decoupe aux bords), redessin seulement quand la
// position change.

#include <cstring>
#include <memory>
#include <string>

#include "fallback.hpp"
#include "font5x7.hpp"
#include "proto.hpp"
#include "runner.hpp"

namespace {

constexpr uint64_t kSecond = 1000000;

struct Image {
    uint16_t px[i75w::kPixels];
    uint16_t at(int x, int y) const { return px[y * i75w::kWidth + x]; }
};

std::unique_ptr<Image> draw(int x) {
    std::unique_ptr<Image> img(new Image());
    std::memset(img->px, 0x5A, sizeof(img->px));  // le fond doit etre efface
    i75w::fallback_draw(x, img->px);
    return img;
}

// Colonnes allumees de l'image : premiere et derniere (-1 si aucune).
void lit_columns(const Image& img, int& first, int& last) {
    first = -1;
    last = -1;
    for (int x = 0; x < i75w::kWidth; ++x) {
        for (int y = 0; y < i75w::kHeight; ++y) {
            if (img.at(x, y) != 0) {
                if (first < 0) first = x;
                last = x;
                break;
            }
        }
    }
}

}  // namespace

TEST(font_has_exactly_the_seven_glyphs) {
    CHECK_EQ(i75w::kFontGlyphs, 7);
    const std::string want = "RASPYDT";
    for (int i = 0; i < i75w::kFontGlyphs; ++i) {
        const i75w::Glyph& g = i75w::kFont5x7[i];
        CHECK(want.find(g.c) != std::string::npos);
        uint8_t any = 0;
        for (int r = 0; r < i75w::kGlyphHeight; ++r) {
            CHECK_EQ(g.rows[r] & ~0x1F, 0);  // 5 colonnes
            any |= g.rows[r];
        }
        CHECK_EQ(int(any), 0x1F);  // chaque glyphe occupe ses 5 colonnes
    }
    for (char c : std::string(i75w::kFallbackText)) {
        CHECK(i75w::find_glyph(c) != nullptr);
    }
    CHECK(i75w::find_glyph('Z') == nullptr);
}

TEST(geometry_constants) {
    CHECK_EQ(int(i75w::kFallbackColor), 0xFC00);
    CHECK_EQ(i75w::kFallbackTextWidth, 118);
    CHECK_EQ(i75w::kFallbackTextHeight, 14);
    CHECK_EQ(i75w::kFallbackTop, 9);
    CHECK_EQ(i75w::kFallbackPeriod, 246);
}

TEST(position_over_time) {
    CHECK_EQ(i75w::fallback_x(0), 128);                       // bord droit : rien de visible
    CHECK_EQ(i75w::fallback_x(kSecond / 30 - 1), 128);
    CHECK_EQ(i75w::fallback_x(kSecond / 30 + 1), 127);
    CHECK_EQ(i75w::fallback_x(kSecond), 98);                  // 30 px/s
    CHECK_EQ(i75w::fallback_x(245 * kSecond / 30 + 1), -117);  // derniere colonne visible
    CHECK_EQ(i75w::fallback_x(246 * kSecond / 30), 128);      // recommence (8,2 s)
    CHECK_EQ(i75w::fallback_x(246 * kSecond / 30 + kSecond), 98);
    // Sur plusieurs boucles, x reste dans [-117, 128].
    for (uint64_t t = 0; t < 30 * kSecond; t += 7919) {
        const int x = i75w::fallback_x(t);
        CHECK(x >= -117 && x <= 128);
    }
}

TEST(draw_colour_rows_and_width) {
    std::unique_ptr<Image> img = draw(5);
    int first = 0;
    int last = 0;
    lit_columns(*img, first, last);
    CHECK_EQ(first, 5);
    CHECK_EQ(last, 5 + 117);  // 118 px
    int lit = 0;
    for (int y = 0; y < i75w::kHeight; ++y) {
        for (int x = 0; x < i75w::kWidth; ++x) {
            const uint16_t c = img->at(x, y);
            CHECK(c == 0 || c == i75w::kFallbackColor);
            if (c != 0) {
                ++lit;
                CHECK(y >= 9 && y <= 22);
            }
        }
    }
    CHECK(lit > 0);
    CHECK_EQ(lit % 4, 0);  // pixels doubles : carres de 2 x 2
    // Lignes 9 et 22 utilisees (haut et bas des glyphes).
    bool top = false;
    bool bottom = false;
    for (int x = 0; x < i75w::kWidth; ++x) {
        top = top || img->at(x, 9) != 0;
        bottom = bottom || img->at(x, 22) != 0;
    }
    CHECK(top && bottom);
}

TEST(draw_glyph_r_and_letter_gap) {
    std::unique_ptr<Image> img = draw(0);
    // R, ligne 0 = 11110 : x 0 a 7 allumes, 8 et 9 eteints (lignes 9 et 10).
    for (int y = 9; y <= 10; ++y) {
        for (int x = 0; x < 8; ++x) {
            CHECK_EQ(int(img->at(x, y)), int(i75w::kFallbackColor));
        }
        CHECK_EQ(int(img->at(8, y)), 0);
        CHECK_EQ(int(img->at(9, y)), 0);
    }
    // Ecart : colonnes 10 et 11 de chaque lettre toujours eteintes.
    for (int i = 0; i < i75w::kFallbackChars - 1; ++i) {
        for (int dx = 10; dx < 12; ++dx) {
            for (int y = 0; y < i75w::kHeight; ++y) {
                CHECK_EQ(int(img->at(i * 12 + dx, y)), 0);
            }
        }
    }
}

TEST(draw_clipped_at_edges) {
    std::unique_ptr<Image> right = draw(128);
    std::unique_ptr<Image> gone = draw(-118);
    int first = 0;
    int last = 0;
    lit_columns(*right, first, last);
    CHECK_EQ(first, -1);  // tout noir
    lit_columns(*gone, first, last);
    CHECK_EQ(first, -1);
    std::unique_ptr<Image> entering = draw(120);
    lit_columns(*entering, first, last);
    CHECK_EQ(first, 120);
    CHECK_EQ(last, 127);
    std::unique_ptr<Image> leaving = draw(-117);
    lit_columns(*leaving, first, last);
    CHECK_EQ(first, 0);
    CHECK_EQ(last, 0);
    std::unique_ptr<Image> wide = draw(-50);
    lit_columns(*wide, first, last);
    CHECK_EQ(first, 0);
    CHECK_EQ(last, 67);
}

TEST(step_redraws_only_on_move_and_restarts_right) {
    std::unique_ptr<Image> img(new Image());
    i75w::Fallback f;
    const uint64_t t0 = 7 * kSecond + 11;
    f.start(t0);
    CHECK(f.step(t0, img->px));  // premiere image, meme au bord droit
    CHECK_EQ(f.x(), 128);
    CHECK(!f.step(t0 + 1000, img->px));
    CHECK(f.step(t0 + kSecond / 30 + 1, img->px));
    CHECK_EQ(f.x(), 127);
    CHECK(!f.step(t0 + kSecond / 30 + 2, img->px));
    // Une seconde : environ 30 redessins.
    int redraws = 0;
    for (uint64_t t = t0 + kSecond; t < t0 + 2 * kSecond; t += 1000) {
        redraws += f.step(t, img->px) ? 1 : 0;
    }
    CHECK(redraws >= 29 && redraws <= 31);
    // Nouvelle entree : repart du bord droit.
    f.start(t0 + 5 * kSecond);
    CHECK(f.step(t0 + 5 * kSecond, img->px));
    CHECK_EQ(f.x(), 128);
    // Heure anterieure au depart : traitee comme le depart.
    f.start(t0);
    CHECK(f.step(t0 - 5, img->px));
    CHECK_EQ(f.x(), 128);
}
