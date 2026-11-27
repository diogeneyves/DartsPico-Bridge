// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Firmware de l'Interstate 75 W (specification section 8), jalons M4-f2a,
// M4-f2b et M4-f2c : coeur 0 = USB CDC (TinyUSB, sans stdio), trames du section 6
// traitees par la bibliotheque portable (lib/card.hpp), mesures, chien de
// garde, publication des images au coeur 1 ; coeur 1 = rafraichissement des
// dalles (display.cpp, pilote HUB75), triple tampon Pixel, luminosite.
//
// Images publiees au coeur 1 (le coeur 0 n'attend jamais) : l'image affichee
// a chaque echange et sur CLEAR (accroche de Card), sauf pendant l'animation
// locale (MODE 1 ou delai de 3 s) : l'animation de secours (lib/fallback.hpp),
// dessinee dans un tampon statique et publiee quand elle bouge (environ
// 30 images/s), repartant du bord droit a chaque entree ; au retour en mode 0,
// de nouveau l'image affichee du Pi (noire apres un CLEAR) [38e complement].
//
// STATS (section 6.2) : les champs propres a la carte (replaced, plus long
// tour de boucle, cycles de rafraichissement par seconde, demarrage par le
// chien de garde) sont fournis a Card par une accroche.
//
// Aucun texte n'est jamais ecrit sur l'USB : ni stdio USB ni stdio UART (les
// GPIO 0 et 1 sont des lignes HUB75) ; les erreurs passent par ERROR.

#include <cstddef>
#include <cstdint>

#include "hardware/adc.h"
#include "hardware/gpio.h"
#include "hardware/watchdog.h"
#include "pico/multicore.h"
#include "pico/stdlib.h"
#include "tusb.h"

#include "board.hpp"
#include "card.hpp"
#include "display.hpp"
#include "fallback.hpp"
#include "liveness.hpp"
#include "measures.hpp"

namespace {

constexpr uint32_t kWatchdogMs = 2000;          // section 8
constexpr uint64_t kMeasurePeriodUs = 250000;   // 5 V et temperature
constexpr uint64_t kTxTimeoutUs = 20000;        // attente de place pour une reponse
constexpr size_t kRxChunk = 512;
constexpr uint64_t kRefreshPeriodUs = 1000000;  // refresh_hz de STATS

// Machine d'etat de la carte : environ 25 ko (deux images RGB565 et le
// recepteur), en memoire statique.
i75w::Card g_card;

// Ce que montrent les dalles : true = animation de secours.
bool g_shown_local = false;

// Animation de secours : etat et image RGB565 (8 Ko, statique : rien sur la pile).
i75w::Fallback g_fallback;
alignas(4) uint16_t g_fallback_image[i75w::kPixels];

// Champs de STATS propres a la carte (coeur 0 seulement).
uint32_t g_loop_max_us = 0;     // depuis le STATS precedent
uint16_t g_refresh_hz = 0;      // cycles complets pendant la derniere seconde
bool g_watchdog_boot = false;   // dernier demarrage cause par le chien de garde

// Accroche de Card : nouvelle image affichee (echange ou CLEAR).
void on_display_change(void* /*ctx*/, const uint16_t* front) {
    if (g_card.mode() == i75w::kModeLocal) {
        return;  // l'animation continue (CLEAR : tampons du Pi seulement)
    }
    display::publish_rgb565(front);
    g_shown_local = false;
}

// Accroche de Card a chaque STATS_REQ ; remet le plus long tour a zero.
void on_stats(void* /*ctx*/, i75w::BoardStats& st) {
    st.replaced = display::replaced();
    st.loop_max_us = g_loop_max_us;
    g_loop_max_us = 0;
    st.refresh_hz = g_refresh_hz;
    st.flags = g_watchdog_boot ? i75w::kStatsFlagWatchdog : 0;
}

// Animation de secours (depart du bord droit a chaque entree) ou retour a
// l'image affichee du Pi.
void follow_mode(uint64_t now) {
    const bool local = g_card.mode() == i75w::kModeLocal;
    if (local) {
        if (!g_shown_local) {
            g_fallback.start(now);
            g_shown_local = true;
        }
        if (g_fallback.step(now, g_fallback_image)) {
            display::publish_rgb565(g_fallback_image);
        }
    } else if (g_shown_local) {
        display::publish_rgb565(g_card.front());
        g_shown_local = false;
    }
}

// Reponses de la carte vers le port USB, trame par trame. Une trame n'est
// jamais coupee : si personne n'ecoute (DTR absent) ou si la place ne se
// libere pas a temps, elle est abandonnee entiere (le Pi s'en remet :
// silence, puis nouveau HELLO_REQ).
class UsbSink final : public i75w::ByteSink {
public:
    void write(const uint8_t* data, size_t len) override {
        if (!tud_cdc_connected()) {
            return;
        }
        const uint64_t deadline = time_us_64() + kTxTimeoutUs;
        while (tud_cdc_write_available() < len) {
            tud_cdc_write_flush();
            tud_task();
            if (!tud_cdc_connected() || time_us_64() > deadline) {
                return;
            }
        }
        tud_cdc_write(data, static_cast<uint32_t>(len));
        tud_cdc_write_flush();
    }
};

void output_low(unsigned pin) {
    gpio_init(pin);
    gpio_put(pin, false);
    gpio_set_dir(pin, GPIO_OUT);
}

// Etat sur des broches des le demarrage (section 8, 36e complement).
void init_pins() {
    // OE d'abord : niveau haut (inactif) avant de passer en sortie, puis
    // force au niveau haut. gpio_init et pio_gpio_init effacent ce forcage :
    // le pilote le repose a la fin de start() (37e complement).
    gpio_init(board::kHub75Oe);
    gpio_put(board::kHub75Oe, true);
    gpio_set_dir(board::kHub75Oe, GPIO_OUT);
    gpio_set_outover(board::kHub75Oe, GPIO_OVERRIDE_HIGH);

    // Autres lignes HUB75 (donnees, adresses, CLK, STB) : niveau bas, en
    // attendant le pilote (coeur 1).
    for (unsigned pin = board::kHub75First; pin <= board::kHub75Stb; ++pin) {
        output_low(pin);
    }

    // Boutons A et B inutilises : tirage interne vers le haut.
    gpio_init(board::kSwitchA);
    gpio_pull_up(board::kSwitchA);
    gpio_init(board::kSwitchB);
    gpio_pull_up(board::kSwitchB);

    output_low(board::kLedData);  // LED SK6812 inutilisee
    output_low(board::kWlOn);     // puce sans fil eteinte
}

void init_adc() {
    adc_init();
    adc_gpio_init(board::k5vSense);  // entree numerique coupee (erratum RP2350-E9)
    adc_set_temp_sensor_enabled(true);
}

void measure() {
    adc_select_input(board::k5vSenseAdcInput);
    const uint16_t raw_5v = adc_read();
    adc_select_input(ADC_TEMPERATURE_CHANNEL_NUM);
    const uint16_t raw_temp = adc_read();
    g_card.set_measures(i75w::millivolts_5v(raw_5v), i75w::temperature_dc(raw_temp));
}

}  // namespace

int main() {
    // A lire avant watchdog_enable(), qui reecrit les registres du chien de garde.
    g_watchdog_boot = watchdog_enable_caused_reboot();
    init_pins();

    // Tampons Pixel noirs avant start() (37e complement), puis coeur 1.
    display::init();
    g_card.set_swap_hook(on_display_change, nullptr);
    g_card.set_stats_hook(on_stats, nullptr);
    display::set_brightness(g_card.brightness());
    multicore_launch_core1(display::core1_main);
    i75w::LivenessGate gate(display::alive());
    watchdog_enable(kWatchdogMs, true);

    init_adc();
    g_card.set_firmware_version(I75W_FW_MAJOR, I75W_FW_MINOR, I75W_FW_PATCH);
    measure();
    uint64_t next_measure = time_us_64() + kMeasurePeriodUs;
    g_card.boot(time_us_64());
    uint64_t next_refresh = time_us_64() + kRefreshPeriodUs;
    uint32_t last_cycles = display::cycles();

    tusb_init();

    UsbSink sink;
    static uint8_t rx[kRxChunk];

    for (;;) {
        const uint64_t loop_start = time_us_64();
        tud_task();

        const uint64_t now = time_us_64();
        if (now >= next_refresh) {
            const uint32_t c = display::cycles();
            const uint32_t d = c - last_cycles;
            g_refresh_hz = static_cast<uint16_t>(d > 0xFFFF ? 0xFFFF : d);
            last_cycles = c;
            next_refresh = now + kRefreshPeriodUs;
        }
        if (now >= next_measure) {
            measure();
            next_measure = now + kMeasurePeriodUs;
        }

        const uint32_t n = tud_cdc_available() ? tud_cdc_read(rx, sizeof(rx)) : 0;
        if (n > 0) {
            g_card.feed(rx, n, now, sink);  // appelle tick()
        } else {
            g_card.tick(now, sink);
        }
        follow_mode(now);
        display::set_brightness(g_card.brightness());

        // Nourri seulement si le coeur 1 a progresse ; la boucle du coeur 0,
        // elle, progresse par construction.
        if (gate.should_feed(display::alive())) {
            watchdog_update();
        }
        const uint64_t loop_us = time_us_64() - loop_start;
        if (loop_us > g_loop_max_us) {
            g_loop_max_us = loop_us > 0xFFFFFFFFu ? 0xFFFFFFFFu : static_cast<uint32_t>(loop_us);
        }
    }
}
