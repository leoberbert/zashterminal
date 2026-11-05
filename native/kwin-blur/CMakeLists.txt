# SPDX-License-Identifier: GPL-3.0-or-later
cmake_minimum_required(VERSION 3.22)
project(zashterminalblur VERSION 1.0.0 LANGUAGES CXX)

# KWin's native effect API is private. Do not silently build against another ABI.
find_package(ECM 6.30 REQUIRED NO_MODULE)
list(APPEND CMAKE_MODULE_PATH ${ECM_MODULE_PATH})

include(KDEInstallDirs6)
include(KDECMakeSettings)
include(KDECompilerSettings NO_POLICY_SCOPE)
find_package(Qt6 6.11.2 EXACT REQUIRED COMPONENTS Core Gui DBus Widgets Quick OpenGL)
find_package(KWin 6.8.2 EXACT REQUIRED CONFIG)

set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

kcoreaddons_add_plugin(zashterminalblur
    SOURCES
        blur.cpp
        blur.h
        main.cpp
        zashterminalblur.qrc
    INSTALL_NAMESPACE "kwin/effects/plugins"
)
set_property(TARGET zashterminalblur APPEND PROPERTY
    AUTOMOC_MACRO_NAMES KWIN_EFFECT_FACTORY_SUPPORTED_ENABLED
)
target_link_libraries(zashterminalblur PRIVATE
    KWin::kwin
    Qt6::DBus
)
# Never embed a container-only library search path in the staged effect.
set_target_properties(zashterminalblur PROPERTIES
    SKIP_BUILD_RPATH TRUE
    INSTALL_RPATH ""
)
