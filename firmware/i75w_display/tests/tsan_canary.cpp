// Copyright (C) 2026 Gohy Yves
// Licence : GPL v3 (voir LICENSE a la racine du depot).
//
// Temoin de ThreadSanitizer (specification section 8, 53e complement) : deux
// fils incrementent la meme variable sans atomique ni verrou. Compile avec
// -fsanitize=thread, il DOIT etre signale (« data race ») ; avec
// TSAN_OPTIONS="halt_on_error=1 exitcode=66", il sort avec le code 66. Un autre
// code prouve que TSan n'est pas actif : le job i75w-host-tsan echoue alors.
// Hors de ctest ; compile seulement avec l'option CMake I75W_TSAN.

#include <cstdio>
#include <thread>

namespace {

int g_shared = 0;  // ni atomique ni verrou : acces concurrent volontaire

void bump() {
    for (int i = 0; i < 1000; ++i) {
        g_shared = g_shared + 1;
    }
}

}  // namespace

int main() {
    std::thread a(bump);
    std::thread b(bump);
    a.join();
    b.join();
    std::printf("tsan_canary : %d (TSan n'a rien signale)\n", g_shared);
    return 0;
}
