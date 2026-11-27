# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE a la racine du depot).
#
# Tests sur PC de la bibliotheque portable du firmware de l'Interstate 75 W
# (specification section 8 et section 11.1) : projet CMake autonome, sans Pico SDK.
#
#   cmake -S firmware/i75w_display/tests -B build/i75w
#   cmake --build build/i75w
#   ctest --test-dir build/i75w --output-on-failure

cmake_minimum_required(VERSION 3.16)
project(i75w_host_tests LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

include(${CMAKE_CURRENT_SOURCE_DIR}/../lib/lib.cmake)

set(I75W_WARNINGS -Wall -Wextra -Werror)
target_compile_options(i75w_lib PRIVATE ${I75W_WARNINGS})

set(I75W_VECTORS ${CMAKE_CURRENT_SOURCE_DIR}/vectors)

enable_testing()

foreach(name test_lib test_card test_timing test_board test_display test_fallback)
    add_executable(${name} ${name}.cpp runner.cpp)
    target_link_libraries(${name} PRIVATE i75w_lib)
    target_compile_options(${name} PRIVATE ${I75W_WARNINGS})
endforeach()

# Essai du triple tampon a deux fils d'execution (comme les deux coeurs).
find_package(Threads REQUIRED)
target_link_libraries(test_display PRIVATE Threads::Threads)

# Temoin de ThreadSanitizer (53e complement), hors de ctest : acces concurrent
# volontaire, que TSan doit signaler. Seulement pour le job i75w-host-tsan, qui
# passe -fsanitize=thread dans CMAKE_CXX_FLAGS.
option(I75W_TSAN "Compiler le temoin de ThreadSanitizer (tsan_canary)" OFF)
if(I75W_TSAN)
    add_executable(tsan_canary tsan_canary.cpp)
    target_link_libraries(tsan_canary PRIVATE Threads::Threads)
    target_compile_options(tsan_canary PRIVATE ${I75W_WARNINGS})
endif()

add_test(NAME lib COMMAND test_lib ${I75W_VECTORS}/dmd_lib.txt)
add_test(NAME card COMMAND test_card ${I75W_VECTORS}/dmd_card.txt)
add_test(NAME timing COMMAND test_timing)
add_test(NAME board COMMAND test_board)
add_test(NAME display COMMAND test_display)
add_test(NAME fallback COMMAND test_fallback)
