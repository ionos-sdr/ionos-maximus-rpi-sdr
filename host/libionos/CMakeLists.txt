# SPDX-License-Identifier: MIT
# libionos — Raspberry Pi I/Q + packet transport for ionos-maximus
cmake_minimum_required(VERSION 3.16)
project(libionos VERSION 0.1.0 LANGUAGES C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
add_compile_options(-Wall -Wextra)

option(IONOS_BUILD_TESTS "Build unit tests" ON)
option(IONOS_WITH_GPIOD "Use libgpiod for DRDY (polling fallback otherwise)" ON)

find_package(Threads REQUIRED)

set(IONOS_SOURCES
  src/ionos.c
  src/backend_replay.c
  src/backend_spi.c)

add_library(ionos ${IONOS_SOURCES})
target_include_directories(ionos PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)
target_link_libraries(ionos PUBLIC Threads::Threads)
set_target_properties(ionos PROPERTIES POSITION_INDEPENDENT_CODE ON
                                       VERSION ${PROJECT_VERSION} SOVERSION 0)

if(IONOS_WITH_GPIOD)
  find_path(GPIOD_INCLUDE_DIR gpiod.h)
  find_library(GPIOD_LIBRARY gpiod)
  if(GPIOD_INCLUDE_DIR AND GPIOD_LIBRARY)
    # v1 API only (gpiod_chip_open_by_name exists in 1.x, not in 2.x)
    include(CheckSymbolExists)
    set(CMAKE_REQUIRED_INCLUDES ${GPIOD_INCLUDE_DIR})
    set(CMAKE_REQUIRED_LIBRARIES ${GPIOD_LIBRARY})
    check_symbol_exists(gpiod_chip_open_by_name gpiod.h IONOS_GPIOD_V1)
    if(IONOS_GPIOD_V1)
      target_compile_definitions(ionos PRIVATE IONOS_HAVE_GPIOD=1)
      target_include_directories(ionos PRIVATE ${GPIOD_INCLUDE_DIR})
      target_link_libraries(ionos PUBLIC ${GPIOD_LIBRARY})
      message(STATUS "libionos: libgpiod v1 found, DRDY edge wait enabled")
    else()
      message(STATUS "libionos: libgpiod found but not the v1 API, DRDY polling fallback")
    endif()
  else()
    message(STATUS "libionos: libgpiod not found, DRDY polling fallback")
  endif()
endif()

# ionos_cat: dump I/Q to stdout, print statistics
add_executable(ionos_cat tools/ionos_cat.c)
target_link_libraries(ionos_cat PRIVATE ionos)

install(TARGETS ionos ionos_cat
        LIBRARY DESTINATION lib ARCHIVE DESTINATION lib RUNTIME DESTINATION bin)
install(FILES include/ionos.h include/pktlink.h DESTINATION include/ionos)

if(IONOS_BUILD_TESTS)
  enable_testing()
  add_executable(test_replay tests/test_replay.c)
  target_link_libraries(test_replay PRIVATE ionos)
  target_include_directories(test_replay PRIVATE src)
  add_test(NAME replay COMMAND test_replay)

  # the firmware's wM-Bus frame code, compiled on the host against the
  # same public vectors as host/tests/test_wmbus.py
  add_executable(test_wmbus_frame tests/test_wmbus_frame.c
                 ${CMAKE_CURRENT_SOURCE_DIR}/../../firmware/sniffer/wmbus_frame.c)
  target_include_directories(test_wmbus_frame PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../../firmware/sniffer)
  add_test(NAME wmbus_frame COMMAND test_wmbus_frame)

  # the shared header must be byte-identical in firmware and host
  add_test(NAME pktlink_header_identical
           COMMAND ${CMAKE_COMMAND} -E compare_files
                   ${CMAKE_CURRENT_SOURCE_DIR}/include/pktlink.h
                   ${CMAKE_CURRENT_SOURCE_DIR}/../../firmware/sniffer/pktlink.h)
endif()
