cmake_minimum_required(VERSION 3.20)

project(overlay_ui LANGUAGES CXX)

add_executable(overlay_ui WIN32
    overlay_ui.cpp
    overlay_window.cpp
    overlay_window.h
)

target_compile_features(overlay_ui PRIVATE cxx_std_17)

if(MSVC)
    target_compile_options(overlay_ui PRIVATE /W4 /permissive- /utf-8)
else()
    target_compile_options(overlay_ui PRIVATE -Wall -Wextra -Wpedantic)
endif()

target_compile_definitions(overlay_ui PRIVATE UNICODE _UNICODE NOMINMAX WIN32_LEAN_AND_MEAN)

target_link_libraries(overlay_ui PRIVATE
    d2d1
    dwrite
    dwmapi
)

set_target_properties(overlay_ui PROPERTIES
    OUTPUT_NAME "overlay_ui"
)
