# Max Payne 3: the recompiled game and its app, built inside gta4-ps5's tree
# (glue/rexglue-sdk-main/mp3-recomp, see .github/workflows/ps5-build.yml) as
# one static PS5 program, the way gta4-recomp builds GTA IV.

if(NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/generated/sources.cmake")
    message(FATAL_ERROR "mp3-recomp generated sources are missing: run rexglue codegen mp3_manifest.toml")
endif()
include(generated/sources.cmake)

set(MP3_APP_SOURCES
    src/main.cpp
    src/mp3_app.cpp
)

if(PS5)
    # The menu (its fonts: gta4-ps5's, SIL Open Font License) and gta4-ps5's
    # own sound mixer for its song.
    set(GTA4_PS5_SRC "${REXSDK_DIR}/gta4-recomp/src/ps5")
    set(MP3_PS5_FONTS "${REXSDK_DIR}/../../ps5/assets/fonts")
    list(APPEND MP3_APP_SOURCES src/ps5/mp3_menu.cpp src/ps5/mp3_install.cpp src/ps5/mp3_textures_install.cpp src/ps5/mp3_trophies.cpp src/ps5/mp3_trophies_game.cpp
         "${GTA4_PS5_SRC}/game/sound.cpp" "${GTA4_PS5_SRC}/hub/hub_sfx.cpp")
    set_source_files_properties(src/ps5/mp3_menu.cpp PROPERTIES
        COMPILE_DEFINITIONS "MP3PS5_FONTS_DIR=\"${MP3_PS5_FONTS}\"")
    add_executable(mp3 ${MP3_APP_SOURCES} ${GENERATED_SOURCES})
    target_include_directories(mp3 PRIVATE "${GTA4_PS5_SRC}")
    # The recompiled game's own files, as gta4-recomp marks GTA IV's.
    set(MP3_PS5_GUEST_SOURCES ${GENERATED_SOURCES})
    list(FILTER MP3_PS5_GUEST_SOURCES INCLUDE REGEX "mp3_recomp\\.[0-9]+\\.cpp$")
    set_property(SOURCE ${MP3_PS5_GUEST_SOURCES} APPEND PROPERTY COMPILE_DEFINITIONS REX_PS5_GUEST_CODE=1)
    set_property(SOURCE generated/mp3_register.cpp APPEND PROPERTY COMPILE_OPTIONS
        "$<$<CONFIG:Release,RelWithDebInfo>:-O0>")
    target_compile_definitions(mp3 PRIVATE REX_FUSED_VMADDFP=1)
    target_include_directories(mp3 PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/src
        ${CMAKE_CURRENT_SOURCE_DIR}/generated
        ${CMAKE_CURRENT_SOURCE_DIR}/../thirdparty/crypto
        ${CMAKE_CURRENT_SOURCE_DIR}/../thirdparty/imgui
        ${CMAKE_CURRENT_SOURCE_DIR}/../thirdparty/inja/third_party/include
        ${CMAKE_CURRENT_SOURCE_DIR}/../thirdparty/stb
    )
    target_compile_options(mp3 PRIVATE "SHELL:-include ${REXSDK_DIR}/include/rex/ps5_compat.h")
    # zlib (the toolchain's), for the HD textures made on the console (mp3_textures_install.cpp)
    set(MP3_ZLIB_ROOT "${PS5_PAYLOAD_SDK}/../zlib/root/usr")
    target_include_directories(mp3 PRIVATE "${MP3_ZLIB_ROOT}/include")
    target_link_libraries(mp3 PRIVATE "${MP3_ZLIB_ROOT}/lib/libz.a")
    target_link_libraries(mp3 PRIVATE
        rex::runtime
        rexgpu-xenos
        xxHash::xxhash
        SDL3::SDL3
        SceUserService ScePad SceAudioOut SceSystemService SceVideoOut
    )
    rexglue_configure_target(mp3)
else()
    add_executable(mp3 ${MP3_APP_SOURCES} ${GENERATED_SOURCES})
    target_include_directories(mp3 PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/src
        ${CMAKE_CURRENT_SOURCE_DIR}/generated
        ${CMAKE_CURRENT_SOURCE_DIR}/../thirdparty/imgui
    )
    target_link_libraries(mp3 PRIVATE rex::runtime)
    rexglue_configure_target(mp3 GPU_PLUGINS xenos)
endif()
