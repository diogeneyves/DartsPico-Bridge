# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE a la racine du depot).
#
# Firmware C++ de l'Interstate 75 W (specification section 8) : compilation
# du .uf2 avec le Pico SDK 2.1.0 (95ea6ac) et picotool 2.1.0 (df21059),
# versions figees, clonees par la CI (job i75w-uf2) ou dans external/.
#
#   cmake -S firmware/i75w_display -B build/i75w-fw \
#         -DPICO_SDK_PATH=<pico-sdk> -Dpicotool_DIR=<picotool installe>/picotool
#   cmake --build build/i75w-fw
#
# Les tests sur PC de lib/ ont leur propre projet : tests/CMakeLists.txt.

cmake_minimum_required(VERSION 3.13)

# Carte : celle du firmware officiel de Pimoroni pour l'Interstate 75 W RP2350
# (section 8, 34e complement). La puce sans fil n'est pas utilisee.
set(PICO_BOARD pico2_w CACHE STRING "Carte du Pico SDK")

if (NOT PICO_SDK_PATH AND DEFINED ENV{PICO_SDK_PATH})
    set(PICO_SDK_PATH $ENV{PICO_SDK_PATH})
endif()
if (NOT PICO_SDK_PATH)
    message(FATAL_ERROR "PICO_SDK_PATH non defini : chemin du Pico SDK 2.1.0 (section 8)")
endif()
include(${PICO_SDK_PATH}/external/pico_sdk_import.cmake)

# Version du firmware, definie ici seulement (section 8) : renvoyee par HELLO.
project(i75w_display VERSION 0.1.0 LANGUAGES C CXX ASM)

if (NOT PICO_SDK_VERSION_STRING STREQUAL "2.1.0")
    message(FATAL_ERROR "Pico SDK ${PICO_SDK_VERSION_STRING} : la version figee est 2.1.0 (section 8)")
endif()

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

pico_sdk_init()

# Bibliotheque portable (protocole, machine d'etat), avertissements bloquants
# comme sur PC.
include(${CMAKE_CURRENT_LIST_DIR}/lib/lib.cmake)
target_compile_options(i75w_lib PRIVATE -Wall -Wextra -Werror)

add_executable(i75w_display
    src/main.cpp
    src/display.cpp
    # Pilote HUB75 de pimoroni-pico (39b017a), copie et reduit (section 8).
    src/hub75.cpp
    src/usb_descriptors.c
    # Pilote de l'interface de remise a zero (picotool reboot -f), sans stdio USB.
    ${PICO_SDK_PATH}/src/rp2_common/pico_stdio_usb/reset_interface.c
)

# Notre tusb_config.h, puis l'en-tete de compatibilite de reset_interface.c.
target_include_directories(i75w_display PRIVATE
    ${CMAKE_CURRENT_LIST_DIR}/src
    ${CMAKE_CURRENT_LIST_DIR}/src/sdk_shim
)

target_compile_definitions(i75w_display PRIVATE
    I75W_FW_MAJOR=${PROJECT_VERSION_MAJOR}
    I75W_FW_MINOR=${PROJECT_VERSION_MINOR}
    I75W_FW_PATCH=${PROJECT_VERSION_PATCH}
    # reset_interface.c : interface de remise a zero, BOOTSEL et redemarrage
    # normal ; aucune broche d'activite commandee par l'hote (les GPIO sont
    # au HUB75) ; pas de remise a zero a 1 200 bauds ; pas de Microsoft OS 2.0.
    PICO_STDIO_USB_ENABLE_RESET_VIA_VENDOR_INTERFACE=1
    PICO_STDIO_USB_RESET_INTERFACE_SUPPORT_RESET_TO_BOOTSEL=1
    PICO_STDIO_USB_RESET_INTERFACE_SUPPORT_RESET_TO_FLASH_BOOT=1
    PICO_STDIO_USB_RESET_INTERFACE_SUPPORT_MS_OS_20_DESCRIPTOR=0
    PICO_STDIO_USB_RESET_BOOTSEL_FIXED_ACTIVITY_LED=1
    PICO_STDIO_USB_RESET_BOOTSEL_INTERFACE_DISABLE_MASK=0u
    PICO_STDIO_USB_RESET_RESET_TO_FLASH_DELAY_MS=100
    PICO_STDIO_USB_ENABLE_RESET_VIA_BAUD_RATE=0
    # .uf2 reproductible (complement 69) : pas de date de compilation (__DATE__)
    # dans les informations binaires (standard_binary_info.c du SDK, l. 30 a 34).
    PICO_NO_BI_PROGRAM_BUILD_DATE=1
)

set_source_files_properties(src/main.cpp src/display.cpp src/hub75.cpp
    PROPERTIES COMPILE_OPTIONS "-Wall;-Wextra")

# Programmes PIO du pilote (hub75.pio.h).
pico_generate_pio_header(i75w_display ${CMAKE_CURRENT_LIST_DIR}/src/hub75.pio)

target_link_libraries(i75w_display PRIVATE
    i75w_lib
    pico_stdlib
    pico_multicore
    pico_unique_id
    pico_usb_reset_interface
    hardware_adc
    hardware_dma
    hardware_irq
    hardware_pio
    hardware_sync
    hardware_watchdog
    tinyusb_device
)

# Ni stdio USB ni stdio UART (section 8) : aucun texte sur l'USB, et l'UART
# par defaut est sur GPIO 0 et 1 (R0 et G0 du HUB75).
pico_enable_stdio_usb(i75w_display 0)
pico_enable_stdio_uart(i75w_display 0)

pico_set_program_name(i75w_display "RaspyDarts DMD")
pico_set_program_version(i75w_display "${PROJECT_VERSION}")

pico_add_extra_outputs(i75w_display)
