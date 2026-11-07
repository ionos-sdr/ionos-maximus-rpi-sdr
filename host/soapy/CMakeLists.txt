# SPDX-License-Identifier: MIT
# SoapySDR module for ionos-maximus (builds libionos from ../libionos)
cmake_minimum_required(VERSION 3.16)
project(SoapyIonos VERSION 0.1.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

find_package(SoapySDR CONFIG REQUIRED)

set(IONOS_BUILD_TESTS OFF CACHE BOOL "" FORCE)
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../libionos ${CMAKE_CURRENT_BINARY_DIR}/libionos)

SOAPY_SDR_MODULE_UTIL(
  TARGET IonosSupport
  SOURCES SoapyIonos.cpp
  LIBRARIES ionos
)
