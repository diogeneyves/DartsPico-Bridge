// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Logique de la carte sans materiel (specification section 8, M4-f2a) :
// conversions des mesures (5 V par GPIO28, temperature) et porte du chien de
// garde (nourri seulement si le coeur 1 progresse).

#include "liveness.hpp"
#include "measures.hpp"
#include "runner.hpp"

namespace {

int mv(uint16_t raw) { return i75w::millivolts_5v(raw); }
int dc(uint16_t raw) { return i75w::temperature_dc(raw); }

}  // namespace

TEST(millivolts_scale) {
    CHECK_EQ(mv(0), 0);
    CHECK_EQ(mv(2047), 3299);
    CHECK_EQ(mv(3103), 5001);  // environ 5 V
    CHECK_EQ(mv(4095), 6600);  // pleine echelle : 3 300 mV x 2
}

TEST(millivolts_saturates_above_full_scale) {
    CHECK_EQ(mv(4096), 6600);
    CHECK_EQ(mv(65535), 6600);
}

TEST(temperature_formula) {
    CHECK_EQ(dc(876), 270);     // V = 0,706 V environ : 27 degres
    CHECK_EQ(dc(1000), -310);   // troncature vers zero
    CHECK_EQ(dc(0), 4372);
    CHECK_EQ(dc(4095), -14802);
    CHECK_EQ(dc(65535), -14802);
}

TEST(liveness_needs_progress) {
    i75w::LivenessGate gate(7);
    CHECK(!gate.should_feed(7));   // coeur 1 immobile depuis le demarrage
    CHECK(gate.should_feed(8));    // il a progresse
    CHECK(!gate.should_feed(8));   // pas de nouvelle progression
    CHECK(!gate.should_feed(8));
    CHECK(gate.should_feed(12));   // plusieurs pas d'un coup
}

TEST(liveness_counter_wraps) {
    i75w::LivenessGate gate(0xFFFFFFFFu);
    CHECK(gate.should_feed(0));
    CHECK(!gate.should_feed(0));
    CHECK(gate.should_feed(1));
}
