# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE a la racine du depot).
#
# Bibliotheque portable du firmware de l'Interstate 75 W (specification section 8) :
# CRC-32, recepteur, DELTA, machine d'etat de la carte, animation de secours. C++17, sans SDK ni
# allocation dynamique. Incluse par tests/CMakeLists.txt (tests sur PC) et,
# au jalon M4-f2, par le CMakeLists.txt du firmware.

set(I75W_LIB_DIR ${CMAKE_CURRENT_LIST_DIR})

add_library(i75w_lib STATIC
    ${I75W_LIB_DIR}/crc32.cpp
    ${I75W_LIB_DIR}/decoder.cpp
    ${I75W_LIB_DIR}/delta.cpp
    ${I75W_LIB_DIR}/card.cpp
    ${I75W_LIB_DIR}/fallback.cpp
)
target_include_directories(i75w_lib PUBLIC ${I75W_LIB_DIR})
target_compile_features(i75w_lib PUBLIC cxx_std_17)
set_target_properties(i75w_lib PROPERTIES CXX_EXTENSIONS OFF)
