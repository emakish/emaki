# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
cmake_minimum_required(VERSION 3.25)
project(emaki_i18n LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(Qt6 REQUIRED COMPONENTS Core Qml WaylandClientPrivate)
set(EMAKI_LOCALE_DIR "${CMAKE_INSTALL_PREFIX}/share/emaki/locale" CACHE PATH "Message catalogues")
add_library(emakii18nplugin MODULE plugin.cpp)
set_target_properties(emakii18nplugin PROPERTIES AUTOMOC ON
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/qml/Emaki/I18n")
target_link_libraries(emakii18nplugin PRIVATE Qt6::Core Qt6::Qml Qt6::WaylandClientPrivate)
target_compile_definitions(emakii18nplugin PRIVATE EMAKI_LOCALE_DIR="${EMAKI_LOCALE_DIR}")
target_compile_options(emakii18nplugin PRIVATE -Wall -Wextra -Wpedantic -Werror)
configure_file(qmldir "${CMAKE_CURRENT_BINARY_DIR}/qml/Emaki/I18n/qmldir" COPYONLY)
file(GENERATE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/install-paths"
    CONTENT "${CMAKE_INSTALL_PREFIX}|${EMAKI_LOCALE_DIR}\n")
