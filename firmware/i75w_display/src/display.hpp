// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Affichage de l'Interstate 75 W (specification section 8, 36e et 37e
// complements) : pilote HUB75 sur le coeur 1, triple tampon Pixel, luminosite.
//
// Coeur 0 (ecrivain, n'attend jamais) : init() avant de lancer le coeur 1,
// puis publish_rgb565() ou publish_black() et set_brightness().
// Coeur 1 : core1_main(), qui demarre le pilote et ne rend jamais la main ;
// tout se passe ensuite dans l'interruption DMA_IRQ_0.

#ifndef I75W_DISPLAY_HPP
#define I75W_DISPLAY_HPP

#include <cstdint>

namespace display {

// Coeur 0, avant multicore_launch_core1 : trois tampons Pixel a zero (noir).
void init();

// Coeur 1 : constructeur du pilote, start(), puis attente des interruptions.
void core1_main();

// Coeur 0 : convertit une image RGB565 (128 x 32) dans un tampon libre et la
// publie ; elle sera prise a la fin du cycle de rafraichissement en cours.
void publish_rgb565(const uint16_t* image);

// Coeur 0 : publie une image noire.
void publish_black();

// Coeur 0 : BRIGHTNESS (0 a 255), appliquee par le coeur 1 en fin de cycle.
void set_brightness(uint8_t b);

// Compteur de vie du coeur 1 : avance a chaque interruption DMA_IRQ_0.
uint32_t alive();

// Cycles complets de rafraichissement depuis le demarrage.
uint32_t cycles();

// Images remplacees avant d'avoir ete prises par le coeur 1 (STATS, replaced).
uint32_t replaced();

}  // namespace display

#endif  // I75W_DISPLAY_HPP
