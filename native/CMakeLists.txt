cmake_minimum_required(VERSION 3.25)
project(facut_native VERSION 0.9.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

include(FetchContent)
FetchContent_Declare(
  nlohmann_json
  URL https://github.com/nlohmann/json/releases/download/v3.12.0/json.tar.xz
  URL_HASH SHA256=42f6e95cad6ec532fd372391373363b62a14af6d771056dbfc86160e6dfff7aa
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
)
FetchContent_MakeAvailable(nlohmann_json)

set(FACUT_FFMPEG_ROOT "" CACHE PATH "Root of an LGPL shared FFmpeg distribution")
if(NOT FACUT_FFMPEG_ROOT AND DEFINED ENV{FACUT_FFMPEG_ROOT})
  file(TO_CMAKE_PATH "$ENV{FACUT_FFMPEG_ROOT}" FACUT_FFMPEG_ROOT)
endif()
if(NOT FACUT_FFMPEG_ROOT)
  message(FATAL_ERROR "Set FACUT_FFMPEG_ROOT to an LGPL shared FFmpeg distribution")
endif()

find_path(FFMPEG_INCLUDE_DIR libavformat/avformat.h PATHS "${FACUT_FFMPEG_ROOT}/include" NO_DEFAULT_PATH REQUIRED)
foreach(component avformat avcodec avutil swscale)
  find_library(FFMPEG_${component}_LIBRARY NAMES ${component} PATHS "${FACUT_FFMPEG_ROOT}/lib" NO_DEFAULT_PATH REQUIRED)
endforeach()

add_executable(facut-native
  src/main.cpp
  src/media_scanner.cpp
)
target_include_directories(facut-native PRIVATE include "${FFMPEG_INCLUDE_DIR}")
target_link_libraries(facut-native PRIVATE
  nlohmann_json::nlohmann_json
  ${FFMPEG_avformat_LIBRARY}
  ${FFMPEG_avcodec_LIBRARY}
  ${FFMPEG_avutil_LIBRARY}
  ${FFMPEG_swscale_LIBRARY}
)
target_compile_definitions(facut-native PRIVATE FACUT_NATIVE_VERSION="${PROJECT_VERSION}")
if(MSVC)
  target_compile_options(facut-native PRIVATE /W4 /permissive- /EHsc /utf-8)
  target_compile_definitions(facut-native PRIVATE _SILENCE_CXX20_U8PATH_DEPRECATION_WARNING)
else()
  target_compile_options(facut-native PRIVATE -Wall -Wextra -Wpedantic)
endif()

if(WIN32)
  file(GLOB FFMPEG_RUNTIME_DLLS "${FACUT_FFMPEG_ROOT}/bin/*.dll")
  add_custom_command(TARGET facut-native POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different ${FFMPEG_RUNTIME_DLLS} $<TARGET_FILE_DIR:facut-native>
    COMMAND_EXPAND_LISTS
  )
endif()

install(TARGETS facut-native RUNTIME DESTINATION .)
