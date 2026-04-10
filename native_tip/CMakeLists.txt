cmake_minimum_required(VERSION 3.20)

project(native_tip LANGUAGES CXX)

add_library(native_tip SHARED
    src/DllMain.cpp
    src/Register.cpp
    src/TipContextGate.cpp
    src/TipTraceSink.cpp
    src/TipEditSession.cpp
    src/TipTextService.cpp
    src/TipCompositionManager.cpp
    src/TipServiceClient.cpp
    src/TipGatewayServer.cpp
)

target_compile_features(native_tip PRIVATE cxx_std_17)

target_include_directories(native_tip PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_SOURCE_DIR}/src
)

if(MSVC)
    target_compile_options(native_tip PRIVATE /W4 /permissive- /utf-8)
else()
    target_compile_options(native_tip PRIVATE -Wall -Wextra -Wpedantic)
endif()

target_compile_definitions(native_tip PRIVATE
    UNICODE
    _UNICODE
    NOMINMAX
    WIN32_LEAN_AND_MEAN
    NATIVE_TIP_EXPORTS
)

target_link_libraries(native_tip PRIVATE
    ole32
    advapi32
)

set_target_properties(native_tip PROPERTIES
    OUTPUT_NAME "native_tip"
)

add_executable(native_tip_spike_tests
    tests/tip_spike_smoke.cpp
    src/TipContextGate.cpp
    src/TipTraceSink.cpp
    src/TipEditSession.cpp
    src/TipTextService.cpp
    src/TipCompositionManager.cpp
    src/TipServiceClient.cpp
    src/TipGatewayServer.cpp
)

add_executable(native_tip_gateway_host
    src/TipGatewayHostMain.cpp
    src/TipContextGate.cpp
    src/TipTraceSink.cpp
    src/TipEditSession.cpp
    src/TipTextService.cpp
    src/TipCompositionManager.cpp
    src/TipServiceClient.cpp
    src/TipGatewayServer.cpp
)

target_compile_features(native_tip_spike_tests PRIVATE cxx_std_17)
target_include_directories(native_tip_spike_tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_SOURCE_DIR}/src
)

if(MSVC)
    target_compile_options(native_tip_spike_tests PRIVATE /W4 /permissive- /utf-8)
else()
    target_compile_options(native_tip_spike_tests PRIVATE -Wall -Wextra -Wpedantic)
endif()

target_compile_definitions(native_tip_spike_tests PRIVATE
    UNICODE
    _UNICODE
    NOMINMAX
    WIN32_LEAN_AND_MEAN
)

target_compile_features(native_tip_gateway_host PRIVATE cxx_std_17)
target_include_directories(native_tip_gateway_host PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_SOURCE_DIR}/src
)

if(MSVC)
    target_compile_options(native_tip_gateway_host PRIVATE /W4 /permissive- /utf-8)
else()
    target_compile_options(native_tip_gateway_host PRIVATE -Wall -Wextra -Wpedantic)
endif()

target_compile_definitions(native_tip_gateway_host PRIVATE
    UNICODE
    _UNICODE
    NOMINMAX
    WIN32_LEAN_AND_MEAN
)

enable_testing()
add_test(NAME native_tip_spike_tests COMMAND native_tip_spike_tests)
