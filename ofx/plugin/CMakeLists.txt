cmake_minimum_required(VERSION 3.20)
project(CorridorKey2 LANGUAGES CXX)
if(NOT WIN32)
  message(FATAL_ERROR "This backend launcher currently targets Windows x64")
endif()
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
file(GLOB SUPPORT_SOURCES CONFIGURE_DEPENDS openfx/Support/Library/*.cpp)
add_library(CorridorKey2 SHARED CorridorKey2.cpp ${SUPPORT_SOURCES})
target_include_directories(CorridorKey2 PRIVATE openfx/include openfx/Support/include openfx/Support/Library)
target_compile_definitions(CorridorKey2 PRIVATE WIN32 _WINDOWS NOMINMAX WIN32_LEAN_AND_MEAN)
target_link_libraries(CorridorKey2 PRIVATE bcrypt)
if(MSVC)
  target_compile_options(CorridorKey2 PRIVATE $<$<CONFIG:Release>:/O2>)
else()
  target_compile_options(CorridorKey2 PRIVATE $<$<CONFIG:Release>:-O3>)
endif()
include(CTest)
if(BUILD_TESTING)
  add_executable(HostMessagesTest ../tests/test_host_messages.cpp)
  target_include_directories(HostMessagesTest PRIVATE . openfx/Support/include)
  add_test(NAME UnsupportedHostMessages COMMAND HostMessagesTest)
endif()
set_target_properties(CorridorKey2 PROPERTIES PREFIX "" SUFFIX ".ofx")
set(BUNDLE "${CMAKE_BINARY_DIR}/CorridorKey2.ofx.bundle/Contents")
add_custom_command(TARGET CorridorKey2 POST_BUILD
  COMMAND ${CMAKE_COMMAND} -E make_directory "${BUNDLE}/Win64"
  COMMAND ${CMAKE_COMMAND} -E copy "$<TARGET_FILE:CorridorKey2>" "${BUNDLE}/Win64/"
  COMMAND ${CMAKE_COMMAND} -E copy_directory "${CMAKE_CURRENT_SOURCE_DIR}/../backend" "${BUNDLE}/Resources/backend")
add_custom_command(TARGET CorridorKey2 POST_BUILD
  COMMAND ${CMAKE_COMMAND} -E copy "${CMAKE_CURRENT_SOURCE_DIR}/openfx/LICENSE.md" "${BUNDLE}/Resources/OpenFX-LICENSE.md"
  COMMAND ${CMAKE_COMMAND} -E copy "${CMAKE_CURRENT_SOURCE_DIR}/../../LICENSE" "${BUNDLE}/Resources/CorridorKey-LICENSE.txt"
  COMMAND ${CMAKE_COMMAND} -E copy "${CMAKE_CURRENT_SOURCE_DIR}/../../THIRD_PARTY_NOTICES.md" "${BUNDLE}/Resources/THIRD_PARTY_NOTICES.md"
  COMMAND ${CMAKE_COMMAND} -E copy "${CMAKE_CURRENT_SOURCE_DIR}/../V1-LICENSE.txt" "${BUNDLE}/Resources/V1-LICENSE.txt")
